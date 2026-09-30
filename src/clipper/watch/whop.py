"""Reading a Whop community feed the user belongs to, through Whop's official API.

Whop's terms forbid automated access to its site (D65), but its API is the
sanctioned way in: `GET /forum_posts` accepts a user's OAuth token with the
`forum:read` scope, and a user token reaches what that user can see. So the
user makes their own Whop app, signs in once, and Clipper reads feeds they've
joined (e.g. Content Rewards' campaign posts) as them.

Experimental (D66): kept only if the test against the Content Rewards feed works.
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
from pathlib import Path

import httpx

from ..paths import data_root, ensure

API = "https://api.whop.com/api/v1"
AUTH_URL = "https://api.whop.com/oauth/authorize"
TOKEN_URL = "https://api.whop.com/oauth/token"
PORT = 3456
REDIRECT_URI = f"http://localhost:{PORT}/callback"
ENV_ID, ENV_SECRET, ENV_SCOPES = "WHOP_CLIENT_ID", "WHOP_CLIENT_SECRET", "WHOP_SCOPES"
SCOPES = "openid profile forum:read"


class WhopError(RuntimeError):
    """Whop refused or couldn't be reached; the message is for the user."""


def token_path() -> Path:
    return data_root() / "whop" / "token.json"


def client() -> tuple[str, str]:
    cid = os.environ.get(ENV_ID, "").strip()
    if not cid:
        raise WhopError(f"add your Whop app's client ID to .env as {ENV_ID}")
    return cid, os.environ.get(ENV_SECRET, "").strip()


def pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    digest = hashlib.sha256(verifier.encode()).digest()
    return verifier, base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def login(*, timeout: float = 300.0, open_browser=webbrowser.open) -> dict:
    """Open Whop's consent page, wait for the redirect, keep the tokens."""
    cid, secret = client()
    verifier, challenge = pkce_pair()
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
                              + ("Connected. You can close this tab." if ok else
                                 "Login did not complete. Try again from Clipper.")
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
        "response_type": "code", "client_id": cid, "redirect_uri": REDIRECT_URI,
        "scope": os.environ.get(ENV_SCOPES, "").strip() or SCOPES, "state": state,
        "nonce": secrets.token_urlsafe(16), "code_challenge": challenge,
        "code_challenge_method": "S256"}))
    thread.join(timeout + 5)
    server.server_close()
    if got.get("state") != state:
        raise WhopError("the login didn't come back (timed out, or the state didn't match)")
    if "code" not in got:
        raise WhopError(f"Whop refused the login: {got.get('error_description') or got.get('error')}")
    body = {"grant_type": "authorization_code", "code": got["code"], "redirect_uri": REDIRECT_URI,
            "client_id": cid, "code_verifier": verifier}
    if secret:
        body["client_secret"] = secret
    return _save(_token(body))


def _token(body: dict) -> dict:
    try:
        r = httpx.post(TOKEN_URL, data=body, timeout=20)
    except httpx.HTTPError as exc:
        raise WhopError(f"couldn't reach Whop: {exc}") from exc
    data = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    if r.status_code >= 400 or "access_token" not in data:
        raise WhopError(f"token request failed ({r.status_code}): {data or r.text[:200]}")
    data["obtained_at"] = time.time()
    return data


def _save(token: dict) -> dict:
    path = token_path()
    ensure(path.parent)
    path.write_text(json.dumps(token, indent=1), encoding="utf-8")
    return token


def access_token() -> str:
    """A valid access token; Whop's last an hour and refresh tokens rotate."""
    path = token_path()
    if not path.exists():
        raise WhopError("not signed in to Whop yet: run `clipper whop login`")
    token = json.loads(path.read_text(encoding="utf-8"))
    if time.time() - float(token.get("obtained_at", 0)) < float(token.get("expires_in", 3600)) - 300:
        return token["access_token"]
    cid, secret = client()
    body = {"grant_type": "refresh_token", "refresh_token": token["refresh_token"], "client_id": cid}
    if secret:
        body["client_secret"] = secret
    return _save(_token(body))["access_token"]


def get(path: str, params: dict | None = None) -> dict:
    try:
        r = httpx.get(API + path, params=params, timeout=20,
                      headers={"Authorization": f"Bearer {access_token()}"})
    except httpx.HTTPError as exc:
        raise WhopError(f"couldn't reach Whop: {exc}") from exc
    if r.status_code >= 400:
        raise WhopError(f"Whop answered {r.status_code} for {path}: {r.text[:300]}")
    return r.json()


def experience_in(text: str) -> str | None:
    """An experience id (exp_...) in a pasted Whop link, if there is one."""
    found = re.search(r"exp_[A-Za-z0-9]+", text)
    return found.group(0) if found else None


def posts(experience_id: str, *, first: int = 20) -> list[dict]:
    """The newest top-level posts in a forum/feed experience."""
    return get("/forum_posts", {"experience_id": experience_id, "first": first}).get("data") or []
