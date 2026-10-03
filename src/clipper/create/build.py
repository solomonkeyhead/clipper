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


def _subject_x(src: Path, start: float, seconds: float, hint: float | None) -> float:
    """Where across the clip (0 left, 1 right) its subject is: the faces, when people are
    in it (YuNet, sampled across the shot, the biggest face weighted most), else where the
    footage judge saw the subject in the thumbnail, else the middle."""
    try:
        import cv2

        from ..render.faces import DETECT_WIDTH, FaceDetectionUnavailable, _detect, _load_detector

        cap = cv2.VideoCapture(str(src))
        try:
            fw, fh = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            detector = _load_detector(fw, fh)
            scale = min(1.0, DETECT_WIDTH / fw) if fw else 1.0
            dw, dh = max(64, round(fw * scale)), max(64, round(fh * scale))
            detector.setInputSize((dw, dh))
            xs, weights = [], []
            for k in range(5):
                cap.set(cv2.CAP_PROP_POS_MSEC, (start + seconds * (k + 0.5) / 5) * 1000)
                ok, frame = cap.read()
                if not ok:
                    continue
                small = cv2.resize(frame, (dw, dh), interpolation=cv2.INTER_AREA) if scale < 1 else frame
                faces = _detect(detector, small, min_confidence=0.75, frame_height=dh, t=0.0, upscale=1 / scale)
                if faces:
                    face = max(faces, key=lambda f: f.width * f.height)
                    xs.append(face.x / fw)
                    weights.append(face.width * face.height)
        finally:
            cap.release()
        if len(xs) >= 2:
            return sum(x * w for x, w in zip(xs, weights, strict=True)) / sum(weights)
    except (FaceDetectionUnavailable, ImportError, ZeroDivisionError) as exc:
        log.debug("create: no face framing (%s)", exc)
    return hint if hint is not None else 0.5


def _stock_shot(src: Path, seconds: float, out: Path, center: float | None = None) -> Path:
    """`seconds` of a stock clip filling the 9:16 frame, pushing in slowly, the crop placed on
    its subject (D111): a wide shot cut to its middle lost the speaker cone to one side and
    the skull to the other; shown whole over a blur it looked small. The window keeps the
    subject's spot across the push-in and never leaves the picture."""
    info = probe(src)
    length = info.duration or seconds
    offset = min(length * 0.15, max(0.0, length - seconds - 0.1))
    x = min(1.0, max(0.0, _subject_x(src, offset, seconds, center)))
    push = f"(1+{PUSH_IN}*t/{seconds:.3f})"
    # Cover the frame, then push in; the crop's left edge sits so the subject lands as near
    # the middle as the picture allows: (iw*x - W/2) clamped to [0, iw - W].
    vf = (f"scale={W}:{H}:force_original_aspect_ratio=increase,"
          f"scale=w='trunc(iw*{push}/2)*2':h=-2:eval=frame,"
          f"crop={W}:{H}:x='max(0,min(iw-{W},iw*{x:.4f}-{W // 2}))':y='(ih-{H})/2',fps={FPS},setsar=1")
    run(["-hide_banner", "-nostdin", "-loglevel", "error", "-y",
         *(["-stream_loop", "-1"] if length < seconds + offset else ["-ss", f"{offset:.3f}"]),
         "-i", str(src), "-t", f"{seconds:.3f}", "-an", "-vf", vf,
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "16", "-pix_fmt", "yuv420p", str(out)])
    return out


def _too_dark(shot: Path) -> bool:
    """Whether a finished shot is all but black: a "velvet curtain" clip cropped on its dark
    middle gave four seconds of black screen (D112). Judged on a frame a third of the way in."""
    import io
    import subprocess

    from PIL import Image, ImageStat

    from ..render.ffmpeg import ffmpeg_path

    seconds = probe(shot).duration or 1.0
    try:
        png = subprocess.run([str(ffmpeg_path()), "-loglevel", "error", "-ss", f"{seconds / 3:.2f}", "-i", str(shot),
                              "-frames:v", "1", "-vf", "scale=270:480", "-f", "image2pipe", "-vcodec", "png", "-"],
                             capture_output=True, timeout=60, check=True).stdout
        stat = ImageStat.Stat(Image.open(io.BytesIO(png)).convert("L"))
    except (subprocess.SubprocessError, OSError):
        return False
    return stat.mean[0] < 28 and stat.stddev[0] < 22


def _fallback(beat, script: Script) -> Visual:
    """For a sentence no footage fits: a sketch of it, else its phrase chalked on the board
    (a single word like "stranger" on an empty board opened a video once, D111)."""
    from .sketch import draw

    try:
        drawn = draw(beat.text, f"A simple, striking sketch of what this sentence shows; the key idea: "
                                f"{beat.visual.card or beat.emphasis}.", script.text)
        if drawn.marks:
            return Visual(kind="diagram", template="sketch", sketch=drawn)
    except CreateError as exc:
        log.info("create: no fallback sketch (%s)", exc)
    return Visual(kind="diagram", template="card", title=beat.visual.card or beat.emphasis or beat.text)


def shots(script: Script, timings: Timings, work: Path, progress=None) -> list[Path]:
    made, used = [], set()
    for i, (beat, (a, b)) in enumerate(zip(script.beats, timings.beats, strict=True)):
        if progress:
            progress(f"Shot {i + 1} of {len(script.beats)}", 10 + 60 * i / len(script.beats))
        seconds = b - a
        said = [(w.start - a, w.text) for w in timings.words if a - 0.05 <= w.start < b]
        visual = beat.visual
        if visual.kind == "diagram" and visual.template == "sketch" and not (visual.sketch and visual.sketch.marks):
            visual = _fallback(beat, script)  # planned before sketches existed, or its drawing failed
        if visual.kind == "diagram":
            made.append(diagrams.render(visual, seconds, work / f"{i:02d}_diagram.mp4", words=said))
            continue
        parts = [seconds] if seconds <= MAX_SHOT else [seconds / 2, seconds - seconds / 2]
        queries = visual.queries or [visual.query]
        hits = [stock.choose(queries, part, used, sentence=beat.text) for part in parts]
        for hit in hits:
            if hit:
                used.add(hit["id"])
        if not hits[0]:  # nothing fits: drawn instead, the whole sentence, never unrelated footage
            made.append(diagrams.render(_fallback(beat, script), seconds, work / f"{i:02d}_sketch.mp4", words=said))
            continue
        hits = [h or hits[0] for h in hits]  # half a sentence without its own clip keeps the first
        clips = []
        for k, (part, hit) in enumerate(zip(parts, hits, strict=True)):
            out = work / f"{i:02d}_{k}_stock.mp4"
            clip = _stock_shot(stock.fetch(hit), part, out, hit.get("center"))
            if _too_dark(clip):  # the crop found the dark part: the middle, else no footage
                clip = _stock_shot(stock.fetch(hit), part, out, 0.5)
            if _too_dark(clip):
                clips = []
                break
            clips.append(clip)
        if not clips:
            made.append(diagrams.render(_fallback(beat, script), seconds, work / f"{i:02d}_sketch.mp4", words=said))
            continue
        made += clips
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

