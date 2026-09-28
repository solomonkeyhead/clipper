"""Post links and stats for the Control Center, from the performance log."""

from __future__ import annotations

from ..learn import log as perf

#: Per-post numbers shown on a clip, in display order.
POST_FIELDS = ("posted_at", "views_latest", "likes", "comments", "shares", "saves",
               "avg_watch_s", "watched_full_pct", "skip_rate_pct", "drop_off_s")


def sync_all(rows: list[dict[str, str]]) -> list[str]:
    """Sync `rows` from TikTok and, once connected, Instagram. Returns what failed."""
    from ..instagram import api as ig_api
    from ..instagram import sync as ig_sync
    from ..tiktok import api as tt_api
    from ..tiktok import sync as tt_sync

    problems = []
    try:
        tt_sync.apply(tt_api.list_videos(tt_api.access_token()), rows)
    except tt_api.TikTokError as exc:
        problems.append(f"TikTok: {exc}")
    if ig_api.token_path().exists():
        try:
            ig_sync.apply(ig_api.list_reels(ig_api.access_token()), rows,
                          account=ig_api.username())
        except ig_api.InstagramError as exc:
            problems.append(f"Instagram: {exc}")
    return problems


def posts_by_clip(rows: list[dict[str, str]]) -> dict[tuple[str, str, str], list[dict]]:
    """(campaign, source_id, clip_id) -> that clip's posts, one per platform row with a link."""
    out: dict[tuple[str, str, str], list[dict]] = {}
    for row in rows:
        url = (row.get("url") or "").strip()
        if not url:
            continue
        post = {"platform": (row.get("platform") or "").strip() or "tiktok",
                "account": (row.get("account") or "").strip(),
                "url": url.split("?", 1)[0]}
        for name in POST_FIELDS:
            value = (row.get(name) or "").strip()
            number = perf.number(value) if name != "posted_at" else None
            post[name] = number if number is not None else (value or None)
        key = (row.get("campaign", ""), row.get("source_id", ""), row.get("clip_id", ""))
        out.setdefault(key, []).append(post)
    return out


def last_synced(rows: list[dict[str, str]]) -> str:
    return max((r.get("synced_at") or "" for r in rows), default="")
