"""Work on the source before the normal render (docs/DECISIONS.md D59).

* `join_segments`: when pauses and fillers are cut, the kept pieces are joined
  into one intermediate file -- 12 ms audio fades at every join so cuts never
  click, and optionally a 10% punch-in on every other piece to hide the jump
  cut. The normal render then runs on that file as if it were the source, so
  framing, captions and QA all see the tightened timeline.
* `darkness` / `lift_for`: dark footage is lifted with a gamma curve, which
  brightens midtones and keeps black black; only when the picture is dark
  overall and has no bright areas (lit faces keep a scene "intentionally dark").
* `first_bright`: a clip must not open on a black frame.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

from ..utils.logging import get_logger
from .ffmpeg import run

log = get_logger(__name__)

JOIN_FADE = 0.012
PUNCH_IN = 1.10
#: Research thresholds on a 0-1 luma scale.
DARK_MEAN, DARK_P95 = 0.22, 0.55
BLACK_FRAME = 0.12
#: ffmpeg's eq gamma is out = in^(1/gamma), so above 1 brightens. 1.12 is the
#: research's gentle end (power 0.89): measured on a Chad Powers night frame at
#: 1.15, mean luma went 0.085 -> 0.126 (+0.57 EV), more than its +0.2-0.4 EV.
LIFT_GAMMA = 1.12


def join_segments(source: Path, segments: list[tuple[float, float]], output: Path, *,
                  width: int, height: int, has_audio: bool, punch_in: bool = False,
                  zooms: list[float] | None = None) -> Path:
    """Concatenate `segments` (source seconds) of `source` into `output`.

    Each segment is zoomed by `zooms` (the editor's choice, D103); without them,
    `punch_in` alternates every other one in by PUNCH_IN, hiding the jump."""
    base = segments[0][0]
    span = segments[-1][1] - base
    if zooms is None:
        zooms = [PUNCH_IN if punch_in and i % 2 == 1 else 1.0 for i in range(len(segments))]
    parts, labels = [], []
    for i, (a, b) in enumerate(segments):
        s, e = a - base, b - base
        video = f"[0:v]trim=start={s:.3f}:end={e:.3f},setpts=PTS-STARTPTS"
        if zooms[i] > 1.0:
            # Zoom towards the upper-middle, where heads are, not the dead centre.
            zw, zh = (int(width * zooms[i]) // 2) * 2, (int(height * zooms[i]) // 2) * 2
            video += (f",scale={zw}:{zh}:flags=lanczos,"
                      f"crop={width}:{height}:{(zw - width) // 2}:{int((zh - height) * 0.35)}")
        parts.append(f"{video},setsar=1[v{i}]")
        labels.append(f"[v{i}]")
        if has_audio:
            length = e - s
            parts.append(
                f"[0:a]atrim=start={s:.3f}:end={e:.3f},asetpts=PTS-STARTPTS,"
                f"afade=t=in:st=0:d={JOIN_FADE},"
                f"afade=t=out:st={max(0.0, length - JOIN_FADE):.3f}:d={JOIN_FADE}[a{i}]")
            labels.append(f"[a{i}]")
    n = len(segments)
    parts.append("".join(labels) + f"concat=n={n}:v=1:a={1 if has_audio else 0}"
                 + ("[v][a]" if has_audio else "[v]"))
    args = ["-hide_banner", "-nostdin", "-loglevel", "error", "-y",
            "-ss", f"{base:.3f}", "-t", f"{span + 0.05:.3f}", "-i", str(source),
            "-filter_complex", ";".join(parts), "-map", "[v]"]
    if has_audio:
        args += ["-map", "[a]", "-c:a", "pcm_s16le"]
    args += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "12", "-pix_fmt", "yuv420p",
             str(output)]
    output.parent.mkdir(parents=True, exist_ok=True)
    run(args)
    return output


@dataclass(frozen=True)
class Darkness:
    mean: float
    p95: float
    p99: float

    @property
    def dark(self) -> bool:
        return self.mean < DARK_MEAN and self.p95 < DARK_P95


def _gray(frame) -> np.ndarray:
    import cv2

    small = cv2.resize(frame, (320, int(320 * frame.shape[0] / frame.shape[1])))
    return cv2.cvtColor(small, cv2.COLOR_BGR2GRAY).astype(np.float32) / 255.0


def darkness(video: Path, start: float, duration: float, samples: int = 12) -> Darkness | None:
    """Luma statistics over `samples` frames spread through the span."""
    import cv2

    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        return None
    values = []
    try:
        for i in range(samples):
            cap.set(cv2.CAP_PROP_POS_MSEC, (start + duration * (i + 0.5) / samples) * 1000)
            ok, frame = cap.read()
            if ok and frame is not None:
                values.append(_gray(frame).ravel())
    finally:
        cap.release()
    if not values:
        return None
    luma = np.concatenate(values)
    return Darkness(mean=float(luma.mean()), p95=float(np.percentile(luma, 95)),
                    p99=float(np.percentile(luma, 99)))


def lift_for(stats: Darkness | None) -> float | None:
    """The gamma to apply, or None to leave the picture alone."""
    if stats is None or not stats.dark:
        return None
    return LIFT_GAMMA


def first_bright(video: Path, start: float, limit: float = 1.0) -> float:
    """Seconds after `start` until the first frame that isn't black (0.0 if it isn't)."""
    import cv2

    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        return 0.0
    try:
        fps = cap.get(cv2.CAP_PROP_FPS) or 24.0
        cap.set(cv2.CAP_PROP_POS_MSEC, start * 1000)
        for i in range(int(limit * fps) + 1):
            ok, frame = cap.read()
            if not ok or frame is None:
                break
            if float(_gray(frame).mean()) >= BLACK_FRAME:
                return i / fps
    finally:
        cap.release()
    return 0.0
