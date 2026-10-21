"""Stored platform stats follow what the platforms' rules and the privacy page promise (D231).

What the TikTok, Instagram and YouTube APIs gave Clipper (views, likes, watch time, a post's link and caption)
lives in two places: the performance log's synced columns and the studio database's `snapshots` (plus the
Shorts adopted from an own channel, which exist only because the YouTube API listed them). This deletes it:

* an account is disconnected in Clipper, or Google says its access was revoked: its data goes at once. The
  YouTube API Services Developer Policies (III.D.2) allow 7 days after a revocation (30 when it was revoked on
  Google's security page); Meta (Platform Terms 3.d.i) says "as soon as reasonably possible"; TikTok's
  developer terms set no deadline for a disconnect.
* a sync that couldn't refresh a YouTube post for 30 days: "After 30 calendar days, the API Client must either
  delete or refresh the stored data" (III.E.4.c). A sync that works refreshes every post it reads, so what is
  still unrefreshed after 30 days is a post that is gone or an account that no longer answers.
* anything left (Excel held the log open, a crash): `sweep` finds data whose account is no longer connected.

Only columns the APIs filled are cleared. What the user typed (notes, payouts, verified views, or numbers
collected from TikTok Studio) stays, and a row only the sync made is dropped when nothing else is left on it.
A row is the API's when its `synced_at` is set: a link pasted by hand has none and is the user's.

Only on the hosted site. On the owner's own computer the data is his own and the 24h/7d/30d snapshots can't be
fetched again, so nothing is cleared there (D231); a revoked channel still shows "connect again".
"""

from __future__ import annotations

import time
from datetime import datetime

from ..hosting import hosted
from ..learn import log as perf
from ..utils.logging import get_logger
from . import db

log = get_logger(__name__)

DAY = 86400.0
#: YouTube data not refreshed for this long is deleted (policy III.E.4.c).
FRESH_DAYS = 30

#: Columns a sync fills, on every platform.
_API = set(perf.SYNCED) | {"video_id", "posted_caption", "synced_at"}
#: Instagram and YouTube rows are made by the sync alone, so their watch time and saves are the API's too;
#: on a TikTok row those come from the user's copy of TikTok Studio.
_API_MORE = {"saves", "avg_watch_s", "avg_view_pct"}
#: Columns the user fills: they keep a row alive, and are never cleared.
_USER = {"notes", "verified_views", "payout_usd", "new_followers", "watched_full_pct", "drop_off_s"}


def _account(row: dict[str, str]) -> str:
    from . import stats

    return (row.get("account") or stats.handle_from_url(row.get("url", "")) or "").lstrip("@").lower()


def _platform(row: dict[str, str]) -> str:
    return (row.get("platform") or "").strip() or "tiktok"


def connected_names() -> dict[str, set[str] | None]:
    """The accounts still connected, by platform, by the name their rows carry. None for TikTok when it has
    accounts: a TikTok row's account is the handle in its link, which a token may not know yet, so with an
    account left only a name given to `enforce` is removed."""
    from ..instagram import api as ig_api
    from ..tiktok import api as tt_api
    from ..youtube import api as yt_api

    return {
        "youtube": {yt_api.name(p).lstrip("@").lower() for p in yt_api.token_files() if not yt_api.is_revoked(p)},
        "instagram": {ig_api.username(p).lstrip("@").lower() for p in ig_api.token_files()},
        "tiktok": None if tt_api.token_files() else set(),
    }


def _stale(row: dict[str, str], now: float) -> bool:
    try:
        return now - datetime.strptime(row["synced_at"][:16], "%Y-%m-%d %H:%M").timestamp() > FRESH_DAYS * DAY
    except (KeyError, ValueError):
        return False


def enforce(rows: list[dict[str, str]], *, gone: dict[str, set[str]] | None = None, fresh: bool = False,
            now: float | None = None) -> int:
    """Clear, in `rows` (the log, changed in place), the API data of accounts that are gone, and (`fresh`) of
    YouTube posts not refreshed for 30 days. `gone`: platform -> account names just disconnected. The
    database side (snapshots, adopted Shorts) is cleaned too. Returns how many rows were touched; the caller
    writes the log."""
    if not hosted():
        return 0
    now = time.time() if now is None else now
    connected = connected_names()
    gone = gone or {}
    urls: list[str] = []
    adopted: list[tuple[str, str]] = []
    keep: list[dict[str, str]] = []
    touched = 0
    for row in rows:
        platform = _platform(row)
        if platform not in connected or not (row.get("synced_at") or "").strip():
            keep.append(row)
            continue
        account = _account(row)
        names = connected[platform]
        orphan = account in gone.get(platform, ()) or (names is not None and account not in names)
        if not (orphan or (fresh and platform == "youtube" and _stale(row, now))):
            keep.append(row)
            continue
        touched += 1
        if row.get("url"):
            urls.append(row["url"].split("?", 1)[0])
        for column in _API | (_API_MORE if platform != "tiktok" else set()):
            row[column] = ""
        if platform != "tiktok" and not any((row.get(c) or "").strip() for c in _USER):
            if row.get("source_id") == "channel" and (row.get("clip_id") or "").startswith("yt-"):
                adopted.append((row.get("campaign", ""), row["clip_id"]))
            continue          # made by the sync, nothing of the user's left on it
        keep.append(row)
    rows[:] = keep
    if touched:
        _clean_db(urls, adopted)
        log.info("deleted stored platform data of %d post(s)", touched)
    return touched


def _clean_db(urls: list[str], adopted: list[tuple[str, str]]) -> None:
    with db.connect() as con:
        con.executemany("DELETE FROM snapshots WHERE url = ?", [(u,) for u in urls])
        con.executemany("DELETE FROM clips WHERE campaign = ? AND source_id = 'channel' AND clip_id = ?", adopted)


def purge_now(gone: dict[str, set[str]] | None = None, *, fresh: bool = False) -> int:
    """`enforce` on the stored log, written back. -1: the log is open in Excel (the next sweep tries again)."""
    from . import stats

    with stats.log_lock:
        rows = perf.read()
        touched = enforce(rows, gone=gone, fresh=fresh)
        if touched:
            try:
                perf.write(rows)
            except PermissionError:
                log.warning("%s is open in another program; stored platform data will be deleted at the "
                            "next sync or start", perf.log_path().name)
                return -1
    return touched


def sweep() -> int:
    """At start: delete what an account that is no longer connected left behind."""
    return purge_now()


def account_names(platform: str, account: str) -> set[str]:
    """The names a connected account's rows carry, read before its token is removed."""
    from ..instagram import api as ig_api
    from ..tiktok import api as tt_api
    from ..youtube import api as yt_api

    module = {"tiktok": tt_api, "instagram": ig_api, "youtube": yt_api}[platform]
    path = next((p for p in module.token_files() if p.stem == module._safe(account)), None)
    if path is None:
        return set()
    token = module.read_token(path)
    names = {token.get(k) for k in ("handle", "display_name", "username", "title")}
    if platform == "youtube":
        names.add(yt_api.name(path))
    return {n.lstrip("@").lower() for n in names if n}


def disconnect(platform: str, account: str) -> bool:
    """Remove an account and delete its stored API data at once (D231). False: no such account."""
    from ..instagram import api as ig_api
    from ..tiktok import api as tt_api
    from ..youtube import api as yt_api

    module = {"tiktok": tt_api, "instagram": ig_api, "youtube": yt_api}.get(platform)
    if module is None:
        return False
    names = account_names(platform, account)
    if not module.remove(account):
        return False
    purge_now({platform: names})
    return True


if __name__ == "__main__":      # for the server's own schedule: python -m clipper.studio.platform_data
    print(purge_now(fresh=True))
