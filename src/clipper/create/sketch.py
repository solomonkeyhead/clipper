"""Chalk sketches drawn for one sentence (D111): the real thing the sentence is about -- a
head in profile with sound going two ways, an ear, a straw in a glass -- rather than one of
the fixed templates filled in. Template diagrams were judged "off": three boxes saying
"vocal cords / skull bone / inner ear" don't show sound travelling through a skull.

The model draws with a few chalk marks (ready-drawn icons for the objects, lines, arrows, smooth
curves and closed outlines, circles, dots, boxes, words, waves, sparks, shading, a dot that travels
along a path) on a 1000 x 900 grid, the picture panel (create/diagrams.py draws and animates them).
Then it looks at its own drawing, rendered, and fixes what it sees: overlapping words, a shape nobody
would recognise, physics the picture gets wrong.

D162: the objects come from an icon set (create/icons.py) instead of being plotted point by point (a
plotted magnet read as a bent line), and the prompt asks for one big, simple picture of the mechanism,
built up as the voice explains it.
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
    kind: Literal["line", "arrow", "curve", "loop", "circle", "dot", "box", "text", "wave", "mover", "icon", "zigzag",
                  "hatch"]
    xy: list[float] = Field(default_factory=list)
    text: str = ""                # text: the words; icon: the icon's name
    color: Literal["chalk", "yellow", "blue", "red", "dim"] = "chalk"
    size: int = 2                 # text: 1 small, 2 medium, 3 big; wave: its height, 1-3
    dashed: bool = False
    cycles: float = 3.0           # wave: how many wiggles
    cue: str = ""                 # the sentence's word it appears on; "" = in order


class Sketch(BaseModel):
    title: str = ""
    marks: list[Mark] = Field(default_factory=list)
    # The grid's height it was drawn on (1000 wide): 600 before D162, when drawings had a 640 px band of the
    # frame; 900 since, on the taller picture panel. A sketch saved without it is an old one.
    grid: int = 600


DRAW = """You draw the chalkboard sketches for a 45-second {subject} video watched on a phone.
A good sketch shows how the thing works, not what it is called: the real objects, big and simple,
and the one thing happening between them (the path, the push, the flow, the spark), labelled with a
word or two. A viewer gets it in two seconds with the sound off.

THE BOARD. A grid 1000 wide, 900 tall; x right, y DOWN; (0, 0) top left. Use the whole board: the
drawing should fill most of it, nothing smaller than a thumb on a phone (no icon under 120 wide, no
circle under r 30). Keep everything inside x 30-970, y 30-870. Keep the bottom-right corner
(x > 880 and y > 760) EMPTY: the app's buttons cover it. With a title, keep y < 90 free for it.

HOW TO BUILD IT.
1. One idea. The sentence makes one point; draw that and nothing else. Two to four things, not ten.
2. Objects are icons. Anything on the icon list below (a person, an ear, a speaker, the sun, a magnet,
   a car, a cloud, a battery...) is placed as an icon, never drawn by points. Draw your own outlines
   only for what has no icon or where the shape is the point: a cross-section, a lens, a pipe, a
   wave front, a graph line.
3. The action is the hero. What moves or happens gets the colour and the motion: a yellow arrow, a
   mover on a dashed path, a wave, a zigzag spark. Cause on the left or top, effect on the right or
   bottom, so the eye reads it in order.
4. It builds with the voice. Order the marks as they should appear: the objects first, then what
   happens to them, then the labels. Give a mark cue = one word of the sentence(s), copied exactly,
   to appear as the voice says it: a path on the word that names it ("air", "bone"), a label on its
   own word. Every mark after the first two or three has a cue, so the picture is never finished
   before the voice gets there.
5. Few words. Labels name parts the picture can't show by itself, 1-3 words each, at most 4 labels.
   Put each label BESIDE what it names, never on a line, an icon or another label; leave room: a
   label is about 26 px wide per letter at size 2, 75 px tall.
6. True. Proportions right (a head bigger than an ear canal), arrows the way things really go,
   nothing the {subject} gets wrong.

MARKS (xy is a flat list of numbers):
- icon: a ready-drawn object. text = its name from the list; xy = [cx, cy, width], width 120-420
  (the main object 260-420). Icons are line drawings about as tall as they are wide.
- line / arrow: straight segments through the points [x1,y1, x2,y2, ...]; arrow ends in a head.
- curve: a smooth curve through the points (a sound path bending round the head, a lens edge).
- loop: a smooth CLOSED outline through the points, in order round the shape: a lung, a bubble, a
  drop of water. 8-16 points, spaced evenly round it.
- hatch: shading inside a closed outline (points in order round it, as for loop): a filled region,
  the dense part of a gas, the water in a glass. Usually dim or blue, under a loop with the same points.
- circle: [cx, cy, r]. dot: [cx, cy]. box: [x1, y1, x2, y2].
- text: [x, y] = the centre of the words; size 1 (small), 2 (label), 3 (headline).
- wave: a wiggly line from [x1,y1] to [x2,y2], cycles = how many wiggles (more = higher pitch),
  size = how tall (1-3, louder = taller). It moves along by itself.
- zigzag: a crackling spark or lightning from [x1,y1] to [x2,y2].
- mover: a bright dot that keeps travelling along the points [x1,y1, x2,y2, ...]: sound going
  through bone, blood flowing, a ball rolling, a charge jumping. Draw its path separately (a dashed
  curve) when the path itself matters.
Colours: chalk (white, the default, for the objects), yellow (the main idea: the path or action that
matters), blue (the other path, or the thing being compared; water, cold), red (only for danger or
heat), dim (guides, background things, shading). dashed for paths and guides. At most three colours.

ICONS (use these exact names): {icons}

EXAMPLE, for "A speaker shoves the air, and that shove travels all the way to your ear.":
{"title": "", "marks": [
 {"kind": "icon", "text": "speaker", "xy": [190, 450, 320]},
 {"kind": "icon", "text": "ear", "xy": [820, 450, 300], "cue": "ear"},
 {"kind": "wave", "xy": [360, 450, 650, 450], "color": "yellow", "cycles": 4, "size": 2, "cue": "shoves"},
 {"kind": "arrow", "xy": [380, 640, 630, 640], "color": "yellow", "dashed": true, "cue": "travels"},
 {"kind": "text", "xy": [505, 320], "text": "air", "color": "dim", "cue": "air"}]}
Two icons for the things, the yellow wave and arrow for what happens, one label: that is the size and
the spirit. Yours shows its own sentence, laid out its own way.

Rules: 4-14 marks. Never a flow chart: boxes with words in them only when the sentence is a list."""

REVIEW = """You see your chalk sketch rendered as it will appear on the phone (the picture panel
only). Judge it as a viewer seeing it for two seconds, against what it must show. Problems to fix:
words overlapping lines, icons or other words, or cut off; a shape nobody would recognise (use an
icon, or fix the outline points); the drawing small, or crowded into one part of the board; too many
things or words; the main action not standing out; anything in the bottom-right corner (x > 880 and
y > 760 on the grid); {subject} the picture gets wrong. If it already works, answer ok = true and
return it unchanged. Otherwise ok = false, list the problems, and return the whole corrected sketch."""


class _Drawn(BaseModel):
    """A sketch as the model gives it: without the grid, which is ours to set (a model asked for it filled it
    with one endless number, D162)."""
    title: str = ""
    marks: list[Mark] = Field(default_factory=list)


class _Review(BaseModel):
    ok: bool
    problems: list[str] = Field(default_factory=list)
    sketch: _Drawn


#: Where a sketch is fitted on the grid: clear of the edges, the title, the button corner.
FIT_X, FIT_Y = (30, 970), (25, 880)
TEXT_W, TEXT_H = {1: 22, 2: 28, 3: 40}, {1: 60, 2: 75, 3: 105}
POINTS = {"line": 4, "arrow": 4, "curve": 4, "loop": 6, "circle": 3, "dot": 2, "box": 4, "text": 2, "wave": 4,
          "mover": 4, "icon": 2, "zigzag": 4, "hatch": 6}
ICON_SIZE = 160   # an icon's width on the grid when the model gives none


def _extent(m: Mark) -> list[tuple[float, float]]:
    """The points a mark covers, words and circles included."""
    xy = m.xy
    if m.kind == "circle":
        x, y, r = xy[:3]
        return [(x - r, y - r), (x + r, y + r)]
    if m.kind == "icon":
        x, y = xy[:2]
        half = (xy[2] if len(xy) >= 3 else ICON_SIZE) / 2
        return [(x - half, y - half), (x + half, y + half)]
    if m.kind == "text":
        half = len(m.text) * TEXT_W.get(m.size, 28) / 2
        return [(xy[0] - half, xy[1] - TEXT_H.get(m.size, 75) / 2), (xy[0] + half, xy[1] + TEXT_H.get(m.size, 75) / 2)]
    return [(xy[k], xy[k + 1]) for k in range(0, len(xy) - 1, 2)]


def fit(sketch: Sketch, title: bool = False) -> Sketch:
    """The sketch scaled and centred to fill the board (D112): drawings came back tiny in a
    corner, or in another scale altogether (0-1, or off the grid) and so invisible. Marks
    without the numbers their kind needs are dropped; too little left is a failure."""
    from . import icons

    # An icon there's no such drawing of: its name written instead, so the board still says it.
    marks = [m.model_copy(update={"kind": "text", "xy": m.xy[:2], "text": " ".join(m.text.replace("-", " ").split()[:3])})
             if m.kind == "icon" and not icons.name_for(m.text) else m for m in sketch.marks]
    marks = [m for m in marks if len(m.xy) >= POINTS[m.kind] and (m.kind not in ("text", "icon") or m.text.strip())]
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
        if m.kind in ("circle", "icon"):
            xy = [ox + (xy[0] - x0) * scale, oy + (xy[1] - y0) * scale, (xy[2] if len(xy) >= 3 else ICON_SIZE) * scale]
        else:
            xy = [ox + (v - x0) * scale if k % 2 == 0 else oy + (v - y0) * scale for k, v in enumerate(xy)]
        if m.kind == "text":  # kept whole on the board
            half_w, half_h = len(m.text) * TEXT_W.get(m.size, 28) / 2, TEXT_H.get(m.size, 75) / 2
            xy = [min(max(xy[0], FIT_X[0] + half_w), FIT_X[1] - half_w), min(max(xy[1], top + half_h), FIT_Y[1] - half_h)]
        return m.model_copy(update={"xy": xy})

    return sketch.model_copy(update={"marks": [move(m) for m in marks], "grid": 900})


def _png(sketch: Sketch, title: str = "") -> bytes:
    """The finished sketch, the picture panel, small: what the reviewer looks at."""
    from .diagrams import H, W, frame
    from .script import Visual

    img = frame(Visual(kind="diagram", template="sketch", title=title or sketch.title, sketch=sketch), 99.0, 4.0)
    band = img.resize((W // 2, H // 2))
    buf = io.BytesIO()
    band.save(buf, "PNG")
    return buf.getvalue()


def _prompt(text: str) -> str:
    from . import icons

    return channels.fill(text).replace("{icons}", ", ".join(icons.OFFERED))


def draw(sentence: str, idea: str, script_text: str = "", title: str = "", rounds: int = 2) -> Sketch:
    """A sketch of `idea` for `sentence`, checked by looking at it `rounds` times at most."""
    user = (f"The video's script, for context:\n{script_text}\n\n" if script_text else "") + \
        f"Sentence: {sentence}\nWhat to draw: {idea}\n" + (f"Title over it: {title}\n" if title else "")
    try:
        sketch = Sketch.model_validate(json.loads(ask(_prompt(DRAW), user, _Drawn, temperature=0.4, job="sketch")))
    except (ValueError, TypeError) as exc:
        raise CreateError("the sketch came back unreadable") from exc
    sketch = fit(sketch, bool(title or sketch.title))
    for _ in range(rounds):
        try:
            answer = ask(_prompt(DRAW + "\n\n" + REVIEW), user + "\nYour sketch:\n" + sketch.model_dump_json(exclude={"grid"}),
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
            sketch = fit(Sketch(**review.sketch.model_dump()), bool(title or review.sketch.title))
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
