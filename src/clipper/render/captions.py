"""ASS subtitle generation with word-level highlighting.

Short-form captions are word-chunked and karaoke-highlighted: 2-4 words on
screen at a time, the currently-spoken one in a accent colour. libass has no
"highlight the active word" primitive, so each chunk is emitted once per word
with inline colour overrides -- N short events rather than one long one.

Colours in ASS are ``&HAABBGGRR`` (alpha, then **blue-green-red**, not RGB) and
``AA`` is *inverted* alpha, where 00 is opaque. Getting that backwards yields
invisible captions, so colours are built by `ass_colour` and never by hand.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from itertools import pairwise
from pathlib import Path

from ..config import SafeArea
from ..models import Word
from ..utils.timecode import to_ass
from .placement import place

# Alignment values in ASS "numpad" layout: 2 = bottom-centre, 5 = middle-centre.
ALIGN_BOTTOM_CENTRE = 2
ALIGN_MIDDLE_CENTRE = 5
ALIGN_TOP_CENTRE = 8
ALIGN_TOP_LEFT = 7
ALIGN_TOP_RIGHT = 9
ALIGN_BOTTOM_LEFT = 1
ALIGN_BOTTOM_RIGHT = 3

_CREDIT_ALIGNMENT = {
    "top_left": ALIGN_TOP_LEFT,
    "top_right": ALIGN_TOP_RIGHT,
    "bottom_left": ALIGN_BOTTOM_LEFT,
    "bottom_right": ALIGN_BOTTOM_RIGHT,
}

# Words masked when the campaign asks for it. Deliberately short and explicit
# rather than a broad list: over-masking mangles ordinary speech.
PROFANITY = frozenset({
    "fuck", "fucks", "fucked", "fucking", "fucker", "fuckers",
    "shit", "shits", "shitty", "bullshit",
    "bitch", "bitches", "cunt", "cunts",
    "dick", "dicks", "pussy", "asshole", "assholes",
    "motherfucker", "motherfuckers", "motherfucking",
    "nigga", "niggas", "nigger", "niggers", "faggot", "faggots",
    "whore", "whores", "slut", "sluts", "twat", "wanker",
})


def ass_colour(r: int, g: int, b: int, alpha: int = 0) -> str:
    """Build an ASS ``&HAABBGGRR`` colour from RGB.

    `alpha` is ASS transparency: 0 is fully opaque, 255 fully transparent.
    """
    for value in (r, g, b, alpha):
        if not 0 <= value <= 255:
            raise ValueError("colour components must be 0-255")
    return f"&H{alpha:02X}{b:02X}{g:02X}{r:02X}"


@dataclass(frozen=True)
class CaptionStyle:
    """One caption look. Sizes are in the 1080x1920 output's coordinate space."""

    name: str
    font: str
    font_size: int
    primary: str          # resting word colour
    highlight: str        # active word colour
    outline_colour: str
    outline: float
    shadow: float
    bold: bool = True
    uppercase: bool = False
    max_words_per_chunk: int = 6
    max_chars_per_line: int = 18
    # Two lines of up to `max_chars_per_line`: at one line of 3 words, fast
    # dialogue flipped captions about once a second, too quick to read (user
    # report on the Chad Powers clips, which asked for about double the time).
    max_lines: int = 2
    # The hook line is shouted; dialogue captions are sentence case (research
    # R5.3: all caps slows reading of longer text).
    hook_uppercase: bool = True
    # Scale the whole box when the render is not 1080 wide (e.g. --draft).
    reference_width: int = 1080


WHITE = ass_colour(255, 255, 255)
BLACK = ass_colour(0, 0, 0)
YELLOW = ass_colour(255, 214, 0)
GREEN = ass_colour(57, 255, 136)

STYLES: dict[str, CaptionStyle] = {
    # Heavy display face, white with a thick outline, active word in yellow.
    "bold_pop": CaptionStyle(
        name="bold_pop", font="Anton", font_size=96,
        primary=WHITE, highlight=YELLOW, outline_colour=BLACK,
        outline=7.0, shadow=2.0, uppercase=False, max_words_per_chunk=6,
    ),
    # Quieter: no colour shift, the active word grows instead.
    "clean_white": CaptionStyle(
        name="clean_white", font="Inter", font_size=78,
        primary=WHITE, highlight=WHITE, outline_colour=BLACK,
        outline=4.0, shadow=1.0, uppercase=False, max_words_per_chunk=8,
        max_chars_per_line=22,
    ),
    "yellow_highlight": CaptionStyle(
        name="yellow_highlight", font="Inter", font_size=84,
        primary=YELLOW, highlight=GREEN, outline_colour=BLACK,
        outline=5.0, shadow=2.0, uppercase=True, max_words_per_chunk=6,
    ),
}


def get_style(name: str) -> CaptionStyle:
    try:
        return STYLES[name]
    except KeyError:
        raise ValueError(
            f"unknown caption style {name!r}; available: {', '.join(sorted(STYLES))}"
        ) from None


@dataclass
class Chunk:
    """A group of words shown together, one of which is active at a time."""

    words: list[Word] = field(default_factory=list)

    @property
    def start(self) -> float:
        return self.words[0].start

    @property
    def end(self) -> float:
        return self.words[-1].end

    @property
    def text(self) -> str:
        return " ".join(w.text.strip() for w in self.words)


def mask_profanity(text: str) -> str:
    """Replace the inner letters of a profanity, keeping first and last.

    ``fucking`` -> ``f*****g``. Preserves word shape and length so the caption
    rhythm is unchanged, which a blanket ``****`` would not do.
    """
    def replace(match: re.Match[str]) -> str:
        word = match.group(0)
        if word.lower().strip("'") not in PROFANITY:
            return word
        if len(word) <= 2:
            return "*" * len(word)
        return word[0] + "*" * (len(word) - 2) + word[-1]

    return re.sub(r"[A-Za-z']+", replace, text)


def escape_ass_text(text: str) -> str:
    """Escape text for an ASS Dialogue line.

    ``{`` and ``}`` delimit override tags, so a literal brace in speech would
    silently swallow the rest of the caption. ``\\`` is also an escape lead-in.
    """
    return (
        text.replace("\\", "\\\\")
        .replace("{", "\\{")
        .replace("}", "\\}")
        .replace("\n", " ")
        .replace("\r", " ")
    )


def line_breaks(texts: list[str], max_chars: int) -> list[int]:
    """Indexes of the words that start a new line, filling each line greedily.

    A single word longer than a line still gets a line to itself.
    """
    breaks: list[int] = []
    length = 0
    for i, text in enumerate(texts):
        needed = len(text) if length == 0 else length + 1 + len(text)
        if length and needed > max_chars:
            breaks.append(i)
            length = len(text)
        else:
            length = needed
    return breaks


#: A page never ends on one of these: "the" belongs with its noun, "to" with its
#: verb (research R5.1: break at clause boundaries, never between an article or
#: adjective and its noun).
WEAK_ENDINGS = frozenset([
    "a", "an", "the", "my", "your", "his", "her", "its", "our", "their", "this", "that",
    "these", "those", "to", "of", "in", "on", "at", "for", "with", "from", "by", "as", "and",
    "but", "or", "nor", "so", "if", "than", "then", "because", "is", "are", "was", "were",
    "be", "been", "i", "you", "he", "she", "we", "they", "it's", "i'm", "you're", "we're",
    "they're", "i've", "i'll", "can't", "don't", "won't", "not", "no", "very", "really", "just",
])
#: Above this many words per second, pages hold at most FAST_SPEECH_WORDS
#: (research: BBC's comfortable 160-180 wpm is ~3 words/s).
FAST_SPEECH_RATE, FAST_SPEECH_WORDS = 3.3, 3
#: No page stays up for less than this (Netflix's minimum event is 5/6 s; the
#: research's short-form floor is 0.5 s).
MIN_PAGE_SECONDS = 0.5
#: Two frames at 30 fps between one page and the next (Netflix's minimum gap).
PAGE_GAP = 2 / 30


def _bare_word(text: str) -> str:
    return text.strip().strip(".,!?;:…\"'").lower()


def speech_rate(words: list[Word]) -> float:
    """Words per second while talking (pauses over 0.5 s left out)."""
    spoken = [w for w in words if w.text.strip()]
    if len(spoken) < 2:
        return 0.0
    talking = sum(min(b.start - a.start, 0.5 + (a.end - a.start))
                  for a, b in pairwise(spoken))
    return (len(spoken) - 1) / talking if talking > 0 else 0.0


def chunk_words(words: list[Word], style: CaptionStyle, *,
                max_gap: float = 0.8) -> list[Chunk]:
    """Group words into on-screen pages.

    A page breaks on any of: the word limit (fewer words when speech is fast),
    the line limit, a pause longer than `max_gap`, or sentence-ending
    punctuation -- which keeps a sentence's last word from sharing the screen
    with the next thought. A page that would end on a weak word ("the", "to")
    hands it to the next page, and a page too brief to read is merged into the
    next when both fit.
    """
    max_words = style.max_words_per_chunk
    if speech_rate(words) > FAST_SPEECH_RATE:
        max_words = min(max_words, FAST_SPEECH_WORDS)

    def fits(page: list[Word]) -> bool:
        return (len(page) <= max_words and
                len(line_breaks([w.text.strip() for w in page], style.max_chars_per_line))
                < style.max_lines)

    chunks: list[Chunk] = []
    current = Chunk()

    for word in words:
        text = word.text.strip()
        if not text:
            continue

        would_be = [*current.words, word]
        big_gap = bool(current.words) and (word.start - current.words[-1].end) > max_gap

        if current.words and (big_gap or not fits(would_be)):
            carry: list[Word] = []
            if (not big_gap and len(current.words) > 1
                    and _bare_word(current.words[-1].text) in WEAK_ENDINGS
                    and fits([current.words[-1], word])):
                carry = [current.words.pop()]
            chunks.append(current)
            current = Chunk(words=carry)

        current.words.append(word)

        if text.endswith((".", "!", "?")):
            chunks.append(current)
            current = Chunk()

    if current.words:
        chunks.append(current)
    return _merge_brief(chunks, fits)


def _merge_brief(chunks: list[Chunk], fits) -> list[Chunk]:
    """Merge a page shown under MIN_PAGE_SECONDS into the next, when that fits."""
    out: list[Chunk] = []
    i = 0
    while i < len(chunks):
        page = chunks[i]
        while i + 1 < len(chunks):
            nxt = chunks[i + 1]
            shown = nxt.start - page.start
            ends_sentence = page.words[-1].text.strip().endswith((".", "!", "?"))
            if shown >= MIN_PAGE_SECONDS or ends_sentence or not fits([*page.words, *nxt.words]):
                break
            page = Chunk(words=[*page.words, *nxt.words])
            i += 1
        out.append(page)
        i += 1
    return out


def _scaled(value: float, width: int, style: CaptionStyle) -> int:
    """Scale a style dimension from its reference width to the real output."""
    return max(1, round(value * width / style.reference_width))


def build_ass(
    words: list[Word],
    *,
    style: CaptionStyle,
    width: int,
    height: int,
    safe_area: SafeArea,
    clip_start: float = 0.0,
    duration: float | None = None,
    mask_profanity_words: bool = False,
    hook_text: str = "",
    hook_seconds: float = 0.0,
    credit_text: str = "",
    credit_position: str = "top_left",
    faces: list[tuple[float, list[tuple[float, float, float, float]]]] | None = None,
) -> str:
    """Render an ASS file for one clip.

    `faces` (clip-relative time, face boxes on the output; render/placement.py)
    moves any caption page that would cover a face.

    `words` carry source-absolute times; `clip_start` shifts them to be relative
    to the clip. Events outside [0, duration] are dropped, because the QA gate
    checks for exactly that and a stray event would be a real bug.
    """
    scale = width / style.reference_width
    font_size = _scaled(style.font_size, width, style)
    margin_v = _scaled(safe_area.bottom, width, style)
    margin_h = _scaled(safe_area.side, width, style)
    margin_r = _scaled(max(safe_area.right, safe_area.side), width, style)
    outline = round(style.outline * scale, 1)
    shadow = round(style.shadow * scale, 1)

    header = _header(
        style=style, width=width, height=height, font_size=font_size,
        margin_v=margin_v, margin_h=margin_h, margin_r=margin_r, outline=outline,
        shadow=shadow,
    )

    events: list[str] = []

    # The credit sits inside the safe area like everything else. It used to use
    # the side margin as its top margin, which put it about 100px from the top
    # of a 1920px frame -- under the platform's own top bar, measured on a real
    # render. A credit nobody can see does not meet an attribution licence.
    credit_size = max(18, round(font_size * 0.32))
    top_safe = _scaled(safe_area.top, width, style)
    credit_on_top = bool(credit_text) and credit_position.startswith("top")
    if credit_text:
        edge = top_safe if credit_on_top else _scaled(safe_area.bottom, width, style)
        events.append(_credit_event(
            credit_text, duration=duration, position=credit_position,
            font_size=credit_size, margin_v=edge,
        ))

    hook_until, hook_bottom = 0.0, top_safe
    if hook_text and hook_seconds > 0:
        # Below the credit when both are at the top, not on top of it.
        hook_top = top_safe + (round(credit_size * 1.8) if credit_on_top else 0)
        hook_size = round(font_size * 0.72)  # research R1.4: 60-72 px, white, black stroke
        hook_until = hook_duration(hook_text, hook_seconds)
        hook_bottom = hook_top + len(hook_lines(hook_text)) * hook_size * 1.2
        events.append(_hook_event(
            hook_text, hook_until, style=style, top_margin=hook_top, font_size=hook_size,
        ))

    shifted = _shift_words(words, clip_start, duration)
    chunks = chunk_words(shifted, style)
    line_height = font_size * 1.18 + outline * 2
    bottom_edge = height - margin_v
    for i, chunk in enumerate(chunks):
        # A 2-frame gap before the next page, so the change reads as a new page.
        limit = chunks[i + 1].start - PAGE_GAP if i + 1 < len(chunks) else duration
        hold = caption_hold(chunk, limit)
        where = None
        if faces:
            lines = 1 + len(line_breaks([w.text.strip() for w in chunk.words],
                                        style.max_chars_per_line))
            box = (margin_r, bottom_edge - lines * line_height, width - margin_r, bottom_edge)
            seen = [b for t, boxes in faces if chunk.start - 0.1 <= t <= hold + 0.1 for b in boxes]
            where = place(box, seen, bottom_limit=height * 1248 / 1920,
                          top_limit=(hook_bottom + 20 * scale) if chunk.start < hook_until
                          else top_safe, gap=40 * scale)
        events.extend(_chunk_events(chunk, style, mask_profanity_words, hold_until=hold,
                                    where=where, frame_height=height))

    return header + "\n".join(events) + "\n"


def _shift_words(words: list[Word], clip_start: float, duration: float | None) -> list[Word]:
    """Rebase word times onto the clip, clipping to its bounds."""
    out: list[Word] = []
    end_limit = duration if duration is not None else float("inf")
    for w in words:
        start = w.start - clip_start
        end = w.end - clip_start
        if end <= 0 or start >= end_limit:
            continue
        out.append(Word(
            start=max(0.0, start),
            end=min(end, end_limit),
            text=w.text,
            probability=w.probability,
        ))
    return out


def _header(*, style: CaptionStyle, width: int, height: int, font_size: int,
            margin_v: int, margin_h: int, outline: float, shadow: float,
            margin_r: int | None = None) -> str:
    """The Script Info and V4+ Styles sections.

    ``ScaledBorderAndShadow: yes`` makes the outline scale with PlayRes, which
    matters because we set PlayRes to the real output size.
    """
    # Captions (below y=840) keep the button rail's margin on both sides, so they
    # stay centred: an 18-character line of Anton 96 is ~420-450 px, which fits
    # x 300-780. The hook sits above the rail and uses the plain side margin.
    margin_r = margin_h if margin_r is None else margin_r
    return f"""[Script Info]
; Generated by clipper
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 2
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Caption,{style.font},{font_size},{style.primary},{style.highlight},{style.outline_colour},{BLACK},{-1 if style.bold else 0},0,0,0,100,100,0,0,1,{outline},{shadow},{ALIGN_BOTTOM_CENTRE},{margin_r},{margin_r},{margin_v},1
Style: Hook,{style.font},{font_size},{style.primary},{style.primary},{style.outline_colour},{BLACK},-1,0,0,0,100,100,0,0,1,{outline},{shadow},{ALIGN_TOP_CENTRE},{margin_h},{margin_h},{margin_v},1
Style: Credit,{style.font},{font_size},{style.primary},{style.primary},{style.outline_colour},{BLACK},0,0,0,0,100,100,0,0,1,{max(2.5, outline / 2):.1f},1,{ALIGN_TOP_LEFT},{margin_h},{margin_r},{margin_h},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def _dialogue(start: float, end: float, style_name: str, text: str, *,
              layer: int = 0, margin_v: int = 0) -> str:
    return (
        f"Dialogue: {layer},{to_ass(start)},{to_ass(end)},{style_name},,"
        f"0,0,{margin_v},,{text}"
    )


#: A caption stays up at least this many times as long as it took to say, and
#: at least MIN_CAPTION_SECONDS, unless the next caption arrives first. Held only
#: while its words were spoken, a 3-word caption in a slow scripted scene was up
#: for well under a second and gone during the pause after it -- too short to
#: read (user report on the Chad Powers clips, which asked for about double).
CAPTION_HOLD_FACTOR = 2.0
MIN_CAPTION_SECONDS = 1.2


def caption_hold(chunk: Chunk, limit: float | None) -> float:
    """When `chunk` leaves the screen: held into the following pause, never past `limit`."""
    spoken = chunk.end - chunk.start
    end = chunk.start + max(spoken * CAPTION_HOLD_FACTOR, MIN_CAPTION_SECONDS)
    if limit is not None:
        end = min(end, limit)
    return max(end, chunk.end)


def _chunk_events(chunk: Chunk, style: CaptionStyle, mask: bool, *,
                  hold_until: float | None = None,
                  where: tuple[str, float] | None = None,
                  frame_height: int = 1920) -> list[str]:
    """One event per word, each showing the whole chunk with that word active.

    libass has no karaoke-with-colour primitive that survives outline rendering
    well, so the highlight is done by re-emitting the chunk. Chunks are short,
    so the event count stays modest.
    """
    events: list[str] = []
    breaks = set(line_breaks([w.text.strip() for w in chunk.words], style.max_chars_per_line))
    for i, active in enumerate(chunk.words):
        parts: list[str] = []
        for j, word in enumerate(chunk.words):
            if j in breaks:
                parts.append("\\N")
            text = word.text.strip()
            if mask:
                text = mask_profanity(text)
            if style.uppercase:
                text = text.upper()
            text = escape_ass_text(text)
            if j == i:
                # Colour override plus a slight scale bump on the active word.
                # Research R5.3: no scale bounce above 105%.
                parts.append(f"{{\\c{style.highlight}\\fscx105\\fscy105}}{text}{{\\r}}")
            else:
                parts.append(text)

        start = active.start
        # Hold the last word until the chunk ends so the caption does not blink
        # out between chunks; earlier words hand over at the next word's start.
        end = (chunk.words[i + 1].start if i + 1 < len(chunk.words)
               else max(chunk.end, hold_until or chunk.end))
        if end <= start:
            end = start + 0.05
        text = " ".join(parts).replace(" \\N ", "\\N")
        margin_v = 0  # the style's
        if where is not None:  # moved off a face (render/placement.py)
            kind, y = where
            if kind == "top":
                text, margin_v = f"{{\\an{ALIGN_TOP_CENTRE}}}" + text, round(y)
            else:
                margin_v = round(frame_height - y)
        events.append(_dialogue(start, end, "Caption", text, margin_v=margin_v))
    return events


#: Longer hooks break onto two lines: captions never wrap (WrapStyle 2), and a
#: 50-character hook ran off both edges of the frame.
HOOK_LINE_CHARS = 28


def hook_lines(text: str, limit: int = HOOK_LINE_CHARS) -> list[str]:
    """One line, or two of balanced length when the hook is longer than `limit`."""
    words = text.split()
    if len(" ".join(words)) <= limit or len(words) < 2:
        return [" ".join(words)]
    splits = [(" ".join(words[:i]), " ".join(words[i:])) for i in range(1, len(words))]
    return list(min(splits, key=lambda pair: max(len(pair[0]), len(pair[1]))))


def hook_duration(text: str, cap: float) -> float:
    """How long the hook stays up: its reading time, 2.5 s at least, `cap` at most.

    Research R1.4: max(2.5 s, 0.3 s per word + 0.8 s), capped at 3.5 s.
    """
    words = len(text.split())
    return min(cap, max(2.5, 0.3 * words + 0.8)) if words else 0.0


def _hook_event(text: str, seconds: float, *, style: CaptionStyle,
                top_margin: int, font_size: int) -> str:
    shown = text.strip().upper() if style.hook_uppercase else text.strip()
    body = r"\N".join(escape_ass_text(line) for line in hook_lines(shown))
    return _dialogue(
        0.0, seconds, "Hook",
        f"{{\\fs{font_size}}}{body}",
        layer=1, margin_v=top_margin,
    )


def _credit_event(text: str, *, duration: float | None, position: str,
                  font_size: int, margin_v: int = 0) -> str:
    alignment = _CREDIT_ALIGNMENT.get(position, ALIGN_TOP_LEFT)
    end = duration if duration is not None else 3600.0
    body = escape_ass_text(text.strip())
    return _dialogue(0.0, end, "Credit", f"{{\\an{alignment}\\fs{font_size}}}{body}",
                     layer=2, margin_v=margin_v)


def write_ass(content: str, path: Path) -> Path:
    """Write an ASS file as UTF-8. libass expects UTF-8 and no BOM."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")
    return path


def parse_event_times(content: str) -> list[tuple[float, float, str]]:
    """Read back (start, end, text) for every Dialogue line.

    Used by the QA caption-sync check, which verifies that every transcript word
    inside the clip appears in the ASS and that no event falls outside it.
    """
    from ..utils.timecode import from_ass

    out: list[tuple[float, float, str]] = []
    for line in content.splitlines():
        if not line.startswith("Dialogue:"):
            continue
        fields = line.split(":", 1)[1].split(",", 9)
        if len(fields) < 10:
            continue
        try:
            start, end = from_ass(fields[1].strip()), from_ass(fields[2].strip())
        except (ValueError, IndexError):
            continue
        # Strip override blocks so the caller compares spoken text only.
        text = re.sub(r"\{[^}]*\}", "", fields[9]).strip()
        out.append((start, end, text))
    return out
