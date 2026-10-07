"""Post links and stats for the Control Center, from the performance log."""

from __future__ import annotations

import statistics
import threading
from datetime import datetime

from ..learn import log as perf

#: Per-post numbers shown on a clip, in display order.
POST_FIELDS = ("posted_at", "views_latest", "likes", "comments", "shares", "saves",
               "avg_watch_s", "watched_full_pct", "skip_rate_pct", "drop_off_s", "posted_caption")
TEXT_FIELDS = {"posted_at", "posted_caption"}

#: Instagram insights "can be delayed up to 48 hours" (Meta's reference); YouTube
#: Analytics (watch time, shares) lags a day or two the same way.
SETTLING_HOURS = {"instagram": 48, "youtube": 48}
#: The smallest median a post is compared with ("3x your median"); below it the ratio is noise (D154).
MIN_MEDIAN = 10
#: Fewer posts than this and "x your median" means nothing.
MEDIAN_MIN_POSTS = 3

_sync_lock = threading.Lock()
#: Held by anything that rewrites the performance log, so a sync never overwrites it.
log_lock = _sync_lock


def sync_all(rows: list[dict[str, str]]) -> list[str]:
    """Sync `rows` from every connected TikTok, Instagram and YouTube account. Returns what failed."""
    from ..instagram import api as ig_api
    from ..instagram import sync as ig_sync
    from ..tiktok import api as tt_api
    from ..tiktok import sync as tt_sync
    from ..x import api as x_api
    from ..youtube import api as yt_api

    problems = []
    tiktoks = tt_api.token_files()
    if not tiktoks and not ig_api.token_files() and not yt_api.token_files() and not x_api.account_files():
        return ["No accounts connected yet: connect TikTok, Instagram, YouTube or X on the Accounts page"]
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
            older = ig_api.older_due(path)  # older reels once a day (instagram/api.py)
            ig_sync.apply(ig_api.list_reels(ig_api.access_token(path), older=older), rows,
                          account=ig_api.username(path))
            if older:
                ig_api.mark_older_done(path)
        except ig_api.InstagramError as exc:
            problems.append(f"Instagram @{ig_api.username(path)}: {exc}")
    for path in yt_api.token_files():
        try:
            account = yt_api.name(path)
            result = ig_sync.apply(yt_api.list_shorts(yt_api.access_token(path)), rows,
                                   account=account, platform="youtube")
            own = own_campaign(account)
            if own and result.unmatched:  # a Short on your own channel with no clip of Clipper's: adopt it (D144)
                adopt_uploads(rows, result.unmatched, own, account)
                ig_sync.apply(result.unmatched, rows, account=account, platform="youtube")
        except yt_api.YouTubeError as exc:
            problems.append(f"YouTube {yt_api.name(path)}: {exc}")
    for path in x_api.account_files():  # paid per post read: every few hours, not every sync
        try:
            x_api.sync(path, rows)
        except x_api.XError as exc:
            problems.append(f"X @{x_api.read(path).get('username', path.stem)}: {exc}")
    return problems


def own_campaign(account: str) -> str:
    """The campaign of the own channel whose handle is `account` ("" when it's a clipping account)."""
    from ..create import channel

    handle = account.lstrip("@").lower()
    return next((c.campaign for c in channel.all_channels() if c.handle and c.handle.lstrip("@").lower() == handle), "")


def ensure_create_rows(rows: list[dict[str, str]]) -> None:
    """Every Short Create has built gets a row in the log, so the sync can match the post it becomes
    (D144). Before this, a Short you posted was filed in the library but never in the log, and the
    sync matches posts to log rows by caption: it could never be found."""
    from . import db

    with db.connect() as con:
        built = con.execute("SELECT campaign, source_id, clip_id, caption, source_title, file, duration_s "
                            "FROM clips WHERE source_id='create' AND deleted_at IS NULL").fetchall()
    index = {(r.get("campaign", ""), r.get("source_id", ""), r.get("clip_id", "")): r for r in rows}
    for c in built:
        row = index.get((c["campaign"], c["source_id"], c["clip_id"]))
        if row is None:
            rows.append({"caption": c["caption"], "campaign": c["campaign"], "source_id": c["source_id"],
                         "clip_id": c["clip_id"], "source_title": c["source_title"], "file": c["file"],
                         "duration_s": f"{c['duration_s'] or 0:.1f}"})
        elif not (row.get("url") or "").strip():
            row["caption"] = c["caption"]  # not posted yet: follow the script's description


def adopt_uploads(rows: list[dict[str, str]], shorts: list, campaign: str, account: str) -> None:
    """Shorts already on your own channel that Clipper didn't make: filed as clips without a video
    file, under the channel's campaign, so their views count in Stats and the dashboard (D144)."""
    from . import db

    known = {(r.get("campaign", ""), r.get("source_id", ""), r.get("clip_id", "")) for r in rows}
    with db.connect() as con:
        for s in shorts:
            clip_id = f"yt-{s.id}"
            if (campaign, "channel", clip_id) in known:
                continue
            db.upsert_clip(con, {"campaign": campaign, "source_id": "channel", "clip_id": clip_id,
                                 "source_title": s.title, "title": s.title, "file": "", "hook": s.title,
                                 "caption": s.caption, "duration_s": None, "start_s": 0.0, "end_s": 0.0,
                                 "scores": '{"picked_by": "channel"}'})
            rows.append({"caption": s.caption, "campaign": campaign, "source_id": "channel", "clip_id": clip_id,
                         "source_title": s.title, "platform": "youtube", "account": account,
                         "video_id": s.id, "url": s.url})


def run_sync() -> dict:
    """One full sync: platforms -> performance log -> snapshots. Never two at once."""
    from . import db

    if not _sync_lock.acquire(blocking=False):
        return {"skipped": True, "problems": [], "last_synced": ""}
    try:
        rows = perf.read()
        ensure_create_rows(rows)
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
            number = perf.number(value) if name not in TEXT_FIELDS else None
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


def post_earnings(views: float | None, brief) -> float | None:
    """One post's estimate under its campaign's pay model (D147): per 1,000 views as before, the flat fee
    for a post, or nothing where the campaign doesn't pay through Clipper (your own channel)."""
    if brief is None or not brief.pays:
        return None
    if brief.pay_model == "per_clip":
        return None if brief.flat_fee_usd is None else round(brief.flat_fee_usd, 2)
    return estimate_earnings(views, brief.reward_per_1k_usd, brief.min_payout_usd, brief.max_payout_usd)


def locked_earnings(posts, brief_of) -> tuple[float, int]:
    """What posts have earned on paper but that sits under their campaign's minimum payout, so
    pays nothing yet (D118): (dollars, how many posts). `brief_of(post)` gives the campaign
    brief or None. A post that has cleared its minimum, or has no rate, adds nothing."""
    dollars, count = 0.0, 0
    for p in posts:
        brief = brief_of(p)
        if brief is None or brief.pay_model != "per_view" or p.est_earnings != 0 or not p.views:
            continue
        paper = estimate_earnings(p.views, brief.reward_per_1k_usd, None, brief.max_payout_usd) or 0.0
        if paper > 0:
            dollars += paper
            count += 1
    return round(dollars, 2), count


#: A post's views are compared with others' once it's this old: a short gets
#: most of its views in its first days, so younger posts would look like flops.
OUTCOME_HOURS = 72


def clip_performance(clips: list[dict], rows: list[dict[str, str]],
                     now: datetime | None = None) -> dict[int, float]:
    """Clip id -> its best post's views as a multiple of the median for that
    platform and campaign, among posts at least OUTCOME_HOURS old (learn/feedback.py)."""
    found = posts_by_clip(rows)
    now = now or datetime.now()
    mature: list[dict] = []
    by_clip: dict[int, list[dict]] = {}
    for clip in clips:
        for post in found.get((clip["campaign"], clip["source_id"], clip["clip_id"]), []):
            age = age_hours(post.get("posted_at"), now)
            if age is None or age < OUTCOME_HOURS or post.get("views_latest") is None:
                continue
            post = {**post, "campaign": clip["campaign"]}
            mature.append(post)
            by_clip.setdefault(clip["id"], []).append(post)
    med = medians(mature)
    out = {}
    for clip_id, posts in by_clip.items():
        ratios = [p["views_latest"] / med[(p["platform"], p["campaign"])] for p in posts
                  if (p["platform"], p["campaign"]) in med]
        if ratios:
            out[clip_id] = round(max(ratios), 2)
    return out


def medians(posts: list[dict]) -> dict[tuple[str, str], float]:
    """(platform, campaign) -> median views, where there are enough posts to say."""
    groups: dict[tuple[str, str], list[float]] = {}
    for p in posts:
        if p.get("views_latest") is not None:
            groups.setdefault((p["platform"], p["campaign"]), []).append(p["views_latest"])
    return {k: statistics.median(v) for k, v in groups.items()
            if len(v) >= MEDIAN_MIN_POSTS and statistics.median(v) > 0}
