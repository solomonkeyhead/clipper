"""The finished Short: shots cut to the voice, captions, watermark, cover; filed
into the library under the channel's own campaign.

One shot per beat, so the picture changes as each sentence starts: a diagram
(create/diagrams.py), or stock footage (create/stock.py) filled to 9:16 with a
slow 6% push-in -- split into two clips when a sentence runs past MAX_SHOT, as
a picture held longer than that loses people. Then, in one pass: the shots
joined, the channel's watermark in the top corner, Clipper's captions (the
words timed from the voice) with the question as the hook for the first
seconds, the voice evened to the platform loudness; then the cover frame
(render/cover.py) like every clip.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from ..config import Config
from ..ingest.probe import probe
from ..models import Word
from ..render import captions as cap
from ..render.ffmpeg import bundled_fonts_dir, escape_filter_path, run
from ..render.teaser import reencode_args
from ..utils.cache import slugify
from ..utils.logging import get_logger
from . import channel as channels
from . import diagrams, stock, store
from .ai import CreateError
from .script import Script, Visual
from .voice import Timings, folder

log = get_logger(__name__)

W, H, FPS = 1080, 1920, 30
MAX_SHOT = 4.5
PUSH_IN = 0.06
WATERMARK_WIDTH = 150


def _stock_shot(src: Path, seconds: float, out: Path) -> Path:
    """`seconds` of a stock clip, filled to 9:16, pushing in slowly."""
    length = probe(src).duration or seconds
    offset = min(length * 0.15, max(0.0, length - seconds - 0.1))
    zoom = (f"scale=w='trunc({W}*(1+{PUSH_IN}*t/{seconds:.3f})/2)*2':h=-2:eval=frame,"
            f"crop={W}:{H}")
    run(["-hide_banner", "-nostdin", "-loglevel", "error", "-y",
         *(["-stream_loop", "-1"] if length < seconds + offset else ["-ss", f"{offset:.3f}"]),
         "-i", str(src), "-t", f"{seconds:.3f}", "-an",
         "-vf", f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},{zoom},fps={FPS},setsar=1",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "16", "-pix_fmt", "yuv420p", str(out)])
    return out


def shots(script: Script, timings: Timings, work: Path, progress=None) -> list[Path]:
    made, used = [], set()
    for i, (beat, (a, b)) in enumerate(zip(script.beats, timings.beats, strict=True)):
        if progress:
            progress(f"Shot {i + 1} of {len(script.beats)}", 10 + 60 * i / len(script.beats))
        seconds = b - a
        if beat.visual.kind == "diagram":
            made.append(diagrams.render(beat.visual, seconds, work / f"{i:02d}_diagram.mp4"))
            continue
        parts = [seconds] if seconds <= MAX_SHOT else [seconds / 2, seconds - seconds / 2]
        queries = beat.visual.queries or [beat.visual.query]
        for k, part in enumerate(parts):
            hit = stock.choose(queries, part, used, sentence=beat.text)
            if hit is None:  # nothing fits: the phrase on the board, never unrelated footage
                card = Visual(kind="diagram", template="card", title=beat.visual.card or beat.emphasis or beat.text)
                made.append(diagrams.render(card, part, work / f"{i:02d}_{k}_card.mp4"))
                continue
            used.add(hit["id"])
            made.append(_stock_shot(stock.fetch(hit), part, work / f"{i:02d}_{k}_stock.mp4"))
    return made


def assemble(parts: list[Path], voice: Path, script: Script, timings: Timings, out: Path,
             channel: channels.Channel, config: Config) -> Path:
    rc = config.render
    words = [Word(start=w.start, end=w.end, text=w.text) for w in timings.words]
    ass = out.with_suffix(".ass")
    cap.write_ass(cap.build_ass(words, style=cap.get_style(rc.caption_style), width=W, height=H,
                                safe_area=rc.safe_area, duration=timings.duration,
                                hook_text=script.title if rc.show_hook_text else "",
                                hook_seconds=rc.hook_text_seconds if rc.show_hook_text else 0.0), ass)
    inputs = [arg for p in parts for arg in ("-i", str(p))] + ["-i", str(voice)]
    n = len(parts)
    graph = "".join(f"[{i}:v]" for i in range(n)) + f"concat=n={n}:v=1:a=0[cv]"
    video = "[cv]"
    mark = Path(channel.watermark) if channel.watermark else None
    if mark and mark.is_file():
        inputs += ["-i", str(mark)]
        graph += (f";[{n + 1}:v]scale={WATERMARK_WIDTH}:-1,format=rgba,colorchannelmixer=aa=0.85[wm];"
                  f"[cv][wm]overlay=W-w-48:170[mv]")
        video = "[mv]"
    graph += (f";{video}ass=f='{escape_filter_path(ass)}':fontsdir='{escape_filter_path(bundled_fonts_dir())}'[v]"
              f";[{n}:a]loudnorm=I={rc.loudness_lufs}:TP={rc.true_peak_dbtp}:LRA=11,"
              f"aresample={rc.audio_rate},apad[a]")
    run(["-hide_banner", "-nostdin", "-loglevel", "error", "-y", *inputs,
         "-filter_complex", graph, "-map", "[v]", "-map", "[a]", "-t", f"{timings.duration:.3f}",
         "-c:a", "aac", "-b:a", rc.audio_bitrate, "-ar", str(rc.audio_rate), "-ac", "2",
         *reencode_args(config), "-pix_fmt", "yuv420p", "-r", str(FPS), "-movflags", "+faststart", str(out)])
    return out


def _campaign(channel: channels.Channel) -> None:
    """The channel's own campaign, so its videos post and sync like any clip."""
    from ..studio.server import campaigns_dir

    path = campaigns_dir() / f"{channel.campaign}.yaml"
    if path.exists():
        return
    path.write_text(
        f"# The user's own channel ({channel.handle}): original videos made in Create (D108).\n"
        f"name: {channel.campaign}\ntitle: {channel.name}\n"
        "source_authorization: Original videos made by the channel owner for their own account.\n"
        "platform_targets: [youtube_shorts, tiktok, instagram_reels]\n"
        "duration:\n  min_seconds: 15\n  max_seconds: 60\n", encoding="utf-8")


def build(video_id: int, progress=None) -> int:
    """Make the video and file it; returns its clip id in the library."""
    from ..render.cover import pick, put_first
    from ..studio import db, library
    from ..utils.recycle import recycle

    row = store.video(video_id)
    if row is None:
        raise CreateError("no such video")
    if not row["voice"] or not row["timings"]:
        raise CreateError("drop the voiceover in first")
    script, timings = Script.model_validate(row["script"]), Timings.model_validate(row["timings"])
    channel, config = channels.load(), Config.load()
    work = folder(video_id) / "work"
    if work.exists():
        recycle(work)  # a previous build's working files, to the Recycle Bin like all Clipper's
    work.mkdir(parents=True, exist_ok=True)
    parts = shots(script, timings, work, progress)
    if progress:
        progress("Putting it together", 75)
    out = assemble(parts, Path(row["voice"]), script, timings, work / "final.mp4", channel, config)
    if progress:
        progress("Choosing the cover", 90)
    cover_at = pick(out, timings.duration, ass_text=out.with_suffix(".ass").read_text(encoding="utf-8"))
    if cover_at is not None:
        put_first(out, cover_at, hook=script.title, config=config, work_dir=work, fps=FPS)
    _campaign(channel)
    rel = f"{channel.campaign}/{video_id:03d}_{slugify(script.title, max_length=50)}.mp4"
    dest = library.clip_path(rel)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():  # an earlier build of this video: to the Recycle Bin, not overwritten
        recycle(dest)
    shutil.move(str(out), str(dest))
    caption = "\n\n".join(x for x in (script.description, " ".join(script.hashtags)) if x)
    with db.connect() as con:
        clip_id = db.upsert_clip(con, {
            "campaign": channel.campaign, "source_id": "create", "clip_id": f"create-{video_id}",
            "source_title": script.title, "title": script.title, "file": rel, "hook": script.title,
            "caption": caption, "duration_s": round(probe(dest).duration, 2), "start_s": 0.0,
            "end_s": timings.duration,
            "scores": json.dumps({"picked_by": "create", "create": video_id, "cover": cover_at,
                                  "text": script.text}),
        })
    store.update_video(video_id, status="built", clip_id=clip_id, error="")
    recycle(work)
    log.info("create: video %s built as clip %s (%s)", video_id, clip_id, rel)
    return clip_id

