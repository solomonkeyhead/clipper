"""Your YouTube Shorts' stats, via YouTube's official APIs (D70).

The Data API gives each upload's title, views, likes and comments; the
Analytics API adds what TikTok's API can't -- average watch time and shares --
for the channel's own videos. Both are read-only (`youtube.readonly`,
`yt-analytics.readonly`).

Google signs you in through your own Google Cloud app (a "Desktop app" OAuth
client), with a loopback redirect Google allows on any port. A Brand Account
channel is picked on Google's consent page. One token file per channel.

Keep the app's consent screen "In production": in "Testing", Google expires
the refresh token after 7 days and the channel would need reconnecting weekly.
"""

from __future__ import annotations

import base64
import hashlib
import http.server
import json
import os
import re
import secrets
import threading
import time
import urllib.parse
import webbrowser
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import httpx

from ..paths import data_root, ensure

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
DATA = "https://www.googleapis.com/youtube/v3"
ANALYTICS = "https://youtubeanalytics.googleapis.com/v2/reports"
SCOPES = ("https://www.googleapis.com/auth/youtube.readonly "
          "https://www.googleapis.com/auth/yt-analytics.readonly")
PORT = 3457
REDIRECT_URI = f"http://127.0.0.1:{PORT}/callback"
ENV_ID, ENV_SECRET = "YOUTUBE_CLIENT_ID", "YOUTUBE_CLIENT_SECRET"
#: Shorts can run up to 3 minutes; anything longer is a regular upload.
SHORT_MAX_SECONDS = 180


class YouTubeError(RuntimeError):
    """Not connected, the sign-in was refused, or YouTube returned an error."""


@dataclass
class Short:
    """One upload, shaped like instagram.api.Reel so the same sync fills the log."""

    id: str
    caption: str
    created: float  # UTC epoch seconds
    url: str
    views: int = 0
    likes: int = 0
    comments: int = 0
    shares: int = 0
    saves: int = 0
    avg_watch_s: float | None = None
    skip_rate_pct: float | None = None
    title: str = ""
    description: str = ""

    @property
    def alternatives(self) -> list[str]:
        """Other texts the post may match its clip by (instagram.sync.apply): a Short's
        title is usually the clip's title or hook, and the caption goes in the
        description -- or both run together (D80)."""
        return [t for t in (f"{self.title} {self.description}".strip(), self.title) if t and t != self.caption]

    @property
    def full_text(self) -> str:
        """Everything a viewer reads: title, then description (the proof pack checks it)."""
        return "\n\n".join(t for t in (self.title, self.description) if t)


def accounts_dir() -> Path:
    return data_root() / "youtube" / "accounts"


def token_files() -> list[Path]:
    folder = accounts_dir()
    return sorted(folder.glob("*.json")) if folder.is_dir() else []


def read_token(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def remove(account: str) -> bool:
    path = accounts_dir() / f"{_safe(account)}.json"
    if not path.exists():
        return False
    path.unlink()
    return True


def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", name)[:80] or "channel"


def client() -> tuple[str, str]:
    cid, secret = os.environ.get(ENV_ID, "").strip(), os.environ.get(ENV_SECRET, "").strip()
    if not cid or not secret:
        raise YouTubeError("add your Google app's client ID and secret on the Accounts page first")
    return cid, secret


def login(*, timeout: float = 300.0, open_browser=webbrowser.open) -> dict:
    """Open Google's consent page, wait for the redirect, keep the channel's tokens."""
    cid, secret = client()
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(16)
    got: dict[str, str] = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            url = urllib.parse.urlparse(self.path)
            if url.path.rstrip("/") != "/callback":
                self.send_response(404)
                self.end_headers()
                return
            got.update(dict(urllib.parse.parse_qsl(url.query)))
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            ok = got.get("state") == state and "code" in got
            self.wfile.write(("<p style='font-family:sans-serif;padding:2em'>"
                              + ("Approved. Go back to Clipper: it says when the channel is connected."
                                 if ok else "Sign-in did not complete. Try again from Clipper.")
                              + "</p>").encode())

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", PORT), Handler)
    server.timeout = 1.0

    def serve():
        deadline = time.monotonic() + timeout
        while "code" not in got and "error" not in got and time.monotonic() < deadline:
            server.handle_request()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    open_browser(AUTH_URL + "?" + urllib.parse.urlencode({
        "response_type": "code", "client_id": cid, "redirect_uri": REDIRECT_URI, "scope": SCOPES,
        "state": state, "code_challenge": challenge, "code_challenge_method": "S256",
        # A refresh token, and the channel picker every time (for a second channel).
        "access_type": "offline", "prompt": "consent select_account"}))
    thread.join(timeout + 5)
    server.server_close()
    if got.get("state") != state:
        raise YouTubeError("the sign-in didn't come back (timed out, or the state didn't match)")
    if "code" not in got:
        raise YouTubeError(f"Google refused the sign-in: {got.get('error_description') or got.get('error')}")
    token = _token({"grant_type": "authorization_code", "code": got["code"], "redirect_uri": REDIRECT_URI,
                    "client_id": cid, "client_secret": secret, "code_verifier": verifier})
    if "refresh_token" not in token:
        raise YouTubeError("Google gave no refresh token; remove Clipper's access at "
                           "myaccount.google.com/permissions and connect again")
    channel = _channel(token["access_token"])
    token.update(channel_id=channel["id"], title=channel["title"], handle=channel["handle"])
    _save(token, accounts_dir() / f"{_safe(channel['id'])}.json")
    token["display_name"] = channel["handle"] or channel["title"]
    return token


def _token(body: dict) -> dict:
    try:
        r = httpx.post(TOKEN_URL, data=body, timeout=20)
    except httpx.HTTPError as exc:
        raise YouTubeError(f"couldn't reach Google: {exc}") from exc
    data = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    if r.status_code >= 400 or "access_token" not in data:
        detail = data.get("error_description") or data.get("error") or r.text[:200]
        if data.get("error") == "invalid_grant":
            detail = ("the sign-in has expired (in \"Testing\", Google ends it after 7 days): set the "
                      "app's consent screen to In production, then connect the channel again")
        raise YouTubeError(f"sign-in failed: {detail}")
    data["obtained_at"] = time.time()
    return data


def _save(token: dict, path: Path) -> None:
    ensure(path.parent)
    path.write_text(json.dumps(token, indent=1), encoding="utf-8")


def access_token(path: Path) -> str:
    """A valid access token for the channel; Google's last an hour."""
    token = read_token(path)
    if time.time() - float(token.get("obtained_at", 0)) < float(token.get("expires_in", 3600)) - 300:
        return token["access_token"]
    cid, secret = client()
    fresh = _token({"grant_type": "refresh_token", "refresh_token": token["refresh_token"],
                    "client_id": cid, "client_secret": secret})
    token.update(access_token=fresh["access_token"], expires_in=fresh.get("expires_in", 3600),
                 obtained_at=fresh["obtained_at"])
    _save(token, path)
    return token["access_token"]


def _get(url: str, token: str, params: dict) -> dict:
    try:
        r = httpx.get(url, params=params, headers={"Authorization": f"Bearer {token}"}, timeout=30)
    except httpx.HTTPError as exc:
        raise YouTubeError(f"couldn't reach YouTube: {exc}") from exc
    if r.status_code >= 400:
        try:
            message = r.json()["error"]["message"]
        except (ValueError, KeyError, TypeError):
            message = r.text[:200]
        if "has not been used" in message or "is disabled" in message:
            message = ("turn on the YouTube Data API v3 and YouTube Analytics API in your "
                       "Google Cloud project, then sync again")
        raise YouTubeError(message)
    return r.json()


def _channel(token: str) -> dict:
    items = _get(f"{DATA}/channels", token, {"part": "snippet,contentDetails", "mine": "true"}).get("items") or []
    if not items:
        raise YouTubeError("that Google account has no YouTube channel; pick the channel itself on the sign-in page")
    c = items[0]
    return {"id": c["id"], "title": c["snippet"].get("title") or "",
            "handle": (c["snippet"].get("customUrl") or "").lstrip("@"),
            "uploads": c["contentDetails"]["relatedPlaylists"]["uploads"]}


def seconds(iso: str) -> float:
    """An ISO 8601 duration (PT1M5S) in seconds."""
    m = re.fullmatch(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso or "")
    if not m:
        return 0.0
    d, h, mi, s = (int(x or 0) for x in m.groups())
    return float(d * 86400 + h * 3600 + mi * 60 + s)


def list_shorts(token: str, *, limit: int = 200) -> list[Short]:
    """The channel's Shorts (uploads up to 3 minutes), newest first, with stats."""
    channel = _channel(token)
    ids: list[str] = []
    page = None
    while len(ids) < limit:
        got = _get(f"{DATA}/playlistItems", token, {"part": "contentDetails", "maxResults": 50,
                                                  "playlistId": channel["uploads"], **({"pageToken": page} if page else {})})
        ids += [i["contentDetails"]["videoId"] for i in got.get("items") or []]
        page = got.get("nextPageToken")
        if not page:
            break
    shorts: list[Short] = []
    for start in range(0, len(ids), 50):
        got = _get(f"{DATA}/videos", token, {"part": "snippet,statistics,contentDetails",
                                           "id": ",".join(ids[start:start + 50])})
        for v in got.get("items") or []:
            if seconds(v["contentDetails"].get("duration", "")) > SHORT_MAX_SECONDS:
                continue
            sn, st = v["snippet"], v.get("statistics") or {}
            shorts.append(Short(
                id=v["id"], url=f"https://www.youtube.com/shorts/{v['id']}",
                # The clip's caption usually goes in the description, under a title.
                caption=(sn.get("description") or sn.get("title") or "").strip(),
                title=(sn.get("title") or "").strip(), description=(sn.get("description") or "").strip(),
                created=datetime.fromisoformat(sn["publishedAt"].replace("Z", "+00:00")).timestamp(),
                views=int(st.get("viewCount", 0)), likes=int(st.get("likeCount", 0)),
                comments=int(st.get("commentCount", 0))))
    _add_analytics(shorts, token)
    return shorts


def _add_analytics(shorts: list[Short], token: str) -> None:
    """Average watch time and shares, which only the Analytics API has.

    Analytics lags a day or two behind; a Short too new for it keeps None.
    Optional: a project without the Analytics API still syncs the counts.
    """
    if not shorts:
        return
    first = min(s.created for s in shorts)
    try:
        got = _get(ANALYTICS, token, {
            "ids": "channel==MINE", "dimensions": "video", "metrics": "averageViewDuration,shares",
            "filters": "video==" + ",".join(s.id for s in shorts[:200]),
            "startDate": date.fromtimestamp(first).isoformat(), "endDate": date.today().isoformat()})
    except YouTubeError:
        return
    by_id = {row[0]: row for row in got.get("rows") or []}
    for s in shorts:
        row = by_id.get(s.id)
        if row:
            s.avg_watch_s, s.shares = float(row[1]), int(row[2])


def name(path: Path) -> str:
    token = read_token(path)
    return token.get("handle") or token.get("title") or path.stem
