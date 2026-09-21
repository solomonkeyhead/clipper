"""Time formatting and frame snapping.

ASS uses centisecond precision with a one-digit hour (``0:01:23.45``). Getting
this subtly wrong produces captions that drift by a frame or two -- visible, and
annoying to debug later -- so the conversions live in one place with tests.
"""

from __future__ import annotations

import math

MAX_ASS_SECONDS = 10 * 3600 - 0.005
"""ASS hours are a single digit, so timestamps wrap above ~10 hours."""


def _split(seconds: float) -> tuple[int, int, int, int]:
    """Split into (h, m, s, centiseconds), rounding to the nearest centisecond."""
    if seconds < 0:
        seconds = 0.0
    total_cs = round(seconds * 100)
    h, rem = divmod(total_cs, 360_000)
    m, rem = divmod(rem, 6_000)
    s, cs = divmod(rem, 100)
    return h, m, s, cs


def to_ass(seconds: float) -> str:
    """Format as an ASS timestamp: ``H:MM:SS.cc``."""
    h, m, s, cs = _split(min(seconds, MAX_ASS_SECONDS))
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def from_ass(stamp: str) -> float:
    """Parse ``H:MM:SS.cc`` back to seconds. Inverse of `to_ass`."""
    h, m, rest = stamp.split(":")
    return int(h) * 3600 + int(m) * 60 + float(rest)


def to_srt(seconds: float) -> str:
    """Format as an SRT timestamp: ``HH:MM:SS,mmm``."""
    if seconds < 0:
        seconds = 0.0
    ms_total = round(seconds * 1000)
    h, rem = divmod(ms_total, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def to_ffmpeg(seconds: float) -> str:
    """Format for ``-ss`` / ``-to``: ``HH:MM:SS.mmm``, which FFmpeg parses exactly."""
    if seconds < 0:
        seconds = 0.0
    ms_total = round(seconds * 1000)
    h, rem = divmod(ms_total, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"


def to_slug_timestamp(seconds: float) -> str:
    """Compact form for filenames: ``12m34s``."""
    total = int(seconds)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h}h{m:02d}m{s:02d}s" if h else f"{m}m{s:02d}s"


def snap_to_frame(seconds: float, fps: float, *, mode: str = "nearest") -> float:
    """Quantise a time to a frame boundary.

    Clip boundaries that land mid-frame make the first or last frame a partial
    duplicate. `mode` is "nearest", "floor" (for starts) or "ceil" (for ends).
    """
    if fps <= 0:
        raise ValueError("fps must be positive")
    frames = seconds * fps
    if mode == "floor":
        frame = math.floor(frames)
    elif mode == "ceil":
        frame = math.ceil(frames)
    elif mode == "nearest":
        frame = round(frames)
    else:
        raise ValueError(f"unknown mode {mode!r}; use nearest, floor or ceil")
    return max(0.0, frame / fps)


def format_duration(seconds: float) -> str:
    """Human-readable duration for reports and logs: ``1h02m``, ``3m21s``, ``8.4s``."""
    if seconds < 60:
        return f"{seconds:.1f}s"
    total = round(seconds)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h{m:02d}m"
    return f"{m}m{s:02d}s"
