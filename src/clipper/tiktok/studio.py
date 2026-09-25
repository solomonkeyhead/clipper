"""Read a TikTok Studio post-analytics page the user copied (Ctrl+A, Ctrl+C).

The Display API has no watch time, completion, saves or new followers; TikTok
Studio shows them, and reading Studio automatically would break TikTok's terms.
The user copies the page instead, and this parses the text. The layout it
expects, from real pages (tests/fixtures/tiktok_studio/):

    <caption>
    Posted on 9/23/2026
    <views> <likes> <comments> <shares> <saves>      (one per line)
    Video views / Total play time / Average watch time / Watched full video /
    New followers -- each label followed by its value on the next line
    Retention: "Most viewers stopped watching at 0:01."

The order of the five counts was checked against the API: the second is the
like count, the fourth the shares.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from datetime import datetime

from ..learn import log as perf
from .sync import MATCH_CHARS, normalise


@dataclass
class StudioStats:
    caption: str
    posted: str
    views: float
    likes: float
    comments: float
    shares: float
    saves: float
    avg_watch_s: float | None
    watched_full_pct: float | None
    new_followers: float | None
    drop_off_s: float | None
    processing: bool


def looks_like_studio(text: str) -> bool:
    return bool(text) and "Average watch time" in text and "Posted on" in text


def _after(label: str, lines: list[str]) -> str | None:
    """The first non-empty line after the line that is exactly `label`."""
    for i, line in enumerate(lines):
        if line == label:
            return next((x for x in lines[i + 1:] if x), None)
    return None


def parse(text: str) -> StudioStats | None:
    if not looks_like_studio(text):
        return None
    lines = [line.strip() for line in text.replace("\r", "").split("\n")]
    posted_at = next((i for i, x in enumerate(lines) if x.startswith("Posted on")), None)
    if posted_at is None:
        return None
    caption = next((x for x in reversed(lines[:posted_at]) if x), "")
    counts = []
    for line in lines[posted_at + 1:]:
        if not line:
            continue
        value = perf.number(line)
        if value is None:
            break
        counts.append(value)
        if len(counts) == 5:
            break
    if len(counts) < 5:
        return None
    views, likes, comments, shares, saves = counts
    drop = re.search(r"stopped watching at (\d+):(\d{2})", text)
    avg = perf.number(_after("Average watch time", lines))
    full = perf.number(_after("Watched full video", lines))
    return StudioStats(
        caption=caption, posted=lines[posted_at][len("Posted on"):].strip(),
        views=views, likes=likes, comments=comments, shares=shares, saves=saves,
        avg_watch_s=avg, watched_full_pct=full,
        new_followers=perf.number(_after("New followers", lines)),
        drop_off_s=int(drop.group(1)) * 60 + int(drop.group(2)) if drop else None,
        processing="being processed" in text and views == 0)


def fill(stats: StudioStats, rows: list[dict[str, str]], *,
         now: float | None = None) -> tuple[int | None, list[str]]:
    """Put a page's numbers into its row. Returns (row index or None, columns set).

    Unlike the API sync, the page's own columns (log.COLLECTED) are refreshed
    on every paste: the user pastes on purpose, and a later page is more
    complete. Counts shared with the sync only ever go up.
    """
    key = normalise(stats.caption)[:MATCH_CHARS]
    hits = [i for i, r in enumerate(rows) if key and normalise(r.get("caption", ""))[:MATCH_CHARS] == key]
    if len(hits) != 1:
        return None, []
    row = rows[hits[0]]
    changed: list[str] = []

    def put(column: str, value: float | None) -> None:
        if value is None:
            return
        text = f"{value:g}"
        if row.get(column, "") != text:
            row[column] = text
            changed.append(column)

    def at_least(column: str, value: float) -> None:
        current = perf.number(row.get(column))
        if current is None or value > current:
            put(column, value)

    for column, value in (("likes", stats.likes), ("comments", stats.comments),
                          ("shares", stats.shares), ("views_latest", stats.views)):
        at_least(column, value)
    put("saves", stats.saves)
    put("new_followers", stats.new_followers)
    if stats.views > 0:
        # With no viewers, "0s watched" is not a result: leave the cells empty
        # so `learn` does not read the post as one nobody watched past the start.
        put("avg_watch_s", stats.avg_watch_s)
        put("watched_full_pct", stats.watched_full_pct)
        put("drop_off_s", stats.drop_off_s)
    row["studio_at"] = datetime.fromtimestamp(time.time() if now is None else now).strftime(
        "%Y-%m-%d %H:%M")
    return hits[0], changed


def read_clipboard() -> tuple[int, str]:
    """(sequence number, text) of the Windows clipboard; text is "" when unavailable.

    The sequence number changes on every copy, so a caller can poll cheaply and
    read the text only when something new was copied.
    """
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    user32.GetClipboardSequenceNumber.restype = wintypes.DWORD
    user32.OpenClipboard.argtypes = [wintypes.HWND]
    user32.GetClipboardData.argtypes = [wintypes.UINT]
    user32.GetClipboardData.restype = wintypes.HANDLE
    kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalLock.restype = wintypes.LPVOID
    kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    seq = int(user32.GetClipboardSequenceNumber())
    if not user32.OpenClipboard(None):
        return seq, ""
    try:
        handle = user32.GetClipboardData(13)  # CF_UNICODETEXT
        if not handle:
            return seq, ""
        pointer = kernel32.GlobalLock(handle)
        if not pointer:
            return seq, ""
        try:
            return seq, ctypes.wstring_at(pointer)
        finally:
            kernel32.GlobalUnlock(handle)
    finally:
        user32.CloseClipboard()


def clipboard_sequence() -> int:
    import ctypes

    return int(ctypes.WinDLL("user32").GetClipboardSequenceNumber())
