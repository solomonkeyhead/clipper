"""Choosing the line each selected clip opens on, and the payoff it teases (D93, D97).

Viewers decide in the first second or two, and the clips rated "weak hook" did
not lose them on the on-screen text but on the first line said: some opened on
small talk before the moment ("Okay, did you have fun?"), others on the answer
to a question left in the previous shot ("vote yes", "spot-on average"), and one
a line after its own setup ("I got so scared when we hit turbulence...").
Candidate windows start where a sentence or scene starts, which says nothing
about whether that line hooks.

So once clips are chosen and before any is rendered, one text call per source
looks at each clip's lines plus a few said just before it, and picks the line to
start on: the latest one that keeps everything the payoff needs. Code bounds the
choice -- the clip stays within the campaign's length, never starts on a line
that continues an earlier one, and only reaches back across a camera cut while
the conversation runs on without a pause. Without an answer, the code rule alone
still moves a mid-sentence start back to its sentence.

The same call names the clip's payoff: the line people would quote. Platform
research (D95) says the clips that hold viewers open on it, then play the setup,
so render/teaser.py shows it for a second or two before the clip. Code keeps it
to a whole line of 0.8-3.5 seconds at least 4 seconds in, and the clip with its
teaser within the length limit; a clip with no line that works alone gets none.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from pydantic import BaseModel, ValidationError

from ..llm.base import LLMBackend
from ..llm.cache import LLMCache
from ..models import Candidate, Sentence
from ..utils.logging import get_logger
from .windows import _continues

log = get_logger(__name__)

PROMPT_VERSION = "opening-v5"
#: Lines said just before a clip that it may reach back to.
BEFORE_LINES = 4
#: Lines into a clip it may start later on.
AFTER_LINES = 6
#: A longer pause than this ends the exchange: an earlier line is another moment.
SAME_EXCHANGE_GAP = 2.0
#: Across a camera cut, only while lines run on with less pause than this.
ACROSS_CUT_GAP = 1.0
#: Clips sent to the model per source: the picks and the first few reserves.
MAX_CLIPS = 30
#: Lines of one clip shown; a longer clip's tail is summarised.
MAX_LINES = 40
REST_CHARS = 500
#: A payoff teaser: a whole line this long (render/teaser.py), this far in at least.
PAYOFF_SECONDS = (0.8, 3.5)
PAYOFF_INTO_CLIP = 4.0
#: ...and at most this far: a longer setup after the teaser and viewers give up (D107).
PAYOFF_MAX_SETUP = 45.0
#: A payoff in the last part of the clip ends it (plus a breath), so it loops into the teaser.
PAYOFF_ENDS_AFTER = 0.7
PAYOFF_TAIL = 0.4
#: Only a payoff the model rates at least this is teased: a weak teaser is worse than none.
PAYOFF_MIN_STRENGTH = 8

SYSTEM = """\
You edit short vertical clips cut from TV shows, films and videos. For each clip, \
choose the line it should START on, and the line that is its PAYOFF.

START. Viewers decide in the first one or two seconds whether to keep watching, so \
the first line must make a stranger who has never seen the show want to hear the \
next one.

Strong first lines: a question or claim that raises tension; a strange, rude or \
provocative statement; the start of an argument or confession; the setup line of \
the joke or reveal the clip pays off.

Weak first lines, to cut: greetings, small talk, logistics and warm-up before the \
moment starts ("Okay, did you have fun?", "So I got three waters"); a reply or \
reaction whose prompt is not in the clip ("Yeah, and then he...", "Vote yes"); a \
line that only makes sense after something the clip leaves out.

Keep everything the payoff needs. If the joke or the turn depends on an earlier \
line, start at or before that line -- a line said just before the clip may be the \
real setup. Cut warm-up, never setup: choose the LATEST line that still keeps the \
whole setup. If the current start is already the best, return it.

PAYOFF. The one line a viewer would quote: the punchline, the reveal, the most \
outrageous or awkward thing said. Its FIRST part is ALSO shown for a second or two \
before the clip begins, as a teaser that stops just before the line lands, so its \
opening words must already raise the stakes and make a stranger need the ending \
and to see how it got there ("I'm not sure that I thought it through!", "Because you \
told them that I gave you chlamydia."). It must be surprising, funny or shocking \
read cold, with no context at all. Not a teaser: a plain answer or agreement ("It's \
definitely true", "Yeah, exactly"), a vague line ("What are you hiding?"), or one \
whose meaning needs the scene ("I've never seen you dance"). It must come after the \
start you chose. If no line works on its own, use null -- a weak teaser is worse \
than none.

Only choose from the line numbers listed as allowed for that clip.
Rate the payoff as payoff_strength, 1-10: how hard it would stop a stranger who has \
seen nothing else. 9-10 is a line people screenshot; 5 is mildly interesting; a \
plain answer or vague line is 1-3. Be harsh.

Return JSON: [{"clip": <clip number>, "start_line": <line number>, \
"payoff_line": <line number or null>, "payoff_strength": <1-10>}, ...], one per clip.\
"""


class _Choice(BaseModel):
    clip: int
    start_line: int
    payoff_line: int | None = None
    payoff_strength: int = 0


@dataclass
class _Options:
    candidate: Candidate
    current: int
    allowed: list[int]
    payoffs: list[int]


@dataclass
class Opening:
    """Where a clip starts (a sentence index) and its payoff line, if any."""

    start: int
    payoff: int | None = None


def options(candidate: Candidate, sentences: list[Sentence], *,
            min_seconds: float, max_seconds: float) -> _Options | None:
    """The lines `candidate` may open on and tease, or None if it can't move."""
    lo, hi = candidate.sentence_indices
    if candidate.quiet or not 0 <= lo < hi <= len(sentences):
        return None
    end = candidate.end
    allowed: list[int] = []
    k = lo
    while k > 0 and lo - k < BEFORE_LINES:
        gap = sentences[k].gap_before
        crossing_cut = (candidate.scene_start is not None
                        and sentences[k - 1].start < candidate.scene_start - 0.05)
        if gap > (ACROSS_CUT_GAP if crossing_cut else SAME_EXCHANGE_GAP):
            break
        if end - sentences[k - 1].start > max_seconds:
            break
        k -= 1
        allowed.append(k)
    for k in range(lo, min(hi, lo + AFTER_LINES + 1)):
        if k > lo and end - sentences[k].start < min_seconds:
            break
        allowed.append(k)
    allowed = sorted(k for k in set(allowed) if not _continues(sentences[k].text))
    if not allowed:
        return None
    low, high = PAYOFF_SECONDS
    payoffs = [k for k in range(min(allowed) + 1, hi)
               if low <= sentences[k].duration <= high and not _continues(sentences[k].text)]
    return _Options(candidate, lo, allowed, payoffs)


def teaser_fits(start: int, payoff: int, end: float, sentences: list[Sentence], max_seconds: float) -> bool:
    """A payoff after `start`, far enough in, with the clip and teaser in length."""
    line = sentences[payoff]
    return (payoff > start and PAYOFF_INTO_CLIP <= line.start - sentences[start].start <= PAYOFF_MAX_SETUP
            and end - sentences[start].start + line.duration <= max_seconds)


def build_user(items: list[_Options], sentences: list[Sentence]) -> str:
    blocks = []
    for n, item in enumerate(items, 1):
        c = item.candidate
        lo, hi = c.sentence_indices
        first = min([*item.allowed, lo])
        last = min(hi, first + MAX_LINES)
        lines = [f"Clip {n} (now {c.duration:.0f}s long):"]
        for k in range(first, last):
            s = sentences[k]
            tag = "before the clip" if k < lo else ("CURRENT START" if k == lo else "")
            lines.append(f"  [{k}]{f' ({tag})' if tag else ''} {s.text.strip()}")
        rest = " ".join(s.text.strip() for s in sentences[last:hi])
        if rest:
            lines.append(f"  rest of the clip: {rest[:REST_CHARS]}{'...' if len(rest) > REST_CHARS else ''}")
        lines.append(f"  allowed start lines: {', '.join(map(str, item.allowed))}")
        lines.append(f"  allowed payoff lines: {', '.join(str(k) for k in item.payoffs if k < last) or 'none'}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def choose(candidates: list[Candidate], sentences: list[Sentence], backends: list[LLMBackend], *,
           min_seconds: float, max_seconds: float, cache: LLMCache | None = None,
           payoff: bool = True) -> dict[str, Opening]:
    """candidate_id -> its opening, where the start moves or a payoff is teased."""
    items = [o for c in candidates[:MAX_CLIPS]
             if (o := options(c, sentences, min_seconds=min_seconds, max_seconds=max_seconds))]
    picked: dict[str, _Choice] = {}
    if items and backends:
        from ..transcribe.correct import _ask

        answered = _ask(backends, SYSTEM, build_user(items, sentences), list[_Choice],
                        cache=cache, prompt_key=PROMPT_VERSION)
        for choice in _parse(answered[0] if answered else ""):
            if 1 <= choice.clip <= len(items):
                item = items[choice.clip - 1]
                if choice.start_line in item.allowed:
                    picked[item.candidate.candidate_id] = choice
    out: dict[str, Opening] = {}
    for item in items:
        lo = item.current
        choice = picked.get(item.candidate.candidate_id)
        k = choice.start_line if choice else None
        if k is None and _continues(sentences[lo].text):
            # No answer, and the clip opens mid-sentence: back to where it began.
            earlier = [a for a in item.allowed if a < lo]
            k = max(earlier) if earlier else None
        start = lo if k is None else k
        tease = (choice.payoff_line if choice and payoff and choice.payoff_strength >= PAYOFF_MIN_STRENGTH
                 else None)
        if tease is not None and (tease not in item.payoffs
                                  or not teaser_fits(start, tease, item.candidate.end, sentences, max_seconds)):
            tease = None
        if start != lo or tease is not None:
            out[item.candidate.candidate_id] = Opening(start, tease)
    return out


def moved(candidate: Candidate, k: int, sentences: list[Sentence]) -> Candidate:
    """`candidate` opening on sentence `k` instead."""
    hi = candidate.sentence_indices[1]
    start = sentences[k].start
    update: dict = {"start": start, "sentence_indices": (k, hi),
                    "text": " ".join(s.text for s in sentences[k:hi]).strip()}
    if candidate.scene_start is not None and start < candidate.scene_start:
        # The cut fell inside the exchange: the scene now starts with its setup.
        before = sentences[k - 1].end if k > 0 else 0.0
        update["scene_start"] = max(before, start - 0.3)
    return candidate.model_copy(update=update)


def apply(picks: list, sentences: list[Sentence], backends: list[LLMBackend], *,
          min_seconds: float, max_seconds: float, cache: LLMCache | None = None,
          payoff: bool = True) -> int:
    """Move each pick's start to its chosen line and mark its payoff, in place.
    Returns how many changed."""
    chosen = choose([p.candidate for p in picks], sentences, backends,
                    min_seconds=min_seconds, max_seconds=max_seconds, cache=cache, payoff=payoff)
    for pick in picks:
        opening = chosen.get(pick.candidate.candidate_id)
        if opening is None:
            continue
        lo = pick.candidate.sentence_indices[0]
        if opening.start != lo:
            log.info("%s now opens on %r (%s %d line(s))", pick.candidate.candidate_id,
                     sentences[opening.start].text.strip()[:60], "back" if opening.start < lo else "forward",
                     abs(opening.start - lo))
            pick.candidate = moved(pick.candidate, opening.start, sentences)
        if opening.payoff is not None:
            line = sentences[opening.payoff]
            log.info("%s teases %r", pick.candidate.candidate_id, line.text.strip()[:60])
            pick.candidate = pick.candidate.model_copy(update={"payoff": (line.start, line.end)})
    return len(chosen)


def _parse(text: str) -> list[_Choice]:
    try:
        data = json.loads(text) if text else []
    except json.JSONDecodeError:
        return []
    out = []
    for item in data if isinstance(data, list) else []:
        try:
            out.append(_Choice.model_validate(item))
        except ValidationError:
            continue
    return out
