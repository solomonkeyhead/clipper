"""Sign in with no app of your own (D151): the platform's login goes through Clipper's connect service.

TikTok, Instagram and Google only share a person's stats with a registered app, and an app's secret can't ship
inside a program anyone can open. So one registered app lives on a small web service (`clipper.broker`) that
holds the secrets. A user presses Connect, the browser goes to the service, then to the platform's own login
page; the person logs in; the service swaps the code for tokens and hands them to the browser, which posts
them straight back to this Clipper on 127.0.0.1. The service keeps nothing. Tokens refresh through it too, since
TikTok and Google want the secret for that.

Set `CLIPPER_BROKER_URL` (in `.env`, or in the copy of Clipper you give people) to the service's address. A
user who has put in their own app keys keeps using those; the service is only for a platform with none.

Hosted (D239), the gateway serves the service on the site itself (`<site>/connect`) and sets this Clipper's
CLIPPER_BROKER_URL to it: the page posting the tokens back is then on the site's own address, so the browser
sends the session cookie with it and the gateway passes it to this user's Clipper. It also sets
CLIPPER_BROKER_LOCAL_URL (the gateway's own port, for refreshes, which carry no session) and
CLIPPER_BROKER_PLATFORMS (the platforms the site has an app for).
"""

from __future__ import annotations

import os
import secrets
import threading
import urllib.parse
from pathlib import Path

import httpx

PLATFORMS = ("tiktok", "youtube", "instagram")
RETURN = "/api/accounts/broker/return"     # where the service's page posts a finished login
_pending: dict[str, dict] = {}     # nonce -> {"event": Event, "payload": dict | None}


class ConnectError(RuntimeError):
    """The service was unreachable, or the platform refused the login."""


BUNDLED = Path(__file__).with_name("broker.url")      # a packaged copy carries its service's address here (git-ignored)


def url() -> str:
    found = os.environ.get("CLIPPER_BROKER_URL", "").strip()
    if not found and BUNDLED.exists():
        found = BUNDLED.read_text(encoding="utf-8").strip()
    return found.rstrip("/")


def enabled(platform: str | None = None) -> bool:
    """A service is set up (for `platform`: hosted, one the site has an app for, D239; any other service is
    taken to have all three)."""
    listed = os.environ.get("CLIPPER_BROKER_PLATFORMS")
    return bool(url()) and (platform is None or listed is None or platform in listed.split(","))


def refreshes_via_service(token: dict, own_app: bool) -> bool:
    """A login renews through the same app that made it: the service for one made through it, the user's
    own keys for an older one. An old login with no mark follows whichever is available."""
    if not enabled():
        return False
    return token.get("via") == "service" or (not token.get("via") and not own_app)


def local_return() -> str:
    """This Clipper on 127.0.0.1; hosted, the site's own address, which the gateway passes to the signed-in
    user's Clipper (D239)."""
    from .hosting import hosted

    site = os.environ.get("CLIPPER_PUBLIC_URL", "").strip().rstrip("/")
    if site and hosted():
        return site + RETURN
    from .studio import server

    return f"http://127.0.0.1:{server.PORT}{RETURN}"


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
    """New tokens for a refresh token, from the service (it holds the app secret). Hosted straight to the
    gateway's own port (D239): a refresh carries no session, and needn't leave the machine."""
    base = os.environ.get("CLIPPER_BROKER_LOCAL_URL", "").strip().rstrip("/") or url()
    try:
        r = httpx.post(f"{base}/refresh/{platform}", data={"refresh_token": refresh_token}, timeout=90)   # a free host may be waking up
    except httpx.HTTPError as exc:
        raise ConnectError(f"couldn't reach the connect service: {exc}") from exc
    if r.status_code >= 400:
        raise ConnectError(r.json().get("detail", r.text[:200]) if r.headers.get("content-type", "").startswith("application/json") else r.text[:200])
    return r.json()
