"""ffprobe wrapper.

Returns a `MediaInfo` and nothing looser -- a stage that needs the duration
should not have to reach into a raw ffprobe dict and guess which of
``format.duration`` or ``stream.duration`` is populated for this container.
"""

from __future__ import annotations

import contextlib
import json
import os
from fractions import Fraction
from pathlib import Path

from ..models import MediaInfo
from ..render.ffmpeg import FFmpegError, ffprobe_path, run

DEVNULL = "NUL" if os.name == "nt" else "/dev/null"
"""FFmpeg's null sink target. Windows has no /dev/null."""


def _parse_fps(value: str | None) -> float:
    """Parse ffprobe's ``30000/1001`` rational form."""
    if not value or value == "0/0":
        return 0.0
    try:
        return float(Fraction(value))
    except (ValueError, ZeroDivisionError):
        return 0.0


def _first_float(*values: object) -> float:
    """First value that parses as a positive float. ffprobe scatters duration."""
    for v in values:
        if v in (None, "", "N/A"):
            continue
        try:
            f = float(v)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            continue
        if f > 0:
            return f
    return 0.0


def probe_raw(path: Path | str) -> dict:
    """The full ffprobe JSON, for callers that need something unusual."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"media file not found: {path}")
    proc = run(
        [
            "-v", "error",
            "-print_format", "json",
            "-show_format",
            "-show_streams",
            str(path),
        ],
        exe=ffprobe_path(),
    )
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise FFmpegError(
            f"ffprobe returned unparseable JSON for {path.name}: {exc}",
            returncode=0,
            stderr=proc.stderr,
            args=proc.args if isinstance(proc.args, list) else [str(proc.args)],
        ) from exc


def probe(path: Path | str) -> MediaInfo:
    """Probe one media file into a `MediaInfo`."""
    path = Path(path)
    data = probe_raw(path)
    fmt = data.get("format", {})
    streams = data.get("streams", [])

    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)

    if video is None and audio is None:
        raise FFmpegError(
            f"{path.name} has no video or audio streams; it may be corrupt or not media.",
            returncode=0, stderr="", args=["ffprobe", str(path)],
        )

    duration = _first_float(
        fmt.get("duration"),
        (video or {}).get("duration"),
        (audio or {}).get("duration"),
    )

    fps = 0.0
    if video is not None:
        # avg_frame_rate reflects the whole file; r_frame_rate is the base rate
        # and can be wildly high for variable-frame-rate captures.
        fps = _parse_fps(video.get("avg_frame_rate")) or _parse_fps(video.get("r_frame_rate"))

    # Rotation metadata means the displayed dimensions are swapped; a phone-shot
    # source that is really vertical must not be treated as 16:9.
    width = int(video.get("width", 0)) if video else 0
    height = int(video.get("height", 0)) if video else 0
    if video is not None and _rotation(video) % 180 == 90:
        width, height = height, width

    return MediaInfo(
        path=str(path.resolve()),
        duration=duration,
        width=width,
        height=height,
        fps=fps,
        has_audio=audio is not None,
        video_codec=(video or {}).get("codec_name", ""),
        audio_codec=(audio or {}).get("codec_name", ""),
        audio_channels=int((audio or {}).get("channels", 0) or 0),
        audio_sample_rate=int((audio or {}).get("sample_rate", 0) or 0),
        size_bytes=int(_first_float(fmt.get("size"))) or (path.stat().st_size if path.exists() else 0),
    )


def _rotation(video_stream: dict) -> int:
    """Rotation in degrees, from either the tag or a display-matrix side packet."""
    tags = video_stream.get("tags", {}) or {}
    for key in ("rotate", "rotation"):
        if key in tags:
            try:
                return abs(int(float(tags[key]))) % 360
            except (TypeError, ValueError):
                pass
    for side in video_stream.get("side_data_list", []) or []:
        if "rotation" in side:
            try:
                return abs(int(float(side["rotation"]))) % 360
            except (TypeError, ValueError):
                pass
    return 0


def measure_loudness(path: Path | str, *, start: float | None = None,
                     duration: float | None = None) -> dict[str, float]:
    """Integrated loudness, loudness range and true peak, via ``loudnorm``.

    Used by the QA gate (section 12). Runs loudnorm in analysis mode, which
    prints a JSON block to stderr on completion.
    """
    args: list[str] = ["-hide_banner", "-nostats"]
    if start is not None:
        args += ["-ss", f"{start:.3f}"]
    args += ["-i", str(path)]
    if duration is not None:
        args += ["-t", f"{duration:.3f}"]
    args += ["-af", "loudnorm=print_format=json", "-f", "null", DEVNULL]

    proc = run(args, check=False)
    block = _last_json_object(proc.stderr)
    if block is None:
        raise FFmpegError(
            f"loudnorm produced no JSON for {Path(path).name}; the file may have no audio.",
            returncode=proc.returncode, stderr=proc.stderr, args=list(proc.args),
        )
    out: dict[str, float] = {}
    for key in ("input_i", "input_tp", "input_lra", "input_thresh"):
        try:
            out[key.removeprefix("input_")] = float(block[key])
        except (KeyError, TypeError, ValueError):
            continue
    return out


def _last_json_object(text: str) -> dict | None:
    """Pull the final ``{...}`` block out of FFmpeg's stderr."""
    depth = 0
    start = -1
    best: dict | None = None
    for i, ch in enumerate(text):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}" and depth:
            depth -= 1
            if depth == 0 and start >= 0:
                with contextlib.suppress(json.JSONDecodeError):
                    best = json.loads(text[start : i + 1])
    return best
