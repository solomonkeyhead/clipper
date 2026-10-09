"""The edit (D162): every frame of a Create Short drawn here, in Python, and handed to ffmpeg once.

Why: ffmpeg's expression filters moved the camera in whole pixels (the frame was scaled to an even width
each frame), so a push-in shimmered and a punch-in jumped in four frames; the Professor was a still sticker
switched on and off by `enable`; the captions were the clipping pipeline's subtitle file. Here:

- the camera is a float affine transform (cv2.warpAffine: sub-pixel, eased), with a motion blur on the fast
  moves, so pushes glide and a punch-in snaps and settles;
- the Professor is a sprite on a spring: he stands on the bottom edge of the frame, bounces on every word
  (harder on the highlighted ones), breathes, pops when he changes pose, slides in at the start, and the
  camera cuts in on his face for his reactions and the punchline;
- the captions pop in a page at a time, the word being said lit;
- shots change with a cut, or now and then a whip or a zoom.

Layout (1080x1920): the picture panel on top (0 to PANEL_H), the board below it, the captions just under the
panel and the Professor standing on the bottom edge, bottom left. A wide stock clip loses far less to a
1080x1120 panel than to a whole 9:16 frame, so the thing the sentence is about stays in shot, and the
captions always sit on the plain board, never on busy footage.
"""

from __future__ import annotations

import math
import subprocess
from collections.abc import Iterator
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFile, ImageFilter, ImageFont

from ..paths import REPO_ROOT
from ..render.ffmpeg import ffmpeg_path
from ..utils.logging import get_logger

log = get_logger(__name__)

W, H, FPS = 1080, 1920, 30
PANEL_H = 1120
#: The captions' middle line, on the board just under the panel.
CAPTION_Y = PANEL_H + 84
#: The Professor: this tall, his bottom edge a little below the frame's (his bust is cut flat there), bottom left.
PRESENTER_H = 660
PRESENTER_X = 18
PRESENTER_SINK = 26
ANTON = REPO_ROOT / "assets" / "fonts" / "Anton-Regular.ttf"
CAVEAT = REPO_ROOT / "assets" / "fonts" / "Caveat.ttf"

# The camera on a picture (D162): a push or pull of PUSH, a drift of DRIFT of the width, and a punch-in of PUNCH
# over PUNCH_SECONDS on the highlighted word, held.
PUSH = 0.075
DRIFT = 0.07
PUNCH = 0.16
PUNCH_SECONDS = 0.2
#: How much bigger than the panel a clip is decoded, so the camera can move inside real pixels.
MARGIN = 1.2


# ---------- easing ----------

def as_array(img: Image.Image) -> np.ndarray:
    """`np.asarray(img)`, handed over in one piece: Pillow does it in 64 KB blocks, 17 ms for a drawing's
    2160x2240 layer against 6 ms in one, and a build does it twice a frame (D174).
    ponytail: swaps a Pillow module setting for the call; another thread converting at that moment
    only gets the blocks back, the old speed."""
    keep = ImageFile.MAXBLOCK
    ImageFile.MAXBLOCK = max(keep, img.width * img.height * len(img.getbands()))
    try:
        return np.asarray(img)
    finally:
        ImageFile.MAXBLOCK = keep


def clamp01(x: float) -> float:
    return 0.0 if x < 0 else 1.0 if x > 1 else x


def smooth(x: float) -> float:
    """Ease in and out (smootherstep): a move that starts and stops without a jolt."""
    x = clamp01(x)
    return x * x * x * (x * (6 * x - 15) + 10)


def out_back(x: float, s: float = 1.70158) -> float:
    """Ease out past the mark and back: a snap that settles, for punch-ins and pops."""
    x = clamp01(x)
    return 1 + (s + 1) * (x - 1) ** 3 + s * (x - 1) ** 2


def frames_between(a: float, b: float) -> int:
    """Frames from second `a` to second `b` on the video's one clock. Rounding each shot's own length instead
    let the cuts drift off the sentences by up to half a frame a shot."""
    return max(1, round(b * FPS) - round(a * FPS))


# ---------- reading and writing frames ----------

def decode(src: Path, size: tuple[int, int], *, start: float = 0.0, seconds: float, loop: bool = False,
           slow: float = 1.0, filters: str = "") -> subprocess.Popen:
    """ffmpeg reading `src` from `start` as raw RGB frames of `size`, 30 a second, `seconds` of them (after any
    stretch); `loop` repeats the clip from its start, `slow` stretches time (2 = half speed). Read it with
    `frames()`."""
    w, h = size
    vf = (f"setpts={slow:.4f}*PTS," if slow != 1.0 else "") + f"fps={FPS},scale={w}:{h}:flags=lanczos,setsar=1"
    if filters:
        vf += "," + filters
    args = [str(ffmpeg_path()), "-hide_banner", "-loglevel", "error", "-nostdin",
            *(["-stream_loop", "-1"] if loop else ["-ss", f"{max(0.0, start):.3f}"]),
            "-i", str(src), "-t", f"{seconds + 0.2:.3f}", "-an", "-vf", vf,
            "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
    return subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)


def frames(proc: subprocess.Popen, size: tuple[int, int], n: int) -> Iterator[np.ndarray]:
    """Exactly `n` frames from a decoder: its last frame held if it runs short (a clip shorter than the
    sentence), the rest dropped if it runs long."""
    w, h = size
    need, last, got = w * h * 3, None, 0
    try:
        while got < n:
            buf = proc.stdout.read(need)
            if len(buf) < need:
                break
            last = np.frombuffer(buf, np.uint8).reshape(h, w, 3)
            got += 1
            yield last
    finally:
        proc.stdout.close()
        proc.kill()
        proc.wait()
    if last is None:
        raise RuntimeError("the clip gave no frames")
    for _ in range(n - got):
        yield last


@cache
def _nvenc() -> bool:
    """Whether the graphics card's encoder opens here."""
    from ..render.ffmpeg import probe_encoder

    return probe_encoder("h264_nvenc").usable


class Writer:
    """Frames into an mp4 (the panel's shots: high quality, as they are encoded again at the end). `footage` on the
    graphics card's encoder when it has one (D179): x264 took 15 s of CPU for 10 s of footage, NVENC 4, at a quality
    the final encode can't tell apart (48 dB); a chalkboard costs x264 next to nothing, so drawings keep it."""

    def __init__(self, out: Path, size: tuple[int, int], footage: bool = False):
        self.out, self.size = out, size
        w, h = size
        codec = (["-c:v", "h264_nvenc", "-preset", "p4", "-rc", "vbr", "-cq", "16", "-b:v", "0"] if footage and _nvenc()
                 else ["-c:v", "libx264", "-preset", "veryfast", "-crf", "14"])
        self.proc = subprocess.Popen(
            [str(ffmpeg_path()), "-hide_banner", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24",
             "-s", f"{w}x{h}", "-r", str(FPS), "-i", "-", *codec, "-pix_fmt", "yuv420p", str(out)],
            stdin=subprocess.PIPE)

    def write(self, frame: np.ndarray) -> None:
        self.proc.stdin.write(np.ascontiguousarray(frame, dtype=np.uint8).data)   # no copy (D174)

    def __enter__(self) -> Writer:
        return self

    def __exit__(self, *exc) -> None:
        self.proc.stdin.close()
        self.proc.wait()
        if self.proc.returncode and exc[0] is None:
            raise RuntimeError(f"couldn't encode {self.out.name}")


def cover_size(sw: int, sh: int, w: int = W, h: int = PANEL_H, margin: float = MARGIN) -> tuple[int, int]:
    """A source's size scaled to cover w x h with `margin` to spare, in even pixels."""
    s = max(w / max(sw, 1), h / max(sh, 1)) * margin
    return max(2, round(sw * s / 2) * 2), max(2, round(sh * s / 2) * 2)


# ---------- the camera ----------

def view(img: np.ndarray, z: float, u: float, v: float, size: tuple[int, int] = (W, PANEL_H)) -> np.ndarray:
    """`img` seen through the camera: zoom `z` (1 = just covering `size`) on the point (u, v) of it (0-1
    across and down), kept inside the picture. Sub-pixel: no shimmer however slow the move."""
    w, h = size
    sh, sw = img.shape[:2]
    s = max(w / sw, h / sh) * max(z, 1.0)
    hw, hh = w / (2 * s), h / (2 * s)
    cx = min(max(u * sw, hw), sw - hw)
    cy = min(max(v * sh, hh), sh - hh)
    m = np.float32([[s, 0, w / 2 - s * cx], [0, s, h / 2 - s * cy]])
    return cv2.warpAffine(img, m, (w, h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)


@dataclass
class Move:
    """A shot's camera (D162): "push" in, "pull" out, "drift" across toward the subject, or "still"; a punch-in
    at `punch` seconds, held; (u, v) is the subject's place in the picture, 0-1."""

    kind: str = "push"
    u: float = 0.5
    v: float = 0.5
    punch: float | None = None

    def at(self, t: float, d: float) -> tuple[float, float, float]:
        x = smooth(t / max(d, 1e-3))
        u = self.u
        if self.kind == "push":
            z = 1.0 + PUSH * x
        elif self.kind == "pull":
            z = 1.0 + PUSH * (1 - x)
        elif self.kind == "drift":
            z = 1.0 + PUSH
            side = 1 if self.u >= 0.5 else -1   # from away from the subject toward it
            u = self.u + side * DRIFT * (x - 0.5)
        else:
            z = 1.015
        if self.punch is not None:
            z *= 1 + PUNCH * out_back((t - self.punch) / PUNCH_SECONDS)
        return z, u, self.v

    def blurred(self, t: float) -> bool:
        """Whether the camera is moving fast enough here to want motion blur."""
        return self.punch is not None and 0 <= t - self.punch < PUNCH_SECONDS


def shoot(img: np.ndarray, move: Move, t: float, d: float, size: tuple[int, int] = (W, PANEL_H)) -> np.ndarray:
    """One frame of a shot: the camera at `t`, blurred over a third of a frame either side while it snaps."""
    if not move.blurred(t):
        return view(img, *move.at(t, d), size=size)
    acc = np.zeros((size[1], size[0], 3), np.float32)
    for dt in (-1 / (3 * FPS), 0.0, 1 / (3 * FPS)):
        acc += view(img, *move.at(t + dt, d), size=size)
    return (acc / 3).astype(np.uint8)


# ---------- the panel's shots ----------

def video_panel(src: Path, out: Path, n: int, move: Move, *, start: float = 0.0, loop: bool = False,
                slow: float = 1.0, play: float | None = None, look: str = "",
                source_size: tuple[int, int] | None = None) -> Path:
    """`n` frames of a video (stock footage, the user's own clip) filling the panel, the camera moving on it.
    With `play`, only that many seconds of the clip play (stretched by `slow`), then its last frame stays."""
    from ..ingest.probe import probe

    if source_size is None:
        info = probe(src)
        source_size = (info.width or W, info.height or H)
    size = cover_size(*source_size)
    d = n / FPS
    shown = min(d, play * slow) if play is not None else d
    proc = decode(src, size, start=start, seconds=shown, loop=loop and play is None, slow=slow, filters=look)
    with Writer(out, (W, PANEL_H), footage=True) as wr:
        for k, img in enumerate(frames(proc, size, n)):
            wr.write(shoot(img, move, k / FPS, d))
    return out


def photo_panel(img: Image.Image, out: Path, n: int, move: Move, look: bool = True) -> Path:
    """`n` frames of a still photo filling the panel, the camera moving slowly over it (a Ken Burns move)."""
    rgb = img.convert("RGB")
    size = cover_size(*rgb.size, margin=1.35)
    rgb = rgb.resize(size, Image.LANCZOS)
    arr = np.asarray(rgb)
    if look:   # the footage look (D156): a little less colour, a touch warmer
        arr = _look(arr)
    d = n / FPS
    with Writer(out, (W, PANEL_H), footage=True) as wr:
        for k in range(n):
            wr.write(shoot(arr, move, k / FPS, d))
    return out


def _look(arr: np.ndarray) -> np.ndarray:
    f = arr.astype(np.float32)
    grey = f.mean(axis=2, keepdims=True)
    f = grey + (f - grey) * 0.85
    f[..., 0] *= 1.03
    f[..., 2] *= 0.97
    return np.clip(f, 0, 255).astype(np.uint8)


@dataclass
class Focus:
    """A part of a drawing the camera moves in on while the voice explains it (D162): from `a` to `b` seconds
    into the shot, the box (x0, y0, x1, y1) in panel pixels."""

    a: float
    b: float
    box: tuple[float, float, float, float]


def drawing_panel(render_frame, out: Path, n: int, focus: list[Focus] | None = None, push: float = 0.03,
                  start: int = 0, stop: int | None = None) -> Path:
    """`n` frames of a chalk drawing (`render_frame(t, d)` gives the panel-sized picture at `t`), the camera
    pushing in a touch and, for each `focus`, gliding in on that part and back out to the whole. Only frames
    `start` to `stop` are drawn and written (a piece of a long drawing, D179); the camera's glide is worked out
    from the first frame all the same, which costs next to nothing."""
    d = n / FPS
    u, v, z = 0.5, 0.5, 1.0
    vu = vv = vz = 0.0
    omega = 2 * math.pi * 1.1   # a critically damped glide: there in about half a second, no overshoot
    with Writer(out, (W, PANEL_H)) as wr:
        for k in range(stop if stop is not None else n):
            t = k / FPS
            tu, tv, tz = 0.5, 0.5, 1.0
            for f in focus or []:
                if f.a <= t < f.b:
                    x0, y0, x1, y1 = f.box
                    tz = max(1.0, min(1.45, 0.72 * min(W / max(x1 - x0, 1), PANEL_H / max(y1 - y0, 1))))
                    tu, tv = (x0 + x1) / 2 / W, (y0 + y1) / 2 / PANEL_H
            for _ in range(4):   # sub-steps keep the spring steady
                dt = 1 / (4 * FPS)
                for name, target in (("u", tu), ("v", tv), ("z", tz)):
                    pos, vel = {"u": (u, vu), "v": (v, vv), "z": (z, vz)}[name]
                    acc = omega * omega * (target - pos) - 2 * omega * vel
                    vel += acc * dt
                    pos += vel * dt
                    if name == "u":
                        u, vu = pos, vel
                    elif name == "v":
                        v, vv = pos, vel
                    else:
                        z, vz = pos, vel
            if k < start:   # an earlier piece's frame: only the camera moves on
                continue
            pic = render_frame(t, d)
            img = as_array(pic if pic.mode == "RGB" else pic.convert("RGB"))
            zoom = z * (1 + push * smooth(t / max(d, 1e-3)))
            wr.write(img if zoom < 1.0005 and abs(u - 0.5) < 1e-3 and abs(v - 0.5) < 1e-3
                     else view(img, zoom, u, v))
    return out


def repeat_panel(src: Path, out: Path, n: int) -> Path:
    """`n` frames of a panel shot played again from its start, looped if it's shorter (the ending over the
    opening, D155)."""
    proc = decode(src, (W, PANEL_H), seconds=n / FPS, loop=True)
    with Writer(out, (W, PANEL_H)) as wr:
        for img in frames(proc, (W, PANEL_H), n):
            wr.write(img)
    return out


# ---------- pasting ----------

def paste(frame: np.ndarray, rgba: np.ndarray, x: int, y: int, alpha: float = 1.0) -> None:
    """An RGBA picture onto the frame with its top left at (x, y), clipped to the frame."""
    h, w = rgba.shape[:2]
    x0, y0, x1, y1 = max(0, x), max(0, y), min(frame.shape[1], x + w), min(frame.shape[0], y + h)
    if x1 <= x0 or y1 <= y0:
        return
    src = rgba[y0 - y:y1 - y, x0 - x:x1 - x]
    a = src[..., 3].astype(np.float32) * (alpha / 255.0)
    region = frame[y0:y1, x0:x1]
    # OpenCV's blend: 3 ms for a caption where numpy's took 12, every frame (D174).
    region[:] = cv2.blendLinear(np.ascontiguousarray(src[..., :3]), np.ascontiguousarray(region), a, 1 - a)


def paste_scaled(frame: np.ndarray, rgba: np.ndarray, cx: float, cy: float, scale: float = 1.0,
                 alpha: float = 1.0) -> None:
    """An RGBA picture centred on (cx, cy), scaled (for pops)."""
    if abs(scale - 1.0) > 1e-3:
        h, w = rgba.shape[:2]
        rgba = cv2.resize(rgba, (max(1, round(w * scale)), max(1, round(h * scale))), interpolation=cv2.INTER_LINEAR)
    h, w = rgba.shape[:2]
    paste(frame, rgba, round(cx - w / 2), round(cy - h / 2), alpha)


# ---------- text ----------

@cache
def _font(path: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, size)


def text_image(words: list[str], size: int, *, lit: int = -1, colour=(255, 255, 255), accent=(255, 214, 0),
               stroke: int = 9, font: Path = ANTON, max_width: int = 1000) -> np.ndarray:
    """Words in a heavy face with a thick dark edge and a soft drop shadow, word `lit` in the accent colour,
    shrunk to fit `max_width`; as RGBA."""
    f = _font(str(font), size)
    line = " ".join(words)
    while size > 40 and f.getlength(line) + 2 * stroke > max_width:
        size -= 4
        f = _font(str(font), size)
    pad = stroke + 18
    asc, desc = f.getmetrics()
    width = round(f.getlength(line)) + 2 * pad
    height = asc + desc + 2 * pad
    shadow = Image.new("L", (width, height), 0)
    ImageDraw.Draw(shadow).text((pad, pad + 7), line, font=f, fill=200, stroke_width=stroke, stroke_fill=200)
    shadow = shadow.filter(ImageFilter.GaussianBlur(9))
    img = Image.merge("RGBA", (Image.new("L", (width, height), 0),) * 3 + (shadow,))
    draw = ImageDraw.Draw(img)
    x = pad
    space = f.getlength(" ")
    for i, word in enumerate(words):
        draw.text((x, pad), word, font=f, fill=(*(accent if i == lit else colour), 255),
                  stroke_width=stroke, stroke_fill=(12, 12, 14, 255))
        x += f.getlength(word) + space
    return np.asarray(img)


class Captions:
    """The words on screen a page at a time (2 to 4 words, one line), the word being said lit; each page pops
    in. The clipping pipeline's page rules (create/render/captions.chunk_words) decide the pages."""

    SIZE = 98

    def __init__(self, words: list, accent=(255, 214, 0)):
        from ..models import Word
        from ..render import captions as cap

        style = cap.CaptionStyle(name="create", font="Anton", font_size=self.SIZE, primary=cap.WHITE,
                                 highlight=cap.YELLOW, outline_colour=cap.BLACK, outline=8, shadow=2,
                                 max_words_per_chunk=4, max_chars_per_line=21, max_lines=1)
        timed = cap.spread_squashed([Word(start=w.start, end=w.end, text=w.text) for w in words])
        self.pages = cap.chunk_words(timed, style, max_gap=0.6)
        self.accent = accent
        self.windows = []
        for i, page in enumerate(self.pages):
            nxt = self.pages[i + 1].start if i + 1 < len(self.pages) else None
            end = page.end + 0.7 if nxt is None else min(nxt, page.end + 0.9)
            self.windows.append((page.start, end))
        self._images: dict[tuple[int, int], np.ndarray] = {}

    def image(self, p: int, lit: int) -> np.ndarray:
        key = (p, lit)
        if key not in self._images:
            words = [w.text.strip() for w in self.pages[p].words]
            self._images[key] = text_image(words, self.SIZE, lit=lit, accent=self.accent)
        return self._images[key]

    def draw(self, frame: np.ndarray, t: float, y: float = CAPTION_Y) -> None:
        for p, (a, b) in enumerate(self.windows):
            if a - 1e-6 <= t < b:
                page = self.pages[p]
                lit = max((i for i, w in enumerate(page.words) if w.start <= t + 1e-6), default=0)
                scale = 0.8 + 0.2 * out_back((t - a) / 0.14)
                paste_scaled(frame, self.image(p, lit), W / 2, y, scale)
                return


def hook_image(text: str) -> np.ndarray:
    """The on-screen hook (D155), shouted: up to two lines in capitals, white, the same heavy face."""
    from ..render.captions import hook_lines

    lines = hook_lines(text.upper(), 18)[:2]
    imgs = [text_image(line.split(), 112, stroke=10) for line in lines]
    width = max(i.shape[1] for i in imgs)
    out = np.zeros((sum(i.shape[0] for i in imgs) - 40 * (len(imgs) - 1), width, 4), np.uint8)
    y = 0
    for img in imgs:
        x = (width - img.shape[1]) // 2
        region = out[y:y + img.shape[0], x:x + img.shape[1]]
        a = img[..., 3:4].astype(np.float32) / 255
        region[..., :3] = (img[..., :3] * a + region[..., :3] * (1 - a)).astype(np.uint8)
        region[..., 3] = np.maximum(region[..., 3], img[..., 3])
        y += img.shape[0] - 40
    return out


def chalk_text(text: str, size: int, colour=(240, 240, 236), reveal: float = 1.0) -> np.ndarray | None:
    """Words in the chalk hand, written on letter by letter as `reveal` goes 0 to 1."""
    shown = text[: max(0, round(len(text) * clamp01(reveal)))]
    if not shown.strip():
        return None
    f = _font(str(CAVEAT), size)
    with_full = round(f.getlength(text)) + 20
    asc, desc = f.getmetrics()
    img = Image.new("RGBA", (with_full, asc + desc + 20), (0, 0, 0, 0))
    ImageDraw.Draw(img).text((10, 10), shown, font=f, fill=(*colour, 235))
    return np.asarray(img)


# ---------- the Professor ----------

@dataclass
class Sprite:
    rgb: np.ndarray        # premultiplied colour, float32 0-255
    alpha: np.ndarray      # 0-1, float32
    shadow: np.ndarray     # a soft copy of the alpha, for his shadow on the board

    @property
    def size(self) -> tuple[int, int]:
        return self.alpha.shape[1], self.alpha.shape[0]


def load_sprite(path: Path, height: int) -> Sprite | None:
    """A pose picture as a sprite `height` tall: a cut-out (transparent background) trimmed to what's really in
    it (the faint haze some have at the edges left out); any other picture cut to a round badge with a white
    ring (a profile picture, D156)."""
    try:
        img = Image.open(path).convert("RGBA")
    except OSError:
        return None
    a = np.asarray(img.getchannel("A"))
    if a.min() < 250:
        ys, xs = np.where(a > 40)
        if not len(xs):
            return None
        img = img.crop((int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1))
    else:
        side = round(min(img.size) * 0.72)
        left, top = (img.width - side) // 2, round(img.height * 0.04)
        img = img.crop((left, top, left + side, top + side))
        mask = Image.new("L", img.size, 0)
        ImageDraw.Draw(mask).ellipse((0, 0, side - 1, side - 1), fill=255)
        img.putalpha(mask)
        ring = max(4, side // 40)
        ImageDraw.Draw(img).ellipse((ring // 2, ring // 2, side - ring // 2, side - ring // 2),
                                    outline=(255, 255, 255, 255), width=ring)
    img = img.resize((max(1, round(img.width * height / img.height)), height), Image.LANCZOS)
    arr = np.asarray(img).astype(np.float32)
    alpha = arr[..., 3] / 255.0
    return Sprite(rgb=arr[..., :3] * alpha[..., None], alpha=alpha,
                  shadow=cv2.GaussianBlur(alpha, (0, 0), 14))


def _affine(sprite: Sprite, sx: float, sy: float, angle: float, dest: tuple[float, float]) -> np.ndarray:
    """The 2x3 map from sprite pixels to frame pixels: scaled and turned about his bottom middle, which lands
    on `dest`."""
    w, h = sprite.size
    c, s = math.cos(math.radians(angle)), math.sin(math.radians(angle))
    a = np.array([[c * sx, -s * sy], [s * sx, c * sy]], np.float64)
    t = np.array(dest) - a @ np.array([w / 2, h])
    return np.hstack([a, t[:, None]]).astype(np.float32)


def _blend_warped(frame: np.ndarray, rgb: np.ndarray | None, alpha: np.ndarray, m: np.ndarray,
                  opacity: float = 1.0, colour: tuple[int, int, int] | None = None) -> None:
    """A premultiplied sprite (or, with `colour`, a flat-coloured shape from `alpha`) warped by `m` onto the
    frame, only over the box it lands in."""
    h, w = alpha.shape
    corners = np.array([[0, 0, 1], [w, 0, 1], [0, h, 1], [w, h, 1]], np.float32) @ m.T
    x0, y0 = max(0, int(corners[:, 0].min()) - 1), max(0, int(corners[:, 1].min()) - 1)
    x1, y1 = min(frame.shape[1], int(corners[:, 0].max()) + 2), min(frame.shape[0], int(corners[:, 1].max()) + 2)
    if x1 <= x0 or y1 <= y0:
        return
    local = m.copy()
    local[0, 2] -= x0
    local[1, 2] -= y0
    size = (x1 - x0, y1 - y0)
    a = cv2.warpAffine(alpha, local, size, flags=cv2.INTER_LINEAR, borderValue=0)[..., None] * opacity
    region = frame[y0:y1, x0:x1].astype(np.float32)
    if colour is not None:
        out = np.array(colour, np.float32) * a + region * (1 - a)
    else:
        c = cv2.warpAffine(rgb, local, size, flags=cv2.INTER_LINEAR, borderValue=(0, 0, 0)) * opacity
        out = c + region * (1 - a)
    frame[y0:y1, x0:x1] = np.clip(out, 0, 255).astype(np.uint8)


class Presenter:
    """The channel's character on screen all video (D159), alive (D162). `shows` is (picture, from, to) in
    seconds, covering the video; `words` the voice's words, `strong` the start times of the highlighted ones."""

    #: The spring he bounces on: how fast (Hz), how damped, and the kick each word gives (pixels a second).
    BOB_HZ, BOB_DAMP, KICK, STRONG_KICK = 3.4, 0.34, 190.0, 330.0
    TILT_HZ, TILT_DAMP, TILT_KICK = 2.1, 0.38, 26.0
    POP_FRAMES, ENTER_FRAMES = 8, 10

    def __init__(self, shows: list[tuple[Path, float, float]], words: list, strong: set[float], total: int):
        self.total = total
        self.sprites: dict[Path, Sprite] = {}
        for pic, _, _ in shows:
            if pic not in self.sprites and (sprite := load_sprite(pic, PRESENTER_H)):
                self.sprites[pic] = sprite
        self.pose: list[Path | None] = [None] * total
        for pic, a, b in shows:
            if pic in self.sprites:
                for k in range(max(0, round(a * FPS)), min(total, round(b * FPS))):
                    self.pose[k] = pic
        for k in range(1, total):   # any gap keeps the picture before it
            if self.pose[k] is None:
                self.pose[k] = self.pose[k - 1]
        first = next((p for p in self.pose if p is not None), None)
        self.pose = [p or first for p in self.pose]
        self.changes = {k for k in range(1, total) if self.pose[k] != self.pose[k - 1]}
        self.bob, self.vel, self.tilt = self._springs(words, strong)

    def _springs(self, words: list, strong: set[float]) -> tuple[list[float], list[float], list[float]]:
        kicks: dict[int, float] = {}
        turns: dict[int, float] = {}
        for i, w in enumerate(words):
            k = round(w.start * FPS)
            kicks[k] = kicks.get(k, 0.0) + (self.STRONG_KICK if any(abs(w.start - s) < 1e-3 for s in strong) else self.KICK)
            turns[k] = self.TILT_KICK * (1 if i % 2 else -1)
        for k in self.changes:   # a hop when he changes pose
            kicks[k] = kicks.get(k, 0.0) + 260.0
        bob, vel, tilt = [], [], []
        y = vy = r = vr = 0.0
        wb, wt = 2 * math.pi * self.BOB_HZ, 2 * math.pi * self.TILT_HZ
        for k in range(self.total):
            vy -= kicks.get(k, 0.0)
            vr += turns.get(k, 0.0)
            for _ in range(4):
                dt = 1 / (4 * FPS)
                vy += (-wb * wb * y - 2 * self.BOB_DAMP * wb * vy) * dt
                y += vy * dt
                vr += (-wt * wt * r - 2 * self.TILT_DAMP * wt * vr) * dt
                r += vr * dt
            bob.append(y)
            vel.append(vy)
            tilt.append(r)
        return bob, vel, tilt

    def face(self) -> tuple[float, float]:
        """Where his face is on the frame, about: for the camera cutting in on him."""
        sprite = next(iter(self.sprites.values()), None)
        w = sprite.size[0] if sprite else PRESENTER_H * 0.9
        return PRESENTER_X + w / 2, H + PRESENTER_SINK - PRESENTER_H * 0.74

    def draw(self, frame: np.ndarray, k: int) -> None:
        pic = self.pose[min(k, self.total - 1)]
        sprite = self.sprites.get(pic) if pic else None
        if sprite is None:
            return
        t = k / FPS
        scale = 1.0
        since = max((c for c in self.changes if c <= k), default=None)
        if since is not None and k - since < self.POP_FRAMES:
            scale = 0.88 + 0.12 * out_back((k - since) / self.POP_FRAMES, 2.2)
        y = self.bob[k] if k < len(self.bob) else 0.0
        if k < self.ENTER_FRAMES:   # slides up into the frame as the video starts
            y += (1 - out_back(k / self.ENTER_FRAMES, 1.2)) * 380
        stretch = max(-0.045, min(0.045, -self.vel[min(k, len(self.vel) - 1)] / 2600)) if self.vel else 0.0
        breathe = 0.006 * math.sin(2 * math.pi * t / 3.3)
        sy = scale * (1 + stretch + breathe)
        sx = scale * (1 - stretch * 0.6 - breathe * 0.4)
        w = sprite.size[0]
        dest = (PRESENTER_X + w / 2, H + PRESENTER_SINK + y)
        angle = self.tilt[k] if k < len(self.tilt) else 0.0
        m = _affine(sprite, sx, sy, angle, dest)
        shadow = m.copy()
        shadow[0, 2] += 16
        shadow[1, 2] += 6
        _blend_warped(frame, None, sprite.shadow, shadow, opacity=0.42, colour=(0, 0, 0))
        _blend_warped(frame, sprite.rgb, sprite.alpha, m)


# ---------- the whole video ----------

@dataclass
class Shot:
    """One shot of the panel: its clip (W x PANEL_H, exactly `frames` frames), where it starts on the video's
    clock (frames), what it is ("footage", "drawing", "photo", "own"), and how it comes in."""

    path: Path
    start: int
    frames: int
    kind: str = "footage"
    enter: str = "cut"   # cut | whip | zoom


@dataclass
class Cutaway:
    """The camera cutting in on the Professor (D162): from frame `a` to `b`, zoom `z` on his face, which lands at
    (`x`, `y`) on the frame. A hard cut in and out, as comedy edits do."""

    a: int
    b: int
    z: float
    x: float
    y: float


def transitions(shots: list[Shot], seed: int, mode: str = "auto") -> None:
    """How each shot comes in (D162): a cut, except where the picture changes kind (footage to drawing or
    back), where every other such change is a whip or a zoom, in turn. Never the first shot, never a shot
    under 0.5 s, never two in a row. `mode` (D165) is the user's pick: "cut" has none, "whip" or "zoom" uses
    that one at every such change, "auto" is the above."""
    if mode == "cut":
        return
    turn = seed % 2
    last = -9
    for i in range(1, len(shots)):
        a, b = shots[i - 1], shots[i]
        if a.kind == b.kind or b.frames < FPS // 2 or a.frames < FPS // 2 or i - last < 2:
            continue
        turn += 1
        if mode == "auto" and turn % 2:
            continue
        b.enter = mode if mode != "auto" else "whip" if (turn // 2) % 2 else "zoom"
        last = i


TRANSITION_FRAMES = 4


def _whip(img: np.ndarray, offset: float) -> np.ndarray:
    """The panel slid sideways by `offset` pixels and smeared along the move."""
    m = np.float32([[1, 0, offset], [0, 1, 0]])
    moved = cv2.warpAffine(img, m, (img.shape[1], img.shape[0]), borderMode=cv2.BORDER_REPLICATE)
    k = max(1, int(abs(offset) * 0.35)) | 1
    return cv2.blur(moved, (k, 1))


def _zoom(img: np.ndarray, z: float) -> np.ndarray:
    """The panel zoomed by `z` about its middle, blurred toward the edges like a fast push."""
    acc = np.zeros(img.shape, np.float32)
    for f in (0.94, 0.97, 1.0):
        acc += view(img, max(1.0, z * f), 0.5, 0.5, size=(img.shape[1], img.shape[0]))
    return (acc / 3).astype(np.uint8)


def entering(shot: Shot, k: int, img: np.ndarray, side: int) -> np.ndarray:
    """A shot's frame `k` (from its start) as it comes in, and as the shot before it goes out (`k` negative:
    frames before its start, the outgoing shot's last frames)."""
    n = TRANSITION_FRAMES
    if shot.enter == "whip":
        if -n <= k < 0:     # the outgoing shot whipped away
            x = (k + n + 1) / n
            return _whip(img, -side * W * 0.9 * x * x)
        if 0 <= k < n:      # this one whipped in
            x = 1 - (k + 1) / n
            return _whip(img, side * W * 0.9 * x * x)
    if shot.enter == "zoom":
        if -n <= k < 0:
            return _zoom(img, 1 + 0.5 * ((k + n + 1) / n) ** 2)
        if 0 <= k < n:
            return _zoom(img, 1 + 0.5 * (1 - (k + 1) / n) ** 2)
    return img


@dataclass
class Edit:
    """Everything the final pass draws, besides the shots (D162)."""

    shots: list[Shot]
    total: int                                    # frames in the video
    presenter: Presenter | None = None
    captions: Captions | None = None
    hook: np.ndarray | None = None
    hook_frames: int = 0
    watermark: np.ndarray | None = None
    signoff: str = ""
    signoff_from: int = 0
    cutaways: list[Cutaway] = field(default_factory=list)
    board: np.ndarray | None = None


def base_board(board_rgb: tuple[int, int, int], chalk: tuple[int, int, int]) -> np.ndarray:
    """The frame under everything: the chalkboard, with a chalk line along the panel's bottom edge and its
    shadow on the board."""
    from . import diagrams

    img = np.array(diagrams.board((W, H)), np.float32)
    shade = np.linspace(0.55, 1.0, 30, dtype=np.float32)[:, None, None]
    img[PANEL_H:PANEL_H + 30] *= shade
    rnd = np.random.default_rng(3)
    edge = (rnd.random(W) * 2).astype(int)
    for x in range(W):
        img[PANEL_H + edge[x]:PANEL_H + 5 + edge[x], x] = chalk
    return np.clip(img, 0, 255).astype(np.uint8)


def render(edit: Edit, out: Path, audio_inputs: list[str], audio_graph: str, encode: list[str],
           duration: float) -> Path:
    """Every frame, into `out` with the sound: the board, the panel's shots (with their way in), the
    Professor, any cut-in on him, then the words on top (captions, hook, sign-off, watermark)."""
    board = edit.board if edit.board is not None else base_board((24, 24, 26), (240, 240, 236))
    proc = subprocess.Popen(
        [str(ffmpeg_path()), "-hide_banner", "-loglevel", "error", "-y",
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
         *audio_inputs, "-filter_complex", audio_graph, "-map", "0:v", "-map", "[a]", "-t", f"{duration:.3f}",
         *encode, "-pix_fmt", "yuv420p", "-r", str(FPS), "-movflags", "+faststart", str(out)],
        stdin=subprocess.PIPE)
    try:
        for k, panel in _panel_frames(edit):
            frame = board.copy()
            frame[:PANEL_H] = panel
            if edit.presenter:
                edit.presenter.draw(frame, k)
            cut = next((c for c in edit.cutaways if c.a <= k < c.b), None)
            if cut:
                fx, fy = edit.presenter.face() if edit.presenter else (W / 2, H / 2)
                m = np.float32([[cut.z, 0, cut.x - cut.z * fx], [0, cut.z, cut.y - cut.z * fy]])
                frame = cv2.warpAffine(frame, m, (W, H), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
            t = k / FPS
            if edit.hook is not None and k < edit.hook_frames:
                left = edit.hook_frames - k
                scale = 1.0 if left > 4 else 0.86 + 0.14 * (left / 4)
                paste_scaled(frame, edit.hook, W / 2, 285, scale, alpha=1.0 if left > 4 else left / 4)
            if edit.captions:   # up out of the way while the camera is in on his face
                # With no presenter, in the middle of the board under the picture, not at its top (D164).
                y = CAPTION_Y if edit.presenter else (PANEL_H + H) // 2
                edit.captions.draw(frame, t, y if cut is None else 620)
            if edit.signoff and k >= edit.signoff_from:
                written = chalk_text(edit.signoff, 84, reveal=(k - edit.signoff_from) / (0.5 * FPS))
                if written is not None:
                    paste(frame, written, W - written.shape[1] - 46, PANEL_H + 330)
            if edit.watermark is not None and k >= edit.hook_frames:   # after the hook, which it covered
                paste(frame, edit.watermark, W - edit.watermark.shape[1] - 48, 170,
                      0.85 * min(1.0, (k - edit.hook_frames + 1) / 6))
            proc.stdin.write(np.ascontiguousarray(frame).data)
    finally:
        proc.stdin.close()
        proc.wait()
    if proc.returncode:
        raise RuntimeError("couldn't encode the video")
    return out


def _panel_frames(edit: Edit) -> Iterator[tuple[int, np.ndarray]]:
    """The panel for every frame of the video: each shot's clip in turn, as it comes in and goes out; the last
    picture held to the end."""
    shots = sorted(edit.shots, key=lambda s: s.start)
    last = np.zeros((PANEL_H, W, 3), np.uint8)
    k = 0
    for i, shot in enumerate(shots):
        nxt = shots[i + 1] if i + 1 < len(shots) else None
        end = nxt.start if nxt else edit.total
        n = max(0, end - shot.start)
        if n == 0:
            continue
        while k < shot.start:   # a gap (shouldn't happen): the last picture held
            yield k, last
            k += 1
        proc = decode(shot.path, (W, PANEL_H), seconds=n / FPS)
        side, side_next = (1 if i % 2 else -1), (1 if (i + 1) % 2 else -1)   # whips go one way, then the other
        for j, img in enumerate(frames(proc, (W, PANEL_H), n)):
            shown = entering(shot, j, img, side)
            if nxt is not None and j >= n - TRANSITION_FRAMES:
                shown = entering(nxt, j - n, shown, side_next)
            last = img
            yield k, shown
            k += 1
    while k < edit.total:
        yield k, last
        k += 1

