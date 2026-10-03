"""A script in the channel's voice, split into beats with a picture planned for each,
then checked for physics by a second, stricter pass (a wrong fact is the fastest
way to lose an educational channel's trust).

A beat is one spoken sentence, or two very short ones: one shot. Each gets either
stock footage that literally shows what's said, or an animated diagram when it
explains a mechanism -- about one beat in three, never two in a row -- so the
picture changes every 3-5 seconds instead of holding one clip for 50 (the
channel's own videos used 1-3 shots each).
"""

from __future__ import annotations

import json
import re
from typing import Literal

from pydantic import BaseModel, Field

from . import channel as channels
from .ai import CreateError, ask

PROMPT_VERSION = "create-script-v1"
TEMPLATES = ("forces", "circle", "equation", "compare", "chain", "graph")


class Visual(BaseModel):
    kind: Literal["stock", "diagram"] = "stock"
    query: str = ""                 # stock: 2-4 concrete, filmable words
    template: str = ""              # diagram: one of TEMPLATES
    title: str = ""
    labels: list[str] = Field(default_factory=list)
    values: list[float] = Field(default_factory=list)
    directions: list[str] = Field(default_factory=list)   # forces: up | down | left | right, per label
    equation: str = ""
    shape: str = ""                 # graph: rising | falling | peak | wave


class Beat(BaseModel):
    text: str
    emphasis: str = ""              # the one word to highlight
    visual: Visual = Field(default_factory=Visual)


class Script(BaseModel):
    title: str
    beats: list[Beat]
    description: str = ""
    hashtags: list[str] = Field(default_factory=list)

    @property
    def text(self) -> str:
        return " ".join(b.text.strip() for b in self.beats)

    @property
    def words(self) -> int:
        return len(self.text.split())


VISUALS = """Split the script into beats: one spoken sentence each (two only if both are very
short), 5 to 14 words. For every beat plan ONE picture:
- kind "stock": real footage that literally shows what is said, as a search query of 2-4
  concrete, filmable words ("boiling pasta pot", "airplane window clouds", "elevator doors
  closing", "person touching cold metal railing"). Never abstract words ("pressure",
  "physics", "energy").
- kind "diagram": an animated diagram, when the beat explains HOW it works. About one beat in
  three; never two diagrams in a row; never the first beat. Templates:
  * forces: an object with labelled arrows. labels = the forces ("gravity", "floor pushes
    up"), directions = "up"/"down"/"left"/"right" for each, values = their relative sizes.
  * circle: something moving on a circle, with its velocity and the inward pull. labels =
    [what moves, the inward force].
  * equation: a formula built up piece by piece. equation = the formula ("F = m x a"),
    labels = what each symbol means ("F: force", "m: mass", "a: acceleration").
  * compare: two things side by side as bars. labels = [thing A, thing B], values = their
    sizes, title = what is compared ("Heat flow").
  * chain: causes leading to an effect, 2-4 short steps. labels = the steps.
  * graph: a curve. title = what it shows, labels = [x axis, y axis], shape =
    "rising"/"falling"/"peak"/"wave".
  Keep every label under 4 words.
For each beat also give emphasis: the single most important word in it, copied exactly.

Also write: title (the question, at most 60 characters), description (the script's idea in
3-5 short lines, in the same voice, ending on its punchline), hashtags (3: #physics,
#science and one specific)."""


def _system(channel: channels.Channel) -> str:
    rules = "\n".join(f"- {r}" for r in channel.rules)
    return (f"{channel.persona}\n\nYou write the scripts for the YouTube Shorts channel "
            f"{channel.name} ({channel.niche}). Rules:\n{rules}\n\n{VISUALS}\n\n"
            "The examples are the channel's own scripts: match their voice, rhythm and humour, "
            "never reuse their jokes or lines.")


def write(question: str, angle: str = "", *, take: int = 1, feedback: str = "") -> Script:
    """A new script for `question`; `take` asks for a fresh attempt, `feedback` for fixes."""
    channel = channels.load()
    user = (f"The channel's best scripts:\n\n{channels.examples_block(channel)}\n\n"
            f"Write a new script answering: {question}\n" + (f"(The physics: {angle})\n" if angle else "")
            + (f"\nFix these problems from the last draft:\n{feedback}\n" if feedback else "")
            + f"\n(take {take})")
    answer = ask(_system(channel), user, Script, temperature=0.85)
    try:
        script = Script.model_validate(json.loads(answer))
    except (ValueError, TypeError) as exc:
        raise CreateError("the script came back unreadable; try again") from exc
    return tidy(script)


def tidy(script: Script) -> Script:
    """Rules the model can bend, put straight: known templates, no diagram first or twice
    in a row, an emphasis word that's really in its beat, at most 5 hashtags."""
    beats = []
    for i, beat in enumerate(script.beats):
        v = beat.visual
        diagram = v.kind == "diagram" and v.template in TEMPLATES
        if diagram and (i == 0 or (beats and beats[-1].visual.kind == "diagram")):
            diagram = False
        if not diagram:
            v = v.model_copy(update={"kind": "stock", "query": v.query or _query_from(beat.text)})
        words = {w.strip(".,!?;:'\"").lower() for w in beat.text.split()}
        emphasis = beat.emphasis if beat.emphasis.strip(".,!?").lower() in words else ""
        beats.append(beat.model_copy(update={"visual": v, "emphasis": emphasis}))
    tags = [("#" + t.lstrip("#")).replace(" ", "") for t in script.hashtags if t.strip("# ")][:5]
    return script.model_copy(update={"beats": beats, "hashtags": tags})


def _query_from(text: str) -> str:
    """A fallback stock query: the beat's longest words."""
    words = sorted({w.lower() for w in re.findall(r"[A-Za-z]{4,}", text)}, key=len, reverse=True)
    return " ".join(words[:3])


class Review(BaseModel):
    ok: bool
    problems: list[str] = Field(default_factory=list)


CHECK = """You are a physics professor checking a 45-second educational script for a general
audience. Simplifying is fine; stating something false is not. Flag only real errors: wrong
mechanisms, wrong formulas, wrong numbers, misleading claims, or a myth stated as fact. Jokes
and analogies are fine unless they teach something false. Return ok=true with no problems if
it is correct. Otherwise list each problem in one sentence with the correct physics."""


def check(script: Script) -> Review:
    answer = ask(CHECK, f"Title: {script.title}\nScript:\n{script.text}", Review, temperature=0.0)
    try:
        return Review.model_validate(json.loads(answer))
    except (ValueError, TypeError):
        return Review(ok=False, problems=["The physics check came back unreadable; read it carefully yourself."])


def write_checked(question: str, angle: str = "", *, take: int = 1) -> tuple[Script, str]:
    """A script that passed the physics check (one rewrite if it didn't), and what the
    check said, for the page."""
    script = write(question, angle, take=take)
    review = check(script)
    if not review.ok and review.problems:
        script = write(question, angle, take=take, feedback="\n".join(f"- {p}" for p in review.problems))
        second = check(script)
        if not second.ok and second.problems:
            return script, "Physics check, still unsure:\n" + "\n".join(f"- {p}" for p in second.problems)
        return script, "Physics check: fixed after a first draft got this wrong:\n" + \
            "\n".join(f"- {p}" for p in review.problems)
    return script, "Physics check: no problems found."
