"""Finding scene boundaries in scripted TV, so clips hold whole scenes.

Reported on real sitcom clips: a stray bit of an unrelated scene at the start or
end, clips ending mid-scene or running into the next scene after the joke, and
clips that need context to follow. All come from cutting at sentence boundaries
with no idea where a scene begins or ends. A sitcom cuts between camera angles
every few seconds *inside* a scene, so shot cuts alone do not mark scenes.

Colour does not either. Grouping shots by look was tried first on a real
episode: the whole show is shot on one apartment set under the same warm grade,
and the lowest cross-boundary similarity in the episode was 0.54 -- nothing
separated. A scene is a narrative unit (one situation, one set of people), so
the LLM reads the transcript and marks where each scene starts.

The LLM is not trusted with timing. Each boundary it proposes must coincide with
a real camera cut; on the same episode, two of its seven proposed boundaries sat
in the middle of a continuous shot and were false. Kept boundaries snap to the
*earliest* cut in the silence before the scene's first line, so a wordless
establishing shot (an exterior before an interior) opens its own scene rather
than closing the previous one, and black frames at a fade are trimmed off.
"""

from __future__ import annotations

import itertools
import json
import math
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from pydantic import BaseModel, ValidationError

from ..llm.base import LLMBackend
from ..llm.cache import LLMCache
from ..models import Scene, Scenes, Sentence
from ..render.faces import SCENE_CUT_CORRELATION, SCENE_GRID, SCENE_HIST_BINS
from ..utils.logging import get_logger

log = get_logger(__name__)

PROMPT_VERSION = "scenes-v2"

# Decoded size for the cut scan: enough for tile histograms, cheap to pipe.
SCAN_W, SCAN_H = 128, 72

# A frame this dark on average (0-255) is black: a fade or an act break.
BLACK_LEVEL = 18.0

# A cut this long before the first line still counts even with no silence
# recorded before it, covering a line that starts right on the cut.
MIN_CUT_WINDOW = 1.0
# ...and never further back than this, however long the silence.
MAX_CUT_WINDOW = 8.0

# Shots within this many seconds either side of a cut are compared by colour.
# Measured on a real episode: with 8s, three of four hand-checked scene
# changes ranked in the most dissimilar 4% of 409 cuts; with 15s, back-and-
# forth coverage of neighbouring scenes blurred them together.
LOOK_WINDOW = 8.0
# A cut in this most-dissimilar share of the episode's cuts...
LOOK_SHARE = 0.05
# ...with at least this much silence across it is a scene change even if the
# LLM did not mark one. Neither signal is reliable alone: the LLM missed a
# move from the dinner table to the kitchen ("can I talk to you in the
# kitchen?", then 2.8s of silence and a new room), and colour alone flagged
# mid-conversation angle changes.
MIN_SILENCE_AT_CHANGE = 2.5
# The LLM tends to name a scene's first line a few lines early, so its
# boundary may snap to a cut up to this long after that line.
LLM_LATE_SLACK = 5.0
# A cut this unlike everything within `LOOK_WINDOW` either side is a scene
# change even in a short pause. Checked by eye on all five FX sources: every
# cut below 0.72 changes place (office to home and back, a balloon drop to a
# bar, an end card). A podium and its audience -- one event shot both ways --
# scored 0.65 before shots overlapping the window were counted, 0.72+ after.
# A line running across the cut still vetoes it: one episode opens
# on a one-second flash of the office while the friends at home ask "What did
# you say? You said the C word in a work meeting." The flash belongs to that
# scene, and splitting there started the clip mid-word. Lines 0.08s apart
# across that cut are one exchange too, so a real pause is required.
DISTINCT_LOOK = 0.7
DISTINCT_MIN_PAUSE = 0.15
# Boundaries closer than this are one boundary.
MERGE_WITHIN = 4.0
# An LLM boundary is kept only at a cut that looks like a change of place (in
# the most dissimilar `LOOK_SHARE`) or that falls in at least this pause. On
# episode 201 the boundaries the frames confirmed sat in pauses of 1.8s or
# more; the ones cutting through a running exchange in one room ("Deuces, big
# bros." / "Paul Beaker, I'm so sorry." 0.06s apart; "I think I'm going to let
# him." / "Do it!") in 0.2s or less. At 0.5s, episode 204 still ended a clip on
# "I lied." with the reply "What? About my intention." 0.68s later.
LLM_MIN_PAUSE = 1.0

SYSTEM = """\
You split a TV episode's transcript into scenes. A scene is one continuous
situation: the same place, the same people, one conversation or event. A new
scene starts when the story cuts to a different place or time, or to a
different group of characters. Lines are numbered, with their start time and
the silence before them.

Scenes in a comedy are usually 30 seconds to 3 minutes long. Split whenever
the location changes -- another room of the same home counts, e.g. from the
dinner table to the kitchen -- or time jumps, even if the same characters carry
on. A new conversation starting after a pause is usually a new scene. When
unsure, split: a scene that is too long lets one clip run from one situation
into another.

Return a JSON array, one element per scene in order:
  {"first_line": <number of the scene's first line>, "summary": "<a few words: who and what>"}
The first scene starts at line 0."""


class _Proposal(BaseModel):
    first_line: int
    summary: str = ""


@dataclass(frozen=True)
class CutScan:
    """Every camera cut and black run in a video, in seconds."""

    cuts: list[float]
    black: list[tuple[float, float]]
    duration: float
    #: For each cut, the best colour match between any shot in the
    #: `LOOK_WINDOW` seconds before it and any shot in the same span after it.
    #: Low means nothing on one side resembles the other: a change of place.
    likeness: dict[float, float] = field(default_factory=dict)


def scan_cuts(video: Path, *, ffmpeg: str = "ffmpeg") -> CutScan:
    """Frame-accurate cuts and black runs over the whole video.

    FFmpeg decodes and downscales (it is multi-threaded and fast on HEVC, where
    OpenCV's single-threaded decode is not); frames arrive as small greyscale
    arrays and are compared with the same tiled-histogram test the face scan
    uses (`faces._is_cut`), so a "cut" means the same thing everywhere.
    """
    import cv2

    fps = _probe_fps(video, ffmpeg=ffmpeg)
    if ffmpeg == "ffmpeg":
        from ..render.ffmpeg import ffmpeg_path

        ffmpeg = str(ffmpeg_path())
    command = [ffmpeg, "-v", "error", "-i", str(video), "-an",
               "-vf", f"scale={SCAN_W}:{SCAN_H}:flags=area,format=bgr24",
               "-f", "rawvideo", "-"]
    size = SCAN_W * SCAN_H * 3
    keep_every = max(1, round(fps / 4))
    kept: list[tuple[float, np.ndarray]] = []
    cuts: list[float] = []
    black: list[tuple[float, float]] = []
    black_start: float | None = None
    previous = None
    index = 0
    with subprocess.Popen(command, stdout=subprocess.PIPE) as proc:
        assert proc.stdout is not None
        while True:
            raw = proc.stdout.read(size)
            if len(raw) < size:
                break
            colour = np.frombuffer(raw, dtype=np.uint8).reshape(SCAN_H, SCAN_W, 3)
            frame = cv2.cvtColor(colour, cv2.COLOR_BGR2GRAY)
            t = index / fps
            if index % keep_every == 0:
                kept.append((t, colour.copy()))
            tiles = _tile_histograms(frame, cv2)
            if previous is not None and _mean_correlation(previous, tiles, cv2) < SCENE_CUT_CORRELATION:
                cuts.append(t)
            previous = tiles
            if frame.mean() < BLACK_LEVEL:
                black_start = t if black_start is None else black_start
            elif black_start is not None:
                black.append((black_start, t))
                black_start = None
            index += 1
    duration = index / fps
    if black_start is not None:
        black.append((black_start, duration))
    likeness = _likeness(cuts, kept, cv2)
    log.info("cut scan: %d cuts, %d black runs over %.0fs", len(cuts), len(black), duration)
    return CutScan(cuts=cuts, black=black, duration=duration, likeness=likeness)


def _likeness(cuts: list[float], frames: list[tuple[float, np.ndarray]], cv2) -> dict[float, float]:
    """Best colour match across each cut between the shots either side of it."""
    if not cuts or not frames:
        return {}
    edges = [0.0, *cuts, frames[-1][0] + 1.0]
    shots: list[tuple[float, float, np.ndarray]] = []
    for a, b in itertools.pairwise(edges):
        inside = [f for t, f in frames if a <= t < b]
        if inside:
            sigs = [_colour_signature(f, cv2) for f in inside[::max(1, len(inside) // 4)]]
            shots.append((a, b, np.mean(sigs, axis=0)))
    out: dict[float, float] = {}
    for cut in cuts:
        # Every shot overlapping the window counts, not only those starting in
        # it: a long library shot, then a 4s insert of someone elsewhere, then
        # the library again read as a change of place because the first
        # library shot began more than `LOOK_WINDOW` before the return.
        before = [sig for a, b, sig in shots if a < cut and b > cut - LOOK_WINDOW]
        after = [sig for a, _, sig in shots if cut <= a < cut + LOOK_WINDOW]
        if before and after:
            out[cut] = max(float(np.minimum(x, y).sum()) / 2 for x in before for y in after)
    return out


def _colour_signature(frame: np.ndarray, cv2) -> np.ndarray:
    lab = cv2.cvtColor(frame, cv2.COLOR_BGR2LAB)
    chroma = cv2.calcHist([lab], [1, 2], None, [12, 12], [0, 256, 0, 256])
    light = cv2.calcHist([lab], [0], None, [16], [0, 256])
    cv2.normalize(chroma, chroma, 1, 0, cv2.NORM_L1)
    cv2.normalize(light, light, 1, 0, cv2.NORM_L1)
    return np.concatenate([chroma.ravel(), light.ravel()]).astype(np.float32)


def _probe_fps(video: Path, *, ffmpeg: str) -> float:
    from ..ingest.probe import probe

    return probe(video).fps or 30.0


def _tile_histograms(gray, cv2) -> list:
    h, w = gray.shape
    out = []
    for r in range(SCENE_GRID):
        for c in range(SCENE_GRID):
            tile = gray[r * h // SCENE_GRID:(r + 1) * h // SCENE_GRID,
                        c * w // SCENE_GRID:(c + 1) * w // SCENE_GRID]
            hist = cv2.calcHist([tile], [0], None, [SCENE_HIST_BINS], [0, 256])
            cv2.normalize(hist, hist, 0, 1, cv2.NORM_MINMAX)
            out.append(hist)
    return out


def _mean_correlation(a, b, cv2) -> float:
    return float(np.mean([cv2.compareHist(x, y, cv2.HISTCMP_CORREL) for x, y in zip(a, b, strict=True)]))


def propose_scene_starts(sentences: list[Sentence], backend: LLMBackend | list[LLMBackend],
                         *, cache: LLMCache | None = None) -> list[tuple[int, str]]:
    """The LLM's scene starts as (first sentence index, summary). [(0, "")] on failure."""
    from ..transcribe.correct import _ask

    if not sentences:
        return []
    lines = [f"[{s.index}] {int(s.start // 60)}:{s.start % 60:04.1f} "
             f"(gap {s.gap_before:.1f}s) {s.text.strip()}" for s in sentences]
    backends = backend if isinstance(backend, list) else [backend]
    answered = _ask(backends, SYSTEM, "\n".join(lines), list[_Proposal],
                    cache=cache, prompt_key=PROMPT_VERSION)
    if answered is None:
        log.warning("scene segmentation unavailable; treating the source as one scene")
        return [(0, "")]
    try:
        data = json.loads(answered[0])
    except json.JSONDecodeError:
        return [(0, "")]
    out: list[tuple[int, str]] = []
    for item in data if isinstance(data, list) else []:
        try:
            p = _Proposal.model_validate(item)
        except ValidationError:
            continue
        if 0 <= p.first_line < len(sentences):
            out.append((p.first_line, p.summary.strip()))
    out = sorted(set(out), key=lambda x: x[0])
    if not out or out[0][0] != 0:
        out.insert(0, (0, out[0][1] if out and out[0][0] == 0 else ""))
    return out


def build_scenes(source_id: str, sentences: list[Sentence],
                 proposals: list[tuple[int, str]], scan: CutScan) -> Scenes:
    """Scenes from the union of two checked signals, each landing on a real cut.

    Missing a scene change lets a clip run from one scene into another, which
    is what was reported; an extra boundary only stops a clip crossing a point
    that did not need protecting. So the rule errs towards splitting.
    """
    summaries = {0.0: proposals[0][1] if proposals else ""}
    ranked = sorted(scan.likeness.values())
    limit = ranked[max(0, int(len(ranked) * LOOK_SHARE) - 1)] if ranked else -1.0
    # A fade to black is always a transition. It had put 2.5s of black in the
    # middle of a clip, which QA then rejected.
    fades = [a for a, b in scan.black if a > 1.0 and b < scan.duration - 1.0]
    boundaries: list[float] = list(fades)

    for first, summary in proposals:
        if first == 0:
            continue
        line = sentences[first]
        window = min(MAX_CUT_WINDOW, max(MIN_CUT_WINDOW, line.gap_before + 0.25))
        near = [c for c in scan.cuts if line.start - window <= c <= line.start + LLM_LATE_SLACK]
        if not near:
            log.info("scene boundary at line %d (%.1fs) dropped: no camera cut near it",
                     first, line.start)
            continue
        # A cut inside a running exchange is a cutaway within the scene:
        # snapping there split "please don't / hurt her." between two scenes.
        plausible = [c for c in near if scan.likeness.get(c, 1.0) <= limit
                     or _silence_across(c, sentences) >= LLM_MIN_PAUSE]
        if not plausible:
            log.info("scene boundary at line %d (%.1fs) dropped: no cut near it "
                     "changes place or falls in a pause", first, line.start)
            continue
        cut = min(plausible, key=lambda c: scan.likeness.get(c, 1.0))
        boundaries.append(cut)
        summaries[cut] = summary

    if scan.likeness:
        for cut, like in scan.likeness.items():
            silence = _silence_across(cut, sentences)
            if (like < DISTINCT_LOOK and silence >= DISTINCT_MIN_PAUSE) or (
                    like <= limit and silence >= MIN_SILENCE_AT_CHANGE):
                boundaries.append(cut)

    def strength(cut: float) -> float:
        return -1.0 if cut in fades else scan.likeness.get(cut, 1.0)

    merged: list[float] = []
    for cut in sorted(set(boundaries)):
        if merged and cut - merged[-1] < MERGE_WITHIN:
            if strength(cut) < strength(merged[-1]):
                summaries.setdefault(cut, summaries.get(merged[-1], ""))
                merged[-1] = cut
            continue
        merged.append(cut)

    edges = [_after_black(0.0, scan), *[_after_black(c, scan) for c in merged]]
    scenes: list[Scene] = []
    for i, start in enumerate(edges):
        nxt = edges[i + 1] if i + 1 < len(edges) else scan.duration
        end = _before_black(nxt, start, scan)
        lines = [s.index for s in sentences if start <= (s.start + s.end) / 2 < end]
        if end - start <= 0 or not lines:
            continue
        summary = summaries.get(merged[i - 1] if i else 0.0, "")
        scenes.append(Scene(index=len(scenes), start=round(start, 3), end=round(end, 3),
                            first_sentence=lines[0], last_sentence=lines[-1],
                            summary=summary))
    log.info("%d scenes (%d LLM proposals, %d boundaries after merging)",
             len(scenes), len(proposals), len(merged))
    return Scenes(source_id=source_id, scenes=scenes)


def _silence_across(cut: float, sentences: list[Sentence]) -> float:
    """Seconds with no dialogue spanning a cut; negative if a line runs across it."""
    if any(x.start < cut - 0.3 and x.end > cut + 0.3 for x in sentences):
        return -1.0
    before = [x.end for x in sentences if x.end <= cut + 0.3]
    after = [x.start for x in sentences if x.start >= cut - 0.3]
    if not before or not after:
        # Nothing is said on one side at all: an opening title, or the
        # promo end card that closes a scene released on its own. Counting
        # that as no silence kept 17s of end card in a clip.
        return math.inf
    return min(after) - max(before)


def _after_black(t: float, scan: CutScan) -> float:
    """Move a start past black frames beginning at (or just after) it."""
    for a, b in scan.black:
        if a - 0.25 <= t < b:
            return b
    return t


def _before_black(t: float, floor: float, scan: CutScan) -> float:
    """Move an end back before black frames that run up to it (a fade-out)."""
    for a, b in scan.black:
        if a > floor and a < t <= b + 0.25:
            return a
    return t


