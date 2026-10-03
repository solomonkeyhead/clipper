"""Opening on the payoff: a clip's best line shown first, then the clip (D97).

Platform research (D95): viewers decide in the first second, and the clip
accounts that hold them open on the line people quote, then play the setup; a
re-cut like this is also the kind of edit originality checks count as the
poster's own. So once a clip is rendered and has passed its checks, its payoff
line -- picked with its opening (candidates/opening.py) -- is cut from the
finished file and put in front of it, with the on-screen hook over it so the
first frame carries both. Taking it from the finished clip keeps the framing,
captions and loudness identical; the cost is one more encode.
"""

from __future__ import annotations

from pathlib import Path

from ..config import Config
from ..utils.logging import get_logger
from . import captions as cap
from .ffmpeg import bundled_fonts_dir, escape_filter_path, run, select_video_encoder
from .graph import output_size

log = get_logger(__name__)

#: A payoff shorter than this is a word, not a moment; longer gives the clip away.
MIN_SECONDS, MAX_SECONDS = 0.8, 3.5
#: It must come at least this far into the clip, or the clip already opens on it.
MIN_INTO_CLIP = 4.0
FADE = 0.06


def hook_filter(clip: Path, hook: str, length: float, *, config: Config, work_dir: Path,
                draft: bool, tag: str) -> str:
    """An `ass` filter showing only the on-screen hook for `length` seconds."""
    rc = config.render
    width, height = output_size(rc, draft=draft)
    ass = work_dir / f"{clip.stem}.{tag}.ass"
    cap.write_ass(cap.build_ass([], style=cap.get_style(rc.caption_style), width=width, height=height,
                                safe_area=rc.safe_area, clip_start=0.0, duration=length,
                                hook_text=hook if rc.show_hook_text else "",
                                hook_seconds=length if rc.show_hook_text else 0.0), ass)
    return f"ass=f='{escape_filter_path(ass)}':fontsdir='{escape_filter_path(bundled_fonts_dir())}'"


def reencode_args(config: Config) -> list[str]:
    """A second encode of the same frames: a touch higher quality than the first."""
    rc = config.render
    encoder, _ = select_video_encoder(rc.encoder)
    if encoder == "h264_nvenc":
        return ["-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", str(max(rc.nvenc_cq - 2, 0)),
                "-b:v", "0", "-profile:v", "high"]
    return ["-c:v", "libx264", "-preset", rc.x264_preset, "-crf", str(max(rc.crf - 2, 0)),
            "-profile:v", "high", "-level", "4.2"]


def prepend(clip: Path, start: float, end: float, *, hook: str, config: Config, work_dir: Path,
            draft: bool = False, has_audio: bool = True) -> Path:
    """`clip` with its own `start`-`end` (seconds into it) played first. Replaces
    `clip` in place and returns it; on any failure the clip is left as it was."""
    rc = config.render
    length = end - start
    subs = hook_filter(clip, hook, length, config=config, work_dir=work_dir, draft=draft, tag="teaser")
    video = (f"[0:v]trim=start={start:.3f}:end={end:.3f},setpts=PTS-STARTPTS,{subs}[tv];"
             f"[0:v]setpts=PTS-STARTPTS[mv]")
    if has_audio:
        audio = (f";[0:a]atrim=start={start:.3f}:end={end:.3f},asetpts=PTS-STARTPTS,"
                 f"afade=t=in:d={FADE},afade=t=out:st={length - FADE:.3f}:d={FADE}[ta];"
                 f"[0:a]asetpts=PTS-STARTPTS[ma];[tv][ta][mv][ma]concat=n=2:v=1:a=1[v][a]")
    else:
        audio = ";[tv][mv]concat=n=2:v=1:a=0[v]"
    video_args = reencode_args(config)
    out = clip.with_name(f"{clip.stem}.teased{clip.suffix}")
    args = ["-hide_banner", "-nostdin", "-loglevel", "error", "-i", str(clip),
            "-filter_complex", video + audio, "-map", "[v]",
            *(["-map", "[a]", "-c:a", "aac", "-b:a", rc.audio_bitrate, "-ar", str(rc.audio_rate), "-ac", "2"]
              if has_audio else ["-an"]),
            *video_args, "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-fps_mode", "cfr", "-y", str(out)]
    try:
        run(args)
        out.replace(clip)
    except Exception as exc:  # the clip without its cold open beats no clip
        log.warning("%s: couldn't put its payoff first (%s); kept as rendered", clip.name, str(exc)[:200])
        out.unlink(missing_ok=True)
        return clip
    log.info("%s: opens on its payoff (%.1fs from %.1fs in)", clip.name, length, start)
    return clip
