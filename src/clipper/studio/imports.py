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
"""

from __future__ import annotations

import html
import itertools
import json
import re
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


def _open(url: str):
    request = urllib.request.Request(url, headers={"User-Agent": AGENT})
    try:
        return urllib.request.urlopen(request, timeout=TIMEOUT)
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403, 404):
            raise ImportError_("That link isn't public. Set its sharing to \"Anyone with the "
                               "link\", or download the file and drop it in.") from None
        raise ImportError_(f"The site answered HTTP {exc.code}; try again later.") from None
    except (urllib.error.URLError, OSError) as exc:
        raise ImportError_(f"Couldn't reach that site ({type(exc).__name__}).") from None


def inspect(url: str) -> Found:
    """What's behind a link, without downloading the videos."""
    url = url.strip()
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ImportError_("Paste the full link, starting with https://")
    host = parsed.netloc.lower()
    if host.endswith(("wetransfer.com", "we.tl", "frame.io", "f.io", "mega.nz")):
        raise ImportError_("Clipper can't open that site's links. Download the file there and drop it in.")
    if "drive.google.com" in host or "docs.google.com" in host:
        folder = re.search(r"/folders/([\w-]+)", parsed.path)
        if folder:
            return _drive_folder(folder.group(1))
        file_id = (re.search(r"/(?:file/)?d/([\w-]+)", parsed.path)
                   or re.search(r"[?&]id=([\w-]+)", url))
        if not file_id:
            raise ImportError_("That doesn't look like a Drive file or folder link.")
        return Found("drive-file", [RemoteFile(name="", url=_drive_download(file_id.group(1)))])
    if host.endswith("dropbox.com") or host.endswith("dropboxusercontent.com"):
        direct = _with_query(url, dl="1")
        if "/fo/" in parsed.path or "/sh/" in parsed.path:
            return Found("dropbox-folder", [RemoteFile(name="dropbox-folder.zip", url=direct)], zipped=True)
        return Found("dropbox-file", [RemoteFile(name=_safe_name(urllib.parse.unquote(Path(parsed.path).name)),
                                                 url=direct)])
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
                        item.saved += self._unzip(saved)
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
            target = ensure(downloads_dir()) / name
            partial = target.with_name(target.name + ".part")
            item.current, item.done_bytes = name, 0
            length = response.headers.get("Content-Length")
            item.total_bytes = int(length) if length and length.isdigit() else None
            last = 0
            with partial.open("wb") as out:
                while chunk := response.read(CHUNK):
                    out.write(chunk)
                    item.done_bytes += len(chunk)
                    if item.done_bytes - last >= 16 * CHUNK:
                        last = item.done_bytes
                        self.publish("import.progress", item.view())
            partial.replace(target)
            return target

    def _unzip(self, archive: Path) -> list[str]:
        saved = []
        with zipfile.ZipFile(archive) as z:
            for info in z.infolist():
                name = _safe_name(info.filename)
                if info.is_dir() or Path(name).suffix.lower() not in VIDEO_EXTENSIONS:
                    continue
                with z.open(info) as src, (downloads_dir() / name).open("wb") as out:
                    while chunk := src.read(CHUNK):
                        out.write(chunk)
                saved.append(name)
        return saved
