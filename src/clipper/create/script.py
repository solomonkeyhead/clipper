"""A script in the channel's voice, split into beats with a picture planned for each,
then checked for physics by a second, stricter pass (a wrong fact is the fastest
way to lose an educational channel's trust).

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


#: The fields of a picture only the app sets (the user's clips, what a build picked): left out of
#: the writer's schema, so it isn't shown ~12 fields it can't use and can't invent them.
APP_ONLY = {"sketch", "clip", "clip_start", "fill", "manual", "picked", "avoid", "redo", "previous", "wish", "notice"}
_WriterVisual = create_model("WriterVisual", **{k: (f.annotation, f) for k, f in Visual.model_fields.items()
                                                if k not in APP_ONLY})
_WriterBeat = create_model("WriterBeat", text=(str, ...), emphasis=(str, ""),
                           visual=(_WriterVisual, Field(default_factory=_WriterVisual)))
_WriterScript = create_model("WriterScript", title=(str, ...), beats=(list[_WriterBeat], ...), description=(str, ""),
                             hashtags=(list[str], Field(default_factory=list)))


def _parse(answer: str) -> Script:
    """The writer's answer as a Script: only what the writer's schema holds is taken."""
    return Script.model_validate(_WriterScript.model_validate(json.loads(answer)).model_dump())


VISUALS_HEAD = """Split the script into beats: one spoken sentence each (two only if both are very
short), 5 to 14 words. For every beat plan ONE picture:
- kind "stock": real footage that literally shows what is said. Give queries: three searches
  of a free stock-footage library, most specific first, then broader, 1-3 words each, naming
  subjects such libraries really have: objects, places, nature, everyday scenes, and people
  only in common situations ("woman wearing headphones", "man talking on phone", "boiling
  pot", "airplane window", "elevator doors"). Never abstract words ({abstract}), never a specific person's action that nobody films ("person touching side of
  head"). Also give card: a 1-4 word phrase from the sentence to write on the chalkboard if
  no footage fits ("bone conduction", "100 degrees").
- kind "diagram": an animated chalkboard diagram, when the beat explains HOW or HOW MUCH.
  About half the beats; never the first beat; never three diagrams in a row.
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
    by an illustrator after you. idea = one or two sentences saying exactly what to draw:
    the objects, where things go, the 2-4 labels ("A head in profile. Sound leaves the mouth
    and curves round through the air to the ear, dashed blue, labelled 'air'. A second
    yellow path goes from the throat straight through the skull to the inner ear,
    labelled 'bone'."). Concrete and physical, never a flow chart. Leave sketch empty.
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
For each beat also give emphasis: the single most important word in it, copied exactly.

Also write: title (the question, at most 60 characters), description (the script's idea in
3-5 short lines, in the same voice, ending on its punchline), hashtags (3: {hashtags})."""


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



def _system(channel: channels.Channel) -> str:
    rules = "\n".join(f"- {r}" for r in channel.rules)
    return (f"{channel.persona}\n\nYou write the scripts for the YouTube Shorts channel "
            f"{channel.name} ({channel.niche}). Rules:\n{rules}\n\n{channels.fill(visuals(channel), channel)}\n\n"
            "The examples are the channel's own scripts: match their voice, rhythm and humour, "
            "never reuse their jokes or lines.")


def write(question: str, angle: str = "", *, take: int = 1, feedback: str = "", steer: str = "") -> Script:
    """A new script for `question`; `take` asks for a fresh attempt, `feedback` for fixes, `steer` is the
    owner's note on what to change (D153). The channel's own standing guidance applies every time."""
    channel = channels.load()
    guide = channels.steering("scripts", channel.script_focus, channel.script_avoid, steer,
                              yields="; the word count, the order of the structure and the output format still apply, "
                                     "and every claim must still be true", never="Never in a script")
    user = (guide + f"The channel's best scripts:\n\n{channels.examples_block(channel)}\n\n"
            f"Write a new script answering: {question}\n" + (f"(The {channel.subject}: {angle})\n" if angle else "")
            + (f"\nFix these problems from the last draft:\n{feedback}\n" if feedback else "")
            + f"\n(take {take})")
    answer = ask(_system(channel), user, _WriterScript, temperature=0.85, job="script")
    try:
        return tidy(_parse(answer))
    except (ValueError, TypeError) as exc:
        raise CreateError("the script came back unreadable; try again") from exc


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
    title = " ".join(title.split()) or beats[0].rstrip(".!?")[:60]
    made = Script(title=title[:100], description=description.strip()[:1000],
                  hashtags=[h for h in (hashtags or []) if h.strip()],
                  beats=[Beat(text=b, visual=Visual(kind="stock", query=_query_from(b))) for b in beats])
    return tidy(made)


#: At most this many sentences after a drawing may keep it on screen (D124).
MAX_HOLD = 3


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
    for i, beat in enumerate(script.beats):
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
        if diagram and (i == 0 or pictures[-2:] == ["diagram", "diagram"]):
            diagram = False
        if not diagram:
            queries = [q for q in [*v.queries, v.query] if q.strip()] or [_query_from(beat.text)]
            v = v.model_copy(update={"kind": "stock", "queries": list(dict.fromkeys(queries))[:3],
                                     "query": queries[0], "card": v.card or beat.emphasis or _query_from(beat.text)})
        beats.append(beat.model_copy(update={"visual": v, "emphasis": _emphasis(beat)}))
        pictures.append(v.kind)
    tags = [("#" + t.lstrip("#")).replace(" ", "") for t in script.hashtags if t.strip("# ")][:5]
    return script.model_copy(update={"beats": beats, "hashtags": tags})


def _query_from(text: str) -> str:
    """A fallback stock query: the beat's longest words."""
    words = sorted({w.lower() for w in re.findall(r"[A-Za-z]{4,}", text)}, key=len, reverse=True)
    return " ".join(words[:3])


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
formula, or a "number" that is not the true figure. Return ok=true with no problems if it is correct. Otherwise list each problem in one sentence with the correct {subject}."""


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


def check(script: Script) -> Review:
    diagrams = _diagrams(script)
    user = f"Title: {script.title}\nScript:\n{script.text}" + (f"\n\nDiagrams shown:\n{diagrams}" if diagrams else "")
    answer = ask(channels.fill(CHECK), user, Review, temperature=0.0, job="check", keep=True)
    try:
        return Review.model_validate(json.loads(answer))
    except (ValueError, TypeError):
        return Review(ok=False, problems=[f"The {channels.check_name().lower()} came back unreadable; read it carefully yourself."])


def write_checked(question: str, angle: str = "", *, take: int = 1, steer: str = "") -> tuple[Script, str]:
    """A script that passed the physics check (one rewrite if it didn't), and what the
    check said, for the page."""
    from . import ai

    ai.misses.clear()
    script = write(question, angle, take=take, steer=steer)
    review = check(script)
    if not review.ok and review.problems:
        script = write(question, angle, take=take, steer=steer, feedback="\n".join(f"- {p}" for p in review.problems))
        second = check(script)
        if not second.ok and second.problems:
            note = f"{channels.check_name()}, still unsure:\n" + "\n".join(f"- {p}" for p in second.problems)
        else:
            note = f"{channels.check_name()}: fixed after a first draft got this wrong:\n" + \
                "\n".join(f"- {p}" for p in review.problems)
    else:
        note = f"{channels.check_name()}: no problems found."
    return _signed(script, note)


def _signed(script: Script, note: str) -> tuple[Script, str]:
    """The script, with the check's note and who wrote it. The sketches are drawn when the video is
    built, not now (D136): a script that is rewritten, edited or dropped would waste every drawing."""
    from . import ai

    who = ai.last_used.split(":", 1)[-1] if ai.last_used else "unknown"
    missed = [f"{name.split(':', 1)[0]} didn't answer: {why}" for name, why in ai.misses.items()
              if name != ai.last_used]
    return tidy(script), "\n".join([note, f"Written by: {who}.", *missed])


def replan(script: Script) -> tuple[Script, str]:
    """New pictures for an approved script, every word kept (its voice is already made):
    the visuals planned again, then checked, once more if the check finds a problem."""
    from . import ai

    ai.misses.clear()
    channel = channels.load()
    beats = "\n".join(f"{i}. {b.text}" for i, b in enumerate(script.beats, start=1))
    feedback = ""
    for _ in range(2):
        user = (f"This approved script is already recorded, sentence by sentence. Keep every sentence "
                f"exactly as written, in order, one beat each, and plan the pictures again.\n\n"
                f"Title: {script.title}\n{beats}\n" + (f"\nFix these problems:\n{feedback}\n" if feedback else ""))
        answer = ask(_system(channel), user, _WriterScript, temperature=0.4, job="script")
        try:
            planned = _parse(answer)
        except (ValueError, TypeError) as exc:
            raise CreateError("the new pictures came back unreadable; try again") from exc
        if len(planned.beats) != len(script.beats):
            raise CreateError("the new plan changed the sentences; try again")
        # The words are the recording's: only the pictures are taken from the new plan.
        # ...and the user's own clips stay where they were put (D119).
        # ...and so do the pictures the user chose themselves (D120).
        fresh = tidy(script.model_copy(update={"beats": [
            b.model_copy(update={"visual": b.visual if b.visual.manual else p.visual.model_copy(update={
                "clip": b.visual.clip, "clip_start": b.visual.clip_start, "fill": b.visual.fill}),
                "emphasis": p.emphasis or b.emphasis})
            for b, p in zip(script.beats, planned.beats, strict=True)]}))
        review = check(fresh)
        if review.ok or not review.problems:
            return _signed(fresh, f"Pictures planned again. {channels.check_name()}: no problems found.")
        feedback = "\n".join(f"- {p}" for p in review.problems)
    return _signed(fresh, f"Pictures planned again. {channels.check_name()}, still unsure:\n" + feedback)
