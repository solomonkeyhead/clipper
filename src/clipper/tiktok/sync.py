"""Fill the performance log from the account's videos.

Each video is matched to its log row by TikTok video id once known, otherwise
by caption (the log's first column is the caption as posted). Filling rules:

* `views_24h` / `views_7d` / `views_30d` are set once, by the first sync that
  runs inside that age's window -- a sync nine days after posting must not
  record its count as the 7-day number. Scheduled daily, each window is hit.
* likes, comments and shares only ever go up (the user may have typed a fresher
  number from TikTok Studio).
* `views_latest` and `synced_at` show the most recent sync.
* Nothing the user typed is overwritten, and watch time is never touched: the
  Display API does not provide it.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from datetime import datetime

from ..learn import log as perf
from .api import Video

DAY = 86400.0
#: (column, earliest age, latest age) for one-time view snapshots.
WINDOWS = [("views_24h", 1 * DAY, 3 * DAY), ("views_7d", 7 * DAY, 10 * DAY),
           ("views_30d", 30 * DAY, 40 * DAY)]
#: Captions are compared on this many normalised characters: TikTok keeps up
#: to 150, and the start of a caption is what tells posts apart.
MATCH_CHARS = 60


@dataclass
class SyncResult:
    matched: list[tuple[str, str]] = field(default_factory=list)  # (caption, filled columns)
    unmatched: list[Video] = field(default_factory=list)
    ambiguous: list[Video] = field(default_factory=list)


def normalise(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").lower()).strip()


def match(videos: list[Video], rows: list[dict[str, str]]) -> tuple[dict[int, Video], SyncResult]:
    """Row index -> video, plus the videos that found no row or several."""
    result = SyncResult()
    by_id = {r.get("video_id", ""): i for i, r in enumerate(rows) if r.get("video_id")}
    found: dict[int, Video] = {}
    for video in videos:
        if video.id in by_id:
            found[by_id[video.id]] = video
            continue
        key = normalise(video.caption)[:MATCH_CHARS]
        hits = [i for i, r in enumerate(rows)
                if key and not r.get("video_id")
                and normalise(r.get("caption", ""))[:MATCH_CHARS] == key]
        if len(hits) == 1:
            found[hits[0]] = video
        elif hits:
            result.ambiguous.append(video)
        else:
            result.unmatched.append(video)
    return found, result


def apply(videos: list[Video], rows: list[dict[str, str]], *,
          now: float | None = None) -> SyncResult:
    """Update `rows` in place from `videos`."""
    now = time.time() if now is None else now
    found, result = match(videos, rows)
    for index, video in found.items():
        filled = _update(rows[index], video, now)
        result.matched.append((rows[index].get("caption", "")[:50],
                               ", ".join(filled) or "no change"))
    return result


def _update(row: dict[str, str], video: Video, now: float) -> list[str]:
    """Update one row from its video; returns the columns that changed."""
    filled: list[str] = []

    def put(column: str, value: str, *, only_if_empty: bool = True) -> None:
        if only_if_empty and (row.get(column) or "").strip():
            return
        if (row.get(column) or "") != value:
            row[column] = value
            filled.append(column)

    put("video_id", video.id)
    put("url", video.url)
    if video.created:
        put("posted_at", datetime.fromtimestamp(video.created).strftime("%Y-%m-%d %H:%M"))
    age = now - video.created if video.created else -1
    for column, low, high in WINDOWS:
        if low <= age < high:
            put(column, str(video.views))
    for column, value in (("likes", video.likes), ("comments", video.comments),
                          ("shares", video.shares)):
        current = perf.number(row.get(column))
        if current is None or value > current:
            put(column, str(value), only_if_empty=False)
    put("views_latest", str(video.views), only_if_empty=False)
    row["synced_at"] = datetime.fromtimestamp(now).strftime("%Y-%m-%d %H:%M")
    return filled
