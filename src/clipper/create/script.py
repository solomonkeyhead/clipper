"""A script in the channel's voice, split into beats with a picture planned for each,
then checked for physics by a second, stricter pass (a wrong fact is the fastest
way to lose an educational channel's trust).

D164: two passes, as a writer and a director work. The writer writes the words only (its prompt is the
persona, the rules and the joke shapes, not 5 KB of picture planning); when the words have passed the
checks, the director plans the pictures, the highlighted words and the poses on the final text, and the
diagrams are checked against it. A rewrite after the checks no longer plans every picture twice.

A beat is one spoken sentence, or two very short ones: one shot. Each gets either
stock footage that literally shows what's said, or an animated diagram when it
explains a mechanism -- about half the beats, never the first, never three in a
row (D109: stock is thin on people doing specific things, the board never is) --
so the picture changes every 3-5 seconds instead of holding one clip for 50 (the
channel's own videos used 1-3 shots each).
"""

from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Literal

from pydantic import BaseModel, Field, create_model

from . import channel as channels
from .ai import CreateError, ask
from .sketch import Sketch

TEMPLATES = ("sketch", "forces", "circle", "equation", "compare", "chain", "graph", "wave", "particles", "ray", "number")


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
    shape: str = ""                 # graph: rising | falling | peak | wave; ray: refract | reflect
    subject: str = ""               # forces: what the arrows push on, written in its box
    amounts: list[float] = Field(default_factory=list)    # wave: amplitudes; particles: how many
    idea: str = ""                  # sketch: what to draw, for the sketcher (create/sketch.py)
    sketch: Sketch | None = None    # sketch: the drawing, made after the script (leave empty)
    # The user's own clip for this sentence (create/userclips.py, D119). Set by the user or the
    # placer, never by the script writer; the planned picture above stays as the filler.
    clip: str = ""                  # id of one of the video's own clips; "" = the planned picture
    clip_start: float | None = None  # seconds into that clip; None = where the last sentence left off
    fill: Literal["auto", "planned", "loop", "slow", "hold"] = "auto"   # when the clip is too short
    # Chosen by the user on the page (D120): kept as picked when the script is tidied or the
    # pictures are planned again, not bent to the rules the writer's plans are held to.
    manual: bool = False
    # Keep the drawing before this sentence on screen through it (D124): one picture that builds
    # as the explanation goes on ("two routes... route one is air... route two is bone"), its
    # parts arriving on the words of all those sentences, instead of a new shot each sentence.
    hold: bool = False
    # The camera on this sentence's footage (D162): "" lets the build choose (moves taken in turn), else "push",
    # "pull", "drift" or "still". The user's pick, never the writer's.
    camera: str = ""
    # A change the next build makes to this shot without choosing new footage (a camera move, D164); the page
    # counts it as a change to rebuild for. Cleared by the build.
    restyle: bool = False
    # What the last build used, kept so a rebuild keeps what the user liked (D125): the stock
    # clip(s) picked, the clips turned down ("new footage"), a change waiting for the next build,
    # and the picture before that change, for undo. Set by the app, never by the writer.
    picked: list[dict] = Field(default_factory=list)
    avoid: list[str | int] = Field(default_factory=list)   # stock ids: Pixabay's are numbers (D128)
    redo: bool = False
    previous: dict | None = None
    wish: str = ""              # what the user said they'd rather see, for the footage searches (D129)
    notice: str = ""            # what the last build couldn't do for this picture, shown on it (D129)


class Beat(BaseModel):
    text: str
    emphasis: str = ""              # the one word to highlight
    pose: str = ""                  # one of the channel's `poses`, shown for this sentence (D158)
    visual: Visual = Field(default_factory=Visual)


class Script(BaseModel):
    title: str
    beats: list[Beat]
    description: str = ""
    hashtags: list[str] = Field(default_factory=list)
    hook: str = ""                  # the words on screen at the start, at most 6 (D155); "" = the title
    # Set by the app, not the writer (D155): the shape and ending it was asked for, and the idea's series.
    shape: str = ""
    ending: str = ""
    series: str = ""
    bit: str = ""                   # the running bit it was asked to use (D156)
    # Music and chalk sounds for this video: None = the channel's setting (D156), so two videos can differ.
    music: bool | None = None
    sfx: bool | None = None
    # The edit's flourishes for this video (D165): punch-in zooms on footage, camera cut-ins on the Professor,
    # and how shots change: auto (cut, with a whip or zoom where the picture changes kind), cut, whip or zoom.
    zooms: bool = True
    cut_ins: bool = True
    transitions: str = "auto"

    @property
    def text(self) -> str:
        return " ".join(b.text.strip() for b in self.beats)

    @property
    def words(self) -> int:
        return len(self.text.split())


#: The fields of a picture only the app sets (the user's clips, what a build picked): left out of
#: the writer's schema, so it isn't shown ~12 fields it can't use and can't invent them.
APP_ONLY = {"sketch", "clip", "clip_start", "fill", "manual", "picked", "avoid", "redo", "previous", "wish", "notice",
            "camera", "restyle"}
_WriterVisual = create_model("WriterVisual", **{k: (f.annotation, f) for k, f in Visual.model_fields.items()
                                                if k not in APP_ONLY})
_WriterBeat = create_model("WriterBeat", text=(str, ...), emphasis=(str, ""), pose=(str, ""),
                           visual=(_WriterVisual, Field(default_factory=_WriterVisual)))
#: The writer's answer (D164): the words, a sentence a line, and what goes with them; no pictures.
_WordsScript = create_model("WordsScript", lines=(list[str], ...), title=(str, ...), hook=(str, ""),
                            description=(str, ""), hashtags=(list[str], Field(default_factory=list)))
#: The director's answer: a picture, a highlighted word and a pose for each sentence.
_Plan = create_model("Plan", beats=(list[_WriterBeat], ...))


def _parse(answer: str) -> Script:
    """The director's answer as a Script: only what its schema holds is taken."""
    return Script.model_validate({"title": "", **_Plan.model_validate(json.loads(answer)).model_dump()})


VISUALS_HEAD = """For every numbered sentence (a beat) plan ONE picture:
- kind "stock": real footage that literally shows what is said. Give queries: three searches
  of a free stock-footage library, most specific first, then broader, 1-3 words each, naming
  subjects such libraries really have: objects, places, nature, everyday scenes, and people
  only in common situations ("woman wearing headphones", "man talking on phone", "boiling
  pot", "airplane window", "elevator doors"). Never abstract words ({abstract}), never a specific person's action that nobody films ("person touching side of
  head"). Also give card: a 1-4 word phrase from the sentence to write on the chalkboard if
  no footage fits ("bone conduction", "100 degrees").
  The first beat is the moment itself, already happening: the most striking moving picture of
  the video, with a person in it when the library is likely to have one ("man driving car",
  "elevator doors opening"), never a slow scene-setting shot.
  Change the kind of picture at least every 4 sentences (about 10 seconds): footage to a
  drawing, or a close-up to a wide shot.
- kind "diagram": an animated chalkboard diagram, when the beat explains HOW or HOW MUCH.
  About half the beats; never in the first 4 seconds (about the first 10 words); never three
  diagrams in a row. At most 3 parts in a drawing, unless the idea can't be understood with
  fewer; each part arrives as its word is said.
  hold: when the next sentence (up to 3 of them) goes on explaining the same picture ("two
  routes... route one is air... route two is bone"), set hold = true on those sentences: the
  drawing from the beat before stays up and builds as the voice explains each part, instead
  of a new picture each sentence. Plan the drawing on the first of them, with its idea covering
  all they say. Only after a diagram; a held sentence needs no picture of its own. Prefer a
  diagram to footage that would only loosely match. Templates:
"""

#: The diagram templates, by name; a channel's pack lists the ones it uses (create/packs.py).
TEMPLATES = {
    "sketch": """  * sketch (the default for HOW something works): a chalk drawing of the real thing, made
    by an illustrator after you, who has ready drawings of everyday objects (a person, an ear,
    a speaker, the sun, a magnet, a car, a glass of water...). idea = one or two sentences
    saying exactly what to draw: the 2-3 objects by their plain names, where they sit, the one
    thing that happens between them and its colour, the 2-4 labels ("A speaker on the left, an
    ear on the right. A yellow wave travels from the speaker to the ear, labelled 'air'. Under
    it, a dashed arrow labelled 'pressure'."). Concrete and physical, never a flow chart.
    Leave sketch empty.
""",
    "forces": """  * forces: an object with labelled arrows. subject = the object, 1-2 words ("you",
    "car"), labels = the forces ("gravity", "floor pushes up"), directions =
    "up"/"down"/"left"/"right" for each, values = their relative sizes, true to the {subject}
    (an elevator speeding up going up: floor pushes harder than gravity).
""",
    "circle": """  * circle: something moving on a circle, with its velocity and the inward pull. labels =
    [what moves, the inward force].
""",
    "wave": """  * wave: one or two waves travelling. labels = what each is ("low note", "high note"),
    values = relative frequency, amounts = relative amplitude (loudness, brightness).
""",
    "particles": """  * particles: molecules bouncing in one or two boxes. labels = what each box is ("cold
    air", "hot air"), values = relative speed (temperature), amounts = relative number
    (density, pressure). For heat, pressure, evaporation, smell, sound travelling.
""",
    "ray": """  * ray: a light ray meeting a surface. labels = [medium above, medium below], values = their
    refractive indices (air 1.0, water 1.33, glass 1.5), shape = "refract" or "reflect".
    The bend is computed, so the indices must be right.
""",
    "number": """  * number: one striking real figure. title = the number with its unit ("343 m/s",
    "37 °C", "1,000x"), labels = [what it is, 2-5 words]. Only a figure you are sure of.
""",
    "equation": """  * equation: a REAL {subject} formula, built up piece by piece. equation = the formula
    ("F = m x a", "a = v² / r"), labels = what each symbol means ("F: force", "m: mass").
    Only a formula a {subject} textbook would print; never a made-up word equation
    ("sound = air + bone") -- use chain for that.
""",
    "compare": """  * compare: two things side by side as bars. labels = [thing A, thing B], values = their
    sizes, title = the quantity compared, always ("Heat flow", "Bass reaching your ear").
""",
    "chain": """  * chain: causes leading to an effect, 2-4 short steps. labels = the steps.
""",
    "graph": """  * graph: a curve. title = what it shows, labels = [x axis, y axis], shape = how the y
    quantity changes as the x quantity grows: "rising" (y goes up), "falling" (y goes down),
    "peak" (up then down), "wave". It must agree with the sentence: "bone absorbs high
    frequencies" with x = frequency and y = loudness is "falling".
""",
}

VISUALS_TAIL = """  Keep every label under 4 words.
For each beat also give emphasis: the single most important word in it, copied exactly. It is
lit in the captions and the camera punches in on it, so pick the word that carries the point
(the number, the surprising verb, the thing named), never a small word."""

#: The writer's closing instructions (D164): the words only; the director plans the pictures after.
WRITER_TAIL = """Every sentence gets its own picture under it (real footage, or a chalk drawing that shows
the mechanism), so write concrete sentences a picture can show, and let the explanation go one
step a sentence. The lines quoted in the rules show the kind of line; never use them.

Answer with: lines (the script, one spoken sentence a line, two only if both are very short, in
order), title (the question, at most 60 characters), hook (the words shown on screen for the first
second and a half: at most 6 words, the question's core, "Why elevators make you heavier"),
description (the script's idea in 3-5 short lines, in the same voice, ending on its punchline),
hashtags (3: {hashtags})."""

#: The director's brief (D164), before the picture rules.
DIRECTOR = """You are the director of the YouTube Shorts channel {name} ({niche}). The script is written
and recorded; you plan what the viewer sees under each sentence, on a phone. The picture carries
half the explanation: with the sound off, a viewer should still follow it. One clear subject per
picture, big in the frame. Keep it moving: a real moment, then a drawing that shows how, then a
close-up; never the same kind of picture for long. Plan the drawings where the words explain HOW,
and footage where they name a thing or a moment the viewer knows."""


def channel_visuals_head(channel: channels.Channel) -> str:
    """The stock rules, and (when the channel draws) the diagram intro; footage-only channels get
    footage only."""
    if channel.drawings:
        return VISUALS_HEAD
    stock = VISUALS_HEAD[:VISUALS_HEAD.index('- kind "diagram"')]
    return stock + ('- kind "diagram" is never used on this channel: every beat is kind "stock", with its\n'
                    '  queries and card.\n')


def visuals(channel: channels.Channel) -> str:
    """The picture-planning instructions for `channel` (D146): the stock rules, then the diagram
    templates its pack uses (none: every picture is footage), then the closing rules."""
    head = channel_visuals_head(channel)
    if not channel.drawings:
        return head + VISUALS_TAIL
    used = [TEMPLATES[n] for n in channel.templates if n in TEMPLATES] or list(TEMPLATES.values())
    return head + "".join(used) + VISUALS_TAIL



#: What each pose is for (D158), told to the writer for the poses a channel has; a pose of its own name is
#: still offered, by name alone.
POSE_USE = {
    "shocked": "a surprising fact", "facepalm": "the common mistake", "aha": "the key insight, the click",
    "thinking": "setting up the question", "smug": "a point the viewer should concede", "shrug": "nobody fully knows",
    "deadpan": "the dry joke, then silence", "confused": "a viewer's question", "nervous": "where it gets bad",
    "whisper": "a fun fact, an aside", "laugh": "right after his own joke", "coffee": "the coffee bit",
    "grudge": "the grudge bit", "proud": "the sign-off",
}
MAX_POSES = 6   # he is on screen all the time now (D159): more changes keep him alive
POSE_AFTER_WORDS = 7   # the character already shows at the start (D156): no pose in the first sentence or so


def pose_note(channel: channels.Channel) -> str:
    """The poses the writer may tag sentences with, for its request; nothing when the channel has none."""
    if not channel.poses:
        return ""
    names = "; ".join(f"{n} ({POSE_USE[n]})" if n in POSE_USE else n for n in channel.poses if not n.startswith("talking"))
    if not names:
        return ""
    return (f"Poses for the on-screen presenter: set a beat's pose to one of these on at most {MAX_POSES} sentences "
            f"where it fits what is said, never on the first sentence, and leave it empty "
            f"elsewhere. The last sentence needs none. Poses: {names}.\n")


def _poses(script: Script, channel: channels.Channel) -> list[Beat]:
    """The poses kept: known ones, not on the opening words or the last sentence, at most MAX_POSES. The "talking"
    poses are the build's own, for the sentences in between (D159)."""
    beats, kept, before = [], 0, 0
    for i, beat in enumerate(script.beats):
        pose = beat.pose.strip().lower() if beat.pose else ""
        ok = (pose in channel.poses and not pose.startswith("talking") and before >= POSE_AFTER_WORDS
              and i < len(script.beats) - 1 and kept < MAX_POSES)
        beats.append(beat.model_copy(update={"pose": pose if ok else ""}))
        kept += bool(ok)
        before += len(beat.text.split())
    return beats


def _system(channel: channels.Channel) -> str:
    """The writer's brief (D164): the persona, the rules and joke shapes, what to answer; no picture planning."""
    rules = "\n".join(f"- {r}" for r in channel.rules)
    jokes = ("\n\nJoke shapes to follow (shapes, never lines to copy):\n" + "\n".join(f"- {j}" for j in channel.jokes)
             if channel.jokes else "")
    return (f"{channel.persona}\n\nYou write the scripts for the YouTube Shorts channel "
            f"{channel.name} ({channel.niche}). Rules:\n{rules}{jokes}\n\n{channels.fill(WRITER_TAIL, channel)}\n\n"
            "The examples are the channel's own scripts, for voice and rhythm only: some break today's "
            "rules (length, sentence length, structure), and the rules win. Never reuse their jokes or lines.")


def _director(channel: channels.Channel) -> str:
    """The director's brief (D164): what the pictures are for, then the channel's picture rules."""
    head = DIRECTOR.replace("{name}", channel.name).replace("{niche}", channel.niche)
    return f"{head}\n\n{channels.fill(visuals(channel), channel)}"


#: How a script ends, one per script in turn (D155): a loop back to the start (rewatches), a "send this
#: to" (shares) or a one-word question (comments). The same deadpan two-beat ending every time became a tic.
ENDINGS = {
    "loop": "a last line that leads straight back into the opening question, so the video loops",
    # No example lines (D168): two scripts, on a spoon in water and on ears on a plane, both ended "Metal or wood:
    # which wins?", the example given here.
    "send": 'a dry "send this to..." line naming, in this video\'s own terms, the person who needs it',
    "poll": "a question about this video's own moment that the viewer can answer in one word in the comments: an "
            "either-or between two things this video talks about",
}


def turn(channel: channels.Channel) -> tuple[str, str]:
    """The shape and ending for the channel's next script: picked at random but never the shape of either of the
    last two scripts nor the last one's ending (D156; in strict turn, D155, the order itself became a pattern)."""
    import random

    from . import store

    recent = [v["script"] for v in store.videos()[:2]]
    shapes = [x for x in channel.shapes if x not in {r.get("shape") for r in recent}] or channel.shapes or [""]
    endings = [e for e in ENDINGS if not recent or e != recent[0].get("ending")]
    return random.choice(shapes), random.choice(endings)


#: A running bit is retired after this many uses (D156).
RETIRE = 8


def next_bit(channel: channels.Channel) -> tuple[str, str]:
    """The running bit for the next script, and how to use it (D156): none if either of the last two scripts had one
    (so at most one in three), else the least used one not yet retired."""
    from collections import Counter

    from . import store

    used = [v["script"].get("bit", "") for v in store.videos()]   # newest first
    if not channel.bits or any(used[:2]):
        return "", ""
    counts = Counter(u for u in used if u)
    live = [b for b in channel.bits if counts[b["name"]] < RETIRE]
    if not live:
        return "", ""
    bit = min(live, key=lambda b: counts[b["name"]])
    return bit["name"], bit["how"].replace("{n}", str(counts[bit["name"]] + 1))


def recent_endings(limit: int = 10) -> list[str]:
    """The last lines of the channel's latest scripts, so no joke's shape comes round too soon (D155)."""
    from . import store

    out = []
    for v in store.videos()[:limit]:
        beats = v["script"].get("beats") or []
        if beats:
            out.append(beats[-1].get("text", ""))
    return [e for e in out if e]


def write(question: str, angle: str = "", *, take: int = 1, feedback: str = "", steer: str = "",
          shape: str | None = None, ending: str | None = None, bit: tuple[str, str] | None = None,
          draft: Script | None = None) -> Script:
    """A new script for `question`; `take` asks for a fresh attempt, `feedback` for fixes to `draft`, `steer` is
    the owner's note on what to change (D153). The channel's own standing guidance applies every time.
    `shape` and `ending` default to the channel's next in turn (D155). The draft is shown with the fixes (D168):
    a rewrite was told "the comparison ('coffee cup...') is unclear" about a draft it had never seen."""
    channel = channels.load()
    if shape is None or ending is None:
        next_shape, next_ending = turn(channel)
        shape = next_shape if shape is None else shape
        ending = next_ending if ending is None else ending
    bit = next_bit(channel) if bit is None else bit
    guide = channels.steering("scripts", channel.script_focus, channel.script_avoid, steer,
                              yields="; the word count, the order of the structure and the output format still apply, "
                                     "and every claim must still be true", never="Never in a script")
    endings = recent_endings()
    user = (guide + f"The channel's best scripts:\n\n{channels.examples_block(channel)}\n\n"
            f"Write a new script answering: {question}\n" + (f"(The {channel.subject}: {angle})\n" if angle else "")
            + (f"Shape: {shape}\n" if shape else "")
            + f"End with {ENDINGS.get(ending, ENDINGS['loop'])}.\n"
            + (f"Running bit for this one: {bit[1]}\n" if bit[1] else "")
            + ("The channel's latest endings; reuse none of their jokes or their shape:\n"
               + "\n".join(f"- {e}" for e in endings) + "\n" if endings else "")
            + ("\nYour last draft:\n" + "\n".join(b.text for b in draft.beats) + "\n" if feedback and draft else "")
            + (f"\nFix these problems from the last draft, keeping what works in it:\n{feedback}\n" if feedback else "")
            + f"\n(take {take})")
    answer = ask(_system(channel), user, _WordsScript, temperature=0.85, job="script")
    try:
        got = _WordsScript.model_validate(json.loads(answer))
    except (ValueError, TypeError) as exc:
        raise CreateError("the script came back unreadable; try again") from exc
    lines = [" ".join(str(x).split()) for x in got.lines if str(x).strip()]
    if not lines:
        raise CreateError("the script came back empty; try again")
    return Script(title=got.title, hook=got.hook, description=got.description, hashtags=got.hashtags,
                  beats=[Beat(text=t) for t in lines], shape=shape, ending=ending, bit=bit[0])


#: Habits that mark a script as machine-written (D155), found in code before anyone reads it.
BANNED = [
    (re.compile(r"—"), "an em dash"),
    (re.compile(r"\b(?:it|that|this)(?:'s| is) not\b[^.?!]{1,60}?[,;]\s*(?:it|that|this)(?:'s| is)\b", re.I),
     '"it\'s not X, it\'s Y"'),
    (re.compile(r"\bever wondered\b", re.I), '"ever wondered"'),
    (re.compile(r"\bhere'?s the thing\b", re.I), '"here\'s the thing"'),
    (re.compile(r"\bin this video\b", re.I), '"in this video"'),
    (re.compile(r"\blet'?s dive in\b", re.I), '"let\'s dive in"'),
    (re.compile(r"\bsubscribe\b", re.I), "a call to subscribe"),
]
HOOK_TEXT_MAX = 6


def _sentences(text: str) -> list[str]:
    return [p.strip() for p in re.split(r"(?<=[.!?])\s+", text) if p.strip()]


def lint(script: Script, channel: channels.Channel) -> list[str]:
    """What breaks the channel's countable rules (D155): length, sentence length, the hook, the on-screen
    hook and the banned habits. Free: no model call. Each problem is one line the writer can act on."""
    problems = []
    low, high = [*channel.words, 0, 10_000][:2]
    if not low <= script.words <= high:
        problems.append(f"It is {script.words} words; it must be {low} to {high}.")
    sentences = _sentences(script.text)
    long = [s for s in sentences if len(s.split()) > channel.sentence_max]
    if long:
        problems.append(f"Sentences over {channel.sentence_max} words: " + " | ".join(long[:3]))
    hook = sentences[0] if sentences else ""
    if len(hook.split()) > channel.hook_max:
        problems.append(f"The opening question is {len(hook.split())} words; at most {channel.hook_max}.")
    if channel.hook_you and not re.search(r"\byou(?:r|'re|'ve)?\b", hook, re.I):
        problems.append('The opening question must say "you" or "your".')
    if len(script.hook.split()) > HOOK_TEXT_MAX:
        problems.append(f"The on-screen hook is {len(script.hook.split())} words; at most {HOOK_TEXT_MAX}.")
    for pattern, name in BANNED:
        if pattern.search(script.text):
            problems.append(f"Remove {name}.")
    return problems


#: Short words that end in a full stop without ending the sentence.
_ABBREVIATIONS = {"dr", "mr", "mrs", "ms", "vs", "etc", "e.g", "i.e", "approx", "st", "no", "fig", "eq", "ca", "cf", "prof"}


def split_beats(text: str) -> list[str]:
    """A written script cut into one spoken sentence per beat. A script with line breaks is
    taken as the user cut it, a line a beat. Otherwise it is split after . ! ? (not after
    "Dr.", "e.g.", "3.5"), a too-short fragment joins the next sentence, and a too-long one
    is split at a semicolon or comma near its middle, so each beat is a breath long."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [re.sub(r"^\s*(?:[-*\u2022]|\d{1,2}[.)])\s+", "", ln) for ln in text.split("\n")]
    lines = [" ".join(ln.split()) for ln in lines if ln.strip()]
    if len(lines) >= 2:
        return lines
    flat = " ".join(lines)
    pieces, start = [], 0
    for m in re.finditer(r"[.!?]+[\"')\]]*\s+(?=[\"'(\[]?[A-Z0-9])", flat):
        before = flat[start:m.start()].split()
        if before and before[-1].lower().rstrip(".") in _ABBREVIATIONS:
            continue
        pieces.append(flat[start:m.end()].strip())
        start = m.end()
    if flat[start:].strip():
        pieces.append(flat[start:].strip())
    merged: list[str] = []
    carry = ""
    for piece in pieces:
        piece = f"{carry} {piece}".strip()
        carry = ""
        if len(piece.split()) < 4:
            carry = piece
            continue
        merged.append(piece)
    if carry:
        if merged:
            merged[-1] = f"{merged[-1]} {carry}"
        else:
            merged.append(carry)
    out: list[str] = []
    for piece in merged:
        words = piece.split()
        if len(words) > 26:
            cuts = [i for i, w in enumerate(words) if w.endswith((";", ",")) and 6 <= i + 1 <= len(words) - 6]
            if cuts:
                at = min(cuts, key=lambda i: abs(i + 1 - len(words) / 2)) + 1
                out += [" ".join(words[:at]), " ".join(words[at:])]
                continue
        out.append(piece)
    return out


def from_text(title: str, text: str, description: str = "", hashtags: list[str] | None = None) -> Script:
    """The user's own script (D120): their words, one beat per sentence, a plain footage search
    on each from the sentence's longest words until pictures are planned or chosen."""
    beats = split_beats(text)
    if not beats:
        raise CreateError("write the script first: there are no sentences in it")
    if len(beats) > 40 or sum(len(b.split()) for b in beats) > 400:
        raise CreateError("that's too long for a Short (over 400 words); cut it down")
    title = " ".join(title.split()) or beats[0].rstrip(".")[:60]   # a question keeps its "?" (D161)
    made = Script(title=title[:100], description=description.strip()[:1000],
                  hashtags=[h for h in (hashtags or []) if h.strip()],
                  beats=[Beat(text=b, visual=Visual(kind="stock", query=_query_from(b))) for b in beats])
    return tidy(made)


#: At most this many sentences after a drawing may keep it on screen (D124).
MAX_HOLD = 3
#: About 4 seconds of speech: no drawing starts before this many words are said (D155).
FIRST_DIAGRAM_WORDS = 9


def _emphasis(beat: Beat) -> str:
    """The highlight word, if it's really in the sentence."""
    words = {w.strip(".,!?;:'\"").lower() for w in beat.text.split()}
    return beat.emphasis if beat.emphasis.strip(".,!?").lower() in words else ""


def spans(script: Script) -> list[list[int]]:
    """The sentences grouped by picture: a drawing and the sentences that hold it (D124)."""
    groups: list[list[int]] = []
    for i, b in enumerate(script.beats):
        if b.visual.hold and groups and not b.visual.clip:
            groups[-1].append(i)
        else:
            groups.append([i])
    return groups


def tidy(script: Script) -> Script:
    """Rules the model can bend, put straight: known templates, no diagram first or three
    in a row, an emphasis word that's really in its beat, at most 5 hashtags."""
    beats = []
    pictures: list[str] = []   # the kind of each picture so far: a held sentence adds none
    held = 0
    before = 0                 # words said before this sentence
    for i, beat in enumerate(script.beats):
        before, said_before = before + len(beat.text.split()), before
        v = beat.visual
        if v.hold:
            # Held on: only after a drawing, and not for ever (a picture held past ~15 s goes stale).
            if pictures and pictures[-1] == "diagram" and held < MAX_HOLD:
                held += 1
                beats.append(beat.model_copy(update={"emphasis": _emphasis(beat)}))
                continue
            v = v.model_copy(update={"hold": False})
        held = 0
        if v.manual and (v.kind == "stock" or v.template in (*TEMPLATES, "card")):
            # The user's own pick stands: no rule moves it (a chalk card first, say).
            if v.kind == "stock":
                queries = [q.strip() for q in [*v.queries, v.query] if q.strip()] or [_query_from(beat.text)]
                v = v.model_copy(update={"queries": list(dict.fromkeys(queries))[:3], "query": queries[0]})
            beats.append(beat.model_copy(update={"visual": v, "emphasis": _emphasis(beat)}))
            pictures.append(v.kind)
            continue
        diagram = v.kind == "diagram" and v.template in TEMPLATES
        # Bars of nothing in particular, or an equation with no formula, teach nothing.
        if diagram and v.template == "compare" and not v.title.strip():
            diagram = False
        if diagram and v.template == "equation" and not any(c in v.equation for c in "=<>"):
            diagram = False
        if diagram and v.template == "sketch" and not v.idea.strip() and not (v.sketch and v.sketch.marks):
            diagram = False
        if diagram and v.template == "number" and not any(c.isdigit() for c in v.title):
            diagram = False
        # No drawing in the first 4 seconds (D155): the viewer's own moment, moving, holds them first.
        if diagram and (i == 0 or said_before < FIRST_DIAGRAM_WORDS or pictures[-2:] == ["diagram", "diagram"]):
            diagram = False
        if not diagram:
            queries = [q for q in [*v.queries, v.query] if q.strip()] or [_query_from(beat.text)]
            v = v.model_copy(update={"kind": "stock", "queries": list(dict.fromkeys(queries))[:3],
                                     "query": queries[0], "card": v.card or beat.emphasis or _query_from(beat.text)})
        beats.append(beat.model_copy(update={"visual": v, "emphasis": _emphasis(beat)}))
        pictures.append(v.kind)
    tags = [("#" + t.lstrip("#")).replace(" ", "") for t in script.hashtags if t.strip("# ")][:5]
    kept = script.model_copy(update={"beats": beats, "hashtags": tags})
    return kept.model_copy(update={"beats": _poses(kept, channels.load())})


#: Long words that say nothing a search can show ("float water does" found nothing useful, D161).
_FILLER = {"about", "above", "after", "again", "also", "around", "because", "been", "before", "being", "could",
           # the adverbs a sentence leans on (D164: "actually benches exactly" was a search)
           "actually", "exactly", "basically", "literally", "simply", "always", "never", "often", "still", "even",
           "quite", "rather", "almost", "nearly", "already", "slowly", "quickly", "suddenly", "certainly", "probably",
           "does", "doesn", "didn", "every", "from", "have", "here", "into", "just", "like", "many", "more", "most",
           "much", "only", "other", "over", "really", "same", "should", "some", "than", "that", "their", "them",
           "then", "there", "these", "they", "thing", "things", "this", "those", "through", "under", "very", "were",
           "what", "when", "where", "which", "while", "will", "with", "would", "your", "yours"}


def _query_from(text: str) -> str:
    """A fallback stock query: the beat's longest words, filler left out, ties in the sentence's order."""
    words = [w for w in dict.fromkeys(w.lower() for w in re.findall(r"[A-Za-z]{4,}", text)) if w not in _FILLER]
    return " ".join(sorted(words, key=len, reverse=True)[:3])


class Review(BaseModel):
    ok: bool
    problems: list[str] = Field(default_factory=list)


CHECK = """You are {expert} checking a 45-second educational script for a general
audience. Simplifying is fine; stating something false is not. Flag only real errors: wrong
mechanisms, wrong formulas, wrong numbers, misleading claims, or a myth stated as fact. Jokes
and analogies are fine unless they teach something false. Check the diagrams too: a curve,
arrow, bar or equation that disagrees with its sentence or with the {subject} is an error
(graph shape says how the y axis changes as the x axis grows; wave values are frequencies and
amounts amplitudes; particles values are speeds and amounts how many; ray values are refractive
indices; forces values are relative sizes); so is an "equation" that is not a real {subject}
formula, or a "number" that is not the true figure. Health advice is an error too: a symptom,
a condition, or a tip about what to do for your body, where the script should only explain the
mechanism. Return ok=true with no problems if it is correct. Otherwise list each problem in one sentence with the correct {subject}."""

CRITIC = """You are a short-form video editor reviewing a script for a {subject} Shorts channel before
it is recorded. Judge only these, each pass or fail:
1. Hook: the first line is about a concrete moment the viewer knows, short enough to read in two seconds
   (when the channel opens with a question, it stays a question).
2. First cause early: the real reason starts within about 20 words of the opening line.
3. Ear: every sentence says one thing and is easy to say and hear aloud.
4. Comparison: one everyday comparison that explains the mechanism, not decoration.
5. Payoff: the question is fully answered by three quarters of the way through.
6. Ending: it ends the way it was asked to end, in two short beats.
7. No AI habits: no stock phrases, no lists of three, no "it's not X, it's Y", no over-explaining.
Do not judge whether the jokes are funny, and do not check the facts: others do that. Never ask to remove
what the script was asked to include (its shape, its ending, a running bit). Return ok=true
with no problems if all pass. Otherwise, for each failure, one sentence saying exactly what to change."""


def _diagrams(script: Script) -> str:
    """The diagrams in words, for the check: a curve or arrow that says the opposite of the
    sentence teaches it wrong (a graph rose while the line said high notes are absorbed)."""
    lines = []
    for i, b in enumerate(script.beats, start=1):
        v = b.visual
        if v.kind != "diagram":
            continue
        parts = [f"{v.template}", f"title {v.title!r}" if v.title else "", f"subject {v.subject!r}" if v.subject else "",
                 f"labels {v.labels}" if v.labels else "",
                 f"shape {v.shape}" if v.shape else "", f"values {v.values}" if v.values else "",
                 f"amounts {v.amounts}" if v.amounts else "",
                 f"a sketch of: {v.idea}" if v.idea else "",
                 "drawn with labels " + str([m.text for m in v.sketch.marks if m.text]) if v.sketch else "",
                 f"directions {v.directions}" if v.directions else "", f"equation {v.equation!r}" if v.equation else ""]
        lines.append(f"- Over sentence {i} (\"{b.text}\"): " + ", ".join(p for p in parts if p))
    return "\n".join(lines)


def check(script: Script, diagrams_only: bool = False) -> Review:
    """The fact check; `diagrams_only` when the words passed it already (D164): only the pictures are new."""
    diagrams = _diagrams(script)
    if diagrams_only and not diagrams:
        return Review(ok=True)
    user = f"Title: {script.title}\nScript:\n{script.text}" + (f"\n\nDiagrams shown:\n{diagrams}" if diagrams else "")
    if diagrams_only:
        user += ("\n\nThe words were checked already and stay as they are: check only the diagrams, against "
                 "their sentences and the facts.")
    answer = ask(channels.fill(CHECK), user, Review, temperature=0.0, job="check", keep=True)
    try:
        return Review.model_validate(json.loads(answer))
    except (ValueError, TypeError):
        return Review(ok=False, problems=[f"The {channels.check_name().lower()} came back unreadable; read it carefully yourself."])


def _videos() -> list[dict]:
    from . import store

    return store.videos()


def critique(script: Script, writer: str = "") -> list[str]:
    """What an editor would change (D155), asked of a different AI from the one that wrote it: a model
    goes easy on its own work. Nothing when no other AI answers; the script still goes on."""
    channel = channels.load()
    shape = script.shape if script.shape in channel.shapes else ""
    bit = next((b["how"].replace("{n}", "N") for b in channel.bits if b["name"] == script.bit), "")
    user = (f"It was asked to end with {ENDINGS.get(script.ending, 'a deadpan two-beat line')}.\n"
            + (f"Its shape, as asked: {shape}\n" if shape else "") + (f"It was asked to include: {bit}\n" if bit else "")
            + "\n"
            f"Title: {script.title}\nScript:\n" + "\n".join(b.text for b in script.beats))
    try:
        answer = ask(channels.fill(CRITIC), user, Review, temperature=0.0, job="critic", keep=True, unlike=writer)
        review = Review.model_validate(json.loads(answer))
    except (CreateError, ValueError, TypeError):
        return []
    return [] if review.ok else review.problems


def write_checked(question: str, angle: str = "", *, take: int = 1, steer: str = "") -> tuple[Script, str]:
    """A script held to the channel's countable rules, an editor's read and the physics check, rewritten
    once with everything they found (D155), and what each said, for the page."""
    from . import ai

    ai.misses.clear()
    channel = channels.load()
    script = write(question, angle, take=take, steer=steer)
    writer = ai.last_used
    notes: list[str] = []
    with ThreadPoolExecutor(1) as pool:
        # The fact check and the editor's read don't wait for each other (D168): two models, asked at once.
        editor = pool.submit(critique, script, writer)
        review = _check_or_note(script, notes)
        edits = editor.result()
    facts = review.problems if not review.ok else []
    rules = lint(script, channel)
    planned = None
    if facts or rules or edits:
        feedback = "\n".join(f"- {p}" for p in [*facts, *rules, *edits])
        bit = next(((b["name"], b["how"]) for b in channel.bits if b["name"] == script.bit), ("", ""))
        if bit[0]:   # the same law number as the first draft was asked for
            bit = (bit[0], bit[1].replace("{n}", str(1 + sum(v["script"].get("bit") == bit[0] for v in _videos()))))
        script = write(question, angle, take=take, steer=steer, feedback=feedback, shape=script.shape,
                       ending=script.ending, bit=bit, draft=script)
        writer = ai.last_used
        with ThreadPoolExecutor(1) as pool:
            # The director plans on the final words while they are checked (D168): the check never changes them.
            director = pool.submit(_planned_or_note, script)
            second = _check_or_note(script, notes)
            planned = director.result()
        if not second.ok and second.problems:
            notes.append(f"{channels.check_name()}, still unsure:\n" + "\n".join(f"- {p}" for p in second.problems))
        elif facts:
            notes.append(f"{channels.check_name()}: fixed after a first draft got this wrong:\n"
                         + "\n".join(f"- {p}" for p in facts))
        elif not notes:   # a note already says the check couldn't be done
            notes.append(f"{channels.check_name()}: no problems found.")
        if edits:
            notes.append("Editor's read, fixed in the rewrite:\n" + "\n".join(f"- {p}" for p in edits))
    elif not notes:
        notes.append(f"{channels.check_name()}: no problems found.")
    left = lint(script, channel)
    if left:
        notes.append("Still off the channel's rules:\n" + "\n".join(f"- {p}" for p in left))
    script, pictures = planned or _planned_or_note(script)
    notes.append(pictures)
    return _signed(script, "\n".join(notes), writer)


def _planned_or_note(script: Script) -> tuple[Script, str]:
    """The pictures planned, or the words as they are with a note saying why not: the words stand, and the
    pictures can be planned again from the page."""
    try:
        return plan_pictures(script)
    except CreateError as exc:
        return script, f"Pictures not planned ({exc}): plain footage searches for now; press Plan pictures."


def _check_or_note(script: Script, notes: list[str]) -> Review:
    """The fact check; when no model can do it, a note saying so (the words aren't lost, and aren't
    rewritten for it)."""
    try:
        return check(script)
    except CreateError as exc:
        notes.append(f"{channels.check_name()}: not done ({exc}); press Check physics later.")
        return Review(ok=True)


def _signed(script: Script, note: str, writer: str = "") -> tuple[Script, str]:
    """The script, with the check's note and who wrote it. The sketches are drawn when the video is
    built, not now (D136): a script that is rewritten, edited or dropped would waste every drawing."""
    from . import ai

    writer = writer or ai.last_used
    who = writer.split(":", 1)[-1] if writer else "unknown"
    # One line a provider (D168): four Gemini models out of quota were four lines saying the same thing.
    first: dict[str, str] = {}
    for name, why in ai.misses.items():
        if name not in (ai.last_used, writer):
            first.setdefault(name.split(":", 1)[0], why)
    missed = [f"{provider} didn't answer: {why}" for provider, why in first.items()]
    return tidy(script), "\n".join([note, f"Written by: {who}.", *missed])


def plan_pictures(script: Script) -> tuple[Script, str]:
    """The director's pass (D164): a picture, a highlighted word and a pose for every sentence, every word
    kept, then the diagrams checked against the words, once more if the check finds a problem. The
    user's own clips and pictures stay where they are (D119, D120)."""
    channel = channels.load()
    # How far in each sentence starts, so the director can keep the rules timed in words (no drawing in the
    # first ~9 words: one planned there was turned into footage with a search made of its leftover words).
    starts = [sum(len(b.text.split()) for b in script.beats[:i]) for i in range(len(script.beats))]
    beats = "\n".join(f"{i}. ({w} words in) {b.text}" for i, (b, w) in enumerate(zip(script.beats, starts, strict=True), start=1))
    feedback = ""
    for _ in range(2):
        user = (pose_note(channel) + f"Plan the pictures for this script: one beat for each numbered sentence, "
                f"every sentence kept exactly as written, in order.\n\nTitle: {script.title}\n{beats}\n"
                + (f"\nFix these problems:\n{feedback}\n" if feedback else ""))
        answer = ask(_director(channel), user, _Plan, temperature=0.4, job="script")
        try:
            planned = _parse(answer)
        except (ValueError, TypeError) as exc:
            raise CreateError("the pictures came back unreadable; try again") from exc
        if len(planned.beats) != len(script.beats):
            raise CreateError("the picture plan changed the sentences; try again")
        # The words are the recording's: only the pictures are taken from the plan.
        fresh = tidy(script.model_copy(update={"beats": [
            b.model_copy(update={"visual": b.visual if b.visual.manual else p.visual.model_copy(update={
                "clip": b.visual.clip, "clip_start": b.visual.clip_start, "fill": b.visual.fill}),
                "emphasis": p.emphasis or b.emphasis, "pose": b.pose or p.pose})
            for b, p in zip(script.beats, planned.beats, strict=True)]}))
        try:
            review = check(fresh, diagrams_only=True)
        except CreateError as exc:   # the plan stands; the diagrams can be checked from the page
            return fresh, f"Pictures planned; the diagrams weren't checked ({exc})."
        if review.ok or not review.problems:
            return fresh, f"Pictures: {channels.check_name()} found no problems in the diagrams."
        feedback = "\n".join(f"- {p}" for p in review.problems)
    return fresh, f"Pictures: {channels.check_name()}, still unsure about the diagrams:\n" + feedback


def replan(script: Script) -> tuple[Script, str]:
    """New pictures for an approved script, every word kept (its voice is already made)."""
    from . import ai

    ai.misses.clear()
    planned, note = plan_pictures(script)
    return _signed(planned, "Pictures planned again. " + note.removeprefix("Pictures: "))
