"""The backlog: questions the channel could answer, weighted toward what worked.

On the Professor's channel the body-felt questions (a car turning, an elevator,
your hearing) did two to five times the views of the object ones (batteries,
torque, seatbelts), so most ideas are things the viewer has felt themselves,
across every corner of physics so the channel doesn't repeat itself.
"""

from __future__ import annotations

import json

from pydantic import BaseModel

from . import channel as channels
from . import store
from .ai import CreateError, ask

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
your..."), angle (the {subject} behind it in a few words), felt (true if it's about the
viewer's own body or senses)."""


class _Idea(BaseModel):
    question: str
    angle: str
    felt: bool


def steering(channel, steer: str = "", skipped: list[str] | None = None) -> str:
    """The owner's steering for a batch of ideas (D153). The planner's built-in leaning (most ideas about the
    viewer's body) pulled a physics channel toward biology; this outranks it."""
    return channels.steering("ideas", channel.idea_focus, channel.idea_avoid, steer, skipped,
                             yields=", including the share of ideas about the viewer's own body", never="Never make an idea about")


def generate(count: int = 30, steer: str = "") -> int:
    """Add `count` new ideas to the backlog; returns how many were new."""
    channel = channels.load()
    done = [t["question"] for t in store.topics(status=None)] + [e["title"] for e in channel.examples]
    skipped = [t["question"] for t in store.topics(status="skipped")]
    user = (steering(channel, steer, skipped) + f"Channel: {channel.name} ({channel.niche}).\n"
            f"Already made or planned, don't repeat:\n" + "\n".join(f"- {q}" for q in done) +
            f"\n\nGive {count} new ideas.")
    answer = ask(channels.fill(SYSTEM, channel), user, list[_Idea], temperature=0.9, job="topics")
    try:
        ideas = [_Idea.model_validate(i).model_dump() for i in json.loads(answer)]
    except (ValueError, TypeError) as exc:
        raise CreateError("the ideas came back unreadable; try again") from exc
    return store.add_topics(ideas)
