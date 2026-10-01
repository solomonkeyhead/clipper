"""Fill the performance log from the account's Instagram Reels.

A clip's log row is written when it is rendered and doubles as its TikTok row.
The same clip posted as a Reel gets a row of its own (platform "instagram"),
copied from the clip's row the first time the Reel is matched by caption, and
found again by its media id after that. Filling rules follow `tiktok.sync`:
one-time view snapshots in their age windows, counts that only go up, and
nothing the user typed overwritten.
"""

from __future__ import annotations

import time
from datetime import datetime

from ..learn import log as perf
from ..tiktok.sync import WINDOWS, SyncResult, best_row
from .api import Reel

PLATFORM = "instagram"
#: Copied from the clip's row onto its Instagram row.
CARRIED = ["caption", "duration_s", "opening", "lead_in_s", "hook", "campaign",
           "source_title", "file", "source_id", "clip_id", "candidate_id"]


def apply(reels: list[Reel], rows: list[dict[str, str]], *, account: str = "",
          now: float | None = None, platform: str = PLATFORM) -> SyncResult:
    """Update `rows` in place (appending rows for `platform` as needed) from `reels`.

    YouTube Shorts use it too (youtube/api.Short has the same fields): a clip
    reposted on another platform works the same way whichever it is.
    """
    now = time.time() if now is None else now
    result = SyncResult()
    by_id = {r.get("video_id", ""): i for i, r in enumerate(rows)
             if r.get("platform") == platform and r.get("video_id")}
    # Links pasted by hand in the Control Center have a URL but no media id yet.
    by_url = {_bare(r.get("url", "")): i for i, r in enumerate(rows)
              if r.get("platform") == platform and r.get("url") and not r.get("video_id")}
    for reel in reels:
        index = by_id.get(reel.id)
        if index is None and _bare(reel.url) in by_url:
            index = by_url.pop(_bare(reel.url))
            rows[index]["video_id"] = reel.id
            by_id[reel.id] = index
        if index is None:
            # One candidate per clip: its rows on other platforms carry the same caption.
            # Clip ids repeat across sources ("001_0m00s"), so a clip is its source and id.
            clips = {(r.get("source_id"), r.get("clip_id")) if r.get("clip_id") else i: i
                     for i, r in enumerate(rows) if r.get("platform") != platform}
            candidates = [(i, rows[i].get("caption", "")) for i in clips.values()]
            found, ambiguous = best_row(reel.caption, candidates)
            for text in getattr(reel, "alternatives", []):  # a Short: title + description
                if found is not None or ambiguous:
                    break
                found, ambiguous = best_row(text, candidates)
            if ambiguous:
                result.ambiguous.append(reel)
                continue
            if found is None:
                result.unmatched.append(reel)
                continue
            template = rows[found]
            rows.append({**{c: template.get(c, "") for c in CARRIED},
                         "platform": platform, "account": account, "video_id": reel.id})
            index = len(rows) - 1
            by_id[reel.id] = index
        filled = _update(rows[index], reel, now)
        result.matched.append((rows[index].get("caption", "")[:50],
                               ", ".join(filled) or "no change"))
    return result


def _bare(url: str) -> str:
    return url.split("?", 1)[0].rstrip("/").lower()


def _update(row: dict[str, str], reel: Reel, now: float) -> list[str]:
    filled: list[str] = []

    def put(column: str, value: str, *, only_if_empty: bool = True) -> None:
        if only_if_empty and (row.get(column) or "").strip():
            return
        if (row.get(column) or "") != value:
            row[column] = value
            filled.append(column)

    put("url", reel.url)
    put("posted_caption", getattr(reel, "full_text", reel.caption), only_if_empty=False)
    if reel.created:
        put("posted_at", datetime.fromtimestamp(reel.created).strftime("%Y-%m-%d %H:%M"))
    if not getattr(reel, "measured", True):
        # Its views weren't read this time: keep what the log has, take only the counts.
        for column, value in (("likes", reel.likes), ("comments", reel.comments)):
            current = perf.number(row.get(column))
            if current is None or value > current:
                put(column, str(value), only_if_empty=False)
        row["synced_at"] = datetime.fromtimestamp(now).strftime("%Y-%m-%d %H:%M")
        return filled
    age = now - reel.created if reel.created else -1
    for column, low, high in WINDOWS:
        if low <= age < high:
            put(column, str(reel.views))
    for column, value in (("likes", reel.likes), ("comments", reel.comments),
                          ("shares", reel.shares), ("saves", reel.saves)):
        current = perf.number(row.get(column))
        if current is None or value > current:
            put(column, str(value), only_if_empty=False)
    put("views_latest", str(reel.views), only_if_empty=False)
    # Instagram measures these itself, so its latest figure is the right one.
    if reel.avg_watch_s is not None:
        put("avg_watch_s", f"{reel.avg_watch_s:g}", only_if_empty=False)
    if reel.skip_rate_pct is not None:
        put("skip_rate_pct", f"{reel.skip_rate_pct:g}", only_if_empty=False)
    row["synced_at"] = datetime.fromtimestamp(now).strftime("%Y-%m-%d %H:%M")
    return filled
