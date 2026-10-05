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

import hashlib
import json
import math
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
from . import diagrams, stock, store, userclips
from .ai import CreateError
from .script import Script, Visual, spans
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


def _fill_frame(push: str, x: float) -> str:
    """ffmpeg filters that cover the 9:16 frame, then push in by `push` (an expression in t), the
    crop's left edge placed so the subject at `x` lands as near the middle as the picture allows:
    (iw*x - W/2) clamped to [0, iw - W]."""
    return (f"scale={W}:{H}:force_original_aspect_ratio=increase,"
            f"scale=w='trunc(iw*{push}/2)*2':h=-2:eval=frame,"
            f"crop={W}:{H}:x='max(0,min(iw-{W},iw*{x:.4f}-{W // 2}))':y='(ih-{H})/2',fps={FPS},setsar=1")


def _stock_shot(src: Path, seconds: float, out: Path, center: float | None = None, skip: float = 0.0) -> Path:
    """`seconds` of a stock clip filling the 9:16 frame, pushing in slowly, the crop placed on
    its subject (D111): a wide shot cut to its middle lost the speaker cone to one side and
    the skull to the other; shown whole over a blur it looked small. The window keeps the
    subject's spot across the push-in and never leaves the picture. `skip`: seconds already
    shown of this clip in the shot before, so a clip used twice in a row carries on (D132)."""
    info = probe(src)
    length = info.duration or seconds
    offset = min(length * 0.15 + skip, max(0.0, length - seconds - 0.1))
    x = min(1.0, max(0.0, _subject_x(src, offset, seconds, center)))
    vf = _fill_frame(f"(1+{PUSH_IN}*t/{seconds:.3f})", x)
    run(["-hide_banner", "-nostdin", "-loglevel", "error", "-y",
         *(["-stream_loop", "-1"] if length < seconds + offset else ["-ss", f"{offset:.3f}"]),
         "-i", str(src), "-t", f"{seconds:.3f}", "-an", "-vf", vf,
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "16", "-pix_fmt", "yuv420p", str(out)])
    return out


def _own_shot(src: Path, start: float, play: float, total: float, out: Path, slow: float = 1.0,
              loop: bool = False) -> Path:
    """`total` seconds of the user's own clip filling the 9:16 frame (D119): `play` seconds of
    it from `start`, stretched by `slow` (1 = natural speed), then its last frame held until
    `total`; or, with `loop`, repeated to `total`. Framed like stock footage, on the faces in
    that stretch of the clip; the sound is dropped (the voice is the only sound)."""
    if loop:  # the stretch cut once, then repeated
        piece = _own_shot(src, start, play, play, out.with_name(out.stem + "_piece.mp4"))
        run(["-hide_banner", "-nostdin", "-loglevel", "error", "-y", "-stream_loop", "-1", "-i", str(piece),
             "-t", f"{total:.3f}", "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "16",
             "-pix_fmt", "yuv420p", str(out)])
        return out
    x = min(1.0, max(0.0, _subject_x(src, start, play, None)))
    span = max(play * slow, 0.1)
    vf = (f"setpts={slow:.4f}*PTS," if slow != 1.0 else "") + \
        _fill_frame(f"(1+{PUSH_IN}*min(t,{span:.3f})/{span:.3f})", x)
    rest = total - span
    if rest > 0.03:
        vf += f",tpad=stop_mode=clone:stop_duration={rest:.3f}"
    run(["-hide_banner", "-nostdin", "-loglevel", "error", "-y", "-ss", f"{start:.3f}", "-i", str(src),
         "-an", "-vf", vf, "-t", f"{total:.3f}",
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


def _fallback(beat, script: Script, text: str = "") -> Visual:
    """For a sentence no footage fits: a sketch of it, else its phrase chalked on the board
    (a single word like "stranger" on an empty board opened a video once, D111)."""
    from .sketch import draw

    try:
        idea = beat.visual.idea.strip() or (f"A simple, striking sketch of what this sentence shows; the key idea: "
                                            f"{beat.visual.card or beat.emphasis}.")  # the user's own idea, if they gave one (D120)
        drawn = draw(text or beat.text, idea, script.text)
        if drawn.marks:
            return Visual(kind="diagram", template="sketch", sketch=drawn)
    except CreateError as exc:
        log.info("create: no fallback sketch (%s)", exc)
    return Visual(kind="diagram", template="card", title=beat.visual.card or beat.emphasis or beat.text)


#: What this build chose for each sentence's picture (sentence index -> {"picked": hits},
#: {"drawn": Visual} or {"restore": Visual}), written back into the script so a rebuild keeps
#: it (D125); and notes about it for the page.
chosen: dict[int, dict] = {}
picture_notes: list[str] = []

#: Where finished shots are kept between builds, by what went into them (set by build(); None:
#: not kept). A rebuild that changes one part makes that one part (D126): every shot made
#: before with the same inputs is reused, not drawn or cut again. Bump RENDER_VERSION when the
#: drawing or cutting code changes, so no shot made by older code is reused.
shot_cache: Path | None = None
RENDER_VERSION = 1
used_shots: set[Path] = set()
_RUNTIME = {"picked", "avoid", "redo", "previous", "manual", "hold", "clip", "clip_start", "fill"}


class _TooDark(Exception):
    """A stock shot that came out all but black, even cropped in the middle."""


def _kept(kind: str, key: object, out: Path, make) -> Path:
    """The shot for these inputs: made before and kept, or made now by `make(out)` and kept."""
    if shot_cache is None:
        return make(out)
    digest = hashlib.sha1(json.dumps([RENDER_VERSION, kind, key], sort_keys=True, default=str).encode()).hexdigest()[:24]
    kept = shot_cache / f"{kind}-{digest}.mp4"
    used_shots.add(kept)
    if kept.is_file() and kept.stat().st_size > 0:
        return kept
    made = make(out)
    shot_cache.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(made, kept)
    return kept


def _diagram(visual: Visual, seconds: float, out: Path, said: list) -> Path:
    key = [visual.model_dump(exclude=_RUNTIME), round(seconds, 3), [(round(t, 3), w) for t, w in said]]
    return _kept("diagram", key, out, lambda o: diagrams.render(visual, seconds, o, words=said))


def _drawn(i: int, beat, script: Script, tag: str) -> Visual:
    """A drawing made now for a sentence (its own idea, else what it says), remembered when it is
    a real drawing; a chalk card isn't kept, so the next build tries footage again."""
    # A drawing held over the next sentences is drawn for all of them (D124).
    group = next((g for g in spans(script) if g[0] == i), [i])
    made = _fallback(beat, script, " ".join(script.beats[k].text for k in group))
    if not tag and made.template == "sketch" and made.sketch and made.sketch.marks:
        chosen[i] = {"drawn": made}
    return made


#: The judge's score footage needs when the user asked for footage on that sentence themselves:
#: they want footage there, so a looser match beats a drawing they didn't ask for (D126).
ASKED_GOOD_ENOUGH = 6   # 4 let poor footage through (D129)


def _planned(i: int, beat, visual: Visual, seconds: float, said: list, script: Script, work: Path, used: set,
             tag: str = "") -> list[Path]:
    """The shot(s) of the picture planned for a sentence: a diagram, or stock footage (a clip per
    MAX_SHOT seconds, each a different one), or a drawing when no footage fits. `tag` keeps a
    filler's files apart from the sentence's own clip. Footage a build picked before is used
    again (D125); "new footage" clears it and turns those clips down. When the user asked for
    footage and none fits, the sentence keeps what it had, never a new drawing (D126)."""
    if visual.kind == "diagram" and visual.template == "sketch" and not (visual.sketch and visual.sketch.marks):
        visual = _drawn(i, beat, script, tag)  # asked for a new drawing, or its drawing failed
    if visual.kind == "diagram":
        return [_diagram(visual, seconds, work / f"{i:02d}{tag}_diagram.mp4", said)]
    asked = visual.redo and not tag

    def none_fit() -> list[Path]:
        if asked and visual.previous:
            # Kept what it had, said on the part itself, and the clip turned down stays turned down (D129).
            before = Visual.model_validate({**visual.previous, "redo": False, "previous": None,
                                            "avoid": list(dict.fromkeys([*visual.previous.get("avoid", []), *visual.avoid]))})
            picture_notes.append(f"Sentence {i + 1}: no new footage fit, so it kept what it had.")
            shot = _planned(i, beat, before, seconds, said, script, work, used, tag)
            rec = chosen.get(i, {})
            chosen[i] = {"restore": before, "notice": "No new footage fit well enough, so this part kept what it had. "
                         "Use New footage to pick one yourself, or say what you'd like to see.",
                         **({"picked": rec["picked"]} if "picked" in rec else {})}
            return shot
        return [_diagram(_drawn(i, beat, script, tag), seconds, work / f"{i:02d}{tag}_sketch.mp4", said)]

    count = max(1, math.ceil(seconds / MAX_SHOT - 1e-6))
    parts = [seconds / count] * count
    queries = [q for q in (visual.queries or [visual.query]) if q.strip()]
    turned_down = set(visual.avoid)
    # The part's own footage is kept unless another part already uses it; `avoid` only steers new
    # choices (a part that kept its clip when nothing new fit has that clip in both, D129).
    kept = [h for h in visual.picked if h.get("id") not in used and h.get("url")] if not tag else []
    if kept and len(kept) == len(visual.picked):  # what was picked before, cut as before
        parts = [seconds / len(kept)] * len(kept)
        hits = kept
    else:
        # Searches written for this sentence in the script's context come first; the plan's own
        # were often loose or off the point (D129).
        group = next((g for g in spans(script) if g[0] == i), [i])
        planned = stock.plan_searches(" ".join(script.beats[k].text for k in group), script.text,
                                      wish=visual.wish, tried=queries if asked else None)
        queries = list(dict.fromkeys([*planned, *queries]))[:6] or [beat.text]
        hits = []
        for part in parts:  # one at a time, so each part of a long sentence gets a different clip
            hit = stock.choose(queries, part, turned_down | used | {h["id"] for h in hits if h},
                               sentence=beat.text, context=script.text,
                               good_enough=ASKED_GOOD_ENOUGH if asked else stock.GOOD_ENOUGH)
            hits.append(hit)
    for hit in hits:
        if hit:
            used.add(hit["id"])
    if not hits[0]:  # nothing fits: never unrelated footage
        return none_fit()
    hits = [h or hits[0] for h in hits]  # a part without its own clip keeps the first
    clips = []
    for k, (part, hit) in enumerate(zip(parts, hits, strict=True)):
        holder = {"hit": hit}
        # A part with no clip of its own repeats the one before: from where it stopped, not from
        # the same frame, which played the same seconds twice in a row (D132).
        skip = sum(p for p, h in zip(parts[:k], hits[:k], strict=True) if h.get("id") == hit.get("id"))

        def make(out: Path, part=part, holder=holder, skip=skip) -> Path:
            h = holder["hit"]
            clip = _stock_shot(stock.fetch(h), part, out, h.get("center"), skip)
            if _too_dark(clip):  # the crop found the dark part: the middle, else no footage
                holder["hit"] = {**h, "center": 0.5}
                clip = _stock_shot(stock.fetch(h), part, out, 0.5, skip)
            if _too_dark(clip):
                raise _TooDark
            return clip

        try:
            key = [hit.get("id"), hit.get("center"), round(part, 3)] + ([round(skip, 3)] if skip else [])
            clips.append(_kept("stock", key, work / f"{i:02d}{tag}_{k}_stock.mp4", make))
        except _TooDark:
            return none_fit()
        hits[k] = holder["hit"]
    if not tag:
        chosen[i] = {"picked": [{k: h.get(k) for k in ("id", "url", "width", "height", "duration", "tags", "thumb", "center")}
                                for h in hits], "queries": queries}
    return clips


def remember(script: Script) -> Script:
    """The script with what this build chose written into it (D125), and its waiting changes
    marked done: the next build makes the same pictures unless the user asks for new ones."""
    beats = []
    for i, b in enumerate(script.beats):
        v = b.visual.model_copy(update={"redo": False, "previous": None, "notice": "", "wish": ""})
        got = chosen.get(i)
        if got and "restore" in got:  # asked for footage, none fit: back to what it had (D126)
            v = got["restore"].model_copy(update={"redo": False, "previous": None, "notice": got.get("notice", ""),
                                                  **({"picked": got["picked"]} if "picked" in got else {})})
        elif got and "picked" in got:
            v = v.model_copy(update={"picked": got["picked"], **({"queries": got["queries"][:3], "query": got["queries"][0]}
                                                                 if got.get("queries") else {})})
        elif got and "drawn" in got:
            d = got["drawn"]
            v = v.model_copy(update={"kind": "diagram", "template": "sketch", "sketch": d.sketch,
                                     "idea": v.idea or d.idea, "picked": []})
        beats.append(b.model_copy(update={"visual": v}))
    return script.model_copy(update={"beats": beats})


def _own(i: int, visual: Visual, seconds: float, own: dict, cursor: dict, default_fill: str, work: Path,
         notes: list[str]) -> tuple[list[Path], float] | None:
    """A sentence's own clip cut to the sentence (D119): (its shot, seconds of the sentence
    still to fill with the planned picture), or None when the clip can't be used (gone,
    unreadable), in which case the planned picture is used for the whole sentence."""
    got = own.get(visual.clip)
    if not got:
        notes.append(f"Sentence {i + 1}: its clip is gone; the planned picture is used.")
        return None
    src, length, name = got
    explicit = visual.clip_start is not None
    start = visual.clip_start if explicit else cursor.get(visual.clip, 0.0)
    last = max(0.0, length - userclips.MIN_SECONDS)
    if start > last:
        if not explicit:  # the clip ran out over the sentences before this one
            notes.append(f"Sentence {i + 1}: {name} had run out; the planned picture is used.")
            return None
        notes.append(f"Sentence {i + 1}: {name} is {length:.1f}s long, so it starts at {last:.1f}s instead of {start:.1f}s.")
        start = last
    avail = length - start
    fill = visual.fill if visual.fill != "auto" else default_fill
    mode, slow, rest = userclips.fill_plan(avail, seconds, fill)
    play = seconds if mode == "cut" else avail
    out = work / f"{i:02d}_own.mp4"
    total = seconds if mode != "planned" else play
    try:
        stat = src.stat()
        shot = _kept("own", [str(src), stat.st_size, stat.st_mtime, round(start, 3), round(play, 3), round(total, 3),
                             round(slow, 4), mode == "loop"], out,
                     lambda o: _own_shot(src, start, play, total, o, slow=slow, loop=mode == "loop"))
    except Exception as exc:  # an odd codec or a damaged file: the planned picture instead
        log.warning("create: clip %s couldn't be cut (%s)", name, exc)
        notes.append(f"Sentence {i + 1}: {name} couldn't be read ({str(exc)[:80]}); the planned picture is used.")
        return None
    cursor[visual.clip] = start + play
    if mode in ("hold", "slow"):
        notes.append(f"Sentence {i + 1}: {name} had {avail:.1f}s for {seconds:.1f}s; "
                     + ("held on its last picture." if mode == "hold" else
                        f"slowed to {slow:.1f}x" + (" and held." if rest > 0.05 else ".")))
    elif mode == "loop":
        notes.append(f"Sentence {i + 1}: {name} had {avail:.1f}s for {seconds:.1f}s; repeated.")
    elif mode == "planned":
        notes.append(f"Sentence {i + 1}: {name} had {avail:.1f}s for {seconds:.1f}s; the rest is the planned picture.")
    return [shot], (rest if mode == "planned" else 0.0)


def shots(script: Script, timings: Timings, work: Path, progress=None, own: dict | None = None,
          notes: list[str] | None = None, default_fill: str = "auto") -> list[Path]:
    """One shot (or two) per picture: a sentence, or a drawing and the sentences that hold it (D124). `own` maps clip ids to (file, seconds, name) for the
    user's clips; a sentence with one gets it, cut or filled to the sentence, followed by its
    planned picture for any time the clip couldn't cover (D119)."""
    made, used, cursor = [], set(), {}
    own = own or {}
    notes = notes if notes is not None else []
    groups = []
    for group in spans(script):
        root = script.beats[group[0]].visual
        # A held drawing (or the footage asked for in its place, D126) covers all its sentences;
        # the user's own clip plays its own sentence alone.
        groups += [group] if not root.clip else [[i] for i in group]
    for n, group in enumerate(groups):
        i, beat = group[0], script.beats[group[0]]
        if progress:
            progress(f"Shot {n + 1} of {len(groups)}", 10 + 60 * n / len(groups))
        a, b = timings.beats[group[0]][0], timings.beats[group[-1]][1]
        seconds = b - a
        # Every word said while the picture is up, so a part can arrive on any of them (D124).
        said = [(w.start - a, w.text) for w in timings.words if a - 0.05 <= w.start < b]
        if beat.visual.clip:
            got = _own(i, beat.visual, seconds, own, cursor, default_fill, work, notes)
            if got:
                made += got[0]
                rest = got[1]
                if rest > 0.05:  # the clip ran out: the planned picture takes over for the rest
                    at = seconds - rest
                    made += _planned(i, beat, beat.visual, rest, [(t - at, w) for t, w in said if t >= at - 0.05],
                                     script, work, used, tag="b")
                continue
        made += _planned(i, beat, beat.visual, seconds, said, script, work, used)
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
    global shot_cache
    try:
        return _build(video_id, progress)
    finally:
        shot_cache = None  # also when it failed: the cache belongs to one build


def _build(video_id: int, progress) -> int:
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
    notes: list[str] = []
    stock.unjudged.clear()
    chosen.clear()
    picture_notes.clear()
    used_shots.clear()
    global shot_cache
    shot_cache = folder(video_id) / "shots"
    own = userclips.files(video_id)
    if own or userclips.load(video_id)["clips"]:
        # The sentences' real lengths now known: clips placed for the user if they asked (D119).
        script, said = userclips.prepare(video_id, script, [b - a for a, b in timings.beats])
        if said:
            notes.append(said)
        store.update_video(video_id, script={**script.model_dump(), "take": row["script"].get("take", 1)})
    work = folder(video_id) / "work"
    if work.exists():
        shutil.rmtree(work, ignore_errors=True)  # scratch, deleted outright: it's rebuilt each time (D133)
    work.mkdir(parents=True, exist_ok=True)
    parts = shots(script, timings, work, progress, own=own, notes=notes,
                  default_fill=userclips.load(video_id)["fill"])
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
    notes += picture_notes
    if stock.unjudged:
        n = len(stock.unjudged)
        notes.append(f"Footage for {n} sentence{'s' if n != 1 else ''} was picked by its search words only, as Claude "
                     "couldn't look at it: check those shots, or press Build again once Claude is back.")
    placed = {b.visual.clip for b in script.beats if b.visual.clip}
    idle = [name for cid, (_, _, name) in own.items() if cid not in placed]
    if idle:
        notes.append("Not used: " + ", ".join(idle) + ".")
    script = remember(script)
    store.update_video(video_id, status="built", clip_id=clip_id, error="",
                       script={**script.model_dump(), "take": row["script"].get("take", 1)},
                       check_notes=userclips.with_notes(store.video(video_id)["check_notes"], notes))
    shutil.rmtree(work, ignore_errors=True)
    for old in (shot_cache.glob("*.mp4") if shot_cache.is_dir() else []):
        if old not in used_shots:  # shots of pictures no longer in the video
            old.unlink(missing_ok=True)
    log.info("create: video %s built as clip %s (%s)", video_id, clip_id, rel)
    return clip_id

