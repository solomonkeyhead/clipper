"""Login (desktop OAuth with PKCE), token storage and refresh, and the video list.

Per TikTok's docs for Login Kit for Desktop: the redirect must be localhost or
127.0.0.1 with a port, PKCE is mandatory, and the code challenge is the *hex*
SHA-256 of the verifier (not base64url as in RFC 7636). Access tokens last 24
hours; the refresh token 365 days and may be rotated on refresh.
"""

from __future__ import annotations

import hashlib
import http.server
import json
import os
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from dataclasses import dataclass
from pathlib import Path

from ..paths import data_root, ensure
from ..utils.logging import get_logger

log = get_logger(__name__)

AUTH_URL = "https://www.tiktok.com/v2/auth/authorize/"
TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/"
VIDEO_LIST_URL = "https://open.tiktokapis.com/v2/video/list/"
SCOPES = "user.info.basic,video.list"
PORT = 3455
REDIRECT_URI = f"http://localhost:{PORT}/callback/"
VIDEO_FIELDS = ("id,title,video_description,create_time,share_url,duration,"
                "view_count,like_count,comment_count,share_count")
ENV_KEY, ENV_SECRET = "TIKTOK_CLIENT_KEY", "TIKTOK_CLIENT_SECRET"


class TikTokError(RuntimeError):
    """Setup is missing, the login failed, or TikTok returned an error."""


@dataclass
class Video:
    id: str
    caption: str
    created: float  # UTC epoch seconds
    url: str
    duration: float
    views: int
    likes: int
    comments: int
    shares: int


def token_path() -> Path:
    # Under data/, which is git-ignored: the refresh token is a secret.
    return data_root() / "tiktok" / "token.json"


def client() -> tuple[str, str]:
    key, secret = os.environ.get(ENV_KEY, "").strip(), os.environ.get(ENV_SECRET, "").strip()
    if not key or not secret:
        raise TikTokError(f"missing in .env: {ENV_KEY} and/or {ENV_SECRET} "
                          "(TikTok for Developers -> your app -> App details)")
    return key, secret


def pkce_pair() -> tuple[str, str]:
    """(verifier, challenge): TikTok's desktop flow wants the hex SHA-256."""
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~"
    verifier = "".join(secrets.choice(alphabet) for _ in range(64))
    return verifier, hashlib.sha256(verifier.encode()).hexdigest()


def authorize_url(client_key: str, state: str, challenge: str) -> str:
    query = urllib.parse.urlencode({
        "client_key": client_key, "response_type": "code", "scope": SCOPES,
        "redirect_uri": REDIRECT_URI, "state": state,
        "code_challenge": challenge, "code_challenge_method": "S256"})
    return f"{AUTH_URL}?{query}"


def login(*, timeout: float = 300.0, open_browser=webbrowser.open) -> dict:
    """Open TikTok's consent page, wait for the redirect, store the tokens."""
    key, secret = client()
    verifier, challenge = pkce_pair()
    state = secrets.token_urlsafe(16)
    got: dict[str, str] = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            params = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(self.path).query))
            if urllib.parse.urlparse(self.path).path.rstrip("/") != "/callback":
                self.send_response(404)
                self.end_headers()
                return
            got.update(params)
            ok = params.get("state") == state and "code" in params
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            message = ("Connected. You can close this tab and go back to the terminal."
                       if ok else "Login did not complete. Go back to the terminal.")
            self.wfile.write(f"<html><body style='font-family:sans-serif;padding:2em'>"
                             f"<h2>clipper</h2><p>{message}</p></body></html>".encode())

        def log_message(self, *args):  # keep the console quiet
            pass

    server = http.server.HTTPServer(("127.0.0.1", PORT), Handler)
    server.timeout = 1.0
    thread = threading.Thread(target=lambda: _serve_until(server, got, timeout), daemon=True)
    thread.start()
    open_browser(authorize_url(key, state, challenge))
    thread.join(timeout + 5)
    server.server_close()

    if got.get("state") != state:
        raise TikTokError("the login did not come back (timed out, or the state did not match)")
    if "code" not in got:
        raise TikTokError(f"TikTok refused the login: {got.get('error_description') or got.get('error') or 'no code'}")
    token = _token_request({"client_key": key, "client_secret": secret,
                            "code": got["code"], "grant_type": "authorization_code",
                            "redirect_uri": REDIRECT_URI, "code_verifier": verifier})
    _save(token)
    return token


def _serve_until(server: http.server.HTTPServer, got: dict, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while "code" not in got and "error" not in got and time.monotonic() < deadline:
        server.handle_request()


def _token_request(body: dict) -> dict:
    request = urllib.request.Request(
        TOKEN_URL, data=urllib.parse.urlencode(body).encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded"}, method="POST")
    data = _send(request)
    if "access_token" not in data:
        raise TikTokError(f"token request failed: {data.get('error_description') or data}")
    data["obtained_at"] = time.time()
    return data


def _save(token: dict) -> None:
    path = token_path()
    ensure(path.parent)
    path.write_text(json.dumps(token, indent=1), encoding="utf-8")


def access_token() -> str:
    """A valid access token, refreshed when it is within an hour of expiring."""
    path = token_path()
    if not path.exists():
        raise TikTokError("not connected yet: run `clipper tiktok login` once")
    token = json.loads(path.read_text(encoding="utf-8"))
    age = time.time() - float(token.get("obtained_at", 0))
    if age < float(token.get("expires_in", 0)) - 3600:
        return token["access_token"]
    if age > float(token.get("refresh_expires_in", 0)):
        raise TikTokError("the TikTok login has expired: run `clipper tiktok login` again")
    key, secret = client()
    fresh = _token_request({"client_key": key, "client_secret": secret,
                            "grant_type": "refresh_token",
                            "refresh_token": token["refresh_token"]})
    _save(fresh)
    return fresh["access_token"]


def list_videos(token: str, *, limit: int = 200) -> list[Video]:
    """The account's videos, newest first."""
    videos: list[Video] = []
    cursor = None
    while len(videos) < limit:
        body = {"max_count": 20}
        if cursor is not None:
            body["cursor"] = cursor
        request = urllib.request.Request(
            f"{VIDEO_LIST_URL}?fields={VIDEO_FIELDS}", data=json.dumps(body).encode(),
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            method="POST")
        data = _send(request)
        error = data.get("error") or {}
        if error.get("code") not in (None, "ok"):
            raise TikTokError(f"video list failed: {error.get('message') or error.get('code')}")
        page = data.get("data") or {}
        for v in page.get("videos") or []:
            videos.append(Video(
                id=str(v.get("id", "")),
                caption=(v.get("video_description") or v.get("title") or "").strip(),
                created=float(v.get("create_time") or 0), url=v.get("share_url") or "",
                duration=float(v.get("duration") or 0), views=int(v.get("view_count") or 0),
                likes=int(v.get("like_count") or 0), comments=int(v.get("comment_count") or 0),
                shares=int(v.get("share_count") or 0)))
        if not page.get("has_more"):
            break
        cursor = page.get("cursor")
    return videos


def _send(request: urllib.request.Request) -> dict:
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            return json.loads(exc.read().decode("utf-8"))
        except (ValueError, OSError):
            raise TikTokError(f"TikTok answered HTTP {exc.code}") from exc
    except (urllib.error.URLError, OSError) as exc:
        raise TikTokError(f"cannot reach TikTok: {exc}") from exc
