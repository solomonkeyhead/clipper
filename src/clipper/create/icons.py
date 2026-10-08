"""Ready-drawn things for the chalk sketches (D162): Lucide's line icons (ISC licence, lucide/LICENSE-lucide.txt),
turned into strokes the board draws on one after another.

A sketch the model plotted point by point read as scribbles: a "magnet" was a bent line, a "person" a stick
with a circle. An icon is a recognisable object in a clean line style that suits chalk, so the model places
things ("speaker", "ear", "house") and draws only what's particular to the idea: the paths, arrows and labels.
"""

from __future__ import annotations

import json
import math
import re
from functools import cache
from pathlib import Path

DATA = Path(__file__).parent / "lucide" / "lucide.json"

#: The icons offered to the sketcher: physical things a science or everyday explainer draws. Every icon can be
#: drawn; these are the ones it is told about (the full list is ~1,600 names, mostly software symbols).
OFFERED = (  # noqa: SIM905 (234 names, quoted one by one, would read worse)
    "person-standing footprints hand ear eye brain bone heart heart-pulse droplet droplets flame snowflake sun "
    "moon cloud cloud-rain cloud-lightning cloud-snow wind waves mountain mountain-snow tree-pine tree-deciduous "
    "tree-palm leaf sprout flower earth globe orbit rocket satellite plane car bus bike ship train-front sailboat "
    "house building factory door-open door-closed lamp lightbulb zap battery battery-charging plug plug-zap magnet "
    "atom flask-conical beaker test-tube microscope telescope thermometer thermometer-sun thermometer-snowflake "
    "gauge scale ruler weight clock timer hourglass speaker volume-2 headphones mic music radio phone smartphone tv "
    "monitor laptop camera glasses shirt coffee cup-soda glass-water milk wine beer cooking-pot utensils "
    "refrigerator microwave fan air-vent heater shower-head bath umbrella anchor wrench hammer drill scissors key "
    "lock bell bomb fire-extinguisher bubbles dna pill syringe stethoscope bug bird fish cat dog rabbit snail apple "
    "egg carrot ice-cream-cone candy cookie pizza cake gift wheat dam fence tent trophy flag target compass map "
    "binoculars pyramid cone cylinder cuboid box package activity audio-waveform signal wifi radiation recycle "
    "sparkles star shell tornado rainbow cloud-fog baby skull armchair bed sofa dumbbell axe pickaxe shovel "
    "paintbrush pencil eraser book graduation-cap backpack watch alarm-clock banknote coins piggy-bank "
    "shopping-cart gem crown castle traffic-cone construction cable satellite-dish antenna radio-tower cpu "
    "microchip circuit-board guitar piano drum disc film gamepad-2 dice-5 volleyball thumbs-up smile frown laugh "
    "angry party-popper ghost bandage hospital ambulance siren flashlight spray-can ice-cream-bowl popsicle soup "
    "salad sandwich drumstick croissant banana cherry grape citrus nut leafy-green trees shrub clover squirrel "
    "turtle worm panda rat").split()

#: Words the sketcher might use for an icon by another name.
ALIASES = {
    "person": "person-standing", "man": "person-standing", "woman": "person-standing", "human": "person-standing",
    "people": "person-standing", "lightning": "zap", "bolt": "zap", "electricity": "zap", "water": "droplet",
    "drop": "droplet", "fire": "flame", "ice": "snowflake", "cold": "snowflake", "loudspeaker": "speaker",
    "sound": "volume-2", "volume": "volume-2", "cup": "coffee", "mug": "coffee", "glass": "glass-water",
    "bulb": "lightbulb", "light-bulb": "lightbulb", "planet": "globe", "world": "earth", "thermometer-hot":
    "thermometer-sun", "tree": "tree-deciduous", "pine": "tree-pine", "door": "door-open", "pot": "cooking-pot",
    "fridge": "refrigerator", "phone-mobile": "smartphone", "mobile": "smartphone", "television": "tv",
    "computer": "laptop", "clock-face": "clock", "stopwatch": "timer", "microphone": "mic", "note": "music",
    "train": "train-front", "boat": "sailboat", "airplane": "plane", "aeroplane": "plane", "truck": "bus",
    "medicine": "pill", "virus": "bug", "germ": "bug", "molecule": "atom", "flask": "flask-conical",
    "tube": "test-tube", "weights": "dumbbell", "money": "banknote", "coin": "coins", "storm": "cloud-lightning",
    "rain": "cloud-rain", "snow": "cloud-snow", "fog": "cloud-fog", "heartbeat": "heart-pulse", "pulse": "activity",
    "wave": "audio-waveform", "sound-wave": "audio-waveform", "music-note": "music", "skeleton": "skull",
}


@cache
def _all() -> dict:
    return json.loads(DATA.read_text(encoding="utf-8"))


def name_for(word: str) -> str | None:
    """The icon a word means, by its name, an alias, or a name that starts with it; None if there's none."""
    key = "-".join(word.lower().replace("_", " ").split())
    icons = _all()
    if key in icons:
        return key
    if key in ALIASES:
        return ALIASES[key]
    return next((n for n in OFFERED if n.startswith(key)), None)


# ---------- SVG to strokes ----------

_TOKEN = re.compile(r"[MmLlHhVvCcSsQqTtAaZz]|-?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")


def _arc(x0, y0, rx, ry, phi, large, sweep, x, y, steps=16) -> list[tuple[float, float]]:
    """An SVG elliptical arc as points (the endpoint form turned into the centre form, SVG spec F.6.5)."""
    if rx == 0 or ry == 0 or (x0 == x and y0 == y):
        return [(x, y)]
    rx, ry = abs(rx), abs(ry)
    c, s = math.cos(math.radians(phi)), math.sin(math.radians(phi))
    dx, dy = (x0 - x) / 2, (y0 - y) / 2
    x1, y1 = c * dx + s * dy, -s * dx + c * dy
    lam = x1 * x1 / (rx * rx) + y1 * y1 / (ry * ry)
    if lam > 1:
        rx, ry = rx * math.sqrt(lam), ry * math.sqrt(lam)
    num = rx * rx * ry * ry - rx * rx * y1 * y1 - ry * ry * x1 * x1
    den = rx * rx * y1 * y1 + ry * ry * x1 * x1
    k = math.sqrt(max(0.0, num / den)) if den else 0.0
    if large == sweep:
        k = -k
    cx1, cy1 = k * rx * y1 / ry, -k * ry * x1 / rx
    cx, cy = c * cx1 - s * cy1 + (x0 + x) / 2, s * cx1 + c * cy1 + (y0 + y) / 2

    def angle(ux, uy, vx, vy):
        a = math.atan2(ux * vy - uy * vx, ux * vx + uy * vy)
        return a

    t1 = angle(1, 0, (x1 - cx1) / rx, (y1 - cy1) / ry)
    dt = angle((x1 - cx1) / rx, (y1 - cy1) / ry, (-x1 - cx1) / rx, (-y1 - cy1) / ry)
    if not sweep and dt > 0:
        dt -= 2 * math.pi
    elif sweep and dt < 0:
        dt += 2 * math.pi
    n = max(4, int(abs(dt) / (2 * math.pi) * 32))
    out = []
    for j in range(1, n + 1):
        t = t1 + dt * j / n
        ex, ey = rx * math.cos(t), ry * math.sin(t)
        out.append((c * ex - s * ey + cx, s * ex + c * ey + cy))
    return out


def _bezier(p0, p1, p2, p3, steps=12) -> list[tuple[float, float]]:
    out = []
    for j in range(1, steps + 1):
        t = j / steps
        a, b, c, d = (1 - t) ** 3, 3 * (1 - t) ** 2 * t, 3 * (1 - t) * t * t, t ** 3
        out.append((a * p0[0] + b * p1[0] + c * p2[0] + d * p3[0], a * p0[1] + b * p1[1] + c * p2[1] + d * p3[1]))
    return out


def path_strokes(d: str) -> list[list[tuple[float, float]]]:
    """An SVG path's `d` as polylines, one per subpath."""
    tokens = _TOKEN.findall(d)
    strokes: list[list[tuple[float, float]]] = []
    cur: list[tuple[float, float]] = []
    x = y = sx = sy = 0.0
    last_ctrl = None
    cmd = ""
    i = 0

    def num() -> float:
        nonlocal i
        v = float(tokens[i])
        i += 1
        return v

    while i < len(tokens):
        if tokens[i].isalpha():
            cmd = tokens[i]
            i += 1
            if cmd in "Zz":
                if cur:
                    cur.append((sx, sy))
                    strokes.append(cur)
                    cur = []
                x, y = sx, sy
                last_ctrl = None
                continue
        rel = cmd.islower()
        c = cmd.upper()
        ox, oy = (x, y) if rel else (0.0, 0.0)
        if c == "M":
            if cur and len(cur) > 1:
                strokes.append(cur)
            x, y = num() + ox, num() + oy
            sx, sy = x, y
            cur = [(x, y)]
            cmd = "l" if rel else "L"   # pairs after a moveto are lines
            last_ctrl = None
        elif c == "L":
            x, y = num() + ox, num() + oy
            cur.append((x, y))
            last_ctrl = None
        elif c == "H":
            x = num() + ox
            cur.append((x, y))
            last_ctrl = None
        elif c == "V":
            y = num() + oy
            cur.append((x, y))
            last_ctrl = None
        elif c in "CS":
            if c == "C":
                c1 = (num() + ox, num() + oy)
            else:
                c1 = (2 * x - last_ctrl[0], 2 * y - last_ctrl[1]) if last_ctrl else (x, y)
            c2 = (num() + ox, num() + oy)
            end = (num() + ox, num() + oy)
            cur += _bezier((x, y), c1, c2, end)
            last_ctrl, (x, y) = c2, end
        elif c in "QT":
            if c == "Q":
                q = (num() + ox, num() + oy)
            else:
                q = (2 * x - last_ctrl[0], 2 * y - last_ctrl[1]) if last_ctrl else (x, y)
            end = (num() + ox, num() + oy)
            c1 = (x + 2 / 3 * (q[0] - x), y + 2 / 3 * (q[1] - y))
            c2 = (end[0] + 2 / 3 * (q[0] - end[0]), end[1] + 2 / 3 * (q[1] - end[1]))
            cur += _bezier((x, y), c1, c2, end)
            last_ctrl, (x, y) = q, end
        elif c == "A":
            rx, ry, phi, large, sweep = num(), num(), num(), num(), num()
            end = (num() + ox, num() + oy)
            cur += _arc(x, y, rx, ry, phi, int(large), int(sweep), *end)
            x, y = end
            last_ctrl = None
        else:   # unknown: skip a number so the loop moves on
            i += 1
        if not cur:
            cur = [(x, y)]
    if cur and len(cur) > 1:
        strokes.append(cur)
    return strokes


def _ellipse(cx, cy, rx, ry, n=36) -> list[tuple[float, float]]:
    return [(cx + rx * math.cos(2 * math.pi * j / n - math.pi / 2), cy + ry * math.sin(2 * math.pi * j / n - math.pi / 2))
            for j in range(n + 1)]


def _rect(x, y, w, h, r) -> list[tuple[float, float]]:
    if r <= 0:
        return [(x, y), (x + w, y), (x + w, y + h), (x, y + h), (x, y)]
    r = min(r, w / 2, h / 2)
    pts = []
    for cx, cy, a0 in ((x + w - r, y + r, -90), (x + w - r, y + h - r, 0), (x + r, y + h - r, 90), (x + r, y + r, 180)):
        pts += [(cx + r * math.cos(math.radians(a0 + 90 * j / 6)), cy + r * math.sin(math.radians(a0 + 90 * j / 6)))
                for j in range(7)]
    return [*pts, pts[0]]


@cache
def strokes(name: str) -> tuple[tuple[tuple[float, float], ...], ...]:
    """The icon's strokes in its own 24 x 24 box, in drawing order; () if there's no such icon."""
    out: list[list[tuple[float, float]]] = []
    for tag, a in _all().get(name, []):
        f = {k: float(v) for k, v in a.items() if k != "d" and k != "points" and _isnum(v)}
        if tag == "path":
            out += path_strokes(a.get("d", ""))
        elif tag == "circle":
            out.append(_ellipse(f.get("cx", 0), f.get("cy", 0), f.get("r", 0), f.get("r", 0)))
        elif tag == "ellipse":
            out.append(_ellipse(f.get("cx", 0), f.get("cy", 0), f.get("rx", 0), f.get("ry", 0)))
        elif tag == "rect":
            out.append(_rect(f.get("x", 0), f.get("y", 0), f.get("width", 0), f.get("height", 0),
                             f.get("rx", f.get("ry", 0))))
        elif tag == "line":
            out.append([(f.get("x1", 0), f.get("y1", 0)), (f.get("x2", 0), f.get("y2", 0))])
        elif tag in ("polyline", "polygon"):
            nums = [float(n) for n in re.findall(r"-?(?:\d+\.?\d*|\.\d+)", a.get("points", ""))]
            pts = list(zip(nums[0::2], nums[1::2], strict=False))
            out.append(pts + pts[:1] if tag == "polygon" else pts)
    return tuple(tuple(s) for s in out if len(s) > 1)


def _isnum(v) -> bool:
    try:
        float(v)
    except (TypeError, ValueError):
        return False
    return True


def placed(name: str, cx: float, cy: float, size: float) -> list[list[tuple[float, float]]]:
    """The icon's strokes `size` pixels across, centred on (cx, cy)."""
    k = size / 24
    return [[(cx + (x - 12) * k, cy + (y - 12) * k) for x, y in s] for s in strokes(name)]
