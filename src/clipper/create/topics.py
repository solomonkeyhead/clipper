"""The backlog: questions the channel could answer, weighted toward what worked.

On the Professor's channel the body-felt questions (a car turning, an elevator,
your hearing) did two to five times the views of the object ones (batteries,
torque, seatbelts), so most ideas are things the viewer has felt themselves,
across every corner of physics so the channel doesn't repeat itself.
"""

from __future__ import annotations

import json
import re

from pydantic import BaseModel

from ..utils.logging import get_logger
from . import channel as channels
from . import store
from .ai import CreateError, ask

log = get_logger(__name__)

SYSTEM = """You plan topics for a short-form video channel that explains everyday {subject}.
Each idea is ONE question a curious stranger would stop scrolling for, about something
they have personally felt, heard or seen. Most (about 7 in 10) should be about the
viewer's own body or senses in an everyday moment: driving, flying, cooking, showering,
sports, weather, sleep, phones, music, the gym. The rest: striking everyday objects or
events with a surprising mechanism. Spread them across {areas}. Concrete and specific ("Why does your
voice sound weird on a recording?"), never abstract ("What is entropy?"), never a
comparison of specs ("torque vs horsepower"). Each must have a real, explainable
{subject} mechanism. Do not repeat or rephrase any question already listed.

For each: question (at most 14 words, phrased to the viewer: "Why do you..." / "Why does
your..."), angle (the {subject} behind it in a few words), series (the recurring series it
belongs to, 2-3 words, such as "Kitchen Physics" or "In the Car"; reuse a series already
listed when one fits), and scores, each 0 to 3: felt (in the viewer's own body or senses),
common (how many people meet this moment), surprise (how unexpected the answer is), mechanism
(one clear mechanism, explainable in 40 seconds), showable (footage or a drawing can show it),
searched (people type this question into search), fit (it belongs on the channel: {scope};
never health advice, symptoms or treatments)."""

#: An idea scoring under this (of 21) is dropped, and so is one that doesn't fit the channel (D155).
MIN_SCORE = 15


class _Scores(BaseModel):
    felt: int = 0
    common: int = 0
    surprise: int = 0
    mechanism: int = 0
    showable: int = 0
    searched: int = 0
    fit: int = 0


class _Idea(BaseModel):
    question: str
    angle: str
    series: str = ""
    scores: _Scores = _Scores()


def _kept(ideas: list[_Idea]) -> list[dict]:
    """The ideas worth keeping, with their total score (D155)."""
    out = []
    for idea in ideas:
        sc = {k: max(0, min(3, v)) for k, v in idea.scores.model_dump().items()}
        total = sum(sc.values())
        if total < MIN_SCORE or sc["fit"] < 2:
            continue
        out.append({"question": idea.question, "angle": idea.angle, "series": " ".join(idea.series.split())[:40],
                    "felt": sc["felt"] >= 2, "score": total})
    return out


def steering(channel, steer: str = "", skipped: list[str] | None = None) -> str:
    """The owner's steering for a batch of ideas (D153). The planner's built-in leaning (most ideas about the
    viewer's body) pulled a physics channel toward biology; this outranks it."""
    return channels.steering("ideas", channel.idea_focus, channel.idea_avoid, steer, skipped,
                             yields=", including the share of ideas about the viewer's own body", never="Never make an idea about")


def viewer_comments(channel, videos: int = 10) -> list[str]:
    """Comments on the channel's latest YouTube Shorts (D156), so viewers' own questions become ideas. Nothing when
    the channel isn't connected or its comments can't be read; ideas don't wait on it."""
    from ..youtube import api as yt

    handle = channel.handle.lstrip("@").lower()
    try:
        path = next((p for p in yt.token_files() if (yt.read_token(p).get("handle") or "").lower() == handle), None)
        if not handle or path is None:
            return []
        token = yt.access_token(path)
        return yt.comments(token, [s.id for s in yt.list_shorts(token, limit=videos)][:videos])
    except Exception as exc:  # not connected, no key, a network problem: plan without them
        log.info("create: no viewer comments for ideas (%s)", str(exc)[:160])
        return []


def generate(count: int = 30, steer: str = "") -> int:
    """Add `count` new ideas to the backlog; returns how many were new."""
    channel = channels.load()
    done = [t["question"] for t in store.topics(status=None)] + [e["title"] for e in channel.examples]
    skipped = [t["question"] for t in store.topics(status="skipped")]
    series = sorted({t["series"] for t in store.topics(status=None) if t.get("series")})
    heard = viewer_comments(channel)
    user = (steering(channel, steer, skipped) + f"Channel: {channel.name} ({channel.niche}).\n"
            f"Already made or planned, don't repeat:\n" + "\n".join(f"- {q}" for q in done) +
            (f"\n\nSeries so far: {', '.join(series)}." if series else "") +
            (("\n\nWhat viewers said under recent videos. A real question here is a good idea: answer it, and put "
              "it in the series \"You Asked\":\n" + "\n".join(f"- {c}" for c in heard[:60])) if heard else "") +
            f"\n\nGive {count} new ideas.")
    answer = ask(channels.fill(SYSTEM, channel), user, list[_Idea], temperature=0.9, job="topics")
    try:
        ideas = _kept([_Idea.model_validate(i) for i in json.loads(answer)])
    except (ValueError, TypeError) as exc:
        raise CreateError("the ideas came back unreadable; try again") from exc
    return store.add_topics(ideas)


#: Words that say nothing about the subject of a question.
_PLAIN = set("why does do you your the a an when what how with make makes feel like so much more than "  # noqa: SIM905 (a word list reads better as text)
             "get gets from into out about this that there are is it its can".split())


def _subject(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z]+", text.lower()) if len(w) > 2 and w not in _PLAIN}


def made_already(question: str, titles: list[str]) -> bool:
    """Whether a Short about this question exists (D154): an idea worded differently from the video
    made of it ("helium ... like a cartoon" against "helium ... sound funny") stayed on the list."""
    mine = _subject(question)
    for title in titles:
        theirs = _subject(title)
        shared = mine & theirs
        if len(shared) >= 2 and len(shared) / max(1, min(len(mine), len(theirs))) >= 0.6:
            return True
    return False
