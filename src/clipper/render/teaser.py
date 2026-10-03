"""Opening on the payoff: a clip's best line shown first, then the clip (D97).

Platform research (D95): viewers decide in the first second, and the clip
accounts that hold them open on the line people quote, then play the setup; a
re-cut like this is also the kind of edit originality checks count as the
poster's own. So once a clip is rendered and has passed its checks, its payoff
line -- picked with its opening (candidates/opening.py) -- is cut from the
finished file and put in front of it, with the on-screen hook over it so the
first frame carries both. Taking it from the finished clip keeps the framing,
captions and loudness identical; the cost is one more encode.

Rebuilt after the first ones felt like a glitch (D107, cold-open research):
* an open loop -- the teaser stops in the gap between words before the line's
  last ~40%, so it raises the stakes and holds the punchline back; a line too
  short to split (under 3 words) gets no teaser, as giving it all away loses
  viewers once the tension is spent;
* the jump back is signalled, never silent: a soft white bloom (2 frames out of
  the teaser, 4 into the clip) and equal-power (quarter-sine) fades either side,
  the teaser's ending on the quiet between two words;
* only on unscripted footage (campaign/edits.cold_open_allowed), with the
  payoff at most 45 s in, and a late payoff ends the clip so it loops.
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
#: Of the payoff line's words, the share the teaser shows before it stops.
SHOWN = 0.6
MIN_WORDS = 3
LEAD = 0.12            # seconds of the line's lead-in kept before its first word
FADE_IN = 0.02         # the teaser's first sound, faded in so it doesn't click
FADE_OUT = 0.12        # its last sound, faded into the gap between words
CLIP_FADE_IN = 0.08    # the clip's own first sound after the jump
BLOOM_OUT, BLOOM_IN = 2, 4   # frames of white either side of the jump


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


def page_starts(ass_text: str) -> list[float]:
    """When each caption page appears. A page shows all its words at once (the spoken
    one lit), so a teaser that reaches into a page shows its last words early."""
    import re

    from ..utils.timecode import from_ass

    starts, last = [], None
    for line in ass_text.splitlines():
        if not line.startswith("Dialogue:"):
            continue
        fields = line.split(":", 1)[1].split(",", 9)
        if len(fields) < 10 or fields[3].strip() != "Caption":
            continue
        plain = re.sub(r"\{[^}]*\}", "", fields[9]).strip()
        if plain != last:
            try:
                starts.append(from_ass(fields[1].strip()))
            except ValueError:
                continue
            last = plain
    return starts


def open_loop(start: float, end: float, words: list[tuple[float, float, str]] | None,
              pages: list[float] | None = None) -> tuple[float, float] | None:
    """The part of the payoff line to show: from just before its first word to the
    gap after its first ~60% of words -- pulled back to where that caption page
    starts, so no held-back word is on screen -- or None when too little is left."""
    if words is None:  # no words to go by: the whole line, as before
        return max(0.0, start - LEAD), end
    said = sorted(w for w in words if w[2].strip())
    if len(said) < MIN_WORDS:
        return None
    first = max(0.0, said[0][0] - LEAD)
    keep = max(2, min(len(said) - 1, round(len(said) * SHOWN)))
    # Never shorter than a moment: keep words until it lasts MIN_SECONDS, while one is still held back.
    while keep < len(said) - 1 and said[keep - 1][1] - first < MIN_SECONDS:
        keep += 1
    cut = (said[keep - 1][1] + said[keep][0]) / 2
    # The first held-back word's caption page must not appear: stop before it does.
    page = max((p for p in pages or [] if p <= said[keep][0] + 0.02), default=None)
    if page is not None and page < cut:
        before = [w for w in said if w[1] <= page + 0.02]
        cut = (before[-1][1] + page) / 2 if before else page
    if cut - first < MIN_SECONDS * 0.75:
        return None
    return first, min(cut, first + MAX_SECONDS)


def prepend(clip: Path, start: float, end: float, *, hook: str, config: Config, work_dir: Path,
            draft: bool = False, has_audio: bool = True,
            words: list[tuple[float, float, str]] | None = None, pages: list[float] | None = None) -> Path:
    """`clip` with the opening of its own payoff line (`start`-`end`, seconds into
    it; `words` the line's words on that clock) played first. Replaces `clip` in
    place and returns it; on any failure, or a line too short to tease, the clip is
    left as it was."""
    rc = config.render
    span = open_loop(start, end, words, pages)
    if span is None:
        log.info("%s: payoff too short to hold back; no teaser", clip.name)
        return clip
    start, end = span
    length = end - start
    fps = rc.fps
    subs = hook_filter(clip, hook, length, config=config, work_dir=work_dir, draft=draft, tag="teaser")
    video = (f"[0:v]trim=start={start:.3f}:end={end:.3f},setpts=PTS-STARTPTS,{subs},"
             f"fade=t=out:st={length - BLOOM_OUT / fps:.3f}:d={BLOOM_OUT / fps:.3f}:color=white[tv];"
             f"[0:v]setpts=PTS-STARTPTS,fade=t=in:st=0:d={BLOOM_IN / fps:.3f}:color=white[mv]")
    if has_audio:
        audio = (f";[0:a]atrim=start={start:.3f}:end={end:.3f},asetpts=PTS-STARTPTS,"
                 f"afade=t=in:d={FADE_IN}:curve=qsin,"
                 f"afade=t=out:st={length - FADE_OUT:.3f}:d={FADE_OUT}:curve=qsin[ta];"
                 f"[0:a]asetpts=PTS-STARTPTS,afade=t=in:d={CLIP_FADE_IN}:curve=qsin[ma];"
                 "[tv][ta][mv][ma]concat=n=2:v=1:a=1[v][a]")
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
