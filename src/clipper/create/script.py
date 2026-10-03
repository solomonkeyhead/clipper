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

PROMPT_VERSION = "create-script-v2"
TEMPLATES = ("forces", "circle", "equation", "compare", "chain", "graph")


class Visual(BaseModel):
    kind: Literal["stock", "diagram"] = "stock"
    query: str = ""                 # stock: the first search (kept for older scripts)
    queries: list[str] = Field(default_factory=list)   # stock: searches, most specific first
    card: str = ""                  # if no footage fits: the phrase chalked on the board instead
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
- kind "stock": real footage that literally shows what is said. Give queries: three searches
  of a free stock-footage library, most specific first, then broader, 1-3 words each, naming
  subjects such libraries really have: objects, places, nature, everyday scenes, and people
  only in common situations ("woman wearing headphones", "man talking on phone", "boiling
  pot", "airplane window", "elevator doors"). Never abstract words ("pressure", "physics",
  "energy"), never a specific person's action that nobody films ("person touching side of
  head"). Also give card: a 1-4 word phrase from the sentence to write on the chalkboard if
  no footage fits ("bone conduction", "100 degrees").
- kind "diagram": an animated diagram, when the beat explains HOW it works. About one beat in
  three; never two diagrams in a row; never the first beat. Templates:
  * forces: an object with labelled arrows. labels = the forces ("gravity", "floor pushes
    up"), directions = "up"/"down"/"left"/"right" for each, values = their relative sizes.
  * circle: something moving on a circle, with its velocity and the inward pull. labels =
    [what moves, the inward force].
  * equation: a REAL physics formula, built up piece by piece. equation = the formula
    ("F = m x a", "a = v² / r"), labels = what each symbol means ("F: force", "m: mass").
    Only a formula a physics textbook would print; never a made-up word equation
    ("sound = air + bone") -- use chain for that.
  * compare: two things side by side as bars. labels = [thing A, thing B], values = their
    sizes, title = the quantity compared, always ("Heat flow", "Bass reaching your ear").
  * chain: causes leading to an effect, 2-4 short steps. labels = the steps.
  * graph: a curve. title = what it shows, labels = [x axis, y axis], shape = how the y
    quantity changes as the x quantity grows: "rising" (y goes up), "falling" (y goes down),
    "peak" (up then down), "wave". It must agree with the sentence: "bone absorbs high
    frequencies" with x = frequency and y = loudness is "falling".
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
        # Bars of nothing in particular, or an equation with no formula, teach nothing.
        if diagram and v.template == "compare" and not v.title.strip():
            diagram = False
        if diagram and v.template == "equation" and not any(c in v.equation for c in "=<>"):
            diagram = False
        if diagram and (i == 0 or (beats and beats[-1].visual.kind == "diagram")):
            diagram = False
        if not diagram:
            queries = [q for q in [*v.queries, v.query] if q.strip()] or [_query_from(beat.text)]
            v = v.model_copy(update={"kind": "stock", "queries": list(dict.fromkeys(queries))[:3],
                                     "query": queries[0], "card": v.card or beat.emphasis or _query_from(beat.text)})
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
and analogies are fine unless they teach something false. Check the diagrams too: a curve,
arrow, bar or equation that disagrees with its sentence or with the physics is an error
(graph shape says how the y axis changes as the x axis grows); so is an "equation" that is not
a real physics formula. Return ok=true with no problems if it is correct. Otherwise list each problem in one sentence with the correct physics."""


def _diagrams(script: Script) -> str:
    """The diagrams in words, for the check: a curve or arrow that says the opposite of the
    sentence teaches it wrong (a graph rose while the line said high notes are absorbed)."""
    lines = []
    for i, b in enumerate(script.beats, start=1):
        v = b.visual
        if v.kind != "diagram":
            continue
        parts = [f"{v.template}", f"title {v.title!r}" if v.title else "", f"labels {v.labels}" if v.labels else "",
                 f"shape {v.shape}" if v.shape else "", f"values {v.values}" if v.values else "",
                 f"directions {v.directions}" if v.directions else "", f"equation {v.equation!r}" if v.equation else ""]
        lines.append(f"- Over sentence {i} (\"{b.text}\"): " + ", ".join(p for p in parts if p))
    return "\n".join(lines)


def check(script: Script) -> Review:
    diagrams = _diagrams(script)
    user = f"Title: {script.title}\nScript:\n{script.text}" + (f"\n\nDiagrams shown:\n{diagrams}" if diagrams else "")
    answer = ask(CHECK, user, Review, temperature=0.0)
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


def replan(script: Script) -> tuple[Script, str]:
    """New pictures for an approved script, every word kept (its voice is already made):
    the visuals planned again, then checked, once more if the check finds a problem."""
    channel = channels.load()
    beats = "\n".join(f"{i}. {b.text}" for i, b in enumerate(script.beats, start=1))
    feedback = ""
    for _ in range(2):
        user = (f"This approved script is already recorded, sentence by sentence. Keep every sentence "
                f"exactly as written, in order, one beat each, and plan the pictures again.\n\n"
                f"Title: {script.title}\n{beats}\n" + (f"\nFix these problems:\n{feedback}\n" if feedback else ""))
        answer = ask(_system(channel), user, Script, temperature=0.4)
        try:
            planned = Script.model_validate(json.loads(answer))
        except (ValueError, TypeError) as exc:
            raise CreateError("the new pictures came back unreadable; try again") from exc
        if len(planned.beats) != len(script.beats):
            raise CreateError("the new plan changed the sentences; try again")
        # The words are the recording's: only the pictures are taken from the new plan.
        fresh = tidy(script.model_copy(update={"beats": [
            b.model_copy(update={"visual": p.visual, "emphasis": p.emphasis or b.emphasis})
            for b, p in zip(script.beats, planned.beats, strict=True)]}))
        review = check(fresh)
        if review.ok or not review.problems:
            return fresh, "Pictures planned again. Physics check: no problems found."
        feedback = "\n".join(f"- {p}" for p in review.problems)
    return fresh, "Pictures planned again. Physics check, still unsure:\n" + feedback
