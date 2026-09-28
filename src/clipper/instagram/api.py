"""Instagram's official API (Instagram Login): token storage and refresh, Reels and insights.

For a professional (Creator or Business) account; no Facebook Page needed. The
token is generated once in the Meta App Dashboard (Instagram -> API setup with
Instagram business login -> Generate token) with instagram_business_basic and
instagram_business_manage_insights, and handed to `clipper instagram login`.
Dashboard tokens last 60 days; any token at least a day old can be exchanged
for a fresh 60-day one, which `access_token()` does once it is 30 days old.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from ..paths import data_root, ensure
from ..utils.logging import get_logger

log = get_logger(__name__)

GRAPH = "https://graph.instagram.com/v25.0"
REFRESH_URL = "https://graph.instagram.com/refresh_access_token"
MEDIA_FIELDS = ("id,caption,media_type,media_product_type,permalink,timestamp,"
                "like_count,comments_count")
#: Reels insights, per Meta's reference (2026). `reels_skip_rate` is the share
#: of plays skipped within the first 3 seconds -- the hook's own number.
REEL_METRICS = ("views", "reach", "saved", "shares", "ig_reels_avg_watch_time",
                "reels_skip_rate")
DAY = 86400.0
TOKEN_LIFETIME = 60 * DAY
REFRESH_AFTER = 30 * DAY


class InstagramError(RuntimeError):
    """Not connected, the token was refused, or Instagram returned an error."""


@dataclass
class Reel:
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


def token_path() -> Path:
    # Under data/, which is git-ignored: the token is a secret.
    return data_root() / "instagram" / "token.json"


def login(token: str) -> str:
    """Check a dashboard token, extend it to a fresh 60 days, store it. Returns the username."""
    token = token.strip()
    if not token:
        raise InstagramError("no token given")
    me = _get(f"{GRAPH}/me", {"fields": "user_id,username,account_type", "access_token": token})
    stored = {"access_token": token, "obtained_at": time.time(),
              "username": me.get("username", ""), "account_type": me.get("account_type", "")}
    try:
        stored["access_token"] = _refresh(token)
    except InstagramError as exc:  # a brand-new token can't be refreshed for 24h
        log.info("token not extended yet (%s); it will be on a later sync", exc)
        stored["unrefreshed"] = True
    _save(stored)
    return me.get("username", "")


def username() -> str:
    """The connected account's username, or "" if not connected."""
    path = token_path()
    if not path.exists():
        return ""
    return json.loads(path.read_text(encoding="utf-8")).get("username", "")


def access_token() -> str:
    """The stored token, extended once it is 30 days old."""
    path = token_path()
    if not path.exists():
        raise InstagramError("not connected yet: run `clipper instagram login` once")
    stored = json.loads(path.read_text(encoding="utf-8"))
    age = time.time() - float(stored.get("obtained_at", 0))
    if age >= TOKEN_LIFETIME:
        raise InstagramError("the Instagram token has expired: generate a new one in the Meta "
                             "App Dashboard and run `clipper instagram login` again")
    if age >= REFRESH_AFTER or stored.get("unrefreshed"):
        try:
            stored.update(access_token=_refresh(stored["access_token"]),
                          obtained_at=time.time())
            stored.pop("unrefreshed", None)
            _save(stored)
        except InstagramError as exc:
            log.warning("could not extend the Instagram token (%s); still valid for %.0f days",
                        exc, (TOKEN_LIFETIME - age) / DAY)
    return stored["access_token"]


def list_reels(token: str, *, limit: int = 200, insights: bool = True) -> list[Reel]:
    """The account's Reels, newest first, with their insights."""
    reels: list[Reel] = []
    url, params = f"{GRAPH}/me/media", {"fields": MEDIA_FIELDS, "limit": "50",
                                        "access_token": token}
    while url and len(reels) < limit:
        page = _get(url, params)
        for m in page.get("data") or []:
            if m.get("media_product_type") != "REELS":
                continue
            reel = Reel(id=str(m.get("id", "")), caption=(m.get("caption") or "").strip(),
                        created=_epoch(m.get("timestamp")), url=m.get("permalink") or "",
                        likes=int(m.get("like_count") or 0),
                        comments=int(m.get("comments_count") or 0))
            if insights:
                _add_insights(reel, token)
            reels.append(reel)
        url, params = (page.get("paging") or {}).get("next"), None
    return reels


def _add_insights(reel: Reel, token: str) -> None:
    """Fill a reel's numbers; a metric Instagram refuses is left out, not fatal."""
    metrics = list(REEL_METRICS)
    while metrics:
        try:
            data = _get(f"{GRAPH}/{reel.id}/insights",
                        {"metric": ",".join(metrics), "access_token": token})
            break
        except InstagramError as exc:
            bad = next((m for m in metrics if m in str(exc)), None)
            if bad is None:
                log.warning("no insights for reel %s: %s", reel.id, exc)
                return
            metrics.remove(bad)
    else:
        return
    values = {item.get("name"): (item.get("values") or [{}])[0].get("value")
              for item in data.get("data") or []}
    reel.views = int(values.get("views") or 0)
    reel.shares = int(values.get("shares") or 0)
    reel.saves = int(values.get("saved") or 0)
    if values.get("ig_reels_avg_watch_time") is not None:
        reel.avg_watch_s = round(float(values["ig_reels_avg_watch_time"]) / 1000.0, 1)  # ms
    if values.get("reels_skip_rate") is not None:
        reel.skip_rate_pct = round(float(values["reels_skip_rate"]), 1)


def _epoch(stamp: str | None) -> float:
    if not stamp:
        return 0.0
    return datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%S%z").timestamp()


def _refresh(token: str) -> str:
    data = _get(REFRESH_URL, {"grant_type": "ig_refresh_token", "access_token": token})
    if "access_token" not in data:
        raise InstagramError(f"token refresh failed: {data}")
    return data["access_token"]


def _save(stored: dict) -> None:
    path = token_path()
    ensure(path.parent)
    path.write_text(json.dumps(stored, indent=1), encoding="utf-8")


def _get(url: str, params: dict | None) -> dict:
    full = f"{url}?{urllib.parse.urlencode(params)}" if params else url
    try:
        with urllib.request.urlopen(full, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            error = json.loads(exc.read().decode("utf-8")).get("error") or {}
        except (ValueError, OSError):
            error = {}
        # Never echo the URL: it carries the access token.
        raise InstagramError(error.get("message") or f"Instagram answered HTTP {exc.code}") from None
    except (urllib.error.URLError, OSError) as exc:
        raise InstagramError(f"cannot reach Instagram: {type(exc).__name__}") from None
