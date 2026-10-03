"""The cover: a clip's best still, put first so the platforms show it (D104).

TikTok and Instagram take a post's first frame as its cover unless you pick one,
and a cover is half of whether someone taps it in a grid or on a profile. So
once a clip is rendered and checked, its finished frames are scored -- a face
big and clear enough to read, sharp (no mid-cut blur), well lit, with some
colour, and ideally between caption lines -- and the best is shown for the
first two frames (1/15 s: the platform's cover, too short to notice in play)
with the on-screen hook over it, then the clip plays as made. Taken from the
finished file, so framing and captions are the clip's own. YouTube Shorts picks
its own frame; this changes nothing there.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

from ..config import Config
from ..utils.logging import get_logger
from .ffmpeg import run
from .teaser import hook_filter, reencode_args

log = get_logger(__name__)

STEP = 0.4                  # seconds between the frames scored
EDGE = 0.5                  # not the very first or last half second
FRAMES = 2                  # how long the cover is held, in frames
SAMPLE_WIDTH = 360
#: A face filling this share of the frame's height reads best as a thumbnail.
FACE_BEST = (0.18, 0.45)


@dataclass(frozen=True)
class Frame:
    t: float
    sharp: float
    light: float            # 0..1, 1 = well exposed
    colour: float
    face: float             # 0..1, a readable face, centred and big enough
    speaking: bool          # a caption line is on screen

    def score(self, top_sharp: float, top_colour: float) -> float:
        base = (0.45 * self.face + 0.30 * self.sharp / max(top_sharp, 1e-6)
                + 0.15 * self.colour / max(top_colour, 1e-6) + (0.0 if self.speaking else 0.10))
        # A dark frame makes a dark thumbnail, however good the face: it scales everything.
        return base * (0.35 + 0.65 * self.light)


def caption_times(ass_text: str, style: str = "Caption") -> list[tuple[float, float]]:
    """When lines of `style` are on screen: captions, or with "Hook" the hook."""
    from ..utils.timecode import from_ass

    out = []
    for line in ass_text.splitlines():
        if not line.startswith("Dialogue:"):
            continue
        fields = line.split(":", 1)[1].split(",", 9)
        if len(fields) < 10 or fields[3].strip() != style:
            continue
        try:
            out.append((from_ass(fields[1].strip()), from_ass(fields[2].strip())))
        except (ValueError, IndexError):
            continue
    return out


def _face_score(detector, frame) -> float:
    if detector is None:
        return 0.0
    h, w = frame.shape[:2]
    detector.setInputSize((w, h))
    try:
        _, raw = detector.detect(frame)
    except Exception:  # a malformed frame scores no face
        return 0.0
    if raw is None or not len(raw):
        return 0.0
    best = 0.0
    for row in raw:
        x, y, fw, fh = (float(v) for v in row[:4])
        size = fh / h
        lo, hi = FACE_BEST
        fit = 1.0 if lo <= size <= hi else max(0.0, 1 - (lo - size) / lo) if size < lo else max(0.0, 1 - (size - hi))
        centre = 1 - min(1.0, abs((x + fw / 2) / w - 0.5) * 2)
        upper = 1.0 if (y + fh / 2) / h < 0.65 else 0.6
        best = max(best, float(row[-1]) * fit * (0.6 + 0.4 * centre) * upper)
    return best


def frames(clip: Path, duration: float, *, ass_text: str = "") -> list[Frame]:
    """The clip's frames every STEP seconds, measured."""
    import cv2

    try:
        from .faces import _load_detector

        detector = _load_detector(SAMPLE_WIDTH, SAMPLE_WIDTH * 16 // 9)
    except Exception as exc:  # no face model: judged on the picture alone
        log.debug("cover: no face detector (%s)", exc)
        detector = None
    spoken = caption_times(ass_text)
    cap = cv2.VideoCapture(str(clip))
    out: list[Frame] = []
    try:
        t = EDGE
        while t < duration - EDGE:
            cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
            ok, frame = cap.read()
            if not ok:
                break
            small = cv2.resize(frame, (SAMPLE_WIDTH, int(frame.shape[0] * SAMPLE_WIDTH / frame.shape[1])))
            gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
            mean = float(gray.mean()) / 255
            b, g, r = (small[..., i].astype(np.float32) for i in range(3))
            rg, yb = np.abs(r - g), np.abs(0.5 * (r + g) - b)
            out.append(Frame(
                t=round(t, 3),
                sharp=float(cv2.Laplacian(gray, cv2.CV_64F).var()),
                light=max(0.0, 1 - abs(mean - 0.5) * 2.2),
                colour=float(np.hypot(rg.std(), yb.std()) + 0.3 * np.hypot(rg.mean(), yb.mean())),
                face=_face_score(detector, small),
                speaking=any(a <= t < b for a, b in spoken),
            ))
            t += STEP
    finally:
        cap.release()
    return out


def best(found: list[Frame]) -> Frame | None:
    if not found:
        return None
    top_sharp = max(f.sharp for f in found)
    top_colour = max(f.colour for f in found)
    return max(found, key=lambda f: f.score(top_sharp, top_colour))


def pick(clip: Path, duration: float, *, ass_text: str = "") -> float | None:
    """Seconds into `clip` of its best cover frame, or None if none can be read."""
    chosen = best(frames(clip, duration, ass_text=ass_text))
    return chosen.t if chosen else None


def hook_showing(ass_text: str, at: float) -> bool:
    """Whether the clip's own hook is already on screen at `at` (then it isn't drawn twice)."""
    return any(a <= at < b for a, b in caption_times(ass_text, "Hook"))


def put_first(clip: Path, at: float, *, hook: str, config: Config, work_dir: Path, fps: int,
              draft: bool = False, has_audio: bool = True) -> Path:
    """`clip` opening on its frame at `at` held for FRAMES frames, the hook over it.
    Replaces `clip` in place; on any failure it's left as it was."""
    length = FRAMES / fps
    subs = hook_filter(clip, hook, length, config=config, work_dir=work_dir, draft=draft, tag="cover")
    still = (f"[0:v]trim=start={at:.3f},setpts=PTS-STARTPTS,trim=end_frame=1,"
             f"loop=loop={FRAMES - 1}:size=1:start=0,setpts=N/{fps}/TB,{subs}[cv];"
             f"[0:v]setpts=PTS-STARTPTS[mv]")
    rc = config.render
    if has_audio:
        graph = (still + f";anullsrc=r={rc.audio_rate}:cl=stereo,atrim=end={length:.4f}[ca];"
                 f"[0:a]aresample={rc.audio_rate},aformat=channel_layouts=stereo,asetpts=PTS-STARTPTS[ma];"
                 "[cv][ca][mv][ma]concat=n=2:v=1:a=1[v][a]")
    else:
        graph = still + ";[cv][mv]concat=n=2:v=1:a=0[v]"
    out = clip.with_name(f"{clip.stem}.covered{clip.suffix}")
    args = ["-hide_banner", "-nostdin", "-loglevel", "error", "-i", str(clip),
            "-filter_complex", graph, "-map", "[v]",
            *(["-map", "[a]", "-c:a", "aac", "-b:a", rc.audio_bitrate, "-ar", str(rc.audio_rate), "-ac", "2"]
              if has_audio else ["-an"]),
            *reencode_args(config), "-pix_fmt", "yuv420p", "-movflags", "+faststart",
            "-fps_mode", "cfr", "-r", str(fps), "-y", str(out)]
    try:
        run(args)
        out.replace(clip)
    except Exception as exc:  # the clip without a chosen cover beats no clip
        log.warning("%s: couldn't put its cover first (%s); kept as rendered", clip.name, str(exc)[:200])
        out.unlink(missing_ok=True)
        return clip
    log.info("%s: cover is its frame at %.2fs", clip.name, at)
    return clip
