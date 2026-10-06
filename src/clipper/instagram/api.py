"""Instagram's official API (Instagram Login): token storage and refresh, Reels and insights.

For a professional (Creator or Business) account; no Facebook Page needed. The
token is generated once in the Meta App Dashboard (Instagram -> API setup with
Instagram business login -> Generate token) with instagram_business_basic and
instagram_business_manage_insights, and handed to `clipper instagram login`.
Dashboard tokens last 60 days; any token at least a day old can be exchanged
for a fresh 60-day one, which `access_token()` does once it is 30 days old.
"""

from __future__ import annotations

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
AUTH_URL = "https://www.instagram.com/oauth/authorize"
CODE_URL = "https://api.instagram.com/oauth/access_token"
EXCHANGE_URL = "https://graph.instagram.com/access_token"
SCOPES = "instagram_business_basic,instagram_business_manage_insights"
PORT = 3458
REDIRECT_URI = f"http://localhost:{PORT}/callback/"
ENV_ID, ENV_SECRET = "INSTAGRAM_APP_ID", "INSTAGRAM_APP_SECRET"
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
    #: Its views and insights were read this time. False: they weren't asked for
    #: (an older reel between daily refreshes) or Instagram refused, and the log
    #: keeps the numbers it has rather than taking zeros (instagram/sync.py).
    measured: bool = True


#: Reels this young get their insights on every sync; older ones once a day
#: (`list_reels(older=...)`). Each reel is one insights call, and Instagram
#: caps an account's calls per day; campaigns pay on a post's first days anyway.
FRESH_DAYS = 14
#: How often older reels' numbers are refreshed.
OLDER_EVERY_HOURS = 24


def accounts_dir() -> Path:
    # Under data/, which is git-ignored: tokens are secrets. One file per
    # connected account, named by its username.
    return data_root() / "instagram" / "accounts"


def token_files() -> list[Path]:
    """Every connected account's token file (the single-account file is moved in)."""
    legacy = data_root() / "instagram" / "token.json"
    if legacy.exists():
        stored = json.loads(legacy.read_text(encoding="utf-8"))
        target = ensure(accounts_dir()) / f"{_safe(stored.get('username') or 'account')}.json"
        if not target.exists():
            legacy.replace(target)
    folder = accounts_dir()
    return sorted(folder.glob("*.json")) if folder.is_dir() else []


def token_path(account: str | None = None) -> Path:
    """Account `account`'s token file, or the first connected account's."""
    if account:
        return accounts_dir() / f"{_safe(account)}.json"
    files = token_files()
    return files[0] if files else accounts_dir() / "account.json"


def _safe(name: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in name)[:80] or "account"


def remove(account: str) -> bool:
    """Disconnect an account: its token file is deleted (connect again to undo)."""
    path = token_path(account)
    if path.exists():
        path.unlink()
        return True
    return False


def read_token(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def login(token: str) -> str:
    """Check a dashboard token, extend it to a fresh 60 days, store it. Returns the username.

    Each account gets its own file, so connecting a second account adds it.
    """
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
    _save(stored, token_path(me.get("username") or "account"))
    return me.get("username", "")


def has_app() -> bool:
    return bool(os.environ.get(ENV_ID, "").strip() and os.environ.get(ENV_SECRET, "").strip())


def login_browser(*, timeout: float = 300.0, open_browser=webbrowser.open) -> dict:
    """One-click sign-in (D150): Instagram's own consent page, the code comes back to a local
    redirect, becomes a 60-day token, and goes through `login` like a pasted one. Needs the Meta
    app's ID and secret (INSTAGRAM_APP_ID / _SECRET) with `REDIRECT_URI` listed in the app."""
    from .. import connect

    if connect.enabled():
        tokens = connect.login("instagram", timeout=timeout, open_browser=open_browser)
        return {"display_name": login(tokens["access_token"])}
    app_id, secret = os.environ.get(ENV_ID, "").strip(), os.environ.get(ENV_SECRET, "").strip()
    if not app_id or not secret:
        raise InstagramError("add your Meta app's ID and secret on the Accounts page first")
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
            ok = got.get("state") == state and "code" in got
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(("<p style='font-family:sans-serif;padding:2em'>"
                              + ("Connected. You can close this tab and go back to Clipper."
                                 if ok else "Login did not complete. Go back to Clipper and try again.")
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
        "client_id": app_id, "redirect_uri": REDIRECT_URI, "response_type": "code", "scope": SCOPES,
        "state": state, "force_reauth": "true"}))     # force_reauth: the account picker, for a second account
    thread.join(timeout + 5)
    server.server_close()
    if got.get("state") != state:
        raise InstagramError("the login did not come back (timed out, or the state did not match)")
    if "code" not in got:
        raise InstagramError(f"Instagram refused the login: {got.get('error_description') or got.get('error')}")
    body = urllib.parse.urlencode({"client_id": app_id, "client_secret": secret, "grant_type": "authorization_code",
                                   "redirect_uri": REDIRECT_URI, "code": got["code"].split("#")[0]}).encode()
    try:
        with urllib.request.urlopen(urllib.request.Request(CODE_URL, data=body), timeout=30) as response:
            reply = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise InstagramError(f"Instagram refused the code (HTTP {exc.code})") from None
    except (urllib.error.URLError, OSError) as exc:
        raise InstagramError(f"cannot reach Instagram: {type(exc).__name__}") from None
    reply = (reply.get("data") or [reply])[0]       # the answer has come flat, and inside "data"
    short = reply.get("access_token")
    if not short:
        raise InstagramError("Instagram gave no token")
    long_lived = _get(EXCHANGE_URL, {"grant_type": "ig_exchange_token", "client_secret": secret,
                                     "access_token": short}).get("access_token") or short
    return {"display_name": login(long_lived)}


def username(path: Path | None = None) -> str:
    """An account's username (the first connected one by default), or "" if none."""
    path = path or token_path()
    if not path.exists():
        return ""
    return json.loads(path.read_text(encoding="utf-8")).get("username", "")


def access_token(path: Path | None = None) -> str:
    """The stored token, extended once it is 30 days old."""
    path = path or token_path()
    if not path.exists():
        raise InstagramError("not connected yet: connect Instagram on the Accounts page")
    stored = json.loads(path.read_text(encoding="utf-8"))
    age = time.time() - float(stored.get("obtained_at", 0))
    if age >= TOKEN_LIFETIME:
        raise InstagramError("the Instagram token has expired: generate a new one in the Meta "
                             "App Dashboard and reconnect on the Accounts page")
    if age >= REFRESH_AFTER or stored.get("unrefreshed"):
        try:
            stored.update(access_token=_refresh(stored["access_token"]),
                          obtained_at=time.time())
            stored.pop("unrefreshed", None)
            _save(stored, path)
        except InstagramError as exc:
            log.warning("could not extend the Instagram token (%s); still valid for %.0f days",
                        exc, (TOKEN_LIFETIME - age) / DAY)
    return stored["access_token"]


def list_reels(token: str, *, limit: int = 200, insights: bool = True, older: bool = True) -> list[Reel]:
    """The account's Reels, newest first, with their insights -- for reels older
    than FRESH_DAYS only when `older` is true."""
    reels: list[Reel] = []
    fresh_after = time.time() - FRESH_DAYS * DAY
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
            if insights and (older or reel.created >= fresh_after):
                reel.measured = _add_insights(reel, token)
            else:
                reel.measured = False
            reels.append(reel)
        url, params = (page.get("paging") or {}).get("next"), None
    return reels


def _add_insights(reel: Reel, token: str) -> bool:
    """Fill a reel's numbers; a metric Instagram refuses is left out, not fatal.
    False when none could be read."""
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
                return False
            metrics.remove(bad)
    else:
        return False
    values = {item.get("name"): (item.get("values") or [{}])[0].get("value")
              for item in data.get("data") or []}
    reel.views = int(values.get("views") or 0)
    reel.shares = int(values.get("shares") or 0)
    reel.saves = int(values.get("saved") or 0)
    if values.get("ig_reels_avg_watch_time") is not None:
        reel.avg_watch_s = round(float(values["ig_reels_avg_watch_time"]) / 1000.0, 1)  # ms
    if values.get("reels_skip_rate") is not None:
        reel.skip_rate_pct = round(float(values["reels_skip_rate"]), 1)
    return True


def older_due(path: Path, now: float | None = None) -> bool:
    """Whether this account's older reels are due their daily refresh."""
    stored = json.loads(path.read_text(encoding="utf-8"))
    return (now or time.time()) - float(stored.get("older_at") or 0) >= OLDER_EVERY_HOURS * 3600


def mark_older_done(path: Path, now: float | None = None) -> None:
    stored = json.loads(path.read_text(encoding="utf-8"))
    stored["older_at"] = now or time.time()
    _save(stored, path)


def _epoch(stamp: str | None) -> float:
    if not stamp:
        return 0.0
    return datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%S%z").timestamp()


def _refresh(token: str) -> str:
    data = _get(REFRESH_URL, {"grant_type": "ig_refresh_token", "access_token": token})
    if "access_token" not in data:
        raise InstagramError(f"token refresh failed: {data}")
    return data["access_token"]


def _save(stored: dict, path: Path | None = None) -> None:
    path = path or token_path()
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
