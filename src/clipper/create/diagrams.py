"""Animated physics diagrams, drawn on a chalkboard (the Professor's lecture hall).

Ten templates the script fills in (create/script.py Visual): forces, circle,
equation, compare, chain, graph, wave, particles, ray, number; and the card, a
phrase chalked big when no footage fits. Each is drawn frame by frame with Pillow and
encoded to a clip exactly as long as its sentence: the pieces arrive over the
first 60% of it, one after another, then hold, with the moving parts (a dot
on its circle, a graph's tip) still moving. Everything sits between y 400 and
1040 on the 1080x1920 frame: below the hook line, above the captions (about
y 1100-1200), clear of the platform's buttons.
"""

from __future__ import annotations

import contextlib
import math
import random
import re
import string
import subprocess
from functools import cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from ..paths import REPO_ROOT
from ..render.ffmpeg import ffmpeg_path
from .script import Visual

W, H, FPS = 1080, 1920, 30
TOP, BOTTOM = 400, 1040
BOARD = (28, 44, 40)
CHALK = (238, 236, 226)
YELLOW = (255, 210, 70)
BLUE = (140, 195, 255)
DIM = (150, 160, 150)
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
    return f


def font_for(s: str, size: int) -> ImageFont.FreeTypeFont:
    """The chalk hand, or plain type when the text has a letter the hand can't write."""
    return font(size, plain=not set(s) <= CAVEAT_HAS)


@cache
def board() -> Image.Image:
    """The chalkboard: slate with grain and faint smudges of old lessons."""
    rnd = random.Random(7)
    img = Image.new("RGB", (W, H), BOARD)
    noise = Image.effect_noise((W, H), 18).convert("L")
    img = Image.composite(Image.new("RGB", (W, H), tuple(c + 10 for c in BOARD)), img, noise.point(lambda v: v // 6))
    smudge = Image.new("L", (W, H), 0)
    d = ImageDraw.Draw(smudge)
    for _ in range(9):
        x, y, r = rnd.randint(0, W), rnd.randint(0, H), rnd.randint(120, 380)
        d.ellipse((x - r, y - r * 0.6, x + r, y + r * 0.6), fill=rnd.randint(10, 22))
    smudge = smudge.filter(ImageFilter.GaussianBlur(60))
    return Image.composite(Image.new("RGB", (W, H), (60, 76, 70)), img, smudge)


def ease(x: float) -> float:
    x = min(1.0, max(0.0, x))
    return 1 - (1 - x) ** 3


def stage(t: float, d: float, i: int, n: int) -> float:
    """How far element `i` of `n` has arrived at time `t`: they come in turn over 60% of `d`."""
    window = 0.6 * d
    each = window / max(1, n)
    return ease((t - i * each * 0.85) / max(0.25, each))


def text(draw: ImageDraw.ImageDraw, xy: tuple[float, float], s: str, size: int, fill=CHALK,
         anchor: str = "mm", max_width: int = 900, reveal: float = 1.0) -> None:
    """Chalk text, shrunk to fit, written on letter by letter as `reveal` goes 0 to 1."""
    if reveal <= 0 or not s:
        return
    while size > 30 and draw.textlength(s, font=font_for(s, size)) > max_width:
        size -= 4
    f = font_for(s, size)
    shown = s[: max(1, round(len(s) * min(1.0, reveal)))]
    if anchor == "mm" and shown != s:  # keep the full line's position while it's written
        full = draw.textlength(s, font=f)
        draw.text((xy[0] - full / 2, xy[1]), shown, font=f, fill=fill, anchor="lm")
    else:
        draw.text(xy, shown, font=f, fill=fill, anchor=anchor)


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
        text(draw, (W / 2, TOP + 50), v.title, 84, YELLOW, reveal=p)
        if p > 0.6:
            span = min(760, draw.textlength(v.title, font=font(84)))
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
        p = stage(t, d, i + 2, len(labels) + 2)
        dx, dy = DIRS.get(dirs[i], (0, -1))
        length = want[i] * scale * p
        start = (cx + dx * 100, cy + dy * 100)
        tip = (start[0] + dx * length, start[1] + dy * length)
        color = YELLOW if i == 0 else (BLUE if i == 1 else CHALK)
        arrow(draw, start, tip, color, width=14, head=48)
        if p > 0.5:
            if dy:  # beside an up or down arrow, never over the title or the captions
                text(draw, (start[0] + 45, (start[1] + tip[1]) / 2), label, 64, color, anchor="lm",
                     max_width=420, reveal=(p - 0.5) * 2)
            else:   # under a sideways one, clear of the box
                text(draw, ((start[0] + tip[0]) / 2 + dx * 40, cy + 150), label, 60, color,
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
    pv, pi = stage(t, d, 2, 4), stage(t, d, 3, 4)
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
            y0 = BOTTOM - 85 + i * 66
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
        p = stage(t, d, len(tokens) + i, n)
        text(draw, (W / 2, TOP + 340 + i * 88), label, 64, YELLOW if i == 0 else CHALK, reveal=p)


def compare(draw, v: Visual, t: float, d: float) -> None:
    labels = [*v.labels, "A", "B"][:2]
    values = [*v.values, 1.0, 0.5][:2]
    top = max(values) or 1.0
    title(draw, v, stage(t, d, 0, 3))
    base = BOTTOM - 70
    for i, (label, value) in enumerate(zip(labels, values, strict=True)):
        p = stage(t, d, i + 1, 3)
        x = 330 + i * 420
        height = 400 * (value / top) * p
        color = YELLOW if i == 0 else BLUE
        draw.rectangle((x - 110, base - height, x + 110, base), fill=tuple(int(c * 0.35) + 20 for c in color),
                       outline=color, width=8)
        text(draw, (x, base + 50), label, 64, color, max_width=380, reveal=p)
    draw.line([(150, base), (W - 150, base)], fill=CHALK, width=6)


def chain(draw, v: Visual, t: float, d: float) -> None:
    steps = v.labels[:4] or ["cause", "effect"]
    n = len(steps)
    first = TOP + (140 if v.title else 10)
    gap = 55
    box = min(120, (BOTTOM - first - (n - 1) * gap) / n)
    total = n * box + (n - 1) * gap
    y = first + (BOTTOM - first - total) / 2
    title(draw, v, stage(t, d, 0, n + 1)) if v.title else None
    for i, step in enumerate(steps):
        p = stage(t, d, i + (1 if v.title else 0), n + (1 if v.title else 0))
        if p <= 0:
            break
        color = YELLOW if i == n - 1 else CHALK
        draw.rounded_rectangle((150, y, W - 150, y + box), radius=28, outline=color, width=8)
        text(draw, (W / 2, y + box / 2), step, 64, color, max_width=700, reveal=p)
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
    text(draw, ((ox + x1) / 2, oy + 45), labels[0], 58, DIM, reveal=pa)
    if pa > 0.5 and labels[1]:
        layer = Image.new("RGBA", (700, 120), (0, 0, 0, 0))
        ImageDraw.Draw(layer).text((350, 60), labels[1], font=font_for(labels[1], 58), fill=DIM, anchor="mm")
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
        p = stage(t, d, i + 1, n + 1)
        if p <= 0:
            break
        mid = first + lane * i + lane / 2 + 25
        cycles = 1.2 + 4.8 * max(0.0, freqs[i]) / top_f
        amp = (lane / 2 - 60) * max(0.15, amps[i] / top_a)
        color = YELLOW if i == 0 else BLUE
        text(draw, (W / 2, mid - lane / 2 + 15), label, 60, color, max_width=800, reveal=p)
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
        p = stage(t, d, i + 1, n + 1)
        if p <= 0:
            break
        cx = W / 2 if n == 1 else (W / 2 - 230 if i == 0 else W / 2 + 230)
        x0, x1 = cx - box_w / 2, cx + box_w / 2
        color = YELLOW if i == 0 else BLUE
        draw.rectangle((x0, y0, x1, y1), outline=CHALK, width=8)
        text(draw, (cx, y1 + 50), label, 60, color, max_width=box_w + 40, reveal=p)
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


DRAW = {"forces": forces, "circle": circle, "equation": equation, "compare": compare, "chain": chain, "graph": graph,
        "wave": wave, "particles": particles, "ray": ray, "number": number, "card": card}


def frame(v: Visual, t: float, d: float) -> Image.Image:
    img = board().copy()
    DRAW.get(v.template, chain)(ImageDraw.Draw(img), v, t, d)
    return img


def render(v: Visual, seconds: float, out: Path) -> Path:
    """The diagram as a silent 1080x1920 clip of exactly `seconds`."""
    frames = max(1, round(seconds * FPS))
    proc = subprocess.Popen(
        [str(ffmpeg_path()), "-hide_banner", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24",
         "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-", "-c:v", "libx264", "-preset", "veryfast", "-crf", "16",
         "-pix_fmt", "yuv420p", str(out)], stdin=subprocess.PIPE)
    try:
        for k in range(frames):
            proc.stdin.write(frame(v, k / FPS, seconds).tobytes())
    finally:
        proc.stdin.close()
        proc.wait()
    if proc.returncode:
        raise RuntimeError(f"couldn't encode the {v.template} diagram")
    return out
