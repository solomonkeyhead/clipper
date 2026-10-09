"""Animated physics diagrams, drawn on a chalkboard (the Professor's lecture hall).

Ten templates the script fills in (create/script.py Visual): forces, circle,
equation, compare, chain, graph, wave, particles, ray, number; and the card, a
phrase chalked big when no footage fits. Each is drawn frame by frame with Pillow and
encoded to a clip exactly as long as its sentence: the pieces arrive over the
first 60% of it, one after another, then hold, with the moving parts (a dot
on its circle, a graph's tip) still moving. A drawing fills the picture panel
at the top of the frame (create/compose.py, D162: 1080x1120, the captions and
the Professor on the board below it), between y TOP and BOTTOM: under the
platform's top bar, clear of the panel's edge.
"""

from __future__ import annotations

import contextlib
import contextvars
import itertools
import math
import random
import re
import string
from functools import cache
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from ..paths import REPO_ROOT
from .script import Visual

W, H, FPS = 1080, 1120, 30   # the picture panel (D162); was the whole 1080x1920 frame, drawings in y 400-1040
TOP, BOTTOM = 150, 1075
BOARD = (28, 44, 40)
CHALK = (238, 236, 226)
YELLOW = (255, 210, 70)
BLUE = (140, 195, 255)
DIM = (150, 160, 150)
SMUDGE = (60, 76, 70)

#: Board colours a channel can choose (D156): board, chalk, the two accents, dim chalk and the smudges of old
#: lessons. "slate" is the board as it always was.
PALETTES = {
    "slate": {"board": (28, 44, 40), "chalk": (238, 236, 226), "yellow": (255, 210, 70), "blue": (140, 195, 255),
              "dim": (150, 160, 150), "smudge": (60, 76, 70)},
    # The second report's: one accent only, so the key part is the only colour on the board.
    "one_accent": {"board": (30, 42, 38), "chalk": (242, 240, 230), "yellow": (242, 201, 76), "blue": (200, 206, 196),
                   "dim": (150, 160, 150), "smudge": (60, 74, 68)},
    # Blueprint blue, the profile picture's colour.
    "blueprint": {"board": (22, 52, 92), "chalk": (236, 243, 252), "yellow": (255, 206, 84), "blue": (150, 210, 255),
                  "dim": (140, 168, 200), "smudge": (44, 78, 122)},
    # A black school board.
    "blackboard": {"board": (24, 24, 26), "chalk": (240, 240, 236), "yellow": (255, 214, 90), "blue": (130, 190, 255),
                   "dim": (150, 150, 150), "smudge": (56, 56, 60)},
}


def use_palette(name: str) -> None:
    """Draw on the board `name` from here on (one build at a time draws, so module colours are enough)."""
    global BOARD, CHALK, YELLOW, BLUE, DIM, SMUDGE
    c = PALETTES.get(name) or PALETTES["slate"]
    if (c["board"], c["chalk"]) == (BOARD, CHALK) and c["yellow"] == YELLOW and c["blue"] == BLUE:
        return
    BOARD, CHALK, YELLOW, BLUE, DIM, SMUDGE = c["board"], c["chalk"], c["yellow"], c["blue"], c["dim"], c["smudge"]
    COLORS.update({"chalk": CHALK, "yellow": YELLOW, "blue": BLUE, "dim": DIM})
    board.cache_clear()
FONT = REPO_ROOT / "assets" / "fonts" / "Caveat.ttf"
#: For what the handwriting has no letter for (Greek, most maths): Clipper's own Inter.
PLAIN = REPO_ROOT / "assets" / "fonts" / "Inter.ttf"
#: Letters Caveat has: plain ASCII and a few maths and typographic marks
#: (degree, squared, cubed, times, divide, minus, middle dot, curly quotes).
CAVEAT_HAS = set(string.printable) | set(map(chr, (0xB0, 0xB2, 0xB3, 0xD7, 0xF7, 0x2212, 0xB7, 0x2019, 0x201C, 0x201D)))


@cache
def font(size: int, bold: bool = True, plain: bool = False) -> ImageFont.FreeTypeFont:
    f = ImageFont.truetype(str(PLAIN if plain else FONT), int(size * 0.8) if plain else size)
    if bold:
        with contextlib.suppress(OSError, ValueError):
            f.set_variation_by_name("Bold")
    f.made = (size, bold, plain)   # so the ink layer can make it bigger (D162)
    return f


def font_for(s: str, size: int) -> ImageFont.FreeTypeFont:
    """The chalk hand, or plain type when the text has a letter the hand can't write."""
    return font(size, plain=not set(s) <= CAVEAT_HAS)


@cache
def board(size: tuple[int, int] = (W, H)) -> Image.Image:
    """The chalkboard: slate with grain, faint smudges of old lessons and a soft vignette."""
    w, h = size
    rnd = random.Random(7)
    img = Image.new("RGB", (w, h), BOARD)
    noise = Image.effect_noise((w, h), 18).convert("L")
    img = Image.composite(Image.new("RGB", (w, h), tuple(c + 10 for c in BOARD)), img, noise.point(lambda v: v // 6))
    smudge = Image.new("L", (w, h), 0)
    d = ImageDraw.Draw(smudge)
    for _ in range(round(9 * h / 1920) + 4):
        x, y, r = rnd.randint(0, w), rnd.randint(0, h), rnd.randint(120, 380)
        d.ellipse((x - r, y - r * 0.6, x + r, y + r * 0.6), fill=rnd.randint(10, 22))
    smudge = smudge.filter(ImageFilter.GaussianBlur(60))
    img = Image.composite(Image.new("RGB", (w, h), SMUDGE), img, smudge)
    # Darker toward the corners, so the eye goes to the middle (D162).
    vignette = Image.radial_gradient("L").resize((w, h)).point(lambda v: min(255, int(v * 0.55)))
    return Image.composite(Image.new("RGB", (w, h), tuple(max(0, c - 14) for c in BOARD)), img, vignette)


def ease(x: float) -> float:
    x = min(1.0, max(0.0, x))
    return 1 - (1 - x) ** 3


#: When each label's words are spoken, seconds into the shot (None: not said), for the
#: frame being drawn; set by render().
CUES: contextvars.ContextVar[tuple[float | None, ...]] = contextvars.ContextVar("cues", default=())


def stage(t: float, d: float, i: int, n: int, label: int | None = None) -> float:
    """How far element `i` of `n` has arrived at time `t`: they come in turn over 60% of `d`;
    or, for the element showing label `label`, just as the voice says it (D110: pieces
    drawn on a fixed schedule ran ahead of, or behind, the words)."""
    cues = CUES.get()
    if label is not None and label < len(cues) and cues[label] is not None:
        # On its word, but up for at least the last 1.2 s (a cue said at the very end left the
        # board empty); in a drawing held over several sentences, parts arrive late on purpose (D124).
        return ease((t - min(cues[label], max(0.6 * d, d - 1.2)) + 0.15) / 0.45)
    window = 0.6 * d
    each = window / max(1, n)
    return ease((t - i * each * 0.85) / max(0.25, each))


def _stem(word: str) -> str:
    return re.sub(r"[^a-z0-9]", "", word.lower())[:5]


def cues(v: Visual, words: list[tuple[float, str]]) -> list[float | None]:
    """When each label is first said in the shot's words ((start, text) pairs, seconds into
    the shot): the earliest word sharing a stem with one of the label's words of 3+ letters,
    never before the label ahead of it, so pieces still arrive in order."""
    spoken = [(at, _stem(w)) for at, w in words if len(_stem(w)) >= 3]
    out: list[float | None] = []
    last = 0.0
    labels = [m.cue for m in v.sketch.marks] if v.template == "sketch" and v.sketch else v.labels
    for label in labels:
        keys = {_stem(w) for w in label.split() if len(_stem(w)) >= 3}
        hit = next((at for at, w in spoken if at >= last and any(w.startswith(k) or k.startswith(w) for k in keys)), None)
        out.append(hit)
        if hit is not None:
            last = hit
    return out


def text(draw: ImageDraw.ImageDraw, xy: tuple[float, float], s: str, size: int, fill=None,
         anchor: str = "mm", max_width: int = 900, reveal: float = 1.0, halo: bool = False) -> None:
    """Chalk text, shrunk to fit, written on letter by letter as `reveal` goes 0 to 1; with
    `halo`, a rim of board round the letters so a line behind them never crosses them."""
    s = " ".join(s.split())  # one line: a label with a line break in it broke the drawing
    if reveal <= 0 or not s:
        return
    fill = fill or CHALK   # read now, not at import: the board's colours can change (D156)
    while size > 30 and draw.textlength(s, font=font_for(s, size)) > max_width:
        size -= 4
    f = font_for(s, size)
    shown = s[: max(1, round(len(s) * min(1.0, reveal)))]
    rim = {"stroke_width": max(4, size // 9), "stroke_fill": BOARD} if halo else {}
    if anchor == "mm" and shown != s:  # keep the full line's position while it's written
        full = draw.textlength(s, font=f)
        draw.text((xy[0] - full / 2, xy[1]), shown, font=f, fill=fill, anchor="lm", **rim)
    else:
        draw.text(xy, shown, font=f, fill=fill, anchor=anchor, **rim)


def arrow(draw: ImageDraw.ImageDraw, a: tuple[float, float], b: tuple[float, float], color, width: int = 12,
          head: int = 42) -> None:
    ang = math.atan2(b[1] - a[1], b[0] - a[0])
    length = math.hypot(b[0] - a[0], b[1] - a[1])
    if length < 4:
        return
    head = min(head, length * 0.6)
    base = (b[0] - head * math.cos(ang), b[1] - head * math.sin(ang))
    draw.line([a, base], fill=color, width=width, joint="curve")
    left = (base[0] + head * 0.55 * math.cos(ang + math.pi / 2), base[1] + head * 0.55 * math.sin(ang + math.pi / 2))
    right = (base[0] + head * 0.55 * math.cos(ang - math.pi / 2), base[1] + head * 0.55 * math.sin(ang - math.pi / 2))
    draw.polygon([b, left, right], fill=color)


def title(draw: ImageDraw.ImageDraw, v: Visual, p: float) -> None:
    if v.title:
        text(draw, (W / 2, TOP + 50), v.title, 96, YELLOW, reveal=p)
        if p > 0.6:
            span = min(760, draw.textlength(v.title, font=font(96)))
            draw.line([(W / 2 - span / 2, TOP + 105), (W / 2 - span / 2 + span * ease((p - 0.6) / 0.4), TOP + 105)],
                      fill=YELLOW, width=6)


DIRS = {"up": (0, -1), "down": (0, 1), "left": (-1, 0), "right": (1, 0)}


def forces(draw, v: Visual, t: float, d: float) -> None:
    labels = v.labels[:4] or ["force"]
    dirs = [(v.directions[i] if i < len(v.directions) else "up").lower() for i in range(len(labels))]
    cx, cy = W / 2, (TOP + 380) if v.title else (TOP + 320)
    # Room for each arrow: inside the board, below the title, above the captions.
    room = {"up": cy - 100 - (TOP + (150 if v.title else 30)), "down": BOTTOM - (cy + 100),
            "left": cx - 100 - 200, "right": cx - 100 - 200}
    top = max(v.values[: len(labels)] or [1.0]) or 1.0
    want = [140 + 180 * max(0.2, (v.values[i] / top) if i < len(v.values) else 1.0) for i in range(len(labels))]
    # One scale for all, so the arrows keep their true proportions.
    scale = min([1.0] + [room.get(dirs[i], 300) / want[i] for i in range(len(labels))])
    title(draw, v, stage(t, d, 0, len(labels) + 2))
    p0 = stage(t, d, 1 if v.title else 0, len(labels) + 2)
    s = 90 * p0
    draw.rounded_rectangle((cx - s, cy - s, cx + s, cy + s), radius=24, outline=CHALK, width=10)
    # What the forces act on, written in its box (an empty square read as "a box").
    text(draw, (cx, cy), v.subject, 52, CHALK, max_width=160, reveal=(p0 - 0.6) / 0.4)
    for i, label in enumerate(labels):
        p = stage(t, d, i + 2, len(labels) + 2, label=i)
        dx, dy = DIRS.get(dirs[i], (0, -1))
        length = want[i] * scale * p
        start = (cx + dx * 100, cy + dy * 100)
        tip = (start[0] + dx * length, start[1] + dy * length)
        color = YELLOW if i == 0 else (BLUE if i == 1 else CHALK)
        arrow(draw, start, tip, color, width=14, head=48)
        if p > 0.5:
            if dy:  # beside an up or down arrow, never over the title or the captions
                text(draw, (start[0] + 45, (start[1] + tip[1]) / 2), label, 72, color, anchor="lm",
                     max_width=420, reveal=(p - 0.5) * 2)
            else:   # under a sideways one, clear of the box
                text(draw, ((start[0] + tip[0]) / 2 + dx * 40, cy + 150), label, 68, color,
                     max_width=360, reveal=(p - 0.5) * 2)


def circle(draw, v: Visual, t: float, d: float) -> None:
    cx, cy, r = W / 2, (TOP + 300) if v.title else (TOP + 260), 195
    title(draw, v, stage(t, d, 0, 4))
    p = stage(t, d, 1, 4)
    for k in range(48):  # a dashed orbit, drawn round as it arrives
        if k / 48 > p:
            break
        a0, a1 = 2 * math.pi * k / 48, 2 * math.pi * (k + 0.5) / 48
        draw.arc((cx - r, cy - r, cx + r, cy + r), math.degrees(a0), math.degrees(a1), fill=DIM, width=6)
    if p < 0.3:
        return
    ang = -math.pi / 2 + 2 * math.pi * t / 2.6
    x, y = cx + r * math.cos(ang), cy + r * math.sin(ang)
    pv, pi = stage(t, d, 2, 4, label=0), stage(t, d, 3, 4, label=1)
    tang = (-math.sin(ang), math.cos(ang))
    arrow(draw, (x, y), (x + tang[0] * 230 * pv, y + tang[1] * 230 * pv), YELLOW, width=12)
    arrow(draw, (x, y), (x + (cx - x) * 0.6 * pi, y + (cy - y) * 0.6 * pi), BLUE, width=12)
    draw.ellipse((x - 34, y - 34, x + 34, y + 34), fill=CHALK)
    mover, pull = [*v.labels, "", "pull inward"][:2]
    if mover and p > 0.6:  # what goes round, keyed at the centre, clear of the moving arrows
        draw.ellipse((cx - 150, cy - 14, cx - 122, cy + 14), fill=CHALK)
        text(draw, (cx - 105, cy), mover, 56, CHALK, anchor="lm", max_width=270, reveal=(p - 0.6) / 0.4)
    # The legend says what each arrow is: the yellow one is always the motion.
    for i, (label, color) in enumerate(zip(("direction of motion", pull or "pull inward"), (YELLOW, BLUE),
                                           strict=True)):
        q = (pv, pi)[i]
        if q > 0.3:
            y0 = cy + r + 120 + i * 66   # just under the orbit: at the bottom it kept the drawing small (D162)
            draw.line([(250, y0), (330, y0)], fill=color, width=10)
            text(draw, (360, y0), label, 62, color, anchor="lm", max_width=600, reveal=q)


def equation(draw, v: Visual, t: float, d: float) -> None:
    tokens = (v.equation or v.title or "F = m x a").split()
    labels = v.labels[:4]
    n = len(tokens) + len(labels)
    shown = [tok for i, tok in enumerate(tokens) if stage(t, d, i, n) > 0.2]
    size = 150

    def width(at: int) -> float:
        return sum(draw.textlength(tok + " ", font=font_for(tok, at)) for tok in tokens)

    while size > 60 and width(size) > 940:
        size -= 6
    x = W / 2 - width(size) / 2
    for tok in shown:
        draw.text((x, TOP + 150), tok, font=font_for(tok, size), fill=CHALK, anchor="lm")
        x += draw.textlength(tok + " ", font=font_for(tok, size))
    if len(shown) == len(tokens):
        span = width(size)
        draw.line([(W / 2 - span / 2, TOP + 245), (W / 2 + span / 2, TOP + 245)], fill=YELLOW, width=6)
    for i, label in enumerate(labels):
        p = stage(t, d, len(tokens) + i, n, label=i)
        text(draw, (W / 2, TOP + 340 + i * 90), label, 72, YELLOW if i == 0 else CHALK, reveal=p)


def compare(draw, v: Visual, t: float, d: float) -> None:
    labels = [*v.labels, "A", "B"][:2]
    values = [*v.values, 1.0, 0.5][:2]
    top = max(values) or 1.0
    title(draw, v, stage(t, d, 0, 3))
    base = BOTTOM - 70
    for i, (label, value) in enumerate(zip(labels, values, strict=True)):
        p = stage(t, d, i + 1, 3, label=i)
        x = 330 + i * 420
        height = 400 * (value / top) * p
        color = YELLOW if i == 0 else BLUE
        draw.rectangle((x - 110, base - height, x + 110, base), fill=tuple(int(c * 0.35) + 20 for c in color),
                       outline=color, width=8)
        text(draw, (x, base + 50), label, 74, color, max_width=380, reveal=p)
    draw.line([(150, base), (W - 150, base)], fill=CHALK, width=6)


def chain(draw, v: Visual, t: float, d: float) -> None:
    steps = v.labels[:4] or ["cause", "effect"]
    n = len(steps)
    first = TOP + (140 if v.title else 10)
    gap = 55
    box = min(150, (BOTTOM - first - (n - 1) * gap) / n)
    total = n * box + (n - 1) * gap
    y = first + (BOTTOM - first - total) / 2
    title(draw, v, stage(t, d, 0, n + 1))
    for i, step in enumerate(steps):
        p = stage(t, d, i + (1 if v.title else 0), n + (1 if v.title else 0), label=i)
        if p <= 0:  # not said yet; a later step said already still shows, in its place
            y += box + gap
            continue
        color = YELLOW if i == n - 1 else CHALK
        draw.rounded_rectangle((150, y, W - 150, y + box), radius=28, outline=color, width=8)
        text(draw, (W / 2, y + box / 2), step, 80, color, max_width=720, reveal=p)
        if i < n - 1 and p > 0.7:
            arrow(draw, (W / 2, y + box + 10), (W / 2, y + box + gap - 10), DIM, width=10, head=34)
        y += box + gap


SHAPES = {
    "rising": lambda x: x ** 2,
    "falling": lambda x: math.exp(-3.2 * x),
    "peak": lambda x: math.exp(-(((x - 0.5) / 0.17) ** 2)),
    "wave": lambda x: 0.5 + 0.42 * math.sin(4 * math.pi * x),
}


def graph(draw, v: Visual, t: float, d: float) -> None:
    title(draw, v, stage(t, d, 0, 3))
    ox, oy, x1, y1 = 200, BOTTOM - 60, 920, TOP + 130
    pa = stage(t, d, 1, 3)
    arrow(draw, (ox, oy), (ox + (x1 - ox) * pa, oy), CHALK, width=8, head=30)
    arrow(draw, (ox, oy), (ox, oy - (oy - y1) * pa), CHALK, width=8, head=30)
    labels = [*v.labels, "", ""][:2]
    text(draw, ((ox + x1) / 2, oy + 45), labels[0], 68, CHALK, reveal=pa)
    if pa > 0.5 and labels[1]:
        layer = Image.new("RGBA", (700, 120), (0, 0, 0, 0))
        ImageDraw.Draw(layer).text((350, 60), labels[1], font=font_for(labels[1], 68), fill=CHALK, anchor="mm")
        upright = layer.rotate(90, expand=True)
        draw._image.paste(upright, (ox - 140, int((oy + y1) / 2) - 350), upright)
    pc = stage(t, d, 2, 3)
    f = SHAPES.get(v.shape, SHAPES["rising"])
    pts = [(ox + 30 + (x1 - ox - 80) * k / 120, oy - 30 - (oy - y1 - 80) * f(k / 120)) for k in range(int(120 * pc) + 1)]
    if len(pts) > 1:
        draw.line(pts, fill=YELLOW, width=10, joint="curve")
        tx, ty = pts[-1]
        draw.ellipse((tx - 16, ty - 16, tx + 16, ty + 16), fill=YELLOW)


def card(draw, v: Visual, t: float, d: float) -> None:
    """A phrase chalked big on the board: for a sentence no footage shows."""
    phrase = (v.title or "").strip()
    p = stage(t, d, 0, 1)
    text(draw, (W / 2, (TOP + BOTTOM) / 2 - 40), phrase, 150, CHALK, max_width=900, reveal=p)
    if p > 0.7:
        span = min(860, draw.textlength(phrase, font=font_for(phrase, 150)))
        y = (TOP + BOTTOM) / 2 + 60
        draw.line([(W / 2 - span / 2, y), (W / 2 - span / 2 + span * ease((p - 0.7) / 0.3), y)], fill=YELLOW, width=8)


def wave(draw, v: Visual, t: float, d: float) -> None:
    """One or two waves travelling right: values = how often each one wiggles (frequency),
    amounts = how tall (amplitude). Two waves stack, for "high note vs low note"."""
    labels = v.labels[:2] or [""]
    n = len(labels)
    freqs = [*v.values[:n], *[1.0] * n][:n]
    amps = [*v.amounts[:n], *[1.0] * n][:n]
    top_f, top_a = max(freqs) or 1.0, max(amps) or 1.0
    title(draw, v, stage(t, d, 0, n + 1))
    first = TOP + (150 if v.title else 40)
    lane = (BOTTOM - 20 - first) / n
    x0, x1 = 150, W - 150
    for i, label in enumerate(labels):
        p = stage(t, d, i + 1, n + 1, label=i)
        if p <= 0:
            continue
        mid = first + lane * i + lane / 2 + 25
        cycles = 1.2 + 4.8 * max(0.0, freqs[i]) / top_f
        amp = (lane / 2 - 60) * max(0.15, amps[i] / top_a)
        color = YELLOW if i == 0 else BLUE
        text(draw, (W / 2, mid - lane / 2 + 15), label, 70, color, max_width=800, reveal=p)
        end = x0 + (x1 - x0) * p
        phase = 2 * math.pi * 0.8 * t  # every wave moves at the same speed across the board
        pts = [(x, mid + amp * math.sin(2 * math.pi * cycles * (x - x0) / (x1 - x0) - phase * cycles / 3))
               for x in [x0 + k * 4 for k in range(int((end - x0) / 4) + 1)]]
        if len(pts) > 1:
            draw.line(pts, fill=color, width=9, joint="curve")


def particles(draw, v: Visual, t: float, d: float) -> None:
    """Molecules bouncing in one or two boxes: values = how fast (temperature), amounts = how
    many (density, pressure). For heat, pressure, evaporation, smell."""
    labels = v.labels[:2] or [""]
    n = len(labels)
    speeds = [*v.values[:n], *[1.0] * n][:n]
    counts = [*v.amounts[:n], *[1.0] * n][:n]
    top_s, top_c = max(speeds) or 1.0, max(counts) or 1.0
    title(draw, v, stage(t, d, 0, n + 1))
    box_w = 380 if n == 2 else 600
    y0, y1 = TOP + (150 if v.title else 40), BOTTOM - 90
    for i, label in enumerate(labels):
        p = stage(t, d, i + 1, n + 1, label=i)
        if p <= 0:
            continue
        cx = W / 2 if n == 1 else (W / 2 - 230 if i == 0 else W / 2 + 230)
        x0, x1 = cx - box_w / 2, cx + box_w / 2
        color = YELLOW if i == 0 else BLUE
        draw.rectangle((x0, y0, x1, y1), outline=CHALK, width=8)
        text(draw, (cx, y1 + 50), label, 70, color, max_width=box_w + 40, reveal=p)
        rnd = random.Random(31 + i)  # the same molecules every frame
        many = max(3, round(18 * max(0.0, counts[i]) / top_c))
        speed = 60 + 520 * max(0.0, speeds[i]) / top_s
        r, inner_w, inner_h = 16, (x1 - x0) - 48, (y1 - y0) - 48
        for _ in range(many):
            px, py, ang = rnd.random() * inner_w, rnd.random() * inner_h, rnd.random() * 2 * math.pi
            # Bouncing off the walls: position folded back into the box.
            fx = (px + speed * math.cos(ang) * t) % (2 * inner_w)
            fy = (py + speed * math.sin(ang) * t) % (2 * inner_h)
            fx = fx if fx <= inner_w else 2 * inner_w - fx
            fy = fy if fy <= inner_h else 2 * inner_h - fy
            x, y = x0 + 24 + fx, y0 + 24 + fy
            s = r * p
            draw.ellipse((x - s, y - s, x + s, y + s), fill=color)


def ray(draw, v: Visual, t: float, d: float) -> None:
    """A light ray meeting a surface: labels = [medium above, medium below], values = their
    refractive indices (air 1.0, water 1.33, glass 1.5), shape "reflect" for a mirror. The bend
    is Snell's law, so it is right; a dashed line shows where the light would have gone."""
    above, below = [*v.labels, "air", "water"][:2]
    n1, n2 = [*v.values[:2], 1.0, 1.33][:2]
    title(draw, v, stage(t, d, 0, 4))
    cy = (TOP + (150 if v.title else 40) + BOTTOM) / 2 + 20
    p1, p2, p3 = stage(t, d, 1, 4), stage(t, d, 2, 4), stage(t, d, 3, 4)
    if p1 > 0:  # the lower medium, a shade lighter
        draw.rectangle((90, cy, W - 90, BOTTOM - 20), fill=tuple(c + 14 for c in BOARD))
    draw.line([(90, cy), (90 + (W - 180) * p1, cy)], fill=CHALK, width=6)
    text(draw, (110, cy - 45), above, 58, DIM, anchor="lm", max_width=360, reveal=p1)
    text(draw, (110, cy + 50), below, 58, DIM, anchor="lm", max_width=360, reveal=p1)
    hit = (W / 2 + 40, cy)
    for k in range(10):  # the normal, dashed
        if p1 > 0.5:
            ya = cy - 260 + k * 52
            draw.line([(hit[0], ya), (hit[0], ya + 24)], fill=DIM, width=4)
    # 52 degrees: steep enough to see the bend, past water's and glass's critical angles
    # (49, 42) so light trying to leave them reflects back, as it really does.
    th1 = math.radians(52)
    up, down = min(330, (cy - TOP - (150 if v.title else 40)) / math.cos(th1)), (BOTTOM - 40 - cy) / math.cos(th1)
    start = (hit[0] - up * math.sin(th1), hit[1] - up * math.cos(th1))
    arrow(draw, start, (start[0] + (hit[0] - start[0]) * p2, start[1] + (hit[1] - start[1]) * p2), YELLOW, width=10)
    if p2 < 1:
        return
    s2 = (n1 / n2) * math.sin(th1) if n2 else 2.0
    if v.shape == "reflect" or s2 > 1:  # a mirror, or total internal reflection
        out = (hit[0] + up * math.sin(th1), hit[1] - up * math.cos(th1))
    else:
        th2 = math.asin(s2)
        length = min(330, (BOTTOM - 40 - cy) / math.cos(th2))
        out = (hit[0] + length * math.sin(th2), hit[1] + length * math.cos(th2))
        straight = (hit[0] + down * math.sin(th1), hit[1] + down * math.cos(th1))
        for k in range(8):  # where it would have gone without the bend
            a, b = k / 8, (k + 0.5) / 8
            if b <= p3:
                draw.line([(hit[0] + (straight[0] - hit[0]) * a, hit[1] + (straight[1] - hit[1]) * a),
                           (hit[0] + (straight[0] - hit[0]) * b, hit[1] + (straight[1] - hit[1]) * b)],
                          fill=DIM, width=5)
    arrow(draw, hit, (hit[0] + (out[0] - hit[0]) * p3, hit[1] + (out[1] - hit[1]) * p3), YELLOW, width=10)


NUMBER = re.compile(r"^([^\d]*)(\d[\d,]*(?:\.\d+)?)(.*)$")


def number(draw, v: Visual, t: float, d: float) -> None:
    """One striking number, counting up, with what it is underneath ("343 m/s", "speed of
    sound"). The count lands on the exact figure and holds."""
    figure = (v.title or "").strip()
    p = stage(t, d, 0, 2)
    m = NUMBER.match(figure)
    if m and p < 1:
        head, digits, tail = m.groups()
        value = float(digits.replace(",", ""))
        places = len(digits.split(".")[1]) if "." in digits else 0
        shown = f"{value * p:,.{places}f}" if "," in digits else f"{value * p:.{places}f}"
        figure = head + shown + tail
    mid = (TOP + BOTTOM) / 2 - 60
    text(draw, (W / 2, mid), figure, 190, YELLOW, max_width=940, reveal=min(1.0, p * 4))
    caption = v.labels[0] if v.labels else ""
    text(draw, (W / 2, mid + 160), caption, 70, CHALK, max_width=880, reveal=stage(t, d, 1, 2))


COLORS = {"chalk": CHALK, "yellow": YELLOW, "blue": BLUE, "red": (255, 120, 100), "dim": DIM}
SK_X, SK_Y = 40, TOP + 20     # where the sketch grid's (0, 0) lands on the panel
#: How far down a sketch drawn on the old 1000x600 grid moves, to sit in the middle of the taller one (D162).
GRID_DY: contextvars.ContextVar[float] = contextvars.ContextVar("grid_dy", default=0.0)


def _pts(xy: list[float]) -> list[tuple[float, float]]:
    dy = GRID_DY.get()
    return [(SK_X + xy[k], SK_Y + dy + xy[k + 1]) for k in range(0, len(xy) - 1, 2)]


def _smooth(pts: list[tuple[float, float]], closed: bool) -> list[tuple[float, float]]:
    """A Catmull-Rom curve through the points: chalk curves, not polygons."""
    if len(pts) < 3:
        return pts + pts[:1] if closed and pts else pts
    ring = pts if closed else [pts[0], *pts, pts[-1]]
    n = len(ring)
    out = []
    for k in range(n if closed else n - 3):
        p0, p1, p2, p3 = (ring[(k + j) % n] for j in range(4)) if closed else ring[k:k + 4]
        for j in range(12):
            u = j / 12
            out.append(tuple(0.5 * (2 * p1[c] + (-p0[c] + p2[c]) * u + (2 * p0[c] - 5 * p1[c] + 4 * p2[c] - p3[c]) * u * u
                                    + (-p0[c] + 3 * p1[c] - 3 * p2[c] + p3[c]) * u ** 3) for c in (0, 1)))
    out.append(out[0] if closed else pts[-1])
    return out


def _upto(pts: list[tuple[float, float]], frac: float) -> list[tuple[float, float]]:
    """The first `frac` of a polyline, by length: a line being drawn on."""
    if frac >= 1 or len(pts) < 2:
        return pts
    seg = [math.dist(a, b) for a, b in itertools.pairwise(pts)]
    want, out = sum(seg) * max(0.0, frac), [pts[0]]
    for (a, b), length in zip(itertools.pairwise(pts), seg, strict=True):
        if want <= length:
            f = want / length if length else 0
            out.append((a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f))
            return out
        out.append(b)
        want -= length
    return out


def _at(pts: list[tuple[float, float]], frac: float) -> tuple[float, float]:
    return _upto(pts, frac)[-1]


def _dense(pts: list[tuple[float, float]], step: float = 4.0) -> list[tuple[float, float]]:
    """The polyline as points every `step` px along it."""
    out = pts[:1]
    for a, b in itertools.pairwise(pts):
        n = max(1, int(math.dist(a, b) / step))
        out += [(a[0] + (b[0] - a[0]) * j / n, a[1] + (b[1] - a[1]) * j / n) for j in range(1, n + 1)]
    return out


def _polyline(draw, pts, color, width: int = 9, dashed: bool = False) -> None:
    if len(pts) < 2:
        return
    if not dashed:
        draw.line(pts, fill=color, width=width, joint="curve")
        return
    dense = _dense(pts)
    for j in range(0, len(dense) - 1, 12):  # 28 px dashes, 20 px gaps
        draw.line(dense[j:j + 8], fill=color, width=width - 2, joint="curve")


def _hatch(poly: list[tuple[float, float]], spacing: float = 22) -> list[tuple[tuple[float, float], ...]]:
    """Chalk shading inside a closed outline: lines at 45 degrees, `spacing` px apart, cut to the outline."""
    r2 = math.sqrt(2)
    uv = [((x + y) / r2, (y - x) / r2) for x, y in poly]
    edges = list(zip(uv, uv[1:] + uv[:1], strict=True))
    vs = [v for _, v in uv]
    out = []
    c = min(vs) + spacing / 2
    while c < max(vs):
        cuts = sorted(u1 + (c - v1) * (u2 - u1) / (v2 - v1) for (u1, v1), (u2, v2) in edges
                      if (v1 <= c < v2) or (v2 <= c < v1))
        for a, b in zip(cuts[0::2], cuts[1::2], strict=False):
            if b - a > 8:
                a, b = a + 3, b - 3   # chalk stops short of the outline
                out.append((((a - c) / r2, (a + c) / r2), ((b - c) / r2, (b + c) / r2)))
        c += spacing
    return out


def _zigzag(a: tuple[float, float], b: tuple[float, float], t: float) -> list[tuple[float, float]]:
    """A spark from a to b: a jagged line that crackles, a new shape eight times a second."""
    length = math.dist(a, b) or 1
    ux, uy = (b[0] - a[0]) / length, (b[1] - a[1]) / length
    n = max(3, round(length / 40))
    rnd = random.Random(int(t * 8))
    pts = [a]
    for k in range(1, n):
        side = (1 if k % 2 else -1) * rnd.uniform(12, 26)
        along = length * (k + rnd.uniform(-0.25, 0.25)) / n
        pts.append((a[0] + ux * along - uy * side, a[1] + uy * along + ux * side))
    return [*pts, b]


def _strokes_upto(strokes: list[list[tuple[float, float]]], frac: float) -> list[list[tuple[float, float]]]:
    """The first `frac` of a drawing made of several strokes, by length: one stroke after another."""
    lengths = [sum(math.dist(a, b) for a, b in itertools.pairwise(st)) for st in strokes]
    want, out = sum(lengths) * frac, []
    for st, length in zip(strokes, lengths, strict=True):
        if want <= 0:
            break
        out.append(_upto(st, want / length) if length and want < length else st)
        want -= length
    return out


def _tip(draw, at: tuple[float, float], color, p: float) -> None:
    """The chalk's tip on a line still being drawn: the eye follows the hand."""
    if 0 < p < 1:
        draw.ellipse((at[0] - 7, at[1] - 7, at[0] + 7, at[1] + 7), fill=color)


def sketch(draw, v: Visual, t: float, d: float) -> None:
    """A chalk sketch made for this sentence (create/sketch.py): its marks arrive in order,
    or as the voice says their cue word; lines are drawn on, words written, waves and
    travelling dots keep moving. Things with an icon (create/icons.py) are drawn from it, stroke
    by stroke, a spark crackles, shading is hatched in (D162)."""
    from . import icons

    marks = v.sketch.marks if v.sketch else []
    GRID_DY.set((GRID_H - v.sketch.grid) / 2 if v.sketch else 0.0)
    title(draw, v, stage(t, d, 0, len(marks) + 1))
    # Words after the lines, so no line runs through a word ("vibration" was struck out);
    # the first mark straight away, whatever its cue, so the board is never empty.
    order = [i for i, m in enumerate(marks) if m.kind != "text"] + [i for i, m in enumerate(marks) if m.kind == "text"]
    for i in order:
        m = marks[i]
        p = stage(t, d, i + 1, len(marks) + 1, label=i if i else None)
        if p <= 0:
            continue
        color, pts = COLORS.get(m.color, CHALK), _pts(m.xy)
        if m.kind in ("line", "arrow", "curve", "loop"):
            line = _smooth(pts, closed=m.kind == "loop") if m.kind in ("curve", "loop") else pts
            part = _upto(line, p)
            _polyline(draw, part, color, dashed=m.dashed)
            if m.kind == "arrow" and p > 0.9 and len(part) >= 2:
                tail = _dense(part)   # the head on the last bit only: from part[-2] it redrew a dashed shaft solid
                arrow(draw, tail[max(0, len(tail) - 16)], tail[-1], color, width=9, head=40)
            elif part:
                _tip(draw, part[-1], color, p)
        elif m.kind == "icon" and pts:
            name = icons.name_for(m.text)
            size = m.xy[2] if len(m.xy) >= 3 else 160
            if not name:   # no such thing drawn: its name, so the board still says it
                text(draw, pts[0], m.text, 70, color, max_width=640, reveal=p, halo=True)
                continue
            drawn = _strokes_upto(icons.placed(name, pts[0][0], pts[0][1], size), p)
            width = round(min(11, max(6, size / 22)))
            for st in drawn:
                _polyline(draw, st, color, width=width)
            if drawn and drawn[-1]:
                _tip(draw, drawn[-1][-1], color, p)
        elif m.kind == "zigzag" and len(pts) >= 2:
            part = _upto(_zigzag(pts[0], pts[-1], t), p)
            _polyline(draw, part, color, width=8)
        elif m.kind == "hatch" and len(pts) >= 3:
            lines = _hatch(_smooth(pts, closed=True))
            for a, b in lines[:round(len(lines) * p)]:
                draw.line((a, b), fill=color, width=4)
        elif m.kind == "circle" and len(m.xy) >= 3:
            (cx, cy), r = pts[0], m.xy[2]
            draw.arc((cx - r, cy - r, cx + r, cy + r), -90, -90 + 360 * p, fill=color, width=9)
        elif m.kind == "dot" and pts:
            (cx, cy), r = pts[0], 16 * p
            draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=color)
        elif m.kind == "box" and len(pts) >= 2:
            (x0, y0), (x1, y1) = pts[0], pts[1]
            corners = [(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)]
            _polyline(draw, _upto(corners, p), color, dashed=m.dashed)
        elif m.kind == "text" and pts:
            text(draw, pts[0], m.text, {1: 56, 2: 70, 3: 100}.get(m.size, 70), color, max_width=640, reveal=p,
                 halo=True)
        elif m.kind == "wave" and len(pts) >= 2:
            (x0, y0), (x1, y1) = pts[0], pts[1]
            length = math.dist((x0, y0), (x1, y1)) or 1
            ux, uy = (x1 - x0) / length, (y1 - y0) / length
            amp, phase = {1: 14, 2: 26, 3: 42}.get(m.size, 26), 2 * math.pi * 0.9 * t
            wave_pts = [(x0 + ux * s - uy * amp * math.sin(2 * math.pi * m.cycles * s / length - phase),
                         y0 + uy * s + ux * amp * math.sin(2 * math.pi * m.cycles * s / length - phase))
                        for s in range(0, int(length * p) + 1, 4)]
            _polyline(draw, wave_pts, color, width=8)
        elif m.kind == "mover" and len(pts) >= 2:
            path = _smooth(pts, closed=False)
            k = (t / 1.8) % 1.0
            for lag, r in ((0.08, 8), (0.04, 11), (0.0, 17)):  # a short fading trail
                x, y = _at(path, max(0.0, k - lag))
                draw.ellipse((x - r * p, y - r * p, x + r * p, y + r * p), fill=color)


DRAW = {"sketch": sketch, "forces": forces, "circle": circle, "equation": equation, "compare": compare, "chain": chain, "graph": graph,
        "wave": wave, "particles": particles, "ray": ray, "number": number, "card": card}


#: Drawn at this many times the size, then shrunk: smooth chalk lines, where Pillow's own lines are jagged (D162).
SS = 2


class _Paster:
    """`draw._image.paste` for a template that pastes a picture (the graph's upright label), at the ink's size."""

    def __init__(self, image: Image.Image, scale: int):
        self.image, self.scale = image, scale

    def paste(self, im: Image.Image, box: tuple[int, int], mask=None) -> None:
        big = im.resize((im.width * self.scale, im.height * self.scale), Image.LANCZOS)
        self.image.paste(big, (box[0] * self.scale, box[1] * self.scale), big if mask is not None else None)


class Ink:
    """An ImageDraw that draws on the ink layer at SS times the size: the templates keep their panel
    coordinates, widths and font sizes, and every one is scaled here."""

    def __init__(self, image: Image.Image, scale: int = SS):
        self._d = ImageDraw.Draw(image)
        self.s = scale
        self._image = _Paster(image, scale)

    def _xy(self, xy):
        flat = []
        for item in xy:
            if isinstance(item, (tuple, list)):
                flat.append((item[0] * self.s, item[1] * self.s))
            else:
                flat.append(item * self.s)
        return flat

    def _w(self, width) -> int:
        return max(1, round((width or 1) * self.s))

    def line(self, xy, fill=None, width=1, joint=None):
        self._d.line(self._xy(xy), fill=fill, width=self._w(width), joint=joint)

    def polygon(self, xy, fill=None, outline=None, width=1):
        self._d.polygon(self._xy(xy), fill=fill, outline=outline, width=self._w(width))

    def ellipse(self, xy, fill=None, outline=None, width=1):
        self._d.ellipse(self._xy(xy), fill=fill, outline=outline, width=self._w(width))

    def arc(self, xy, start, end, fill=None, width=1):
        self._d.arc(self._xy(xy), start, end, fill=fill, width=self._w(width))

    def rectangle(self, xy, fill=None, outline=None, width=1):
        self._d.rectangle(self._xy(xy), fill=fill, outline=outline, width=self._w(width))

    def rounded_rectangle(self, xy, radius=0, fill=None, outline=None, width=1):
        self._d.rounded_rectangle(self._xy(xy), radius=radius * self.s, fill=fill, outline=outline, width=self._w(width))

    def text(self, xy, text, fill=None, font=None, anchor=None, stroke_width=0, stroke_fill=None):
        big = _bigger(font, self.s) if font is not None else None
        self._d.text((xy[0] * self.s, xy[1] * self.s), text, fill=fill, font=big, anchor=anchor,
                     stroke_width=round(stroke_width * self.s), stroke_fill=stroke_fill)

    def textlength(self, text, font=None):
        return self._d.textlength(text, font=font)


def _bigger(f: ImageFont.FreeTypeFont, scale: int) -> ImageFont.FreeTypeFont:
    """The same face `scale` times the size, its weight kept (made by font(), which notes how it made it)."""
    made = getattr(f, "made", None)
    return font(made[0] * scale, made[1], made[2]) if made else f.font_variant(size=f.size * scale)


@cache
def _grain(w: int, h: int) -> np.ndarray:
    """Chalk's dry texture: the same grain every frame (lines don't flicker), 0.55 to 1."""
    n = np.random.default_rng(11).random((h, w)).astype(np.float32)
    n = cv2.GaussianBlur(n, (0, 0), 0.9)
    n = (n - n.min()) / max(1e-6, n.max() - n.min())
    return (0.55 + 0.45 * n ** 0.6)[..., None]


@cache
def _board_array(w: int, h: int) -> np.ndarray:
    return np.asarray(board((w, h)))


#: How far the chalk dust reaches past a line, in pixels.
DUST = 24
_last: list = [None, None]   # the last ink layer and its chalked frame: a finished drawing holds still


def chalk(ink: np.ndarray) -> Image.Image:
    """The ink layer (RGBA, its colour premultiplied, as shrinking it leaves it) put on the board as chalk:
    its edges grainy, a faint dust of its own colour around it. Only the part with ink is worked on, the
    dust is made at a quarter size, and a frame the same as the last is not done again: about 10 ms a frame,
    where the plain way took 350."""
    a8 = ink
    if _last[0] is not None and _last[0].shape == a8.shape and np.array_equal(_last[0], a8):
        return _last[1]
    h, w = a8.shape[:2]
    base = _board_array(w, h)
    rows, cols = np.flatnonzero(a8[..., 3].max(1)), np.flatnonzero(a8[..., 3].max(0))
    if not len(rows):
        _last[:] = [a8, board((w, h))]
        return _last[1]
    x0, y0 = max(0, cols[0] - DUST), max(0, rows[0] - DUST)
    x1, y1 = min(w, cols[-1] + 1 + DUST), min(h, rows[-1] + 1 + DUST)
    size = (x1 - x0, y1 - y0)
    crop = np.ascontiguousarray(a8[y0:y1, x0:x1])
    raw = crop[..., 3].astype(np.float32) / 255
    alpha = raw * _grain(w, h)[y0:y1, x0:x1, 0]
    colour = cv2.divide(np.ascontiguousarray(crop[..., :3]), cv2.merge([crop[..., 3]] * 3), scale=255)
    part = cv2.blendLinear(np.ascontiguousarray(base[y0:y1, x0:x1]), colour, 1 - alpha, alpha)
    # The dust: the ink's colour, spread a little past it, faint.
    small = cv2.resize(crop, (max(1, size[0] // 4), max(1, size[1] // 4)), interpolation=cv2.INTER_AREA)
    small = cv2.GaussianBlur(small.astype(np.float32), (0, 0), 1.5)
    soft = small[..., 3] / 255
    colour = np.clip(small[..., :3] / np.maximum(soft, 1e-3)[..., None], 0, 255).astype(np.uint8)
    dust = cv2.resize(soft * 0.16, size) * (1 - raw)
    part = cv2.blendLinear(part, cv2.resize(colour, size), 1 - dust, dust)
    out = base.copy()
    out[y0:y1, x0:x1] = part
    img = Image.fromarray(out)
    _last[:] = [a8, img]
    return img


def _ink(v: Visual, t: float, d: float) -> np.ndarray:
    ink = Image.new("RGBA", (W * SS, H * SS), (0, 0, 0, 0))
    DRAW.get(v.template, chain)(Ink(ink), v, t, d)
    from .compose import as_array

    return as_array(ink)


#: Where a template's drawing is fitted on the panel (D162), and how far it may grow: the templates were
#: laid out for the old 640 px band and sat small at the top of the taller board.
FILL_X, FILL_Y, FILL_MAX = (50, W - 50), (TOP + 10, BOTTOM - 15), 1.5


@cache
def _placement(key: str, d: float) -> tuple[tuple[int, int, int, int], tuple[int, int], tuple[int, int]] | None:
    """For a template: the finished drawing's box on the big ink layer, the size it's shrunk to and where it
    goes on the panel, the same for every frame so nothing jumps while it's drawn. None: leave it as it is."""
    v = Visual.model_validate_json(key)
    # Finished, at a few moments: what keeps moving (an orbit's arrows, travelling dots) is in the box too.
    a = np.maximum.reduce([_ink(v, d + 99 + k * 0.65, d)[..., 3] for k in range(4)])
    rows, cols = np.flatnonzero(a.max(1)), np.flatnonzero(a.max(0))
    if not len(rows):
        return None
    pad = 12 * SS   # the chalk dust and the arrowheads' tips
    x0, x1 = max(0, cols[0] - pad), min(a.shape[1], cols[-1] + 1 + pad)
    y0, y1 = max(0, rows[0] - pad), min(a.shape[0], rows[-1] + 1 + pad)
    room_w, room_h = FILL_X[1] - FILL_X[0], FILL_Y[1] - FILL_Y[0]
    k = min(room_w / ((x1 - x0) / SS), room_h / ((y1 - y0) / SS), FILL_MAX)
    w, h = max(1, round((x1 - x0) / SS * k)), max(1, round((y1 - y0) / SS * k))
    at = (FILL_X[0] + (room_w - w) // 2, FILL_Y[0] + (room_h - h) // 2)
    return (x0, y0, x1, y1), (w, h), at


def _fitted(big: np.ndarray, place) -> np.ndarray:
    """The big ink layer shrunk onto the panel: as drawn, or (a template) its drawing scaled to fill the board."""
    if place is None:
        return cv2.resize(big, (W, H), interpolation=cv2.INTER_AREA)
    (x0, y0, x1, y1), (w, h), (ax, ay) = place
    crop = big[y0:y1, x0:x1]
    small = cv2.resize(crop, (w, h), interpolation=cv2.INTER_AREA if w < crop.shape[1] else cv2.INTER_LINEAR)
    out = np.zeros((H, W, 4), np.uint8)
    sx, sy = max(0, -ax), max(0, -ay)
    ex, ey = min(w, W - ax), min(h, H - ay)
    out[ay + sy:ay + ey, ax + sx:ax + ex] = small[sy:ey, sx:ex]
    return out


def frame(v: Visual, t: float, d: float) -> Image.Image:
    """The drawing at `t` of `d` seconds: drawn big on a clear layer, shrunk (a template's drawing scaled
    to fill the board, D162), then chalked onto the board. A sketch is fitted already (sketch.fit)."""
    place = None if v.template == "sketch" else _placement(v.model_dump_json(), round(d, 2))
    return chalk(_moved(_fitted(_ink(v, t, d), place), v.scale, v.shift))


def _moved(ink: np.ndarray, scale: float, shift: float) -> np.ndarray:
    """The drawing made bigger or smaller about the board's middle and moved up or down (`shift`, a share of the
    panel's height), as the user set it (D171). As drawn when both are left alone."""
    if abs(scale - 1) < 1e-3 and abs(shift) < 1e-3:
        return ink
    cx, cy = W / 2, (TOP + BOTTOM) / 2
    m = np.float32([[scale, 0, cx - scale * cx], [0, scale, cy - scale * cy + shift * H]])
    return cv2.warpAffine(ink, m, (ink.shape[1], ink.shape[0]), flags=cv2.INTER_LINEAR, borderValue=(0, 0, 0, 0))


#: The sketch grid's height now (1000 wide); sketches made before D162 were drawn on a 1000x600 one.
GRID_H = 900


def _box(m, dy: float) -> tuple[float, float, float, float]:
    from .sketch import _extent

    pts = [(SK_X + x, SK_Y + dy + y) for x, y in _extent(m)]
    return min(p[0] for p in pts), min(p[1] for p in pts), max(p[0] for p in pts), max(p[1] for p in pts)


def focus_plan(v: Visual, words: list[tuple[float, str]], seconds: float) -> list:
    """Where the camera moves in while the voice explains a sketch (D162): on a part said a while after the
    drawing starts, with the labels said with it, for about 1.6 s, then back out to the whole. At most one
    such move per 4 seconds of drawing (3 at most), never in the last second, never on a part that is most of
    the drawing anyway."""
    from .compose import Focus

    if v.template != "sketch" or not v.sketch or len(v.sketch.marks) < 4 or seconds < 3.5:
        return []
    marks, said = v.sketch.marks, cues(v, words)
    dy = (GRID_H - v.sketch.grid) / 2
    whole = [_box(m, dy) for m in marks]
    area = (max(b[2] for b in whole) - min(b[0] for b in whole)) * (max(b[3] for b in whole) - min(b[1] for b in whole))
    out: list = []
    for i, m in enumerate(marks):
        at = said[i] if i < len(said) else None
        if m.kind == "text" or at is None or at < 1.2 or at > seconds - 1.8:
            continue
        if out and at - out[-1].a < 2.4:
            continue
        near = [_box(marks[j], dy) for j in range(len(marks))
                if j == i or (marks[j].kind == "text" and said[j] is not None and abs(said[j] - at) < 0.9)]
        x0, y0 = min(b[0] for b in near) - 70, min(b[1] for b in near) - 70
        x1, y1 = max(b[2] for b in near) + 70, max(b[3] for b in near) + 70
        # Where the part is once the drawing is sized and moved by hand (D171).
        cx, cy, s, dy_ = W / 2, (TOP + BOTTOM) / 2, v.scale, v.shift * H
        x0, x1 = cx + s * (x0 - cx), cx + s * (x1 - cx)
        y0, y1 = cy + s * (y0 - cy) + dy_, cy + s * (y1 - cy) + dy_
        if (x1 - x0) * (y1 - y0) > 0.45 * area:
            continue
        out.append(Focus(a=at - 0.15, b=min(at + 1.6, seconds - 1.0), box=(x0, y0, x1, y1)))
        if len(out) >= min(3, max(1, int(seconds // 4))):
            break
    return out


def render(v: Visual, seconds: float, out: Path, words: list[tuple[float, str]] | None = None,
           frames: int | None = None) -> Path:
    """The diagram as a silent clip of the picture panel, `frames` long (else `seconds`); with the shot's
    spoken `words` ((start, text), seconds into the shot), each label arrives as it's said, and the camera
    moves in on the parts as they're explained (D162)."""
    from .compose import drawing_panel

    CUES.set(tuple(cues(v, words or [])))
    n = frames or max(1, round(seconds * FPS))
    return drawing_panel(lambda t, d: frame(v, t, d), out, n, focus_plan(v, words or [], n / FPS))
