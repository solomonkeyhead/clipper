"""Import campaign footage from a shared link: Google Drive, Dropbox, or a direct video URL.

Campaigns share footage as Drive or Dropbox links, so the New clips page takes
the link instead of making the user download and re-upload. Only links shared
publicly ("anyone with the link") work: Clipper signs in to nothing. Password-
protected links, WeTransfer and Frame.io are refused with a message saying to
download the file by hand.

* Drive file:   drive.google.com/file/d/<id>/...  -> drive.usercontent.google.com download
* Drive folder: drive.google.com/drive/folders/<id> -> its public folder view, listed
* Dropbox file: dropbox.com/scl/fi/... or /s/...   -> the same link with dl=1
* Dropbox folder: dropbox.com/scl/fo/... or /sh/... -> dl=1 returns a zip; its videos are kept
* Anything else ending in a video extension         -> downloaded as is

Each imported file's link is remembered (downloads/.imports.json), so a clip's
proof pack can say where its footage came from.

Safety (D229): a link only ever reaches public addresses (every connection, the first and each redirect's, is
checked after its name is resolved, so a link can't make Clipper fetch from the computer itself or the network
it sits on); hosted, only Drive and Dropbox links are taken; files and zips have a size cap (`upload_cap`).
"""

from __future__ import annotations

import html
import http.client
import ipaddress
import itertools
import json
import os
import re
import socket
import threading
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from dataclasses import asdict, dataclass, field
from datetime import datetime
from email.message import Message
from pathlib import Path

from ..paths import downloads_dir, ensure
from ..utils.logging import get_logger

log = get_logger(__name__)

VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".m4v", ".webm", ".avi"}
CHUNK = 1 << 20
TIMEOUT = 60
AGENT = "Mozilla/5.0 (Clipper footage import)"


class ImportError_(ValueError):
    """The link can't be imported; the message says what to do instead."""


@dataclass
class RemoteFile:
    name: str
    url: str
    size: int | None = None


@dataclass
class Found:
    kind: str                       # drive-file | drive-folder | dropbox-file | dropbox-folder | direct
    files: list[RemoteFile]
    zipped: bool = False            # one download holding every file (a Dropbox folder)


def _safe_name(name: str) -> str:
    name = re.sub(r"[^\w .()\-]+", "_", Path(name).name).strip() or "footage.mp4"
    return name[:150]


def free_name(folder: Path, name: str) -> Path:
    """`folder/name`, or "name (2)", "name (3)"... when that is taken: an upload never overwrites (D229)."""
    target, n = folder / name, 1
    while target.exists() or target.with_name(target.name + ".part").exists():
        n += 1
        target = folder / f"{Path(name).stem} ({n}){Path(name).suffix}"
    return target


def upload_cap() -> int | None:
    """The most one uploaded or imported file may be, in bytes (D229): CLIPPER_MAX_UPLOAD_GB, else no cap on
    one's own computer and 8 GB on a server, where a full disk is everyone's problem."""
    from ..hosting import hosted

    given = os.environ.get("CLIPPER_MAX_UPLOAD_GB", "").strip()
    try:
        gb = float(given) if given else (8.0 if hosted() else None)
    except ValueError:
        gb = 8.0
    return int(gb * 1024**3) if gb else None


def too_big(cap: int) -> str:
    return f"That file is over {cap / 1024**3:g} GB, the most one file can be here."


async def receive(request, target: Path, cap: int | None) -> Path:
    """Stream a request's body into `target` (via a .part file, removed if the upload fails or is cut off)."""
    from fastapi import HTTPException

    length = request.headers.get("content-length", "")
    if cap and length.isdecimal() and int(length) > cap:
        raise HTTPException(413, too_big(cap))
    partial = target.with_name(target.name + ".part")
    received = 0
    try:
        with partial.open("wb") as out:
            async for chunk in request.stream():
                received += len(chunk)
                if cap and received > cap:
                    raise HTTPException(413, too_big(cap))
                out.write(chunk)
        partial.replace(target)
    finally:
        partial.unlink(missing_ok=True)
    return target


def _is(host: str, *domains: str) -> bool:
    """The host is one of these domains or under one: `evildropbox.com` is not dropbox.com (D229)."""
    return any(host == d or host.endswith("." + d) for d in domains)


def _blocked(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Not a public internet address: this computer, its network, link-local (cloud metadata), reserved, multicast."""
    if ip.version == 6 and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return not ip.is_global or ip.is_multicast or ip.is_private or ip.is_reserved


PRIVATE = ("That link points to a private or local address. Clipper only downloads from public sites; "
           "download the file and drop it in.")


def _public_connection(address, timeout=socket._GLOBAL_DEFAULT_TIMEOUT, source_address=None, **_):
    """socket.create_connection, for public addresses only (D229). The name is resolved and checked here, at
    connect time, and the connection goes to the very address checked, so neither a redirect nor a name that
    resolves differently the second time can reach the computer or its network."""
    from ..hosting import hosted

    host, port = address
    if hosted() and port not in (80, 443):
        raise ImportError_(PRIVATE)
    try:
        found = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError:
        raise ImportError_("Couldn't reach that site.") from None
    ips = [info[4][0] for info in found]
    if not ips or any(_blocked(ipaddress.ip_address(ip.split("%")[0])) for ip in ips):
        raise ImportError_(PRIVATE)
    last: OSError | None = None
    for ip in ips:
        try:
            return socket.create_connection((ip, port), timeout, source_address)
        except OSError as exc:
            last = exc
    raise last or OSError("no address")


class _PublicHTTP(http.client.HTTPConnection):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._create_connection = _public_connection


class _PublicHTTPS(http.client.HTTPSConnection):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._create_connection = _public_connection


class _HTTPHandler(urllib.request.HTTPHandler):
    def http_open(self, req):
        return self.do_open(_PublicHTTP, req)


class _HTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, req):
        return self.do_open(_PublicHTTPS, req, context=self._context)


def _opener() -> urllib.request.OpenerDirector:
    """http and https only (a redirect to ftp:// or file:// goes nowhere), no proxy (it would be the one
    address checked), every connection through `_public_connection`."""
    opener = urllib.request.OpenerDirector()
    for handler in (urllib.request.ProxyHandler({}), urllib.request.UnknownHandler(), _HTTPHandler(),
                    _HTTPSHandler(), urllib.request.HTTPDefaultErrorHandler(),
                    urllib.request.HTTPRedirectHandler(), urllib.request.HTTPErrorProcessor()):
        opener.add_handler(handler)
    return opener


def _open(url: str):
    request = urllib.request.Request(url, headers={"User-Agent": AGENT})
    try:
        return _opener().open(request, timeout=TIMEOUT)
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403, 404):
            raise ImportError_("That link isn't public. Set its sharing to \"Anyone with the "
                               "link\", or download the file and drop it in.") from None
        raise ImportError_(f"The site answered HTTP {exc.code}; try again later.") from None
    except (urllib.error.URLError, OSError, http.client.HTTPException):
        # One message whatever went wrong: which error it was would tell whether a port is open (D229).
        raise ImportError_("Couldn't reach that site.") from None


def inspect(url: str) -> Found:
    """What's behind a link, without downloading the videos."""
    from ..hosting import hosted

    url = url.strip()
    parsed = urllib.parse.urlparse(url)
    try:
        host = (parsed.hostname or "").lower()
        parsed.port  # noqa: B018 -- ValueError for a port that isn't a number
    except ValueError:
        host = ""
    if parsed.scheme not in ("http", "https") or not host:
        raise ImportError_("Paste the full link, starting with https://")
    if _is(host, "wetransfer.com", "we.tl", "frame.io", "f.io", "mega.nz"):
        raise ImportError_("Clipper can't open that site's links. Download the file there and drop it in.")
    if _is(host, "drive.google.com", "docs.google.com"):
        folder = re.search(r"/folders/([\w-]+)", parsed.path)
        if folder:
            return _drive_folder(folder.group(1))
        file_id = (re.search(r"/(?:file/)?d/([\w-]+)", parsed.path)
                   or re.search(r"[?&]id=([\w-]+)", url))
        if not file_id:
            raise ImportError_("That doesn't look like a Drive file or folder link.")
        return Found("drive-file", [RemoteFile(name="", url=_drive_download(file_id.group(1)))])
    if _is(host, "dropbox.com", "dropboxusercontent.com"):
        direct = _with_query(url, dl="1")
        if "/fo/" in parsed.path or "/sh/" in parsed.path:
            return Found("dropbox-folder", [RemoteFile(name="dropbox-folder.zip", url=direct)], zipped=True)
        return Found("dropbox-file", [RemoteFile(name=_safe_name(urllib.parse.unquote(Path(parsed.path).name)),
                                                 url=direct)])
    if hosted():  # a link to anywhere is the owner's own computer's to fetch, not a server's (D229)
        raise ImportError_("Clipper imports public Google Drive and Dropbox links. For anything else, "
                           "download the file and drop it in.")
    if Path(urllib.parse.unquote(parsed.path)).suffix.lower() in VIDEO_EXTENSIONS:
        return Found("direct", [RemoteFile(name=_safe_name(urllib.parse.unquote(Path(parsed.path).name)), url=url)])
    raise ImportError_("Clipper imports public Google Drive and Dropbox links, or a direct link to "
                       "a video file. For anything else, download the file and drop it in.")


def _with_query(url: str, **params) -> str:
    parts = urllib.parse.urlparse(url)
    query = dict(urllib.parse.parse_qsl(parts.query))
    query.update(params)
    return urllib.parse.urlunparse(parts._replace(query=urllib.parse.urlencode(query)))


def _drive_download(file_id: str) -> str:
    # `confirm=t` skips the "can't scan large files for viruses" page.
    return ("https://drive.usercontent.google.com/download?"
            + urllib.parse.urlencode({"id": file_id, "export": "download", "confirm": "t"}))


def _drive_folder(folder_id: str) -> Found:
    """The videos in a public Drive folder, from its embeddable folder view."""
    with _open(f"https://drive.google.com/embeddedfolderview?id={folder_id}") as response:
        page = response.read().decode("utf-8", "replace")
    files = []
    for entry in re.finditer(r'id="entry-([\w-]+)".*?flip-entry-title">(.*?)</div>', page, re.S):
        name = html.unescape(re.sub(r"<[^>]+>", "", entry.group(2))).strip()
        if Path(name).suffix.lower() in VIDEO_EXTENSIONS:
            files.append(RemoteFile(name=_safe_name(name), url=_drive_download(entry.group(1))))
    if not files:
        raise ImportError_("No videos found in that folder. If it has subfolders, open one and "
                           "paste its link; if it's private, set sharing to \"Anyone with the link\".")
    return Found("drive-folder", files)


def _filename(response, fallback: str) -> str:
    message = Message()
    message["content-disposition"] = response.headers.get("Content-Disposition", "")
    name = message.get_filename() or ""
    return _safe_name(name) if name else fallback


def remember(name: str, link: str) -> None:
    """Keep where an imported file came from, for proof packs."""
    path = ensure(downloads_dir()) / ".imports.json"
    try:
        known = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except ValueError:
        known = {}
    known[name] = {"link": link, "imported_at": datetime.now().strftime("%Y-%m-%d %H:%M")}
    path.write_text(json.dumps(known, indent=1), encoding="utf-8")


def origin(title: str) -> dict | None:
    """The link a source file (by name or stem) was imported from, if it was."""
    path = downloads_dir() / ".imports.json"
    if not path.exists():
        return None
    try:
        known = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return None
    return next((v for k, v in known.items() if k == title or Path(k).stem == title), None)


@dataclass
class Import:
    id: int
    link: str
    kind: str
    names: list[str]
    status: str = "queued"          # queued | running | done | failed
    current: str = ""
    done_bytes: int = 0
    total_bytes: int | None = None
    files_done: int = 0
    message: str = ""
    saved: list[str] = field(default_factory=list)

    def view(self) -> dict:
        return asdict(self)


class ImportRunner:
    """One download at a time in the background; the page follows `import.progress`."""

    def __init__(self, publish) -> None:
        self.publish = publish
        self.imports: dict[int, Import] = {}
        self._ids = itertools.count(1)
        self._lock = threading.Lock()

    def list(self) -> list[dict]:
        return [i.view() for i in sorted(self.imports.values(), key=lambda i: -i.id)][:10]

    def start(self, link: str, found: Found, pick: list[str] | None = None, *,
              campaign: str = "") -> Import:
        files = [f for f in found.files if not pick or f.name in pick or found.zipped]
        item = Import(id=next(self._ids), link=link, kind=found.kind,
                      names=[f.name or "Drive file" for f in files])
        self.imports[item.id] = item
        threading.Thread(target=self._run, args=(item, files, found.zipped, campaign), daemon=True,
                         name=f"import-{item.id}").start()
        self.publish("import.progress", item.view())
        return item

    def _run(self, item: Import, files: list[RemoteFile], zipped: bool, campaign: str = "") -> None:
        with self._lock:                      # one download at a time
            item.status = "running"
            try:
                for index, remote in enumerate(files):
                    saved = self._download(item, remote, index)
                    if zipped:
                        try:
                            item.saved += self._unzip(saved)
                        finally:
                            saved.unlink(missing_ok=True)
                    else:
                        item.saved.append(saved.name)
                    item.files_done += 1
                for name in item.saved:
                    remember(name, item.link)
                if campaign:  # filed under the campaign it was imported for (studio/footage.py)
                    from .footage import remember as file_under

                    file_under([str(downloads_dir() / n) for n in item.saved], campaign, "added")
                if not item.saved:
                    raise ImportError_("The download had no video files in it.")
                item.status = "done"
                item.message = f"Added {len(item.saved)} video{'s' if len(item.saved) != 1 else ''}"
            except ImportError_ as exc:
                item.status, item.message = "failed", str(exc)
            except Exception as exc:  # shown on the page; logged for debugging
                log.exception("import %s failed", item.id)
                item.status, item.message = "failed", f"The download stopped: {str(exc)[:160]}"
            finally:
                self.publish("import.progress", item.view())
                self.publish("sources.changed")

    def _download(self, item: Import, remote: RemoteFile, index: int) -> Path:
        with _open(remote.url) as response:
            kind = response.headers.get("Content-Type", "")
            if kind.startswith("text/html"):
                raise ImportError_("That link opens a web page, not the file: it may be private, "
                                   "over its download limit, or need a password. Download it by "
                                   "hand and drop it in.")
            name = _filename(response, remote.name or f"drive-file-{index + 1}.mp4")
            if Path(name).suffix.lower() not in VIDEO_EXTENSIONS | {".zip"}:
                raise ImportError_(f"{name} isn't a video file.")
            cap = upload_cap()
            if cap and name.lower().endswith(".zip"):
                cap *= 4  # a folder's zip holds several files; what it unpacks is capped again in _unzip
            target = free_name(ensure(downloads_dir()), name)
            partial = target.with_name(target.name + ".part")
            item.current, item.done_bytes = target.name, 0
            length = response.headers.get("Content-Length")
            item.total_bytes = int(length) if length and length.isdecimal() else None
            if cap and item.total_bytes and item.total_bytes > cap:
                raise ImportError_(too_big(cap))
            last = 0
            try:
                with partial.open("wb") as out:
                    while chunk := response.read(CHUNK):
                        out.write(chunk)
                        item.done_bytes += len(chunk)
                        if cap and item.done_bytes > cap:
                            raise ImportError_(too_big(cap))
                        if item.done_bytes - last >= 16 * CHUNK:
                            last = item.done_bytes
                            self.publish("import.progress", item.view())
                partial.replace(target)
            finally:  # a failed or cut-off download leaves no half file behind (D229)
                partial.unlink(missing_ok=True)
            return target

    def _unzip(self, archive: Path) -> list[str]:
        """The videos in a downloaded folder's zip. With a cap (D229): no member over it, and no more than
        4 times it in all, checked before anything is written and again while it is (a zip can lie)."""
        cap = upload_cap()
        saved = []
        with zipfile.ZipFile(archive) as z:
            videos = [i for i in z.infolist()
                      if not i.is_dir() and Path(_safe_name(i.filename)).suffix.lower() in VIDEO_EXTENSIONS]
            if cap and (any(i.file_size > cap for i in videos) or sum(i.file_size for i in videos) > 4 * cap):
                raise ImportError_(f"That folder is too big to import here (over {cap / 1024**3:g} GB a file, "
                                   f"or {4 * cap / 1024**3:g} GB in all). Download it and drop the videos in.")
            total = 0
            for info in videos:
                target = free_name(downloads_dir(), _safe_name(info.filename))
                partial = target.with_name(target.name + ".part")
                written = 0
                try:
                    with z.open(info) as src, partial.open("wb") as out:
                        while chunk := src.read(CHUNK):
                            written += len(chunk)
                            total += len(chunk)
                            if cap and (written > cap or total > 4 * cap):
                                raise ImportError_("That folder unpacks to more than it said; not imported.")
                            out.write(chunk)
                    partial.replace(target)
                finally:
                    partial.unlink(missing_ok=True)
                saved.append(target.name)
        return saved
