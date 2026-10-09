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
import math
from concurrent.futures import ThreadPoolExecutor
from typing import Literal

import numpy as np
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
a shape nobody would recognise (use an icon, or fix the outline points); the drawing small, or
crowded into one part of the board; too many things or words; the main action not standing out; a
label naming the wrong thing, or with no room near what it names; {subject} the picture gets wrong.
The drawing is fitted to the board after you, kept out of the bottom-right corner, and its labels
moved off lines and each other: don't spend a fix on those. If it works, answer ok = true and nothing
else. Otherwise ok = false, list the problems, and return the whole corrected sketch."""


class _Drawn(BaseModel):
    """A sketch as the model gives it: without the grid, which is ours to set (a model asked for it filled it
    with one endless number, D162)."""
    title: str = ""
    marks: list[Mark] = Field(default_factory=list)


class _Review(BaseModel):
    ok: bool
    problems: list[str] = Field(default_factory=list)
    sketch: _Drawn = Field(default_factory=_Drawn)   # left out when ok: echoing it back was most of the answer (D182)


#: Where a sketch is fitted on the grid: clear of the edges, the title, the button corner.
FIT_X, FIT_Y = (30, 970), (25, 880)
TEXT_W, TEXT_H = {1: 22, 2: 28, 3: 40}, {1: 60, 2: 75, 3: 105}
TEXT_MAX = 640    # diagrams.text shrinks wider words to this
#: The app's buttons cover the board's bottom-right corner (x > 880 and y > 760): nothing is drawn there.
CORNER = (880, 760)
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
        half = min(len(m.text) * TEXT_W.get(m.size, 28), TEXT_MAX) / 2
        return [(xy[0] - half, xy[1] - TEXT_H.get(m.size, 75) / 2), (xy[0] + half, xy[1] + TEXT_H.get(m.size, 75) / 2)]
    return [(xy[k], xy[k + 1]) for k in range(0, len(xy) - 1, 2)]


def _covered(marks: list[Mark]) -> tuple[np.ndarray, list[tuple[float, float, float, float]]]:
    """What the shapes cover on the board: discs (x, y, r) every few px along each line as it is drawn (curves
    smoothed, a wave's band, a spark's jags, an arrow's head) and the icons' squares."""
    from .diagrams import _dense, _smooth

    discs, boxes = [], []
    for m in marks:
        pts = [(m.xy[k], m.xy[k + 1]) for k in range(0, len(m.xy) - 1, 2)]
        if m.kind == "text":
            continue
        if m.kind == "icon":
            (x0, y0), (x1, y1) = _extent(m)
            inset = (x1 - x0) * 0.08   # line drawings: the square's rim is mostly board
            boxes.append((x0 + inset, y0 + inset, x1 - inset, y1 - inset))
            continue
        if m.kind == "circle":
            (x, y), r = pts[0], m.xy[2]
            line = [(x + r * math.cos(a * math.pi / 18), y + r * math.sin(a * math.pi / 18)) for a in range(37)]
        elif m.kind == "box":
            (x0, y0), (x1, y1) = pts[:2]
            line = [(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)]
        elif m.kind in ("curve", "loop", "hatch", "mover"):
            line = _smooth(pts, closed=m.kind in ("loop", "hatch"))
        elif m.kind in ("wave", "zigzag"):
            line = [pts[0], pts[1 if m.kind == "wave" else -1]]
        else:
            line = pts
        r = 5 + {"wave": {1: 14, 2: 26, 3: 42}.get(m.size, 26), "zigzag": 26, "mover": 17, "dot": 16}.get(m.kind, 0)
        discs += [(x, y, r) for x, y in _dense(line, 8.0)]
        if m.kind == "arrow":
            discs.append((*pts[-1], 25))
    return np.array(discs, dtype=float).reshape(-1, 3), boxes


def _hits(box: tuple[float, float, float, float], discs: np.ndarray, boxes: list, pad: float = 6.0) -> bool:
    x0, y0, x1, y1 = box
    if len(discs):
        dx = np.maximum(np.maximum(x0 - discs[:, 0], discs[:, 0] - x1), 0)
        dy = np.maximum(np.maximum(y0 - discs[:, 1], discs[:, 1] - y1), 0)
        if (dx * dx + dy * dy < (discs[:, 2] + pad) ** 2).any():
            return True
    return any(x0 - pad < b[2] and b[0] < x1 + pad and y0 - pad < b[3] and b[1] < y1 + pad for b in boxes)


def _corner_scale(marks: list[Mark], px: float, py: float) -> float:
    """How far the drawing must shrink about (px, py) so no shape reaches into the button corner: 1 when none does.
    Each shape's part in the corner clears it when it is left of it or above it, whichever comes first."""
    discs, boxes = _covered(marks)
    parts = [*discs.tolist(), *([b[2], b[3], 0.0] for b in boxes)]   # a line's width stays, an icon shrinks
    need = [max((CORNER[0] - r - px) / (x - px) if x > px else 1.0, (CORNER[1] - r - py) / (y - py) if y > py else 1.0)
            for x, y, r in parts if x + r > CORNER[0] and y + r > CORNER[1]]
    return max(0.6, min([1.0, *need]))


def _letters(m: Mark) -> tuple[float, float, float, float]:
    """Where a label's letters and their rim of board are, about its centre, as diagrams.text writes them."""
    from .diagrams import font_for

    s, size = " ".join(m.text.split()), {1: 56, 2: 70, 3: 100}.get(m.size, 70)
    while size > 30 and font_for(s, size).getlength(s) > TEXT_MAX:
        size -= 4
    x0, y0, x1, y1 = font_for(s, size).getbbox(s, anchor="mm")
    rim = max(4, size // 9)
    return x0 - rim, y0 - rim, x1 + rim, y1 + rim


def _unclutter(marks: list[Mark], top: float) -> list[Mark]:
    """Each label moved to the nearest clear place (D182) when its letters sit on a line, an icon, another label
    or the button corner, no further than 120 px (further, it would leave what it names: the reviewer sees it);
    one in a box stays (the box is its frame). The reviewer spent half its fixes on exactly this, each another call."""
    discs, boxes = _covered(marks)
    boxes.append((*CORNER, 1000.0, 900.0))
    frames = [m.xy[:4] for m in marks if m.kind == "box"]
    out = list(marks)
    for i, m in enumerate(marks):
        if m.kind != "text":
            continue
        (lx0, ly0, lx1, ly1), (cx, cy) = _letters(m), m.xy[:2]

        def box(x, y, b=(lx0, ly0, lx1, ly1)):
            return x + b[0], y + b[1], x + b[2], y + b[3]

        if not any(min(f[0], f[2]) < cx < max(f[0], f[2]) and min(f[1], f[3]) < cy < max(f[1], f[3]) for f in frames):
            rings = ((r, 2 * math.pi * k / max(8, round(r / 4))) for r in range(0, 121, 8) for k in range(max(8, round(r / 4))))
            spots = ((min(max(cx + r * math.cos(a), FIT_X[0] - lx0), FIT_X[1] - lx1),
                      min(max(cy + r * math.sin(a), top - ly0), FIT_Y[1] - ly1)) for r, a in rings)
            cx, cy = next((s for s in spots if not _hits(box(*s), discs, boxes)), (cx, cy))
            out[i] = m.model_copy(update={"xy": [cx, cy]})
        boxes.append(box(cx, cy))
    return out


def fit(sketch: Sketch, title: bool = False) -> Sketch:
    """The sketch scaled and centred to fill the board (D112): drawings came back tiny in a
    corner, or in another scale altogether (0-1, or off the grid) and so invisible. Marks
    without the numbers their kind needs are dropped; too little left is a failure. Then it is
    shrunk towards the top until the button corner is clear, and its labels moved off what they
    sit on (D182)."""
    from . import icons

    # An icon there's no such drawing of: its name written instead, so the board still says it.
    marks = [m.model_copy(update={"kind": "text", "xy": m.xy[:2], "text": " ".join(m.text.replace("-", " ").split()[:3])})
             if m.kind == "icon" and not icons.name_for(m.text) else m for m in sketch.marks]
    marks = [m for m in marks if len(m.xy) >= POINTS[m.kind] and (m.kind not in ("text", "icon") or m.text.strip())]
    if len(marks) < 3 or all(m.kind == "text" for m in marks):
        raise CreateError("the sketch had too little in it to draw")
    marks = _icons_at_least(marks)
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

    px, py = (FIT_X[0] + FIT_X[1]) / 2, top
    if (s := _corner_scale([move(m) for m in marks], px, py)) < 1:
        scale, ox, oy = scale * s, px + (ox - px) * s, py + (oy - py) * s
    return sketch.model_copy(update={"marks": _unclutter([move(m) for m in marks], top), "grid": 900})


#: The smallest an icon is drawn, as a share of the drawing's size (D170): asked for 260-420 wide on the 1000
#: grid, models gave 120-170, and a magnet and a paperclip at either end of a long arrow were thumbnails on a phone.
ICON_SHARE = 0.34   # 0.34 of the span, before fitting: about 210-280 wide on the board


def _icons_at_least(marks: list[Mark]) -> list[Mark]:
    """Each icon grown to ICON_SHARE of the drawing's span, before it is fitted to the board, and kept clear of the
    icons beside it (at most 0.9 of the way to the nearest one's centre, so two never meet)."""
    centres = [(i, m.xy[0], m.xy[1]) for i, m in enumerate(marks) if m.kind == "icon" and len(m.xy) >= 2]
    if not centres:
        return marks
    pts = [p for m in marks if m.kind != "text" for p in _extent(m)]
    span = max(max(p[0] for p in pts) - min(p[0] for p in pts), max(p[1] for p in pts) - min(p[1] for p in pts))
    out = list(marks)
    for i, x, y in centres:
        w = out[i].xy[2] if len(out[i].xy) >= 3 else ICON_SIZE
        gap = min((((x - x2) ** 2 + (y - y2) ** 2) ** 0.5 for j, x2, y2 in centres if j != i), default=float("inf"))
        out[i] = out[i].model_copy(update={"xy": [x, y, max(w, min(ICON_SHARE * span, 0.9 * gap))]})
    return out


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


def draw(sentence: str, idea: str, script_text: str = "", title: str = "", rounds: int = 2, keep: bool = False) -> Sketch:
    """A sketch of `idea` for `sentence`, checked by looking at it `rounds` times at most. `keep`: the same
    question gets the same drawing, for one the build doesn't save in the script (D182)."""
    user = (f"The video's script, for context:\n{script_text}\n\n" if script_text else "") + \
        f"Sentence: {sentence}\nWhat to draw: {idea}\n" + (f"Title over it: {title}\n" if title else "")
    try:
        sketch = Sketch.model_validate(json.loads(ask(_prompt(DRAW), user, _Drawn, temperature=0.4, job="sketch",
                                                      keep=keep)))
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
        if review.ok or not review.sketch.marks:   # ok, or a "fix" with nothing in it: keep what we have
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
