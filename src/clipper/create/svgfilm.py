"""Animated SVG drawings filmed frame by frame in a headless browser (D219).

Opus draws a sketch as one SVG with CSS animations (create/sketch.draw_svg) instead of a list of chalk marks: fills,
cut-aways, a chalk texture, and things that really move. The animations are timed to the sentence's words through
CSS variables, --w1 for the first word, --w2 for the second..., and --d for the shot's length, so the drawing
keeps time with whatever voice is recorded later. Here a page holds the SVG with those variables set, every
animation is paused and set to each frame's moment, and the page is photographed.

The browser is Edge (on every Windows machine) or Chrome through Playwright, else Playwright's own Chromium.
No browser: `ready()` is False, and sketches are drawn the old way, as marks.
"""

from __future__ import annotations

import base64
import io
import re
from contextlib import contextmanager
from functools import cache
from html import escape, unescape

from PIL import Image

from ..utils.logging import get_logger

log = get_logger(__name__)

#: The voice's pace, for times guessed before the voice is recorded (the look-and-fix round's frames).
WORDS_PER_SECOND = 2.3

#: Pauses every animation, CSS or SMIL, at the moment `t` seconds.
SEEK = """t => {
  for (const a of document.getAnimations()) { a.pause(); a.currentTime = t * 1000; }
  for (const s of document.querySelectorAll('svg')) { if (s.pauseAnimations) { s.pauseAnimations(); s.setCurrentTime(t); } }
}"""


def words(text: str) -> list[str]:
    """The sentence's words as the drawer numbers them, w1 first."""
    return text.split()


def timings(n: int, said: list[tuple[float, str]] | None, seconds: float) -> list[float]:
    """When each of the `n` numbered words is said, seconds into the shot: from the voice's words when there are as many,
    spread over them by position when the counts differ (a number read as two words), else at the usual pace.
    ponytail: by position, not by matching the words; match them if a drawing lands on the wrong word."""
    if said:
        if len(said) == n:
            return [t for t, _ in said]
        return [said[min(len(said) - 1, k * len(said) // max(n, 1))][0] for k in range(n)]
    pace = min(1 / WORDS_PER_SECOND, 0.85 * seconds / max(n, 1))
    return [k * pace for k in range(n)]


def guessed(n_words: int) -> float:
    """A shot's length before the voice exists: its words at the usual pace, and a moment to land."""
    return round(n_words / WORDS_PER_SECOND + 0.6, 2)


@cache
def _font(name: str) -> str:
    """A font as a data address: a page set from text can't load a file from disk, and the labels came out in Times."""
    from . import diagrams

    path = diagrams.FONT if name == "Caveat" else diagrams.PLAIN
    return "data:font/ttf;base64," + base64.b64encode(path.read_bytes()).decode() if path.is_file() else ""


def _page(svg: str, at: list[float], seconds: float, board: tuple[int, int, int], width: int, height: int,
          scale: float = 1.0, shift: float = 0.0) -> str:
    variables = "".join(f"--w{k + 1}:{t:.3f}s;" for k, t in enumerate(at)) + f"--d:{seconds:.3f}s;"
    r, g, b = board
    return (f"<!doctype html><html><head><meta charset='utf-8'><style>"
            f"@font-face{{font-family:'Caveat';src:url('{_font('Caveat')}')}}"
            f"@font-face{{font-family:'Inter';src:url('{_font('Inter')}')}}"
            f":root{{{variables}}}"
            f"html,body{{margin:0;width:{width}px;height:{height}px;overflow:hidden;background:rgb({r},{g},{b})}}"
            # The user's size and place for a drawing (D171), as for the marks.
            f"body>svg{{display:block;width:{width}px;height:{height}px;transform-origin:50% 50%;"
            f"transform:translateY({shift * height:.1f}px) scale({scale})}}"
            f"</style></head><body>{svg}</body></html>")


@contextmanager
def _browser():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        last = None
        for channel in ("msedge", "chrome", None):
            try:
                b = p.chromium.launch(channel=channel) if channel else p.chromium.launch()
                break
            except Exception as exc:   # this browser isn't installed: the next
                last = exc
        else:
            raise RuntimeError(f"no browser to film drawings in ({last})")
        try:
            yield b
        finally:
            b.close()


@cache
def ready() -> bool:
    """Whether drawings can be filmed here (asked once per process)."""
    try:
        with _browser():
            return True
    except Exception as exc:
        log.info("create: SVG drawings off, no browser to film them (%s)", exc)
        return False


@contextmanager
def film(svg: str, at: list[float], seconds: float, board: tuple[int, int, int], width: int, height: int,
         scale: float = 1.0, shift: float = 0.0):
    """A camera on the drawing: yields `shoot(t)`, the picture at `t` seconds as an RGB image."""
    with _browser() as b:
        page = b.new_page(viewport={"width": width, "height": height})
        page.set_content(_page(svg, at, seconds, board, width, height, scale, shift))
        page.evaluate("document.fonts.ready.then(() => true)")

        def shoot(t: float) -> Image.Image:
            page.evaluate(SEEK, t)
            return Image.open(io.BytesIO(page.screenshot(type="png"))).convert("RGB")

        yield shoot


def sheet(svg: str, text: str, board: tuple[int, int, int], width: int, height: int, n: int = 10) -> tuple[bytes, list[float]]:
    """`n` moments of the drawing, evenly through it, side by side (5 across), for the look-and-fix round; and
    their times. Timed at the usual pace, since the voice isn't recorded yet."""
    count = len(words(text))
    seconds = guessed(count)
    times = [round(seconds * (k + 0.5) / n, 2) for k in range(n)]
    small = (width // 3, height // 3)
    cols = 5
    out = Image.new("RGB", (small[0] * cols, small[1] * ((n + cols - 1) // cols)), board)
    with film(svg, timings(count, None, seconds), seconds, board, width, height) as shoot:
        for k, t in enumerate(times):
            out.paste(shoot(t).resize(small), ((k % cols) * small[0], (k // cols) * small[1]))
    buf = io.BytesIO()
    out.save(buf, "PNG")
    return buf.getvalue(), times


_TEXT = re.compile(r"(<text\b[^>]*>)(.*?)(</text>)", re.S)


def texts(svg: str) -> list[str]:
    """The words written on the drawing, in order (a <text> with <tspan>s inside read as one)."""
    return [" ".join(unescape(re.sub(r"<[^>]+>", " ", m.group(2))).split()) for m in _TEXT.finditer(svg)]


def set_texts(svg: str, new: list[str]) -> str:
    """The drawing with its words replaced in order, as the user typed them; an empty one keeps the drawing's."""
    it = iter(new)

    def one(m: re.Match) -> str:
        word = next(it, "")
        return f"{m.group(1)}{escape(word)}{m.group(3)}" if word.strip() else m.group(0)

    return _TEXT.sub(one, svg)


def clean(svg: str) -> str:
    """Just the <svg> element, with nothing that runs or loads: no scripts, event handlers or outside links."""
    start, end = svg.find("<svg"), svg.rfind("</svg>")
    if start < 0 or end < start:
        raise ValueError("no <svg> element")
    svg = svg[start:end + 6]
    svg = re.sub(r"<script\b.*?</script>", "", svg, flags=re.S | re.I)
    svg = re.sub(r"<foreignObject\b.*?</foreignObject>", "", svg, flags=re.S | re.I)
    svg = re.sub(r"\son\w+\s*=\s*(\"[^\"]*\"|'[^']*')", "", svg, flags=re.I)
    svg = re.sub(r"@import[^;]*;", "", svg, flags=re.I)
    svg = re.sub(r"url\(\s*['\"]?(?!#)[^)]*\)", "none", svg, flags=re.I)   # url(#chalk) stays, url(http...) goes
    return re.sub(r"(href\s*=\s*[\"'])(?!#)[^\"']*", r"\1#", svg, flags=re.I)
