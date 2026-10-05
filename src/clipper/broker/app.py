"""The connect service (D151): one registered app per platform, so a user only logs in.

Run it on any host with HTTPS (`python -m clipper.broker`) and give Clipper its address as CLIPPER_BROKER_URL.
It keeps no accounts and no tokens: it sends the browser to the platform, swaps the returned code for tokens
with the app's secret, and posts them to the Clipper on the user's own computer. `/refresh` does the same for
a refresh token. Settings, all environment variables:

  PUBLIC_URL             this service's public address, e.g. https://connect.example.com
  BROKER_SIGNING_KEY     a long random string; signs the state that rides through the platform's login
  TIKTOK_CLIENT_KEY / TIKTOK_CLIENT_SECRET          a TikTok "Login Kit for web" app, redirect PUBLIC_URL/callback/tiktok
  YOUTUBE_CLIENT_ID / YOUTUBE_CLIENT_SECRET         a Google "Web application" client, redirect PUBLIC_URL/callback/youtube
  INSTAGRAM_APP_ID / INSTAGRAM_APP_SECRET           a Meta app with Instagram login, redirect PUBLIC_URL/callback/instagram

The platforms' own review decides who may log in (sandbox: listed testers only; after review: anyone).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import html
import json
import os
import time
import urllib.parse

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from ..instagram import api as ig
from ..tiktok import api as tt
from ..youtube import api as yt

LOCAL = ("http://127.0.0.1:", "http://localhost:")      # where a login may be handed back to
STATE_LIFE = 600


def _env(name: str) -> str:
    return os.environ.get(name, "").strip()


def sign(data: dict) -> str:
    body = base64.urlsafe_b64encode(json.dumps(data).encode()).decode().rstrip("=")
    mac = hmac.new(_env("BROKER_SIGNING_KEY").encode(), body.encode(), hashlib.sha256).hexdigest()
    return f"{body}.{mac}"


def unsign(state: str) -> dict:
    body, _, mac = state.rpartition(".")
    good = hmac.new(_env("BROKER_SIGNING_KEY").encode(), body.encode(), hashlib.sha256).hexdigest()
    if not body or not hmac.compare_digest(mac, good):
        raise HTTPException(400, "bad state")
    data = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    if time.time() - data["t"] > STATE_LIFE:
        raise HTTPException(400, "this login took too long; start it again")
    return data


def redirect_uri(platform: str) -> str:
    return f"{_env('PUBLIC_URL').rstrip('/')}/callback/{platform}"


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
        if not back.startswith(LOCAL) or not nonce:
            raise HTTPException(400, "this login must come from Clipper on your own computer")
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
