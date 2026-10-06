"""Synthetic media fixtures, generated with FFmpeg.

docs/BUILD_BRIEF.md section 15 / Phase 1 asks for test video built from scratch so the
render path can be exercised without copyrighted footage. Moving coloured shapes
stand in for faces: `moving_face_video` puts a bright disc on a known, analytic
trajectory, so a test can assert the crop followed *the right path* rather than
merely that a file was produced.

Generated files are cached per-signature in the pytest tmp tree, because encoding
them takes a second or two and several tests want the same clip.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from clipper.render.ffmpeg import run

# One shared place to put generated fixtures, set by the `media_cache` fixture.
_CACHE_DIR: Path | None = None


def set_cache_dir(path: Path) -> None:
    global _CACHE_DIR
    _CACHE_DIR = path
    path.mkdir(parents=True, exist_ok=True)


def _cached(name: str, signature: str) -> Path:
    if _CACHE_DIR is None:  # pragma: no cover - fixture always sets it
        raise RuntimeError("tests.fixtures.synthetic.set_cache_dir was never called")
    digest = hashlib.sha1(signature.encode()).hexdigest()[:10]
    return _CACHE_DIR / f"{name}_{digest}.mp4"


@dataclass(frozen=True)
class FaceTrack:
    """The analytic trajectory of the stand-in 'face' disc, in source pixels."""

    x0: float
    x1: float
    y: float
    radius: float
    duration: float

    def x_at(self, t: float) -> float:
        """Centre x at time `t`, linear between x0 and x1."""
        frac = min(1.0, max(0.0, t / self.duration))
        return self.x0 + (self.x1 - self.x0) * frac


def _encode(args: list[str], out: Path) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    run([*args, "-c:v", "libx264", "-preset", "ultrafast", "-crf", "18",
         "-pix_fmt", "yuv420p", "-y", str(out)])
    return out


def bars_video(duration: float = 10.0, width: int = 1920, height: int = 1080,
               fps: int = 30, *, with_audio: bool = True) -> Path:
    """A 16:9 test-pattern clip with a 440 Hz tone. The generic source."""
    sig = f"bars-{duration}-{width}x{height}-{fps}-{with_audio}"
    out = _cached("bars", sig)
    if out.exists():
        return out

    args = ["-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i",
            f"testsrc2=size={width}x{height}:rate={fps}:duration={duration}"]
    if with_audio:
        args += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={duration}",
                 "-c:a", "aac", "-b:a", "128k", "-shortest"]
    return _encode(args, out)


def moving_face_video(
    duration: float = 10.0, width: int = 1920, height: int = 1080, fps: int = 30,
) -> tuple[Path, FaceTrack]:
    """A bright disc sliding left-to-right on a dark field, plus its trajectory.

    Returns the file and the `FaceTrack` describing exactly where the disc is at
    any time, so a follow-crop test can check the crop tracked it.
    """
    track = FaceTrack(x0=width * 0.2, x1=width * 0.8, y=height * 0.45,
                      radius=height * 0.12, duration=duration)
    sig = f"face-{duration}-{width}x{height}-{fps}"
    out = _cached("face", sig)
    if out.exists():
        return out, track

    # A radial gradient masked into a disc, translated over time with `overlay`.
    speed = (track.x1 - track.x0) / duration
    size = int(track.radius * 2)
    args = [
        "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", f"color=c=0x202030:size={width}x{height}:rate={fps}:duration={duration}",
        "-f", "lavfi", "-i", f"color=c=0xF0C8A0:size={size}x{size}:rate={fps}:duration={duration}",
        "-f", "lavfi", "-i", f"sine=frequency=330:duration={duration}",
        "-filter_complex",
        f"[1:v]format=yuva420p,geq="
        f"lum='p(X,Y)':a='if(lte(hypot(X-{size / 2},Y-{size / 2}),{size / 2}),255,0)'[disc];"
        f"[0:v][disc]overlay=x='{track.x0 - track.radius}+{speed}*t':"
        f"y={track.y - track.radius}:shortest=1[v]",
        "-map", "[v]", "-map", "2:a", "-c:a", "aac", "-b:a", "128k",
    ]
    _encode(args, out)
    return out, track


def two_faces_video(duration: float = 10.0, width: int = 1920, height: int = 1080,
                    fps: int = 30) -> Path:
    """Two static discs, well separated horizontally: a stand-in two-shot."""
    sig = f"two-{duration}-{width}x{height}-{fps}"
    out = _cached("two", sig)
    if out.exists():
        return out

    r = int(height * 0.13)
    size = r * 2
    disc = (
        f"format=yuva420p,geq=lum='p(X,Y)':"
        f"a='if(lte(hypot(X-{size / 2},Y-{size / 2}),{size / 2}),255,0)'"
    )
    args = [
        "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", f"color=c=0x181828:size={width}x{height}:rate={fps}:duration={duration}",
        "-f", "lavfi", "-i", f"color=c=0xE8C0A0:size={size}x{size}:rate={fps}:duration={duration}",
        "-f", "lavfi", "-i", f"color=c=0xC0D8F0:size={size}x{size}:rate={fps}:duration={duration}",
        "-f", "lavfi", "-i", f"sine=frequency=520:duration={duration}",
        "-filter_complex",
        f"[1:v]{disc}[d1];[2:v]{disc}[d2];"
        f"[0:v][d1]overlay=x={int(width * 0.25) - r}:y={int(height * 0.45) - r}[b1];"
        f"[b1][d2]overlay=x={int(width * 0.75) - r}:y={int(height * 0.45) - r}:shortest=1[v]",
        "-map", "[v]", "-map", "3:a", "-c:a", "aac", "-b:a", "128k",
    ]
    return _encode(args, out)


def silent_video(duration: float = 5.0, width: int = 1920, height: int = 1080,
                 fps: int = 30) -> Path:
    """Video with a silent audio track. QA should flag the silence ratio."""
    sig = f"silent-{duration}-{width}x{height}-{fps}"
    out = _cached("silent", sig)
    if out.exists():
        return out
    args = ["-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", f"testsrc2=size={width}x{height}:rate={fps}:duration={duration}",
            "-f", "lavfi", "-i", f"anullsrc=r=48000:cl=stereo:d={duration}",
            "-c:a", "aac", "-b:a", "128k", "-shortest"]
    return _encode(args, out)


def black_video(duration: float = 5.0, width: int = 1920, height: int = 1080,
                fps: int = 30) -> Path:
    """Entirely black frames. QA's blackdetect check should fail this."""
    sig = f"black-{duration}-{width}x{height}-{fps}"
    out = _cached("black", sig)
    if out.exists():
        return out
    args = ["-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", f"color=c=black:size={width}x{height}:rate={fps}:duration={duration}",
            "-f", "lavfi", "-i", f"sine=frequency=200:duration={duration}",
            "-c:a", "aac", "-b:a", "128k", "-shortest"]
    return _encode(args, out)


def vertical_video(duration: float = 5.0, width: int = 1080, height: int = 1920,
                   fps: int = 30) -> Path:
    """An already-vertical source: reframing should decline to crop it."""
    sig = f"vert-{duration}-{width}x{height}-{fps}"
    out = _cached("vert", sig)
    if out.exists():
        return out
    args = ["-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", f"testsrc2=size={width}x{height}:rate={fps}:duration={duration}",
            "-f", "lavfi", "-i", f"sine=frequency=440:duration={duration}",
            "-c:a", "aac", "-b:a", "128k", "-shortest"]
    return _encode(args, out)


def extract_frame(video: Path, time: float, out: Path, *, scale: int | None = None) -> Path:
    """Pull a single frame for visual inspection or pixel assertions."""
    out.parent.mkdir(parents=True, exist_ok=True)
    vf = f"scale={scale}:-1" if scale else "null"
    run(["-hide_banner", "-loglevel", "error", "-ss", f"{time:.3f}", "-i", str(video),
         "-vf", vf, "-frames:v", "1", "-y", str(out)])
    return out
