"""Reading a Whop community feed the user belongs to, through Whop's official API.

Whop's terms forbid automated access to its site (D65), but its API is the
sanctioned way in: `GET /forum_posts` accepts a user's OAuth token with the
`forum:read` scope, and a user token reaches what that user can see. So the
user makes their own Whop app, signs in once, and Clipper reads feeds they've
joined (e.g. Content Rewards' campaign posts) as them.

Worked (D69): the sign-in and the feed picker live in the Control Center.
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
# Read-only: your joined communities (member), their feeds (company), their posts (forum).
SCOPES = "openid profile member:basic:read company:basic:read forum:read"


class WhopError(RuntimeError):
    """Whop refused or couldn't be reached; the message is for the user."""


def token_path() -> Path:
    return data_root() / "whop" / "token.json"


def client() -> tuple[str, str]:
    cid = os.environ.get(ENV_ID, "").strip()
    if not cid:
        raise WhopError("save your Whop app's ID first (Find campaigns > Set up alerts > Whop)")
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
                              + ("Approved. Go back to the terminal: it says when Clipper has finished connecting." if ok else
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
        raise WhopError("not signed in to Whop yet: Find campaigns > Set up alerts > Whop")
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


def posts(experience_id: str, *, first: int = 20) -> list[dict]:
    """The newest top-level posts in a forum/feed experience."""
    return get("/forum_posts", {"experience_id": experience_id, "first": first}).get("data") or []


def signed_in() -> bool:
    return token_path().exists()


def sign_out() -> None:
    token_path().unlink(missing_ok=True)


def feeds() -> list[dict]:
    """Forum feeds in the communities the user belongs to: [{id, name, company}].

    A canceled membership is left out: Whop refuses its members-only posts.
    """
    out, seen = [], set()
    for m in get("/memberships", {"first": 100}).get("data") or []:
        company = m.get("company") or {}
        cid = company.get("id")
        if not cid or cid in seen or m.get("status") == "canceled":
            continue
        seen.add(cid)
        try:
            listed = get("/experiences", {"account_id": cid, "first": 100}).get("data") or []
        except WhopError:  # one community refusing shouldn't hide the others
            continue
        names: dict[str, int] = {}
        for e in listed:
            if "forum" in ((e.get("app") or {}).get("name") or "").lower():
                name = e.get("name") or "Feed"
                names[name] = names.get(name, 0) + 1
                # Content Rewards has two feeds named "New Campaigns" (the same posts).
                shown = name if names[name] == 1 else f"{name} ({names[name]})"
                out.append({"id": e["id"], "name": shown, "company": company.get("title") or ""})
    return out


def new_posts(experience_id: str, *, after: str | None, first: int = 25) -> list[dict]:
    """Top-level posts newer than `after` (an ISO time), oldest first."""
    got = [p for p in posts(experience_id, first=first) if not p.get("parent_id")]
    if after:
        got = [p for p in got if (p.get("created_at") or "") > after]
    return sorted(got, key=lambda p: p.get("created_at") or "")


def text_of(post: dict) -> str:
    return "\n".join(x.strip() for x in (post.get("title") or "", post.get("content") or "") if x and x.strip())


def link_in(text: str) -> str:
    """The campaign link a post carries, if it points at a campaign site."""
    from .judge import safe_link

    for url in re.findall(r"https://\S+", text):
        if safe_link(url.rstrip(").,")):
            return url.rstrip(").,")
    return ""
