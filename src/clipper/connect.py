"""Sign in with no app of your own (D151): the platform's login goes through Clipper's connect service.

TikTok, Instagram and Google only share a person's stats with a registered app, and an app's secret can't ship
inside a program anyone can open. So one registered app lives on a small web service (`clipper.broker`) that
holds the secrets. A user presses Connect, the browser goes to the service, then to the platform's own login
page; the person logs in; the service swaps the code for tokens and hands them to the browser, which posts
them straight back to this Clipper on 127.0.0.1. The service keeps nothing. Tokens refresh through it too, since
TikTok and Google want the secret for that.

Set `CLIPPER_BROKER_URL` (in `.env`, or in the copy of Clipper you give people) to the service's address. A
user who has put in their own app keys keeps using those; the service is only for a platform with none.
"""

from __future__ import annotations

import os
import secrets
import threading
import urllib.parse
from pathlib import Path

import httpx

PLATFORMS = ("tiktok", "youtube", "instagram")
_pending: dict[str, dict] = {}     # nonce -> {"event": Event, "payload": dict | None}


class ConnectError(RuntimeError):
    """The service was unreachable, or the platform refused the login."""


BUNDLED = Path(__file__).with_name("broker.url")      # a packaged copy carries its service's address here (git-ignored)


def url() -> str:
    found = os.environ.get("CLIPPER_BROKER_URL", "").strip()
    if not found and BUNDLED.exists():
        found = BUNDLED.read_text(encoding="utf-8").strip()
    return found.rstrip("/")


def enabled() -> bool:
    return bool(url())


def refreshes_via_service(token: dict, own_app: bool) -> bool:
    """A login renews through the same app that made it: the service for one made through it, the user's
    own keys for an older one. An old login with no mark follows whichever is available."""
    if not enabled():
        return False
    return token.get("via") == "service" or (not token.get("via") and not own_app)


def local_return() -> str:
    from .studio import server

    return f"http://127.0.0.1:{server.PORT}/api/accounts/broker/return"


def login(platform: str, *, timeout: float = 300.0, open_browser=None) -> dict:
    """Hand the consent link to `open_browser`, wait for the service's answer, return the platform's tokens."""
    if not enabled():
        raise ConnectError("no connect service is set up (CLIPPER_BROKER_URL)")
    nonce = secrets.token_urlsafe(24)
    entry = _pending[nonce] = {"event": threading.Event(), "payload": None}
    try:
        open_browser(f"{url()}/login/{platform}?" + urllib.parse.urlencode({"nonce": nonce, "return": local_return()}))
        if not entry["event"].wait(timeout) or not entry["payload"]:
            raise ConnectError("the login did not come back (timed out)")
        payload = entry["payload"]
    finally:
        _pending.pop(nonce, None)
    if payload.get("error"):
        raise ConnectError(f"{platform} refused the login: {payload['error']}")
    return payload


def deliver(nonce: str, payload: dict) -> bool:
    """The browser's post-back from the service. False for a nonce this Clipper didn't hand out."""
    entry = _pending.get(nonce)
    if not entry:
        return False
    entry["payload"] = payload
    entry["event"].set()
    return True


def refresh(platform: str, refresh_token: str) -> dict:
    """New tokens for a refresh token, from the service (it holds the app secret)."""
    try:
        r = httpx.post(f"{url()}/refresh/{platform}", data={"refresh_token": refresh_token}, timeout=90)   # a free host may be waking up
    except httpx.HTTPError as exc:
        raise ConnectError(f"couldn't reach the connect service: {exc}") from exc
    if r.status_code >= 400:
        raise ConnectError(r.json().get("detail", r.text[:200]) if r.headers.get("content-type", "").startswith("application/json") else r.text[:200])
    return r.json()
