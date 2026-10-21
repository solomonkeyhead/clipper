"""The connect service (D151): one registered app per platform, so a user only logs in.

Run it on any host with HTTPS (`python -m clipper.broker`) and give Clipper its address as CLIPPER_BROKER_URL.
It keeps no accounts and no tokens: it sends the browser to the platform, swaps the returned code for tokens
with the app's secret, and posts them to the Clipper on the user's own computer. `/refresh` does the same for
a refresh token. Settings, all environment variables:

  PUBLIC_URL             this service's public address, e.g. https://connect.example.com
  BROKER_SIGNING_KEY     a long random string; signs the state that rides through the platform's login
                         (unset: a random one per process, so a restart ends the logins under way)
  TIKTOK_CLIENT_KEY / TIKTOK_CLIENT_SECRET          a TikTok "Login Kit for web" app, redirect PUBLIC_URL/callback/tiktok
  YOUTUBE_CLIENT_ID / YOUTUBE_CLIENT_SECRET         a Google "Web application" client, redirect PUBLIC_URL/callback/youtube
  INSTAGRAM_APP_ID / INSTAGRAM_APP_SECRET           a Meta app with Instagram login, redirect PUBLIC_URL/callback/instagram

The platforms' own review decides who may log in (sandbox: listed testers only; after review: anyone).

The hosted site's gateway (D239) serves this same app at `<CLIPPER_PUBLIC_URL>/connect` (MOUNT), with PUBLIC_URL
left unset: its address, and so its redirects, are then CLIPPER_PUBLIC_URL + MOUNT, and besides a Clipper on the
user's own computer it hands logins back to the site's own return address (`connect.RETURN`).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import html
import json
import os
import secrets
import time
import urllib.parse

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .. import connect
from ..instagram import api as ig
from ..tiktok import api as tt
from ..youtube import api as yt

LOCAL = ("http://127.0.0.1:", "http://localhost:")      # where a login may be handed back to
STATE_LIFE = 600
MOUNT = "/connect"          # where the hosted gateway serves this app on its own site (D239)
#: Each platform's app: its id and its secret.
KEYS = {"tiktok": ("TIKTOK_CLIENT_KEY", "TIKTOK_CLIENT_SECRET"), "youtube": ("YOUTUBE_CLIENT_ID", "YOUTUBE_CLIENT_SECRET"),
        "instagram": ("INSTAGRAM_APP_ID", "INSTAGRAM_APP_SECRET")}
# ponytail: per process; set BROKER_SIGNING_KEY when more than one process serves logins.
_SPARE_KEY = secrets.token_hex(32)


def _env(name: str) -> str:
    return os.environ.get(name, "").strip()


def ready(platform: str) -> bool:
    """This service has an app for `platform`."""
    return platform in KEYS and all(_env(name) for name in KEYS[platform])


def _mac(body: str) -> str:
    # Never an empty key: anyone could then sign a state that sends a login's tokens to their own address.
    key = _env("BROKER_SIGNING_KEY") or _SPARE_KEY
    return hmac.new(key.encode(), body.encode(), hashlib.sha256).hexdigest()


def sign(data: dict) -> str:
    body = base64.urlsafe_b64encode(json.dumps(data).encode()).decode().rstrip("=")
    return f"{body}.{_mac(body)}"


def unsign(state: str) -> dict:
    body, _, mac = state.rpartition(".")
    good = _mac(body)
    if not body or not hmac.compare_digest(mac, good):
        raise HTTPException(400, "bad state")
    data = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    if time.time() - data["t"] > STATE_LIFE:
        raise HTTPException(400, "this login took too long; start it again")
    return data


def public_url() -> str:
    """PUBLIC_URL; inside the hosted gateway, the site's address + MOUNT (D239)."""
    site = _env("CLIPPER_PUBLIC_URL").rstrip("/")
    return _env("PUBLIC_URL").rstrip("/") or (site + MOUNT if site else "")


def may_return_to(back: str) -> bool:
    """A Clipper on the user's own computer; on the hosted site also the site's own return address (D239): same
    site, so the browser sends the user's session with the post and the gateway gives it to their Clipper.
    The host is read as a browser would, so `http://127.0.0.1:1@evil.example/` is not this computer."""
    if back.startswith(LOCAL) and urllib.parse.urlsplit(back).hostname in ("127.0.0.1", "localhost"):
        return True
    site = _env("CLIPPER_PUBLIC_URL").rstrip("/")
    return bool(site) and back == site + connect.RETURN


def redirect_uri(platform: str) -> str:
    return f"{public_url()}/callback/{platform}"


def _post(url: str, data: dict) -> dict:
    r = httpx.post(url, data=data, timeout=30)
    try:
        body = r.json()
    except ValueError:
        body = {}
    if r.status_code >= 400 or ("access_token" not in body and "error" in body):
        raise HTTPException(502, body.get("error_description") or body.get("error") or r.text[:200])
    return body


def authorize_url(platform: str, state: str) -> str:
    if platform == "tiktok":
        return tt.AUTH_URL + "?" + urllib.parse.urlencode({
            "client_key": _env("TIKTOK_CLIENT_KEY"), "response_type": "code", "scope": tt.SCOPES,
            "redirect_uri": redirect_uri(platform), "state": state})
    if platform == "youtube":
        return yt.AUTH_URL + "?" + urllib.parse.urlencode({
            "response_type": "code", "client_id": _env("YOUTUBE_CLIENT_ID"), "redirect_uri": redirect_uri(platform),
            "scope": yt.SCOPES, "state": state, "access_type": "offline", "prompt": "consent select_account"})
    if platform == "instagram":
        return ig.AUTH_URL + "?" + urllib.parse.urlencode({
            "client_id": _env("INSTAGRAM_APP_ID"), "redirect_uri": redirect_uri(platform), "response_type": "code",
            "scope": ig.SCOPES, "state": state, "force_reauth": "true"})
    raise HTTPException(404, "unknown platform")


def exchange(platform: str, code: str) -> dict:
    """The platform's tokens for a code."""
    code = code.split("#")[0]
    if platform == "tiktok":
        return _post(tt.TOKEN_URL, {"client_key": _env("TIKTOK_CLIENT_KEY"), "client_secret": _env("TIKTOK_CLIENT_SECRET"),
                                    "code": code, "grant_type": "authorization_code", "redirect_uri": redirect_uri(platform)})
    if platform == "youtube":
        return _post(yt.TOKEN_URL, {"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri(platform),
                                    "client_id": _env("YOUTUBE_CLIENT_ID"), "client_secret": _env("YOUTUBE_CLIENT_SECRET")})
    short = _post(ig.CODE_URL, {"client_id": _env("INSTAGRAM_APP_ID"), "client_secret": _env("INSTAGRAM_APP_SECRET"),
                                "grant_type": "authorization_code", "redirect_uri": redirect_uri(platform), "code": code})
    short = (short.get("data") or [short])[0]
    long_lived = httpx.get(ig.EXCHANGE_URL, params={"grant_type": "ig_exchange_token", "client_secret": _env("INSTAGRAM_APP_SECRET"),
                                                    "access_token": short["access_token"]}, timeout=30).json()
    return {"access_token": long_lived.get("access_token") or short["access_token"]}


def renew(platform: str, refresh_token: str) -> dict:
    if platform == "tiktok":
        return _post(tt.TOKEN_URL, {"client_key": _env("TIKTOK_CLIENT_KEY"), "client_secret": _env("TIKTOK_CLIENT_SECRET"),
                                    "grant_type": "refresh_token", "refresh_token": refresh_token})
    if platform == "youtube":
        return _post(yt.TOKEN_URL, {"grant_type": "refresh_token", "refresh_token": refresh_token,
                                    "client_id": _env("YOUTUBE_CLIENT_ID"), "client_secret": _env("YOUTUBE_CLIENT_SECRET")})
    raise HTTPException(404, "that platform refreshes without the service")


def handoff(back: str, payload: dict) -> HTMLResponse:
    """A page that posts the tokens to the user's own Clipper, so they never sit in a URL."""
    fields = html.escape(json.dumps(payload), quote=True)
    return HTMLResponse(
        "<body style='font-family:sans-serif;padding:2em'><p>Connected. Going back to Clipper…</p>"
        f"<form id=f method=post action='{html.escape(back, quote=True)}'><input type=hidden name=payload value='{fields}'></form>"
        "<script>document.getElementById('f').submit()</script></body>")


def create_app() -> FastAPI:
    app = FastAPI(title="Clipper connect service", docs_url=None, redoc_url=None)

    @app.get("/")
    def home() -> HTMLResponse:
        return HTMLResponse("<p style='font-family:sans-serif;padding:2em'>Clipper's connect service. "
                            "Open Clipper and press Connect.</p>")

    @app.get("/login/{platform}")
    def start(platform: str, request: Request) -> RedirectResponse:
        # `return` is a Python keyword, so the query is read by hand.
        nonce, back = request.query_params.get("nonce", ""), request.query_params.get("return", "")
        if not may_return_to(back) or not nonce:
            raise HTTPException(400, "this login must come from Clipper")
        return RedirectResponse(authorize_url(platform, sign({"p": platform, "n": nonce, "b": back, "t": time.time()})))

    @app.get("/callback/{platform}")
    def callback(platform: str, request: Request) -> HTMLResponse:
        q = request.query_params
        data = unsign(q.get("state", ""))
        if data["p"] != platform:
            raise HTTPException(400, "bad state")
        if "code" not in q:
            return handoff(data["b"], {"nonce": data["n"], "error": q.get("error_description") or q.get("error") or "no code"})
        return handoff(data["b"], {"nonce": data["n"], "tokens": exchange(platform, q["code"])})

    @app.post("/refresh/{platform}")
    async def refresh(platform: str, request: Request) -> dict:
        token = urllib.parse.parse_qs((await request.body()).decode()).get("refresh_token", [""])[0]
        if not token:
            raise HTTPException(400, "no refresh token")
        return renew(platform, token)

    return app
