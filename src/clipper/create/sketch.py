"""Chalk sketches drawn for one sentence (D111): the real thing the sentence is about -- a
head in profile with sound going two ways, an ear, a straw in a glass -- rather than one of
the fixed templates filled in. Template diagrams were judged "off": three boxes saying
"vocal cords / skull bone / inner ear" don't show sound travelling through a skull.

The model draws with a few chalk marks (lines, arrows, smooth curves and closed outlines,
circles, dots, boxes, words, waves, a dot that travels along a path) on a 1000 x 600 grid,
the diagram band of the frame (create/diagrams.py draws and animates them). Then it looks
at its own drawing, rendered, and fixes what it sees: overlapping words, a shape nobody
would recognise, physics the picture gets wrong.
"""

from __future__ import annotations

import io
import json
from concurrent.futures import ThreadPoolExecutor
from typing import Literal

from pydantic import BaseModel, Field

from ..utils.logging import get_logger
from . import channel as channels
from .ai import CreateError, ask

log = get_logger(__name__)


class Mark(BaseModel):
    kind: Literal["line", "arrow", "curve", "loop", "circle", "dot", "box", "text", "wave", "mover"]
    xy: list[float] = Field(default_factory=list)
    text: str = ""
    color: Literal["chalk", "yellow", "blue", "red", "dim"] = "chalk"
    size: int = 2                 # text: 1 small, 2 medium, 3 big; wave: its height, 1-3
    dashed: bool = False
    cycles: float = 3.0           # wave: how many wiggles
    cue: str = ""                 # the sentence's word it appears on; "" = in order


class Sketch(BaseModel):
    title: str = ""
    marks: list[Mark] = Field(default_factory=list)


DRAW = """You draw quick, clear chalkboard sketches for a 45-second {subject} video on a phone, in
the style of a good lecturer: the real thing, simply drawn, a few labels, motion where things
move. One sketch shows what its sentence says, so a viewer gets it in two seconds. When it
stays up for several sentences, it builds as they go: the object first, then each part on the
word where the voice explains it, so the picture grows with the explanation and is never
finished before the voice gets there.

The board is a grid 1000 wide, 600 tall; x right, y DOWN; (0, 0) top left. Keep everything
inside x 30-970, y 30-570. Keep the bottom-right corner (x > 740 and y > 400) EMPTY: the
app's buttons cover it. With a title, keep y < 90 free for it.

Marks (xy is a flat list of numbers):
- line / arrow: straight segments through the points [x1,y1, x2,y2, ...]; arrow ends in a head.
- curve: a smooth curve through the points (a sound path bending round the head, a lens edge).
- loop: a smooth CLOSED outline through the points, in order round the shape: a head in
  profile, an ear, a lung, a bubble. 8-16 points, spaced evenly round it.
- circle: [cx, cy, r]. dot: [cx, cy]. box: [x1, y1, x2, y2].
- text: [x, y] = the centre of the words; size 1 (small), 2 (label), 3 (headline). At most 3
  words. Put labels BESIDE what they name, never on top of a line or another label; a label
  is about 26 px wide per letter at size 2, 75 px tall.
- wave: a wiggly line from [x1,y1] to [x2,y2], cycles = how many wiggles (more = higher
  pitch), size = how tall (1-3, louder = taller). It moves along by itself.
- mover: a bright dot that keeps travelling along the points [x1,y1, x2,y2, ...]: sound
  going through bone, blood flowing, a ball rolling. Draw its path separately (a dashed
  curve) if the path itself matters.
Colours: chalk (white, the default), yellow (the main idea, the path that matters), blue
(the other path or thing being compared), red (only for danger or heat), dim (guides,
outlines of background things). dashed for paths and guides.
Order the marks as they should appear: the object first, then what happens to it. Give a
mark cue = one word of the sentence(s), copied exactly, to appear when the voice says it: a
path on the word that names it ("air", "bone"), a label on its own word. Every mark after the
first few should have a cue.

Rules: 4-14 marks. Draw the physical thing, not a flow chart -- never boxes with words in
them unless the sentence is about a list. Every label short and true. Proportions right
(a head bigger than an ear canal). Physics right: arrows point the way things really go."""

REVIEW = """You see your chalk sketch rendered as it will appear on the phone (the diagram band
only). Judge it as a viewer seeing it for two seconds, against what it must show. Problems
to fix: words overlapping lines or other words, or cut off; a shape nobody would recognise
(fix the outline points); things crowded into one corner or too small; empty or cluttered;
anything in the bottom-right corner (x > 740 and y > 400 on the grid); {subject} the picture
gets wrong. If it already works, answer ok = true and return it unchanged. Otherwise ok =
false, list the problems, and return the whole corrected sketch."""


class _Review(BaseModel):
    ok: bool
    problems: list[str] = Field(default_factory=list)
    sketch: Sketch


#: Where a sketch is fitted on the grid: clear of the edges, the title, the button corner.
FIT_X, FIT_Y = (30, 970), (25, 590)
TEXT_W, TEXT_H = {1: 22, 2: 28, 3: 40}, {1: 60, 2: 75, 3: 105}
POINTS = {"line": 4, "arrow": 4, "curve": 4, "loop": 6, "circle": 3, "dot": 2, "box": 4, "text": 2, "wave": 4,
          "mover": 4}


def _extent(m: Mark) -> list[tuple[float, float]]:
    """The points a mark covers, words and circles included."""
    xy = m.xy
    if m.kind == "circle":
        x, y, r = xy[:3]
        return [(x - r, y - r), (x + r, y + r)]
    if m.kind == "text":
        half = len(m.text) * TEXT_W.get(m.size, 28) / 2
        return [(xy[0] - half, xy[1] - TEXT_H.get(m.size, 75) / 2), (xy[0] + half, xy[1] + TEXT_H.get(m.size, 75) / 2)]
    return [(xy[k], xy[k + 1]) for k in range(0, len(xy) - 1, 2)]


def fit(sketch: Sketch, title: bool = False) -> Sketch:
    """The sketch scaled and centred to fill the board (D112): drawings came back tiny in a
    corner, or in another scale altogether (0-1, or off the grid) and so invisible. Marks
    without the numbers their kind needs are dropped; too little left is a failure."""
    marks = [m for m in sketch.marks if len(m.xy) >= POINTS[m.kind] and (m.kind != "text" or m.text.strip())]
    if len(marks) < 3 or all(m.kind == "text" for m in marks):
        raise CreateError("the sketch had too little in it to draw")
    # Scaled by its shapes: the words keep their size and move with what they label.
    pts = [p for m in marks if m.kind != "text" for p in _extent(m)]
    x0, x1 = min(p[0] for p in pts), max(p[0] for p in pts)
    y0, y1 = min(p[1] for p in pts), max(p[1] for p in pts)
    top = 110 if title else FIT_Y[0]
    room_w, room_h = (FIT_X[1] - FIT_X[0]) * 0.88, (FIT_Y[1] - top) * 0.9  # a margin for the labels
    scale = min(room_w / max(x1 - x0, 1e-6), room_h / max(y1 - y0, 1e-6))
    ox = FIT_X[0] + ((FIT_X[1] - FIT_X[0]) - (x1 - x0) * scale) / 2
    oy = top + ((FIT_Y[1] - top) - (y1 - y0) * scale) / 2

    def move(m: Mark) -> Mark:
        xy = list(m.xy)
        if m.kind == "circle":
            xy = [ox + (xy[0] - x0) * scale, oy + (xy[1] - y0) * scale, xy[2] * scale]
        else:
            xy = [ox + (v - x0) * scale if k % 2 == 0 else oy + (v - y0) * scale for k, v in enumerate(xy)]
        if m.kind == "text":  # kept whole on the board
            half_w, half_h = len(m.text) * TEXT_W.get(m.size, 28) / 2, TEXT_H.get(m.size, 75) / 2
            xy = [min(max(xy[0], FIT_X[0] + half_w), FIT_X[1] - half_w), min(max(xy[1], top + half_h), FIT_Y[1] - half_h)]
        return m.model_copy(update={"xy": xy})

    return sketch.model_copy(update={"marks": [move(m) for m in marks]})


def _png(sketch: Sketch, title: str = "") -> bytes:
    """The finished sketch, the diagram band only, small: what the reviewer looks at."""
    from .diagrams import BOTTOM, TOP, W, frame
    from .script import Visual

    img = frame(Visual(kind="diagram", template="sketch", title=title or sketch.title, sketch=sketch), 99.0, 4.0)
    band = img.crop((0, TOP - 20, W, BOTTOM + 20)).resize((W // 2, (BOTTOM - TOP + 40) // 2))
    buf = io.BytesIO()
    band.save(buf, "PNG")
    return buf.getvalue()


def draw(sentence: str, idea: str, script_text: str = "", title: str = "", rounds: int = 2) -> Sketch:
    """A sketch of `idea` for `sentence`, checked by looking at it `rounds` times at most."""
    user = (f"The video's script, for context:\n{script_text}\n\n" if script_text else "") + \
        f"Sentence: {sentence}\nWhat to draw: {idea}\n" + (f"Title over it: {title}\n" if title else "")
    try:
        sketch = Sketch.model_validate(json.loads(ask(channels.fill(DRAW), user, Sketch, temperature=0.4)))
    except (ValueError, TypeError) as exc:
        raise CreateError("the sketch came back unreadable") from exc
    sketch = fit(sketch, bool(title or sketch.title))
    for _ in range(rounds):
        try:
            answer = ask(channels.fill(DRAW + "\n\n" + REVIEW), user + "\nYour sketch:\n" + sketch.model_dump_json(),
                         _Review, temperature=0.0, media=[(_png(sketch, title), "image/png")],
                         job="review", keep=True)
            review = _Review.model_validate(json.loads(answer))
        except (CreateError, ValueError, TypeError) as exc:  # no one to look: keep what we have
            log.info("create: sketch not reviewed (%s)", exc)
            break
        if review.ok or not review.sketch.marks:
            break
        log.info("create: sketch fixed: %s", "; ".join(review.problems)[:200])
        try:
            sketch = fit(review.sketch, bool(title or review.sketch.title))
        except CreateError:  # the "fix" broke it: keep the one we had
            break
    return sketch


def draw_all(script) -> tuple[object, list[str]]:
    """Every sketch beat of `script` drawn (at once: each takes the model a while); a beat
    whose sketch fails becomes stock footage with its card. Returns the script and notes."""
    from .script import spans

    beats = list(script.beats)
    todo = [i for i, b in enumerate(beats) if b.visual.kind == "diagram" and b.visual.template == "sketch"
            and not (b.visual.sketch and b.visual.sketch.marks) and not b.visual.clip]  # a clip of the user's: drawn only if needed
    notes = []

    def one(i: int):
        # A drawing held over the next sentences is drawn for all of them, so its parts can
        # arrive on their words (D124).
        v = beats[i].visual
        group = next((g for g in spans(script) if g[0] == i), [i])
        return draw(" ".join(beats[k].text for k in group), v.idea, script.text, v.title)

    with ThreadPoolExecutor(max_workers=4) as pool:
        done = list(pool.map(lambda i: (i, _safe(one, i)), todo))
    for i, sketch in done:
        v = beats[i].visual
        if sketch is None:
            notes.append(f"Sentence {i + 1}: the sketch failed; footage or a chalk card instead.")
            v = v.model_copy(update={"kind": "stock", "queries": v.queries or [beats[i].emphasis or v.idea[:30]],
                                     "card": v.card or beats[i].emphasis})
        else:
            v = v.model_copy(update={"sketch": sketch})
        beats[i] = beats[i].model_copy(update={"visual": v})
    return script.model_copy(update={"beats": beats}), notes


def _safe(fn, i):
    try:
        return fn(i)
    except CreateError as exc:
        log.warning("create: sketch %s failed (%s)", i + 1, exc)
        return None
