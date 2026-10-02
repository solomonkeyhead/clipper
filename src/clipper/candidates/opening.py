"""Choosing the line each selected clip opens on (D93).

Viewers decide in the first second or two, and the clips rated "weak hook" did
not lose them on the on-screen text but on the first line said: some opened on
small talk before the moment ("Okay, did you have fun?"), others on the answer
to a question left in the previous shot ("vote yes", "spot-on average"), and one
a line after its own setup ("I got so scared when we hit turbulence...").
Candidate windows start where a sentence or scene starts, which says nothing
about whether that line hooks.

So once clips are chosen and before any is rendered, one text call per source
looks at each clip's opening lines plus a few said just before it, and picks
the line to start on: the latest one that keeps everything the payoff needs.
Code bounds the choice -- the clip stays within the campaign's length, never
starts on a line that continues an earlier one, and only reaches back across a
camera cut while the conversation runs on without a pause. Without an answer,
the code rule alone still moves a mid-sentence start back to its sentence.
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

PROMPT_VERSION = "opening-v1"
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
REST_CHARS = 500

SYSTEM = """\
You edit short vertical clips cut from TV shows, films and videos. For each clip, \
choose the line it should START on.

Viewers decide in the first one or two seconds whether to keep watching, so the \
first line must make a stranger who has never seen the show want to hear the next \
one.

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

Only choose from the line numbers listed as allowed for that clip.
Return JSON: [{"clip": <clip number>, "start_line": <line number>}, ...], one per clip.\
"""


class _Choice(BaseModel):
    clip: int
    start_line: int


@dataclass
class _Options:
    candidate: Candidate
    current: int
    allowed: list[int]


def options(candidate: Candidate, sentences: list[Sentence], *,
            min_seconds: float, max_seconds: float) -> _Options | None:
    """The lines `candidate` may open on, or None if it can't move."""
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
    return _Options(candidate, lo, allowed) if allowed else None


def build_user(items: list[_Options], sentences: list[Sentence]) -> str:
    blocks = []
    for n, item in enumerate(items, 1):
        c = item.candidate
        lo, hi = c.sentence_indices
        first = min([*item.allowed, lo])
        last = max([*item.allowed, lo])
        lines = [f"Clip {n} (now {c.duration:.0f}s long):"]
        for k in range(first, last + 1):
            s = sentences[k]
            tag = "before the clip" if k < lo else ("CURRENT START" if k == lo else "")
            lines.append(f"  [{k}]{f' ({tag})' if tag else ''} {s.text.strip()}")
        rest = " ".join(s.text.strip() for s in sentences[last + 1:hi])
        if rest:
            lines.append(f"  rest of the clip: {rest[:REST_CHARS]}{'...' if len(rest) > REST_CHARS else ''}")
        lines.append(f"  allowed start lines: {', '.join(map(str, item.allowed))}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def choose(candidates: list[Candidate], sentences: list[Sentence], backends: list[LLMBackend], *,
           min_seconds: float, max_seconds: float, cache: LLMCache | None = None) -> dict[str, int]:
    """candidate_id -> the sentence index it should open on, where that differs."""
    items = [o for c in candidates[:MAX_CLIPS]
             if (o := options(c, sentences, min_seconds=min_seconds, max_seconds=max_seconds))]
    picked: dict[str, int] = {}
    if items and backends:
        from ..transcribe.correct import _ask

        answered = _ask(backends, SYSTEM, build_user(items, sentences), list[_Choice],
                        cache=cache, prompt_key=PROMPT_VERSION)
        for choice in _parse(answered[0] if answered else ""):
            if 1 <= choice.clip <= len(items):
                item = items[choice.clip - 1]
                if choice.start_line in item.allowed:
                    picked[item.candidate.candidate_id] = choice.start_line
    out: dict[str, int] = {}
    for item in items:
        lo = item.current
        k = picked.get(item.candidate.candidate_id)
        if k is None and _continues(sentences[lo].text):
            # No answer, and the clip opens mid-sentence: back to where it began.
            earlier = [a for a in item.allowed if a < lo]
            k = max(earlier) if earlier else None
        if k is not None and k != lo:
            out[item.candidate.candidate_id] = k
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
          min_seconds: float, max_seconds: float, cache: LLMCache | None = None) -> int:
    """Move each pick's start to its chosen line, in place. Returns how many moved."""
    starts = choose([p.candidate for p in picks], sentences, backends,
                    min_seconds=min_seconds, max_seconds=max_seconds, cache=cache)
    for pick in picks:
        k = starts.get(pick.candidate.candidate_id)
        if k is None:
            continue
        lo = pick.candidate.sentence_indices[0]
        log.info("%s now opens on %r (%s %d line(s))", pick.candidate.candidate_id,
                 sentences[k].text.strip()[:60], "back" if k < lo else "forward", abs(k - lo))
        pick.candidate = moved(pick.candidate, k, sentences)
    return len(starts)


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
