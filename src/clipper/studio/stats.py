"""Post links and stats for the Control Center, from the performance log."""

from __future__ import annotations

import statistics
import threading
from datetime import datetime

from ..learn import log as perf

#: Per-post numbers shown on a clip, in display order.
POST_FIELDS = ("posted_at", "views_latest", "likes", "comments", "shares", "saves",
               "avg_watch_s", "watched_full_pct", "skip_rate_pct", "drop_off_s")

#: Instagram insights "can be delayed up to 48 hours" (Meta's reference).
SETTLING_HOURS = {"instagram": 48}
#: Fewer posts than this and "x your median" means nothing.
MEDIAN_MIN_POSTS = 3

_sync_lock = threading.Lock()


def sync_all(rows: list[dict[str, str]]) -> list[str]:
    """Sync `rows` from every connected TikTok and Instagram account. Returns what failed."""
    from ..instagram import api as ig_api
    from ..instagram import sync as ig_sync
    from ..tiktok import api as tt_api
    from ..tiktok import sync as tt_sync

    problems = []
    tiktoks = tt_api.token_files()
    if not tiktoks and not ig_api.token_files():
        return ["No accounts connected yet: connect TikTok or Instagram on the Accounts page"]
    for path in tiktoks:
        name = tt_api.read_token(path).get("handle") or tt_api.read_token(path).get("display_name") or ""
        try:
            videos = tt_api.list_videos(tt_api.access_token(path))
            tt_sync.apply(videos, rows)
            handle = next((handle_from_url(v.url) for v in videos if "/@" in v.url), "")
            tt_api.set_handle(path, handle)
        except tt_api.TikTokError as exc:
            problems.append(f"TikTok{f' @{name}' if name else ''}: {exc}")
    for path in ig_api.token_files():
        try:
            ig_sync.apply(ig_api.list_reels(ig_api.access_token(path)), rows,
                          account=ig_api.username(path))
        except ig_api.InstagramError as exc:
            problems.append(f"Instagram @{ig_api.username(path)}: {exc}")
    return problems


def run_sync() -> dict:
    """One full sync: platforms -> performance log -> snapshots. Never two at once."""
    from . import db

    if not _sync_lock.acquire(blocking=False):
        return {"skipped": True, "problems": [], "last_synced": ""}
    try:
        rows = perf.read()
        problems = sync_all(rows)
        try:
            perf.write(rows)
        except PermissionError:
            problems.append(f"{perf.log_path().name} is open in Excel; stats will save "
                            "on the next sync after it's closed")
        at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        posts = [p for found in posts_by_clip(rows).values() for p in found]
        with db.connect() as con:
            db.add_snapshots(con, at, posts)
        return {"skipped": False, "problems": problems, "last_synced": last_synced(rows)}
    finally:
        _sync_lock.release()


def syncing() -> bool:
    return _sync_lock.locked()


def posts_by_clip(rows: list[dict[str, str]]) -> dict[tuple[str, str, str], list[dict]]:
    """(campaign, source_id, clip_id) -> that clip's posts, one per platform row with a link."""
    out: dict[tuple[str, str, str], list[dict]] = {}
    for row in rows:
        url = (row.get("url") or "").strip()
        if not url:
            continue
        post = {"platform": (row.get("platform") or "").strip() or "tiktok",
                "account": (row.get("account") or "").strip() or handle_from_url(url),
                "url": url.split("?", 1)[0]}
        for name in POST_FIELDS:
            value = (row.get(name) or "").strip()
            number = perf.number(value) if name != "posted_at" else None
            post[name] = number if number is not None else (value or None)
        key = (row.get("campaign", ""), row.get("source_id", ""), row.get("clip_id", ""))
        out.setdefault(key, []).append(post)
    return out


def handle_from_url(url: str) -> str:
    """"@solomonkeyclips" from a TikTok link; "" otherwise."""
    marker = "tiktok.com/@"
    if marker in url:
        return url.split(marker, 1)[1].split("/", 1)[0]
    return ""


def last_synced(rows: list[dict[str, str]]) -> str:
    return max((r.get("synced_at") or "" for r in rows), default="")


def age_hours(posted_at: str | None, now: datetime | None = None) -> float | None:
    if not posted_at:
        return None
    try:
        posted = datetime.strptime(posted_at[:16], "%Y-%m-%d %H:%M")
    except ValueError:
        return None
    return max(0.0, ((now or datetime.now()) - posted).total_seconds() / 3600)


def estimate_earnings(views: float | None, rate: float | None, minimum: float | None,
                      maximum: float | None) -> float | None:
    """views/1000 x rate, nothing until the campaign minimum, capped at its maximum."""
    if rate is None or views is None:
        return None
    amount = views / 1000.0 * rate
    if minimum is not None and amount < minimum:
        return 0.0
    if maximum is not None:
        amount = min(amount, maximum)
    return round(amount, 2)


def medians(posts: list[dict]) -> dict[tuple[str, str], float]:
    """(platform, campaign) -> median views, where there are enough posts to say."""
    groups: dict[tuple[str, str], list[float]] = {}
    for p in posts:
        if p.get("views_latest") is not None:
            groups.setdefault((p["platform"], p["campaign"]), []).append(p["views_latest"])
    return {k: statistics.median(v) for k, v in groups.items()
            if len(v) >= MEDIAN_MIN_POSTS and statistics.median(v) > 0}
