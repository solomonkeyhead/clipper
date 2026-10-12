"""The finished Short: shots cut to the voice, the Professor, captions, watermark, cover; filed
into the library under the channel's own campaign.

One shot per beat, so the picture changes as each sentence starts: a diagram
(create/diagrams.py), or stock footage (create/stock.py) filling the picture panel
with a camera move -- split into two clips when a sentence runs past MAX_SHOT, as
a picture held longer than that loses people. Then the edit (create/compose.py,
D162) draws every frame: the shots in the panel, the Professor on the board below
it, alive, the captions popping in a page at a time, the hook, the watermark, with
the voice evened to the platform loudness; then the cover frame (render/cover.py)
like every clip.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import math
import os
import shutil
import time
from collections import Counter
from concurrent.futures import Future, ProcessPoolExecutor, ThreadPoolExecutor, as_completed, wait
from pathlib import Path
from typing import NamedTuple

import numpy as np

from ..config import Config
from ..ingest.probe import probe
from ..render.ffmpeg import run
from ..render.teaser import reencode_args
from ..utils.cache import slugify
from ..utils.logging import get_logger
from . import channel as channels
from . import compose, diagrams, stock, store, userclips
from .ai import CreateError
from .script import FIRST_DIAGRAM_WORDS, Script, Visual, _query_from, loops_back, spans
from .sketch import draw_all
from .voice import Timings, folder, nudged

log = get_logger(__name__)

W, H, FPS = 1080, 1920, 30
PANEL = (compose.W, compose.PANEL_H)   # where the pictures go (D162)
MAX_SHOT = 3.5   # 4.5 held one clip too long; spares from the same judgement fill the extra parts (D196)
WATERMARK_WIDTH = 150
#: D156, from the second research report: shots of at most HOOK_SHOT seconds while the first ~5 s are spoken
#: (HOOK_WORDS words), the camera move taken in turn so no two shots in a row move alike (the same push-in on
#: every shot was the most visible "template" sign), and a quick punch-in on the highlighted word of at most
#: PUNCHES sentences, PUNCH_GAP seconds apart.
HOOK_SHOT = 2.5
HOOK_WORDS = 11
#: The pull-out crashed ffmpeg when it was an ffmpeg filter (D160); the camera is Python's now (D162), so it's back.
MOTIONS = ("push", "drift", "still", "pull")
PUNCHES, PUNCH_GAP = 3, 6.0
#: One look for footage from every library (D156): a little less colour, a little warmer, a faint grain.
LOOK = "eq=saturation=0.85:contrast=1.05,colorbalance=rm=0.03:bm=-0.03,noise=alls=5:allf=t"
#: The channel's character (D156), on screen the whole video (D159), standing on the frame's bottom edge (D162).
CHARACTER_HEIGHT = compose.PRESENTER_H
CHARACTER_START = 2.5
#: The judge's score footage needs on the opening words (D159): a looser real clip beats a drawing there, as the
#: viewer's own moment, moving, holds them first (D155); a drawing only when even this finds nothing.
OPENING_GOOD_ENOUGH = 5
#: A loop's end (D197): the silence from the last word round to the first, as between two sentences, and the last
#: LOOP_TAIL seconds played on the opening clip, leading into its first frame.
LOOP_GAP, LOOP_TAIL = 0.25, 1.5


def loops(script: Script, timings: Timings) -> bool:
    """Whether the video's end runs into its start (D197): the user's switch, else when the script ends on the loop."""
    return loops_back(script) and len(timings.beats) == len(script.beats) >= 4


def loop_timing(timings: Timings) -> Timings:
    """The video cut LOOP_GAP after the last word, counting the lead before the first (D197): 0.6 s of silence, then
    the lead, was a pause the loop's viewer heard on every pass."""
    if not timings.words:
        return timings
    end = round(timings.words[-1].end + max(0.08, LOOP_GAP - timings.words[0].start), 3)
    if end >= timings.duration:
        return timings
    beats = [*timings.beats[:-1], (timings.beats[-1][0], end)] if timings.beats else []
    return timings.model_copy(update={"duration": end, "beats": beats})


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


def _frames(at: float | None, seconds: float) -> int:
    """A shot's length in frames: on the video's one clock when its start is known (D162)."""
    return compose.frames_between(at, at + seconds) if at is not None else max(1, round(seconds * FPS))


#: What each shot made in this build is (its frames and kind), for the edit (D162).
shot_info: dict[Path, tuple[int, str]] = {}


def _framing(src: Path, seconds: float, center: float | None, skip: float, frame: float | None):
    """Where in a clip a shot starts and where across it the camera looks (D111, D132, D180); and the clip's probe."""
    info = probe(src)
    length = info.duration or seconds
    offset = min(length * 0.15 + skip, max(0.0, length - seconds - 0.1))
    x = min(1.0, max(0.0, frame if frame is not None else _subject_x(src, offset, seconds, center)))
    return offset, x, info


def _stock_shot(src: Path, seconds: float, out: Path, center: float | None = None, skip: float = 0.0,
                motion: str = "push", punch: float | None = None, frames: int | None = None,
                frame: float | None = None) -> Path:
    """`seconds` (or `frames`) of a stock clip filling the picture panel, the camera moving on it (D162), the crop
    placed on its subject (D111): a wide shot cut to its middle lost the speaker cone to one side and the skull to
    the other. The camera keeps the subject's spot and never leaves the picture. `skip`: seconds already shown of
    this clip in the shot before, so a clip used twice in a row carries on (D132). `frame`: the user's own crop
    place, used as it is (D180)."""
    n = frames or max(1, round(seconds * FPS))
    if src.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp"):   # a photo: the camera moves over it (D162)
        from PIL import Image

        x = min(1.0, max(0.0, frame if frame is not None else 0.5 if center is None else center))
        with Image.open(src) as img:
            compose.photo_panel(img, out, n, compose.Move(kind=motion if motion != "still" else "push", u=x, punch=punch),
                                look=LOOK)
        return out
    seconds = n / FPS
    offset, x, info = _framing(src, seconds, center, skip, frame)
    compose.video_panel(src, out, n, compose.Move(kind=motion, u=x, punch=punch), start=offset,
                        loop=(info.duration or seconds) < seconds + offset, look=LOOK,
                        source_size=(info.width or W, info.height or H))
    return out


def _own_shot(src: Path, start: float, play: float, total: float, out: Path, slow: float = 1.0,
              loop: bool = False, frames: int | None = None) -> Path:
    """`total` seconds of the user's own clip filling the panel (D119): `play` seconds of it from `start`,
    stretched by `slow` (1 = natural speed), then its last frame held until `total`; or, with `loop`, repeated
    to `total`. Framed like stock footage, on the faces in that stretch of the clip; the sound is dropped (the
    voice is the only sound)."""
    n = frames or max(1, round(total * FPS))
    if loop:  # the stretch cut once, then repeated
        piece = _own_shot(src, start, play, play, out.with_name(out.stem + "_piece.mp4"))
        return compose.repeat_panel(piece, out, n)
    x = min(1.0, max(0.0, _subject_x(src, start, play, None)))
    info = probe(src)
    compose.video_panel(src, out, n, compose.Move(kind="push", u=x), start=start, slow=slow,
                        play=play if play * slow < n / FPS - 0.03 else None,
                        source_size=(info.width or W, info.height or H))
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


def _fallback(beat, script: Script, text: str = "", keep: bool = False) -> Visual:
    """For a sentence no footage fits: a sketch of it, else its phrase chalked on the board
    (a single word like "stranger" on an empty board opened a video once, D111)."""
    from .sketch import draw

    try:
        idea = beat.visual.idea.strip() or (f"A simple, striking sketch of what this sentence shows; the key idea: "
                                            f"{beat.visual.card or beat.emphasis}.")  # the user's own idea, if they gave one (D120)
        drawn = draw(text or beat.text, idea, script.text, keep=keep)
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
#: This build's camera moves taken so far, the punch-in times by sentence, and when each picture starts and what
#: kind it is (for the chalk taps), all set by shots() (D156).
_moves = [0]
punches: dict[int, list[float]] = {}
picture_times: list[tuple[float, str]] = []
#: The video's zooms switch, for the drawings' camera (D197); the opening's first shot (its clip, camera move and
#: frames), for a loop's lead-in; and where that lead-in starts, in seconds.
_zooms = [True]
#: When each drawing's parts arrive, seconds into its shot, for their chalk sounds (D197).
chalk_at: dict[Path, list[float]] = {}
opening: list[dict] = []
loop_cut: list[float | None] = [None]


def _shows(word: str, hit: dict) -> bool:
    """Whether a clip's own words name the highlighted word (D197): a punch-in on "outsourced" or "thirty-seven"
    zoomed into a ceiling fan and a fireplace for no reason the viewer could see."""
    tags = stock._words(hit.get("tags", ""))
    return any(stock._same(w, t) for w in stock._words(word) for t in tags)


def _next_motion() -> str:
    _moves[0] += 1
    return MOTIONS[(_moves[0] - 1) % len(MOTIONS)]


def choose_punches(script: Script, timings: Timings) -> dict[int, list[float]]:
    """When to punch in (D156): on the highlighted word of footage sentences, after the hook and before the last
    sentence (the punchline holds still), at most PUNCHES of them, PUNCH_GAP seconds apart."""
    out: dict[int, list[float]] = {}
    words, k, last = timings.words, 0, -PUNCH_GAP
    for i, beat in enumerate(script.beats):
        n = len(beat.text.split())
        mine, k = words[k:k + n], k + n
        if (i == 0 or i == len(script.beats) - 1 or not beat.emphasis or beat.visual.kind != "stock"
                or beat.visual.hold or len(out) >= PUNCHES):
            continue
        key = beat.emphasis.strip(".,!?;:'\"").lower()
        at = next((w.start for w in mine if w.text.strip(".,!?;:'\"").lower() == key), None)
        if at is not None and at - last >= PUNCH_GAP:
            out[i], last = [at], at
    return out


RENDER_VERSION = 5   # 2: camera moves, punch-ins and one footage look (D156); 3: the picture panel (D162);
#: 4: drawings built left to right, coloured by temperature, a graph that moves the way its sentence says (D190);
#: 5: a gentler punch-in, the drawings' camera after the part is drawn and keeping the title in view (D197)
used_shots: set[Path] = set()
#: What a drawing's shot doesn't depend on: set by the app, or used only when the shots are put together (a part's
#: way in, D171). A change to one of these never draws the drawing again.
_RUNTIME = {"picked", "avoid", "redo", "previous", "manual", "hold", "clip", "clip_start", "fill", "notice", "wish",
            "camera", "restyle", "transition", "frame"}
#: The sentence whose picture each of this build's shots shows (the picture's first sentence), set by shots() (D171).
part_owner: list[int] = []


class _TooDark(Exception):
    """A stock shot that came out all but black, even cropped in the middle."""


def _cache_path(kind: str, key: object) -> Path:
    """Where the shot for these inputs is kept, marked as used by this build."""
    digest = hashlib.sha1(json.dumps([RENDER_VERSION, kind, key], sort_keys=True, default=str).encode()).hexdigest()[:24]
    kept = shot_cache / f"{kind}-{digest}.mp4"
    used_shots.add(kept)
    return kept


def _kept(kind: str, key: object, out: Path, make) -> Path:
    """The shot for these inputs: made before and kept, or made now by `make(out)` and kept."""
    if shot_cache is None:
        return make(out)
    kept = _cache_path(kind, key)
    if kept.is_file() and kept.stat().st_size > 0:
        return kept
    try:
        made = make(out)
    except (RuntimeError, OSError) as exc:   # ffmpeg crashed once (D188): a rebuild of the same shot worked
        log.warning("create: %s shot failed (%s); making it again", kind, str(exc).splitlines()[0][:160] if str(exc) else exc)
        made = make(out)
    shot_cache.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(made, kept)
    return kept


#: Drawings drawn in other processes while this one makes the footage shots (D179): every frame is drawn in Pillow on
#: one core, and drawings took 58 of an 85 s build. One worker (a small computer) draws them here, in turn.
DRAW_WORKERS = max(1, min(4, (os.cpu_count() or 2) // 4))
#: A drawing longer than this many frames is drawn in pieces side by side, joined after: a held drawing of 26 s was the
#: one the others waited for.
PIECE = 120
_pool: ProcessPoolExecutor | None = None
#: Each drawing under way: its pieces' futures and files (one piece: the shot itself).
_drawing: dict[Path, tuple[list[Future], list[Path]]] = {}
#: The channel's board, for the workers (diagrams.use_palette).
_palette = ""


def _draw_shot(visual: dict, n: int, said: list, out: str, palette: str, start: int = 0, stop: int | None = None,
               focus: bool = True) -> str:
    """A drawing's shot, or frames `start` to `stop` of it, in a worker process (D179): written beside where it goes,
    then moved in."""
    diagrams.use_palette(palette)
    part = Path(out).with_suffix(".part.mp4")
    diagrams.render(Visual.model_validate(visual), n / FPS, part, words=said, frames=n, start=start, stop=stop,
                    focus=focus)
    part.replace(out)
    return out


def _finish(made: Path) -> None:
    """A drawing drawn in other processes, waited for and, drawn in pieces, joined (no second encode)."""
    futures, pieces = _drawing.pop(made)
    for f in futures:
        f.result()
    if len(pieces) > 1:
        listing = made.with_suffix(".txt")
        listing.write_text("".join(f"file '{p.as_posix()}'\n" for p in pieces), encoding="utf-8")
        joined = made.with_suffix(".part.mp4")
        run(["-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(listing),
             "-c", "copy", str(joined)])
        joined.replace(made)
        for p in [*pieces, listing]:   # scratch, deleted outright (D133)
            p.unlink(missing_ok=True)


def _drawings_done(progress=None) -> None:
    """Wait for the drawings still being drawn. One that failed fails the build, as it did drawn here; a cancel
    is still heard (progress checks for it)."""
    said = None
    while _drawing:
        ready = [m for m, (futures, _) in _drawing.items() if all(f.done() for f in futures)]
        for made in ready:
            _finish(made)
        if not ready:
            wait([f for futures, _ in _drawing.values() for f in futures], timeout=1, return_when="FIRST_COMPLETED")
        if progress and _drawing and len(_drawing) != said:
            said = len(_drawing)
            progress(f"Drawing: {said} left", 72)


def _diagram(visual: Visual, seconds: float, out: Path, said: list, frames: int | None = None) -> Path:
    global _pool
    n = frames or max(1, round(seconds * FPS))
    key = [visual.model_dump(exclude=_RUNTIME), n, [(round(t, 3), w) for t, w in said], diagrams.BOARD]
    focus = _zooms[0]
    if not focus:   # zooms off: the camera stays on the whole drawing (D197)
        key.append("still")
    if shot_cache is not None and DRAW_WORKERS > 1:
        made = _cache_path("diagram", key)
        if not (made.is_file() and made.stat().st_size > 0) and made not in _drawing:
            if _pool is None:
                shot_cache.mkdir(parents=True, exist_ok=True)
                import multiprocessing

                # Started fresh, never forked from a server with threads running (Linux's default).
                _pool = ProcessPoolExecutor(DRAW_WORKERS, mp_context=multiprocessing.get_context("spawn"))
            spans = [(a, min(n, a + PIECE)) for a in range(0, n, PIECE)]
            if len(spans) > 1 and n - spans[-1][0] < PIECE // 3:   # no stub of a piece at the end
                spans[-2:] = [(spans[-2][0], n)]
            pieces = [made] if len(spans) == 1 else [made.with_name(f"{made.stem}.{k}.mp4") for k in range(len(spans))]
            dumped = visual.model_dump()
            _drawing[made] = ([_pool.submit(_draw_shot, dumped, n, said, str(p), _palette, a, b, focus)
                               for p, (a, b) in zip(pieces, spans, strict=True)], pieces)
    else:
        made = _kept("diagram", key, out, lambda o: diagrams.render(visual, n / FPS, o, words=said, frames=n,
                                                                   focus=focus))
    shot_info[made] = (n, "drawing")
    d = n / FPS   # the parts on their words (diagrams.stage); the first comes on the schedule, at the start
    chalk_at[made] = [0.1] + [min(c, max(0.6 * d, d - 1.2)) for c in diagrams.cues(visual, said)[1:] if c is not None]
    return made


def _drawn(i: int, beat, script: Script, tag: str) -> Visual:
    """A drawing made now for a sentence (its own idea, else what it says), remembered when it is
    a real drawing; a chalk card isn't kept, so the next build tries footage again."""
    # A drawing held over the next sentences is drawn for all of them (D124).
    group = next((g for g in spans(script) if g[0] == i), [i])
    # The opening wants footage (D159): a drawing standing in for it isn't kept, so the next build looks again (D161).
    # Neither is one after the user's own clip: both are remembered by their question instead, so a rebuild
    # where footage still doesn't fit draws the same one for free (D182).
    stand_in = beat.visual.kind == "stock" and _opening(i, script)
    made = _fallback(beat, script, " ".join(script.beats[k].text for k in group), keep=stand_in or bool(tag))
    if not tag and made.template == "sketch" and made.sketch and made.sketch.marks and not stand_in:
        chosen[i] = {"drawn": made}
    return made


def _opening(i: int, script: Script) -> bool:
    """Whether sentence i is said before the first drawing may come (the hook)."""
    return sum(len(b.text.split()) for b in script.beats[:i]) < FIRST_DIAGRAM_WORDS


def retry_opening_footage(script: Script) -> Script:
    """Opening sentences back to footage where an earlier build kept a drawing that stood in for it
    (a sketch with the searches still on it and no idea of its own, D161). The user's own picks stay."""
    beats = []
    for i, b in enumerate(script.beats):
        v = b.visual
        if _opening(i, script) and v.kind == "diagram" and v.template == "sketch" and v.queries and not v.idea and not v.manual:
            b = b.model_copy(update={"visual": v.model_copy(update={"kind": "stock", "template": "", "sketch": None})})
        beats.append(b)
    return script.model_copy(update={"beats": beats})


#: The judge's score footage needs when the user asked for footage on that sentence themselves:
#: they want footage there, so a looser match beats a drawing they didn't ask for (D126).
ASKED_GOOD_ENOUGH = 6   # 4 let poor footage through (D129)


class _Footage(NamedTuple):
    parts: list[float]       # seconds of each shot of the sentence
    hits: list               # the clip for each (None: nothing fit)
    queries: list[str]       # the searches that found them
    drawn: Visual | None = None   # nothing fit: its drawing, made ahead (D188)


def _pick_footage(i: int, beat, visual: Visual, seconds: float, script: Script, avoid: set,
                  asked: bool) -> _Footage:
    """The stock clip(s) for a sentence's picture, a clip per MAX_SHOT seconds, each a different one while
    enough are good enough (else a part goes on with the clip before it, D132). One judgement serves every
    part, and the judge's own better searches are the only second look (D181): a call per part, then the
    footage model's searches and another judgement, came to about four calls a part. A wish, or new footage
    asked for, goes straight to searches written for the sentence (D129)."""
    said_before = sum(len(b.text.split()) for b in script.beats[:i])
    longest = HOOK_SHOT if said_before < HOOK_WORDS else MAX_SHOT   # quicker pictures while the hook is said (D156)
    count = max(1, math.ceil(seconds / longest - 1e-6))
    parts = [seconds / count] * count
    queries = [q for q in (visual.queries or [visual.query]) if q.strip()]
    group = next((g for g in spans(script) if g[0] == i), [i])
    text = " ".join(script.beats[k].text for k in group)
    # Fewer than two real searches: written for the sentence (D196), where the sentence's longest words were used.
    widened = asked or bool(visual.wish) or len(queries) < 2
    # The opening's looser bar is the first sentence's only (D196): it caught the second too, and a fireplace
    # scored about 5 for "thirty-seven degrees".
    good = ASKED_GOOD_ENOUGH if asked else OPENING_GOOD_ENOUGH if i == 0 else stock.GOOD_ENOUGH
    searches = queries or [_query_from(beat.text)]
    if widened:
        written = stock.plan_searches(text, script.text, wish=visual.wish, tried=queries if asked else None)
        searches = list(dict.fromkeys([*written, *queries]))[:6] or [_query_from(beat.text)]
    found = stock.choose(searches, parts[0], avoid, sentence=beat.text, context=script.text, good_enough=good,
                         count=count, photos=i > 0)
    log.info("create: footage for sentence %d: %d of %d part(s) found%s", i + 1, len(found), count,
             ", searches written for it" if widened else "")
    return _Footage(parts, [found[k] if k < len(found) else None for k in range(count)], searches)


FOOTAGE_WORKERS = 4


def _prefetch(script: Script, timings: Timings, groups: list[list[int]], progress=None) -> dict[int, _Footage]:
    """The footage for every stock picture at once (D136): one sentence's AI wait, searches and
    thumbnails overlap the others'. A choice that clashes with an earlier sentence's clip (the same
    one picked twice) is made again in turn by `_planned`, so no clip is used twice."""
    todo = []
    for group in groups:
        v = script.beats[group[0]].visual
        if v.kind == "stock" and not v.clip and not (v.picked and all(h.get("url") for h in v.picked)):
            todo.append((group[0], timings.beats[group[-1]][1] - timings.beats[group[0]][0]))
    if len(todo) < 2:
        return {}

    def one(job: tuple[int, float]) -> tuple[int, _Footage | None]:
        i, seconds = job
        v = script.beats[i].visual
        try:
            got = _pick_footage(i, script.beats[i], v, seconds, script, set(v.avoid), bool(v.redo))
            if not got.hits[0] and not (v.redo and v.previous):
                # Nothing fits: its drawing now, beside the other sentences' footage (D188). Drawn in turn in the
                # shot loop, four of them took 6 of a 7.5-minute build.
                got = got._replace(drawn=_drawn(i, script.beats[i], script, ""))
            return i, got
        except Exception as exc:  # chosen again in turn, where the same trouble is reported
            log.info("create: footage for sentence %d not chosen ahead (%s)", i + 1, exc)
            return i, None

    # Counted as each one is chosen: a slow AI kept one number on screen for minutes (D154).
    out: dict[int, _Footage] = {}
    with ThreadPoolExecutor(max_workers=FOOTAGE_WORKERS) as pool:
        for n, done in enumerate(as_completed([pool.submit(one, job) for job in todo]), 1):
            i, got = done.result()
            if got:
                out[i] = got
            if progress:
                progress(f"Choosing footage: {n} of {len(todo)}", 8 + 2 * n / len(todo))
    return out


def _planned(i: int, beat, visual: Visual, seconds: float, said: list, script: Script, work: Path, used: set,
             tag: str = "", pre: _Footage | None = None, at: float | None = None) -> list[Path]:
    """The shot(s) of the picture planned for a sentence: a diagram, or stock footage (a clip per
    MAX_SHOT seconds, each a different one), or a drawing when no footage fits. `tag` keeps a
    filler's files apart from the sentence's own clip. Footage a build picked before is used
    again (D125); "new footage" clears it and turns those clips down. When the user asked for
    footage and none fits, the sentence keeps what it had, never a new drawing (D126)."""
    if visual.kind == "diagram" and visual.template == "sketch" and not (visual.sketch and visual.sketch.marks):
        visual = _drawn(i, beat, script, tag)  # asked for a new drawing, or its drawing failed
    if visual.kind == "diagram":
        return [_diagram(visual, seconds, work / f"{i:02d}{tag}_diagram.mp4", said, _frames(at, seconds))]
    asked = visual.redo and not tag

    def none_fit() -> list[Path]:
        if asked and visual.previous:
            # Kept what it had, said on the part itself, and the clip turned down stays turned down (D129).
            before = Visual.model_validate({**visual.previous, "redo": False, "previous": None,
                                            "avoid": list(dict.fromkeys([*visual.previous.get("avoid", []), *visual.avoid]))})
            picture_notes.append(f"Sentence {i + 1}: no new footage fit, so it kept what it had.")
            shot = _planned(i, beat, before, seconds, said, script, work, used, tag, at=at)
            rec = chosen.get(i, {})
            chosen[i] = {"restore": before, "notice": "No new footage fit well enough, so this part kept what it had. "
                         "Use New footage to pick one yourself, or say what you'd like to see.",
                         **({"picked": rec["picked"]} if "picked" in rec else {})}
            return shot
        drawn = pre.drawn if pre is not None and pre.drawn is not None and not tag else _drawn(i, beat, script, tag)
        return [_diagram(drawn, seconds, work / f"{i:02d}{tag}_sketch.mp4", said, _frames(at, seconds))]

    # The part's own footage is kept unless another part already uses it; `avoid` only steers new
    # choices (a part that kept its clip when nothing new fit has that clip in both, D129).
    kept = [h for h in visual.picked if h.get("id") not in used and h.get("url")] if not tag else []
    if kept and len(kept) == len(visual.picked):  # what was picked before, cut as before
        parts, hits, queries = [seconds / len(kept)] * len(kept), kept, []
    else:
        reuse = pre and not any(h and h["id"] in used for h in pre.hits)   # chosen ahead, no clash
        got = pre if reuse else _pick_footage(i, beat, visual, seconds, script, set(visual.avoid) | used, bool(asked))
        parts, hits, queries = got.parts, list(got.hits), got.queries
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
        again = k % 2 == 1 and hits[k - 1].get("id") == hit.get("id")   # no clip of its own: a closer angle, every other part (D196)
        begins = (at + sum(parts[:k])) if at is not None else None
        n = _frames(begins, part)
        # A punch-in only on a word the clip shows, not in its first 0.8 s or last 0.6 s (D197): one on the cut
        # read as a jolt, and "too soon".
        mine = [t for t in punches.get(i, []) if begins is not None and begins <= t < begins + part]
        punch = next((t - begins for t in mine if 0.8 <= t - begins <= part - 0.6 and _shows(beat.emphasis, hit)), None)
        if mine and punch is None:
            punches[i] = [t for t in punches[i] if t not in mine]
        # The user's choice first (D162); a punched shot holds still, so a drift or pull doesn't fight it (D197).
        motion = visual.camera if visual.camera in MOTIONS else "still" if punch is not None \
            else "close" if again else _next_motion()
        if i == 0 and k == 0 and not tag:
            opening[:] = [{"hit": hit, "motion": motion, "n": n, "frame": visual.frame}]

        def make(out: Path, holder=holder, skip=skip, motion=motion, punch=punch, n=n) -> Path:
            h = holder["hit"]
            clip = _stock_shot(stock.fetch(h), n / FPS, out, h.get("center"), skip, motion, punch, frames=n,
                               frame=visual.frame)
            if visual.frame is not None:   # the user's framing stays, dark or not (D180)
                return clip
            if _too_dark(clip):  # the crop found the dark part: the middle, else no footage
                holder["hit"] = {**h, "center": 0.5}
                clip = _stock_shot(stock.fetch(h), n / FPS, out, 0.5, skip, motion, punch, frames=n)
            if _too_dark(clip):
                raise _TooDark
            return clip

        try:
            key = [hit.get("id"), hit.get("center"), n, motion, punch and round(punch, 2)] + \
                ([round(skip, 3)] if skip else []) + ([round(visual.frame, 3)] if visual.frame is not None else [])
            made = _kept("stock", key, work / f"{i:02d}{tag}_{k}_stock.mp4", make)
            shot_info[made] = (n, "footage")
            clips.append(made)
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
        v = b.visual.model_copy(update={"redo": False, "previous": None, "notice": "", "wish": "", "restyle": False})
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
         notes: list[str], at: float | None = None) -> tuple[list[Path], float] | None:
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
    n = _frames(at, total)
    try:
        stat = src.stat()
        shot = _kept("own", [str(src), stat.st_size, stat.st_mtime, round(start, 3), round(play, 3), n,
                             round(slow, 4), mode == "loop"], out,
                     lambda o: _own_shot(src, start, play, n / FPS, o, slow=slow, loop=mode == "loop", frames=n))
        shot_info[shot] = (n, "own")
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


#: The on-screen hook's time (D155): long enough to read 6 words, gone before the first cause.
HOOK_SECONDS = 1.5
#: The hook as big as the captions and more (about 120 px), the question readable from the first frame (D156).
HOOK_SCALE = 1.25


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
    if progress:
        progress("Choosing footage", 8)
    ahead = _prefetch(script, timings, groups, progress)
    _moves[0] = 0
    _zooms[0] = script.zooms
    opening.clear()
    punches.clear()
    punches.update(choose_punches(script, timings) if script.zooms else {})
    picture_times.clear()
    starts = []   # where each picture's shots begin in `made`
    for n, group in enumerate(groups):
        starts.append(len(made))
        picture_times.append((timings.beats[group[0]][0], ""))
        i, beat = group[0], script.beats[group[0]]
        if progress:
            progress(f"Shot {n + 1} of {len(groups)}", 10 + 60 * n / len(groups))
        a, b = timings.beats[group[0]][0], timings.beats[group[-1]][1]
        seconds = b - a
        # Every word said while the picture is up, so a part can arrive on any of them (D124).
        said = [(w.start - a, w.text) for w in timings.words if a - 0.05 <= w.start < b]
        if beat.visual.clip:
            got = _own(i, beat.visual, seconds, own, cursor, default_fill, work, notes, at=a)
            if got:
                made += got[0]
                rest = got[1]
                if rest > 0.05:  # the clip ran out: the planned picture takes over for the rest
                    at = seconds - rest
                    made += _planned(i, beat, beat.visual, rest, [(t - at, w) for t, w in said if t >= at - 0.05],
                                     script, work, used, tag="b", at=a + at)
                continue
        made += _planned(i, beat, beat.visual, seconds, said, script, work, used, pre=ahead.get(i), at=a)
    # What each picture turned out to be: a drawing (diagram or sketch, cached as "diagram-...") gets a chalk tap.
    for n, (at, _) in enumerate(picture_times):
        end = starts[n + 1] if n + 1 < len(starts) else len(made)
        picture_times[n] = (at, "drawing" if any(m.name.startswith("diagram") or "_diagram" in m.name or "_sketch" in m.name
                                                 for m in made[starts[n]:end]) else "other")
    final = _loop_back(made, starts, groups, script, timings, work)
    part_owner[:] = [groups[max(n for n, s in enumerate(starts) if s <= k)][0] for k in range(len(final))]
    return final


def _loop_back(made: list[Path], starts: list[int], groups: list[list[int]], script: Script, timings: Timings,
               work: Path) -> list[Path]:
    """A loop's last LOOP_TAIL seconds (D197): the opening clip's own footage from just before the opening's first
    frame, played up to it, the camera running back into where the opening's move begins, so the restart is no cut at
    all. The rest of the last sentence keeps its own picture (the user's pick too). D155 played the opening's first
    1.3 s again, looped, under the whole last sentence, and the camera jumped on every pass. Not when the opening
    isn't stock footage or the last picture is a drawing."""
    loop_cut[0] = None
    if not opening or len(groups) < 3 or not loops(script, timings):
        return made
    tail_shots = made[starts[-1]:]
    if any(m in _drawing or shot_info.get(m, (0, ""))[1] == "drawing" for m in tail_shots):
        return made
    o = opening[0]
    a, end = timings.beats[groups[-1][0]][0], timings.duration
    try:
        src = stock.fetch(o["hit"])
    except Exception as exc:  # the opening's clip is gone: the end stays as it is
        log.info("create: no loop lead-in (%s)", exc)
        return made
    photo = src.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp")
    d0 = o["n"] / FPS
    if photo:
        offset, x, info = 0.0, min(1.0, max(0.0, o["frame"] if o["frame"] is not None else o["hit"].get("center") or 0.5)), None
    else:
        offset, x, info = _framing(src, d0, o["hit"].get("center"), 0.0, o["frame"])
    want = end - LOOP_TAIL
    cut = a if end - a < LOOP_TAIL + 0.8 else min((w.start for w in timings.words if a + 0.8 <= w.start <= end - 0.6),
                                                  key=lambda t: abs(t - want), default=want)
    if not photo:
        cut = max(cut, end - offset)   # only `offset` seconds of the clip come before the opening's first frame
    keep, lead = round(cut * FPS) - round(a * FPS), round(end * FPS) - round(cut * FPS)
    if lead < round(0.6 * FPS):
        return made
    out = work / "loop_lead.mp4"
    move = compose.Move(kind=o["motion"] if not (photo and o["motion"] == "still") else "push", u=x)
    if photo:
        from PIL import Image

        with Image.open(src) as img:
            compose.photo_panel(img, out, lead, move, look=LOOK, back=d0)
    else:
        compose.video_panel(src, out, lead, move, start=max(0.0, offset - lead / FPS), look=LOOK, back=d0,
                            source_size=(info.width or W, info.height or H))
    shot_info[out] = (lead, "footage")
    kept: list[Path] = []
    for m in tail_shots:   # the last sentence's own shots, up to the cut
        if keep <= 0:
            break
        frames, kind = shot_info.get(m) or (max(1, round((probe(m).duration or 0.0) * FPS)), "footage")
        shot_info[m] = (min(frames, keep), kind)
        kept.append(m)
        keep -= frames
    loop_cut[0] = cut
    return [*made[:starts[-1]], *kept, out]


#: In a long sentence the talking picture changes at a word about this often, and a pose holds this long at the
#: end of its sentence (D196): one picture a sentence, 4 s at a time, looked still.
TALK_SWITCH, POSE_HOLD = 2.0, 2.4


def _switches(a: float, b: float, word_starts: list[float]) -> list[float]:
    """Where a talking picture changes inside (a, b): at word starts, TALK_SWITCH apart, none in the last second."""
    out, last = [], a
    for s in word_starts:
        if s - last >= TALK_SWITCH and b - s >= 1.0:
            out.append(s)
            last = s
    return out


def talking_frames(voice: Path, timings: Timings, total: int) -> list[bool]:
    """The frames his mouth is open (D196): where the voice is loud (its syllables), inside the words. A silent track
    (no voice, D146) opens and closes through the words at a steady pace. No flicker: a run under 2 frames joins
    its neighbours."""
    from .voice import levels

    try:
        loud = levels(voice, raw=True)
    except Exception:  # an unreadable track: the words' steady pace
        loud = []
    speaking = bool(loud) and float(np.percentile(loud, 95)) > -45
    top = float(np.percentile(loud, 95)) if speaking else 0.0
    words = sorted((w.start, w.end) for w in timings.words)
    out, is_open, i = [False] * total, False, 0
    for k in range(total):
        t = k / FPS
        while i < len(words) and words[i][1] < t:
            i += 1
        in_word = i < len(words) and words[i][0] - 0.03 <= t <= words[i][1] + 0.03
        if not in_word:
            is_open = False
        elif speaking:
            db = loud[min(len(loud) - 1, int(t * 100))] - top
            is_open = db > -18 if not is_open else db > -26   # opens on a syllable, shuts in the dip after it
        else:
            is_open = (k // 4) % 2 == 0
        out[k] = is_open
    for k in range(1, total - 1):   # a lone frame either way is a flicker
        if out[k] != out[k - 1] and out[k] != out[k + 1]:
            out[k] = out[k - 1]
    return out


def character_plan(script: Script, timings: Timings, channel: channels.Channel, punchline: float,
                   seed: int, loop_at: float | None = None) -> list[tuple[Path, list[tuple[float, float]]]]:
    """Which picture of the character shows when (D159), each with its times; together they cover the whole video.
    The opening sentence: the channel's picture. Then per sentence: the writer's pose for it (D158), else the presenting
    picture where the first drawing appears (D157), else the "talking" poses in turn, else the channel's picture.
    From the punchline: a reaction (D156). He stays, as CodeBullet's does: popping in for 2 s read as a glitch.
    On a loop (`loop_at`, the last line's start, D197): the reaction holds to the last line, he talks through it, and
    from the lead-in (loop_cut) shows the opening's picture, so the restart doesn't change him."""
    def found(*paths: str) -> list[Path]:
        return [Path(p) for p in paths if p and Path(p).is_file()]

    base = found(channel.character)
    if not base:
        return []
    talking = found(*(p for n, p in channel.poses.items() if n.startswith("talking")))
    moods = found(*channel.reactions)
    point = found(channel.presenter)
    first = next((t for t, kind in picture_times if kind == "drawing" and CHARACTER_START <= t < punchline - 0.5), None)
    starts = [0.0] + [s for s, _ in timings.beats[1:]] if len(timings.beats) == len(script.beats) else [0.0]
    starts = [s for s in starts if s < punchline] + [punchline]
    word_starts = [w.start for w in timings.words]
    shows, idle = [], 0

    def talk(a: float, b: float) -> None:   # the talking pictures in turn, changing at words in a long stretch
        nonlocal idle
        for x, y in itertools.pairwise([a, *_switches(a, b, word_starts), b]):
            shows.append((talking[idle % len(talking)], x, y))
            idle += 1

    for i, (a, b) in enumerate(itertools.pairwise(starts)):
        pose = found(channel.poses.get(script.beats[i].pose, "")) if i and script.beats[i].pose else []
        if i == 0:
            shows.append((base[0], a, b))
        elif pose:   # a long sentence talks first and pulls the pose as it lands, held to its end (a cut-in's there)
            if talking and b - POSE_HOLD - a >= 1.0:
                talk(a, b - POSE_HOLD)
                a = b - POSE_HOLD
            shows.append((pose[0], a, b))
        elif point and first is not None and a <= first < b:
            shows.append((point[0], a, b))
        elif talking:
            talk(a, b)
        else:
            shows.append((base[0], a, b))
    react = moods[seed % len(moods)] if moods else base[0]
    if loop_at is None:
        shows.append((react, punchline, timings.duration + 1))
    else:
        shows.append((react, punchline, loop_at))
        cut = loop_cut[0] if loop_cut[0] is not None and loop_at < loop_cut[0] < timings.duration else loop_at
        if talking and cut - loop_at >= 1.0:
            talk(loop_at, cut)
        else:
            cut = loop_at
        shows.append((base[0], cut, timings.duration + 1))
    plan: dict[Path, list[tuple[float, float]]] = {}
    for pic, a, b in shows:
        times = plan.setdefault(pic, [])
        if times and abs(times[-1][1] - a) < 1e-6:   # the same picture running on: one stretch
            times[-1] = (times[-1][0], b)
        else:
            times.append((a, b))
    return list(plan.items())


def _music_track(channel: channels.Channel, seed: int) -> Path | None:
    """One of the user's own tracks for this channel, if they put any in data/create/music/<channel> (D156)."""
    from ..paths import data_root
    from .voice import AUDIO

    found = sorted(f for f in (data_root() / "create" / "music" / channel.slug).glob("*") if f.suffix.lower() in AUDIO)
    return found[seed % len(found)] if found else None


#: Poses the camera cuts in on, for a beat, as he pulls them (D162): the reactions that read as a joke.
CUT_IN_POSES = {"shocked", "facepalm", "deadpan", "smug", "nervous", "confused", "laugh", "grudge"}
#: Cut-ins mid-video: at most this many, this far apart; never in the first seconds.
CUT_INS, CUT_IN_GAP, CUT_IN_SECONDS = 2, 6.0, 0.7


def cutaways(script: Script, timings: Timings, punchline: float, total: int,
             shots: list[compose.Shot] = (), loop_at: float | None = None) -> list[compose.Cutaway]:
    """When the camera cuts in on the Professor (D162): for the last 0.7 s of a sentence tagged with a reaction
    pose (at most CUT_INS, CUT_IN_GAP apart, not in the hook), and on the punchline's last word to the end, the
    deadpan beat, in closer. A hard cut in and out, as a comedy edit does. Never mid-video over a drawing (D190):
    its last part lands at the end of its sentence, and a cut-in there pushed it off the screen before it could
    be read. Nor within half of PUNCH_GAP of a punch-in (D197): four zooms in 4.6 s. On a loop (`loop_at`) the last
    cut-in ends as the last line starts, which runs back into the opening."""
    drawn = [(s.start, s.start + s.frames) for s in shots if s.kind == "drawing"]
    zooms = [t for ts in punches.values() for t in ts]
    out: list[compose.Cutaway] = []
    last = -CUT_IN_GAP
    if len(timings.beats) == len(script.beats):
        for i, beat in enumerate(script.beats[:-1]):
            a, b = timings.beats[i]
            over_drawing = any(s < round(b * FPS) and round((b - CUT_IN_SECONDS) * FPS) < e for s, e in drawn)
            if beat.pose in CUT_IN_POSES and a >= CHARACTER_START and b - CUT_IN_SECONDS - last >= CUT_IN_GAP \
                    and len(out) < CUT_INS and b - a > 1.5 and not over_drawing \
                    and not any(abs(t - (b - CUT_IN_SECONDS)) < PUNCH_GAP / 2 for t in zooms):
                out.append(compose.Cutaway(a=round((b - CUT_IN_SECONDS) * FPS), b=round(b * FPS), z=1.28))
                last = b
    final = [w for w in timings.words if punchline <= w.start < (loop_at if loop_at is not None else timings.duration)]
    if final:
        a, b = round(final[-1].start * FPS), round(loop_at * FPS) if loop_at is not None else total
        if b - a >= round(0.4 * FPS):
            out.append(compose.Cutaway(a=a, b=b, z=1.5))
    return out


#: The sound a cut-in on the Professor gets, by the pose he pulls (D197).
CUT_IN_SOUND = {"shocked": "scratch", "nervous": "scratch", "confused": "boing", "smug": "boing", "laugh": "boing",
                "facepalm": "bonk", "deadpan": "bonk", "grudge": "bonk"}
NUMBER_WORDS = {"zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven",
                "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen", "twenty",
                "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety", "hundred", "thousand", "million",
                "billion", "half", "double", "twice"}


def sound_events(shots: list[compose.Shot], script: Script, timings: Timings, cuts: list[compose.Cutaway],
                 punchline: float, before: float, hook: bool) -> list[tuple[float, str]]:
    """Every moment of the edit that could take a sound effect (D197), for sound.pick to choose from: the hook's pop,
    a whoosh on a whip or zoom, a swish where the picture changes on a plain cut, chalk as each part of a drawing comes,
    a thump on a punch-in that landed, a cut-in's comic sound, a ding on a highlighted number with no punch-in, a riser
    into the pause before the punchline (`before`), a swell into the loop."""
    out = [(0.03, "pop")] if hook else []
    owners = part_owner if len(part_owner) == len(shots) else list(range(len(shots)))
    for k, s in enumerate(shots):
        at = s.start / FPS
        if k and s.enter in ("whip", "zoom"):
            out.append((at, "whoosh" if s.enter == "whip" else "zoom"))
        elif k and owners[k] != owners[k - 1]:
            out.append((at, "swish"))
        out += [(at + c, "chalk") for c in chalk_at.get(s.path, []) if c < s.frames / FPS]
    for t in (t for ts in punches.values() for t in ts):
        if any(s.kind == "footage" and s.start <= t * FPS < s.start + s.frames for s in shots):
            out.append((t, "thump"))
    if len(timings.beats) == len(script.beats):
        for c in cuts:
            at = c.a / FPS
            i = next((i for i, (a, b) in enumerate(timings.beats) if a <= at < b), None)
            if at < punchline and i is not None and (kind := CUT_IN_SOUND.get(script.beats[i].pose)):
                out.append((at, kind))
        for i, beat in enumerate(script.beats):
            key = beat.emphasis.strip(".,!?;:'\"").lower()
            if key and i not in punches and (any(ch.isdigit() for ch in key) or key.split("-")[0] in NUMBER_WORDS):
                out += [(w.start, "ding") for w in _words_of(timings, script, i)
                        if w.text.strip(".,!?;:'\"").lower() == key][:1]
    if before >= 4.0:
        out.append((before, "riser"))
    if timings.words and timings.duration - timings.words[-1].end >= 0.5:
        out.append((timings.duration, "swell"))
    return out


def assemble(parts: list[Path], voice: Path, script: Script, timings: Timings, out: Path,
             channel: channels.Channel, config: Config) -> Path:
    """The edit (D162): the panel's shots, the Professor, captions, hook, sign-off and watermark, every frame
    drawn by create/compose.py, with the voice evened to the platform loudness and any music under it."""
    from . import sound

    rc = config.render
    seed = int(hashlib.sha1(script.title.encode()).hexdigest()[:8], 16)
    loop = loops(script, timings)
    # A loop's punchline is the line before its last, which runs back into the opening (D196, D197).
    loop_at = timings.beats[-1][0] if loop else None
    punchline = timings.beats[-2 if loop else -1][0] if timings.beats else timings.duration
    total = max(1, round(timings.duration * FPS))
    shots, at = [], 0
    for path in parts:
        n, kind = shot_info.get(path) or (max(1, round((probe(path).duration or 0.0) * FPS)), "footage")
        shots.append(compose.Shot(path=path, start=at, frames=n, kind=kind))
        at += n
    compose.transitions(shots, seed, script.transitions)
    ways_in(shots, script, part_owner)
    palette = diagrams.PALETTES.get(channel.board) or diagrams.PALETTES["slate"]
    edit = compose.Edit(shots=shots, total=total, board=compose.base_board(palette["board"], palette["chalk"]))
    plan = character_plan(script, timings, channel, punchline, seed, loop_at=loop_at)
    if plan:
        shows = sorted(((pic, a, b) for pic, times in plan for a, b in times), key=lambda x: x[1])
        edit.presenter = compose.Presenter(shows, total, talk=talking_frames(voice, timings, total), seed=seed,
                                           enter=not loop)
        edit.cutaways = cutaways(script, timings, punchline, total, shots, loop_at=loop_at) if script.cut_ins else []
        for s in shots:   # straight out of a cut-in: a plain cut, not a whip on top of it
            if any(0 <= s.start - c.b <= 2 for c in edit.cutaways):
                s.enter = "cut"
    lit = tuple(bytes.fromhex(script.caption_colour[1:])) if len(script.caption_colour) == 7 else palette["yellow"]
    edit.captions = compose.Captions(caption_words(script, timings), accent=lit, size=script.caption_size,
                                     shift=script.caption_shift) if script.captions else None
    hook = (script.hook or script.title) if rc.show_hook_text else ""
    if hook:
        edit.hook = compose.hook_image(hook)
        edit.hook_frames = round(HOOK_SECONDS * FPS)
        if loop and timings.words:   # back on screen for the last second, so the last frame is the first (D197)
            edit.hook_again = min(total - 5, max(total - FPS, round(timings.words[-1].start * FPS)))
    if channel.signoff and not loop:   # a loop has no end card
        edit.signoff, edit.signoff_from = channel.signoff, max(0, total - round(1.4 * FPS))
    mark = Path(channel.watermark) if channel.watermark else None
    if mark and mark.is_file():
        from PIL import Image

        img = Image.open(mark).convert("RGBA")
        img = img.resize((WATERMARK_WIDTH, max(1, round(img.height * WATERMARK_WIDTH / img.width))), Image.LANCZOS)
        edit.watermark = np.asarray(img)
    inputs = ["-i", str(voice)]
    graph = (f"[1:a]loudnorm=I={rc.loudness_lufs}:TP={rc.true_peak_dbtp}:LRA=11,"
             f"aresample={rc.audio_rate},apad[vo]")
    # Music and sound effects (D156, D197): one track under the voice, levelled for a voice at -14 LUFS, then a
    # limiter, with its auto level off: on, it lifted the whole mix about 1 dB and pushed the peaks to 0 dBFS.
    music = channel.music if script.music is None else script.music
    sfx = channel.sfx if script.sfx is None else script.sfx
    if music or sfx:
        before = max(0.0, max((w.end for w in timings.words if w.end <= punchline), default=punchline))
        said_to = loop_at if loop_at is not None else timings.words[-1].end if timings.words else timings.duration
        # Nothing over the punchline but the logo: the silence is the joke.
        effects = sound.pick(sound_events(shots, script, timings, edit.cutaways, punchline, before, bool(hook)),
                             timings.duration, quiet=(before, said_to)) if sfx else []
        if effects:   # what was placed, for the page (D120)
            counts = Counter(kind for _, kind in effects)
            picture_notes.append("Sound effects: " + ", ".join(f"{n} {kind}" for kind, n in counts.most_common()) + ".")
        # A loop's music plays on to the end, into its start (D197).
        track = sound.bed(timings.duration, words=[(w.start, w.end) for w in timings.words],
                          quiet_from=timings.duration if loop else before, effects=effects,
                          logo_at=punchline if sfx and not loop else None, with_music=music, seed=seed,
                          track=_music_track(channel, seed) if music else None)
        inputs += ["-i", str(sound.write(track, out.with_name("bed.wav")))]
        graph += (f";[2:a]aresample={rc.audio_rate}[bd];[vo][bd]amix=inputs=2:normalize=0:duration=first,"
                  f"alimiter=limit=0.89:level=0[a]")
    else:
        graph += ";[vo]anull[a]"
    encode = ["-c:a", "aac", "-b:a", rc.audio_bitrate, "-ar", str(rc.audio_rate), "-ac", "2", *reencode_args(config)]
    return compose.render(edit, out, inputs, graph, encode, timings.duration)


def _words_of(timings: Timings, script: Script, i: int) -> list:
    """The voice's words of sentence i."""
    k = sum(len(b.text.split()) for b in script.beats[:i])
    return timings.words[k:k + len(script.beats[i].text.split())]


def ways_in(shots: list, script: Script, owners: list[int]) -> None:
    """A part's own way in, chosen by hand, over the video's (D171): on the first shot of its picture only."""
    if len(owners) != len(shots):
        return
    for k in range(1, len(shots)):
        pick = script.beats[owners[k]].visual.transition
        if owners[k] != owners[k - 1] and pick in ("cut", "whip", "zoom"):
            shots[k].enter = pick


def caption_words(script: Script, timings: Timings) -> list:
    """The words the captions show (D171): the voice's, except where the user wrote a sentence's caption, whose
    words are spread over the time that sentence is said, longer words a little longer."""
    from .voice import TimedWord

    if not any(b.caption.strip() for b in script.beats):
        return timings.words
    out = []
    for i, beat in enumerate(script.beats):
        said = _words_of(timings, script, i)
        mine = beat.caption.split()
        if not mine or not said:
            out += said
            continue
        a, b = said[0].start, said[-1].end
        weights = [len(w) + 2 for w in mine]
        unit, at = (b - a) / sum(weights), a
        for w, weight in zip(mine, weights, strict=True):
            out.append(TimedWord(text=w, start=round(at, 3), end=round(at + weight * unit, 3)))
            at += weight * unit
    return out


def _campaign(channel: channels.Channel) -> None:
    """The channel's own campaign, so its videos post and sync like any clip."""
    from ..studio.server import campaigns_dir

    path = campaigns_dir() / f"{channel.campaign}.yaml"
    if path.exists():
        return
    path.write_text(
        f"# The user's own channel ({channel.handle}): original videos made in Create (D108).\n"
        f"name: {channel.campaign}\ntitle: {channel.name}\nown_channel: true\n"
        "source_authorization: Original videos made by the channel owner for their own account.\n"
        "platform_targets: [youtube_shorts, tiktok, instagram_reels]\n"
        "duration:\n  min_seconds: 15\n  max_seconds: 60\n", encoding="utf-8")


def build(video_id: int, progress=None) -> int:
    """Make the video and file it; returns its clip id in the library."""
    global shot_cache, _pool
    try:
        return _build(video_id, progress)
    finally:
        shot_cache = None  # also when it failed: the cache belongs to one build
        _drawing.clear()
        if _pool is not None:   # a drawing under way when a build stops still lands in the cache, whole
            _pool.shutdown(wait=False, cancel_futures=True)
            _pool = None


def _build(video_id: int, progress) -> int:
    from ..render.cover import pick, put_first
    from ..studio import db, library
    from ..utils.recycle import recycle

    started = time.monotonic()
    took: list[tuple[str, float]] = []   # each stage's seconds, for the log (D174)

    def lap(stage: str) -> None:
        took.append((stage, time.monotonic() - started - sum(t for _, t in took)))

    row = store.video(video_id)
    if row is None:
        raise CreateError("no such video")
    if not row["voice"] or not row["timings"]:
        raise CreateError("drop the voiceover in first")
    script, timings = Script.model_validate(row["script"]), Timings.model_validate(row["timings"])
    script = retry_opening_footage(script)
    timings = nudged(script, timings)
    if loops(script, timings):
        timings = loop_timing(timings)
    channel, config = channels.load(), Config.load()
    diagrams.use_palette(channel.board)   # the channel's board colours, for the sketches' review too (D156)
    global _palette
    _palette = channel.board
    notes: list[str] = []
    stock.unjudged.clear()
    chosen.clear()
    picture_notes.clear()
    used_shots.clear()
    shot_info.clear()
    chalk_at.clear()
    global shot_cache
    shot_cache = folder(video_id) / "shots"
    own = userclips.files(video_id)
    if own or userclips.load(video_id)["clips"]:
        # The sentences' real lengths now known: clips placed for the user if they asked (D119).
        script, said = userclips.prepare(video_id, script, [b - a for a, b in timings.beats])
        if said:
            notes.append(said)
        store.update_video(video_id, script={**script.model_dump(), "take": row["script"].get("take", 1)})
    if progress:
        progress("Drawing the sketches", 4)
    drawn, failed = draw_all(script)  # the sketches the script is still missing, a few at once (D136)
    if drawn != script:
        script = drawn
        store.update_video(video_id, script={**script.model_dump(), "take": row["script"].get("take", 1)})
    notes += failed
    if not stock._pexels_key():   # D196: it was missing for days, and footage came from one library
        notes.append("Pexels isn't searched (no PEXELS_API_KEY in .env): footage comes from Pixabay only. "
                     "A free key from pexels.com/api adds its library.")
    lap("sketches")
    work = folder(video_id) / "work"
    if work.exists():
        shutil.rmtree(work, ignore_errors=True)  # scratch, deleted outright: it's rebuilt each time (D133)
    work.mkdir(parents=True, exist_ok=True)
    parts = shots(script, timings, work, progress, own=own, notes=notes,
                  default_fill=userclips.load(video_id)["fill"])
    _drawings_done(progress)
    lap("shots")
    if progress:
        progress("Putting it together", 75)
    out = assemble(parts, Path(row["voice"]), script, timings, work / "final.mp4", channel, config)
    lap("assembly")
    if progress:
        progress("Choosing the cover", 90)
    # The moment the user chose for the cover, else the best still (D171).
    cover_at = script.cover if script.cover is not None and 0 <= script.cover < timings.duration else pick(out, timings.duration)
    # A loop with its hook on screen opens on it already: two cover frames put first flashed on every pass, so
    # only the user's own cover moment is (D197). A cover from the hook's own seconds already shows the hook.
    looped = loops(script, timings) and config.render.show_hook_text and script.cover is None
    if cover_at is not None and not looped:
        put_first(out, cover_at, hook="" if cover_at < HOOK_SECONDS and config.render.show_hook_text else script.title,
                  config=config, work_dir=work, fps=FPS)
    lap("cover")
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
        notes.append(f"Footage for {n} sentence{'s' if n != 1 else ''} was picked by its search words only, as no AI "
                     "could look at it: check those shots, or press Build again later.")
    placed = {b.visual.clip for b in script.beats if b.visual.clip}
    idle = [name for cid, (_, _, name) in own.items() if cid not in placed]
    if idle:
        notes.append("Not used: " + ", ".join(idle) + ".")
    script = remember(script)
    saved = {**script.model_dump(), "take": row["script"].get("take", 1)}
    store.update_video(video_id, status="built", clip_id=clip_id, error="", script=saved, built=saved,
                       check_notes=userclips.with_notes(store.video(video_id)["check_notes"], notes))
    shutil.rmtree(work, ignore_errors=True)
    for old in (shot_cache.glob("*.mp4") if shot_cache.is_dir() else []):
        if old not in used_shots:  # shots of pictures no longer in the video
            old.unlink(missing_ok=True)
    log.info("create: video %s built as clip %s (%s) in %.0fs: %s", video_id, clip_id, rel, time.monotonic() - started,
             ", ".join(f"{stage} {t:.0f}s" for stage, t in took))
    return clip_id

