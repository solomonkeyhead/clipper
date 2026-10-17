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
import time
from concurrent.futures import ThreadPoolExecutor
from itertools import pairwise
from typing import Literal

from pydantic import BaseModel, Field, create_model

from ..utils.logging import get_logger
from . import channel as channels
from .ai import CreateError, ask
from .sketch import Sketch

log = get_logger(__name__)

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
    move: str = ""                  # graph: "down" | "up", the way the sentence moves along the curve (D190)
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
    # A drawing that goes on with an earlier one (D200): the same objects, footage in between. It starts as that
    # drawing ended and draws only what this sentence adds, instead of a new board of the same lips.
    builds: bool = False
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
    # Set by hand on a built video (D171): how this picture comes in ("" the build's choice, "cut", "whip", "zoom"),
    # and a drawing's size and place on the board (1 and 0: as drawn; shift is a share of the panel's height, + down).
    transition: str = ""
    scale: float = 1.0
    shift: float = 0.0
    # Where footage is cropped across, set by hand (D180): 0 its left edge, 1 its right; None finds the subject.
    frame: float | None = None


class Beat(BaseModel):
    text: str
    emphasis: str = ""              # the one word to highlight
    pose: str = ""                  # one of the channel's `poses`, shown for this sentence (D158)
    visual: Visual = Field(default_factory=Visual)
    # Set by hand on a built video (D171): the words the captions show for this sentence instead of the spoken ones
    # (spread over the time it is said), and how many seconds earlier (-) or later (+) the cut to it falls.
    caption: str = ""
    nudge: float = 0.0


class Script(BaseModel):
    title: str
    beats: list[Beat]
    description: str = ""
    hashtags: list[str] = Field(default_factory=list)
    hook: str = ""                  # the words on screen at the start, at most 6 (D155); "" = the title
    # Set by the app, not the writer (D155): the shape and ending it was asked for, and the idea's series.
    shape: str = ""
    length: str = ""                # "short": a short take, held to SHORT_WORDS (D199)
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
    # When the presenter is on screen (D203): "all" the whole video (D159), "ends" the opening sentence and from the
    # punchline, so the drawings carry the middle alone, as in the channel's best three.
    on_screen: str = "all"
    # The end runs straight into the start (D197): None = when the script ends on the loop.
    loop: bool | None = None
    # Captions on or off, and the cover's moment in seconds (None: the build picks the best still) (D171).
    captions: bool = True
    cover: float | None = None
    # How the captions look (D180): size (1 as made), place (a share of the frame's height, + down) and the colour
    # of the word being said ("" the channel's, else "#rrggbb").
    caption_size: float = 1.0
    caption_shift: float = 0.0
    caption_colour: str = ""

    @property
    def text(self) -> str:
        return " ".join(b.text.strip() for b in self.beats)

    @property
    def words(self) -> int:
        return len(self.text.split())


#: The fields of a picture only the app sets (the user's clips, what a build picked): left out of
#: the writer's schema, so it isn't shown ~12 fields it can't use and can't invent them.
APP_ONLY = {"sketch", "clip", "clip_start", "fill", "manual", "picked", "avoid", "redo", "previous", "wish", "notice",
            "camera", "restyle", "transition", "scale", "shift", "frame"}
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

#: When the viewer gets a reason to stay (WRITER_TAIL's {reason}): in the opening line on a channel that opens on the
#: surprise (D202), else by the second line, after the question (D203).
REASON_FIRST = ("Give the viewer a reason to stay within the first two seconds: the first line already shows the\n"
                "obvious answer wrong, or gives a number or a contradiction that makes the moment strange; explain after that.")
REASON_SECOND = ("Give the viewer a reason to stay within the first three seconds: by the second line the\n"
                 "obvious answer is shown wrong, or a number or a contradiction makes the question stranger; explain after that.")
#: D203: Pursed lips said "37 degrees Celsius or 98.6 Fahrenheit" in its second line, and viewers of the elevator
#: Short left at 13 s, as "mass times gravity plus mass times acceleration" began.
NUMBERS_LATE = ("No number in the first ten seconds except one surprising figure in the first two lines, and no formula "
                "before the halfway point: say what happens in plain words first.")

VISUALS_TAIL = """  Keep every label under 4 words.
For each beat also give emphasis: the single most important word in it, copied exactly. It is
lit in the captions and the camera punches in on it, so pick the word that carries the point
(the number, the surprising verb, the thing named), never a small word."""

#: The writer's closing instructions (D164): the words only; the director plans the pictures after.
WRITER_TAIL = """Every sentence gets its own picture under it (real footage, or a chalk drawing that shows
the mechanism), so write concrete sentences a picture can show, and let the explanation go one
step a sentence. {reason}
Write for the ear, as every line is read aloud: numbers as they are said, rounded, in one unit (never two units,
never digits with symbols), no abbreviations, no word a narrator would trip on; a technical term only when it is
the point, said once and explained in plain words. Name a thing before you point back to it.
Lines quoted in the rules, the shape, the joke shapes, the persona and the example scripts show the kind of line
only: never reuse three or more words in a row from one, nor its sentence frame with new words in it.

Answer with: lines (the script, one spoken sentence a line, two only if both are very short, in
order), title (the question, at most 60 characters), hook (the words on screen for the first second and a half, read while the first line is heard: at most 6 words
that carry the surprise or the stakes and add what the voice does not say, never a question and never the title or
the first line reworded, "The thermometer did not move"),
description (the script's idea in 3-5 short lines, in the same voice, ending on its punchline),
hashtags (3: {hashtags})."""

#: The director's brief (D164), before the picture rules.
DIRECTOR = """You are the director of the YouTube Shorts channel {name} ({niche}). The script is written
and recorded; you plan what the viewer sees under each sentence, on a phone. The picture carries
half the explanation: with the sound off, a viewer should still follow it. One clear subject per
picture, big in the frame. Keep it moving: a real moment, then a drawing that shows how, then a
close-up; never the same kind of picture for long. Plan the drawings where the words explain HOW,
and footage where they name a thing or a moment the viewer knows. Every beat gets queries and a card, a drawing
too: when a drawing can't be used there, footage is searched with them. A search names what moves, written like
a clip's title, a subject doing the action ("water boiling in pot", "man blowing on hands"): a search for a thing
alone finds it sitting still."""
#: For a channel that draws (D196): video 25 redrew the same lips on four sentences in a row, each from a blank board.
ONE_DRAWING = """When two or more sentences in a row explain one mechanism, plan ONE drawing on the first, its idea
covering all of them, and set hold = true on the rest. Never plan a new drawing that redraws the objects of the
drawing just before it."""
#: For a channel that draws (D200): footage between two sentences of one mechanism made the second drawing start over.
BUILDS = """When a drawing explains more of an earlier drawing (the same objects), with footage in between, plan a
sketch with builds = true and give as its idea only what this sentence adds: it starts as the earlier drawing ended."""
#: When the script loops (D196), the director plans the seam away: the last sentence plays over the opening shot.
LOOP_PICTURE = """This video loops: its last sentence runs back into its first. Give the last beat kind "stock" (never
a drawing, never hold) and the first beat's queries, and choose the first beat's queries so one moving shot of the
everyday moment the question is about fits both: the opening shot plays again under the last sentence, so the end
cuts into the start without a seam."""
#: For a channel that draws (D188): "Professor's Law No. 1: What cannot punch through simply goes" got footage
#: searches; nothing scored, and four such lines were drawn one by one at the end of the build.
UNFILMABLE = """A line with nothing in it a camera could film (a law or rule, a conclusion, a number on its own) gets a
drawing, never footage: no library has a clip of it."""
#: For a channel that draws (D190), the owner's notes on video 22: a drop in temperature drawn as a rising line,
#: the colder molecules drawn in the warm colour, chill drawn flowing into the skin.
AGREE = """A drawing agrees with its own sentence with the sound off. A drop, a loss or "less" goes down; a rise
goes up; what leaves moves away; heat flows from warm to cold, never "cold" flowing in. A graph about a change says
which way: move = "down" for a drop, "up" for a rise (the dot runs along the curve that way); when the sentence
says something falls as time goes on, x = time and shape "falling" shows it best. Particles: values = speeds, so
the colder, slower molecules get the lower value (they are drawn cold, the faster ones warm). Two boxes or things
side by side: the one the sentence starts from on the left."""


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
    "whisper": "a fun fact, an aside", "laugh": "right after his own joke", "coffee": "a calm aside",
    "grudge": "mild annoyance", "proud": "the answer landing",   # no running bits, no sign-off (D190)
}
MAX_POSES = 6   # he is on screen all the time now (D159): more changes keep him alive


def pose_note(channel: channels.Channel) -> str:
    """The poses the writer may tag sentences with, for its request; nothing when the channel has none."""
    if not channel.poses:
        return ""
    names = "; ".join(f"{n} ({POSE_USE[n]})" if n in POSE_USE else n for n in channel.poses if not n.startswith("talking"))
    if not names:
        return ""
    return (f"Poses for the on-screen presenter: set a beat's pose to one of these on at most {MAX_POSES} sentences "
            f"where it fits what is said (the first sentence too: he reacts as the video opens), and leave it empty "
            f"elsewhere. The last sentence needs none. Poses: {names}.\n")


def _poses(script: Script, channel: channels.Channel) -> list[Beat]:
    """The poses kept: known ones, not on the last sentence, at most MAX_POSES. The "talking" poses are the build's
    own, for the sentences in between (D159). The opening may have one (D202): he stood still through it, while
    most viewers swiped."""
    beats, kept = [], 0
    for i, beat in enumerate(script.beats):
        pose = beat.pose.strip().lower() if beat.pose else ""
        ok = (pose in channel.poses and not pose.startswith("talking")
              and i < len(script.beats) - 1 and kept < MAX_POSES)
        beats.append(beat.model_copy(update={"pose": pose if ok else ""}))
        kept += bool(ok)
    return beats


def _system(channel: channels.Channel) -> str:
    """The writer's brief (D164): the persona, the rules and joke shapes, what to answer; no picture planning."""
    rules = "\n".join(f"- {r}" for r in channel.rules)
    jokes = ("\n\nJoke shapes to follow (shapes, never lines to copy):\n" + "\n".join(f"- {j}" for j in channel.jokes)
             if channel.jokes else "")
    if not channel.bits:   # the owner took the coffee cup, the grudges and "Class dismissed" out (D190)
        jokes += ("\n\nNo running gags carried from video to video: no catchphrase, no named law or rule of the "
                  "presenter's, no recurring object or grudge, no sign-off line. Each video stands on its own.")
    reason = (REASON_FIRST if channel.open_surprise else REASON_SECOND) + ("\n" + NUMBERS_LATE if channel.numbers_late else "")
    tail = channels.fill(WRITER_TAIL.replace("{reason}", reason), channel)
    if not channel.drawings:   # a footage-only channel was told of chalk drawings it never makes (D199)
        tail = tail.replace(" (real footage, or a chalk drawing that shows\nthe mechanism)", " (real footage)")
    return (f"{channel.persona}\n\nYou write the scripts for the YouTube Shorts channel "
            f"{channel.name} ({channel.niche}). Rules:\n{rules}{jokes}\n\n{tail}\n\n"
            "The examples are the channel's own scripts, for voice and rhythm only: some break today's "
            "rules (the opening, length, sentence length, structure), and the rules win. Never reuse their jokes or lines."
            # Here, not in each request (D199): the system prompt is cached, and five went uncached into both the
            # write and the rewrite. Three: examples steer more than rules, and the older ones break them.
            + (f"\n\nThe channel's best scripts:\n\n{channels.examples_block(channel, limit=3)}" if channel.examples else ""))


def _director(channel: channels.Channel) -> str:
    """The director's brief (D164): what the pictures are for, then the channel's picture rules."""
    head = DIRECTOR.replace("{name}", channel.name).replace("{niche}", channel.niche)
    if channel.drawings:
        head += " " + " ".join(t.replace("\n", " ") for t in (UNFILMABLE, AGREE, ONE_DRAWING, BUILDS))
    return f"{head}\n\n{channels.fill(visuals(channel), channel)}"


#: How a script ends, one per script in turn (D155): a loop back to the start (rewatches), a "send this
#: to" (shares) or a one-word question (comments). The same deadpan two-beat ending every time became a tic.
ENDINGS = {
    # D196: "a last line that leads back" gave a finished last line about a new moment (soup, after lips), so the
    # restart was heard and seen as a seam.
    "loop": ("two short beats: the punchline, then one last line that is unfinished and runs straight into the "
             "opening line, so when the video starts again the two are heard as one sentence. The last line comes "
             "back to the opening moment (the thing the first picture shows, not a new example), sets the opening "
             "line up without answering or repeating it, and stops on the word just before it: no full stop, no "
             "question mark, no \"...\", so the voice doesn't sound finished. Read the last line, then the opening "
             "line, aloud: if they don't make one natural sentence, rewrite the last line"),
    # No example lines (D168): two scripts, on a spoon in water and on ears on a plane, both ended "Metal or wood:
    # which wins?", the example given here.
    "send": 'a dry "send this to..." line naming, in this video\'s own terms, the person who needs it',
    "poll": "a question about this video's own moment that the viewer can answer in one word in the comments: an "
            "either-or between two things this video talks about",
}


def turn(channel: channels.Channel) -> tuple[str, str]:
    """The shape and ending for the channel's next script: the shape picked at random but never the shape of either
    of the last two scripts (D156; in strict turn, D155, the order itself became a pattern); the ending always
    the loop back to the opening line (D195)."""
    import random

    from . import store

    recent = [v["script"] for v in store.videos()[:2]]
    shapes = [x for x in channel.shapes if x not in {r.get("shape") for r in recent}] or channel.shapes or [""]
    # Always the loop (Marc, D195): "send this to" and the one-word question stay in ENDINGS for older scripts.
    return random.choice(shapes), "loop"


#: A short take's words (D199): about 30 to 38 seconds. Viewers left the 40-50 s videos at 19 to 21 s on average.
SHORT_WORDS = (70, 90)


def next_length(channel: channels.Channel) -> str:
    """"short" for every other script when the channel tests short takes (D199), else ""."""
    from . import store

    if not channel.short_takes:
        return ""
    newest = store.videos()[:1]
    return "" if newest and newest[0]["script"].get("length") == "short" else "short"


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
    """The last lines of the channel's latest scripts, so no joke's shape comes round too soon (D155): the last two,
    as the punchline is the line before a loop's last line (D196)."""
    from . import store

    out = []
    for v in store.videos()[:limit]:
        beats = v["script"].get("beats") or []
        if beats:
            out.append(" ".join(b.get("text", "") for b in beats[-2:]))
    return [e for e in out if e]


def write(question: str, angle: str = "", *, take: int = 1, feedback: str = "", steer: str = "",
          shape: str | None = None, ending: str | None = None, bit: tuple[str, str] | None = None,
          draft: Script | None = None, previous: Script | None = None, length: str | None = None) -> Script:
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
    length = next_length(channel) if length is None else length
    guide = channels.steering("scripts", channel.script_focus, channel.script_avoid, steer,
                              yields="; the word count, the order of the structure and the output format still apply, "
                                     "and every claim must still be true", never="Never in a script")
    endings = recent_endings()
    user = (guide + f"Write a new script answering: {question}\n" + (f"(The {channel.subject}: {angle})\n" if angle else "")
            + (f"Shape: {shape}\n" if shape else "")
            + f"End with {ENDINGS.get(ending, ENDINGS['loop'])}.\n"
            + (f"Running bit for this one: {bit[1]}\n" if bit[1] else "")
            + (f"Length: this one is a short take, {SHORT_WORDS[0]} to {SHORT_WORDS[1]} words in all, which replaces the "
               "word count in the rules. Keep the opening, "
               + ("the answer held back" if channel.answer_late else "the first cause early") + " and the ending; leave out "
               "a second example and any line that only says again what another said.\n" if length == "short" else "")
            + ("The channel's latest endings. Every video ends on the loop, so vary the rest: reuse none of their "
               "words or jokes, and lead back into the opening a different way from each:\n"
               + "\n".join(f"- {e}" for e in endings) + "\n" if endings else "")
            + _previous_block(previous, steer)
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
                  beats=[Beat(text=t) for t in lines], shape=shape, ending=ending, bit=bit[0], length=length)


def _previous_block(previous: Script | None, steer: str) -> str:
    """The take being replaced, for Another take (D193). With the owner's note it is revised: what the note
    doesn't touch stays word for word ("change only the ending" got a whole new script). Without one, the new
    take is a different script, so it is shown to be avoided."""
    if previous is None or not previous.beats:
        return ""
    lines = "\n".join(b.text for b in previous.beats)
    if steer.strip():
        return ("\nThe owner's current script, which they want changed only as their note says:\n"
                f"Title: {previous.title}\n{lines}\n"
                "Change only what the note asks for. Keep every other line exactly as it is, in the same order, "
                "and keep the title unless the note is about it.\n")
    return f"\nThe last take, which the owner wants replaced; write a different one, reusing none of its lines:\n{lines}\n"


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
    # Filler a viewer hears as padding (D199): video 25 had two.
    (re.compile(r"\b(?:essentially|basically|literally|fundamentally)\b", re.I), 'filler words ("essentially", "basically")'),
]
HOOK_TEXT_MAX = 6
#: A number as said or written, or a unit that only comes with one (D203); one, two and ten are everyday words.
_NUMBER = re.compile(r"\d|\b(?:eleven|twelve|(?:thir|four|fif|six|seven|eigh|nine)teen|(?:twen|thir|for|fif|six|seven|"
                     r"eigh|nine)ty|hundred|thousand|million|billion|degrees?|celsius|fahrenheit|kelvin|joules?|newtons?|"
                     r"watts?|volts?|amps?|pascals?|percent|hertz|decibels?)\b", re.I)
#: A formula said in words: two of these in one sentence ("mass times gravity plus mass times acceleration"). One
#: alone is plain speech ("several times faster", "vapour pressure equals the air's").
_OPERATOR = re.compile(r"\b(?:equals|times|divided by|multiplied by|squared|plus|minus)\b", re.I)
#: The same quantity in two units, which the writer is told never to give (D203: "37 degrees Celsius or 98.6
#: Fahrenheit").
_UNITS = [[re.compile(rf"\b(?:{u})\b", re.I) for u in family] for family in (
    ("celsius", "fahrenheit", "kelvin"), (r"miles?", r"kilomet(?:re|er)s?"), (r"pounds?", r"kilo(?:gram)?s?"),
    ("feet|foot", r"met(?:re|er)s?"))]


def _sentences(text: str) -> list[str]:
    return [p.strip() for p in re.split(r"(?<=[.!?])\s+", text) if p.strip()]


def lint(script: Script, channel: channels.Channel) -> list[str]:
    """What breaks the channel's countable rules (D155): length, sentence length, the hook, the on-screen
    hook and the banned habits. Free: no model call. Each problem is one line the writer can act on."""
    problems = []
    low, high = SHORT_WORDS if script.length == "short" else [*channel.words, 0, 10_000][:2]
    if not low <= script.words <= high:
        problems.append(f"It is {script.words} words; it must be {low} to {high}.")
    sentences = _sentences(script.text)
    long = [s for s in sentences if len(s.split()) > channel.sentence_max]
    if long:
        problems.append(f"Sentences over {channel.sentence_max} words: " + " | ".join(long[:3]))
    hook = sentences[0] if sentences else ""
    opening = "opening line" if channel.open_surprise else "opening question"
    if len(hook.split()) > channel.hook_max:
        problems.append(f"The {opening} is {len(hook.split())} words; at most {channel.hook_max}.")
    if channel.hook_you and not re.search(r"\byou(?:r|'re|'ve)?\b", hook, re.I):
        problems.append(f'The {opening} must say "you" or "your".')
    if channel.open_surprise and _asks(hook):
        problems.append(f'Open on the surprise, not a question: "{hook}". The question may come second.')
    if len(script.hook.split()) > HOOK_TEXT_MAX:
        problems.append(f"The on-screen hook is {len(script.hook.split())} words; at most {HOOK_TEXT_MAX}.")
    if script.hook.strip() and (_asks(script.hook) or _echoes(script.hook, [script.title, *sentences[:2]])):
        problems.append(f'The on-screen hook "{script.hook}" says what the title or the voice says. Give it the '
                        "surprise or the stakes in other words, not a question.")
    for pattern, name in BANNED:
        if pattern.search(script.text):
            problems.append(f"Remove {name}.")
    problems += _numbers(sentences, channel)
    # A loop's last line runs into the first (D195, D199): a full stop there makes the voice sound finished.
    last = script.beats[-1].text.strip() if script.beats else ""
    if loops_back(script) and len(script.beats) >= 3 and re.search(r"[.!?…]$", last):
        problems.append(f'The last line must stop on the word before the opening line, with no full stop, question '
                        f'mark or "...": "{last}"')
    copied = _copied(script.text, [channel.persona, *channel.jokes, *channel.shapes])
    if copied:
        problems.append("Lines copied from the examples you were given: " + " | ".join(copied[:3]) +
                        ". Write your own words.")
    return problems


def _numbers(sentences: list[str], channel: channels.Channel) -> list[str]:
    """Numbers, units and formulas before the viewer knows why they matter (D203): a quantity in two units anywhere;
    with `numbers_late`, a number in the first ten seconds past the first two lines, and a formula in the first half."""
    out, said, total = [], 0, sum(len(s.split()) for s in sentences)
    for i, s in enumerate(sentences):
        if any(sum(bool(u.search(s)) for u in family) > 1 for family in _UNITS):
            out.append(f'One unit only: "{s}".')
        if channel.numbers_late and i >= 2 and said < 10 * channel.words_per_second and _NUMBER.search(s):
            out.append(f'A number in the first ten seconds: "{s}". Only one surprising figure, in the first two lines; '
                       "the rest after the reason is clear.")
        if channel.numbers_late and said < total / 2 and len(_OPERATOR.findall(s)) >= 2:
            out.append(f'A formula before the halfway point: "{s}". Say what happens in plain words first, and give '
                       "the formula after the viewer knows why it matters, or leave it out.")
        said += len(s.split())
    return out


def _words(text: str) -> list[str]:
    return re.sub(r"[^a-z0-9' ]+", " ", text.lower()).split()


def _asks(line: str) -> bool:
    """A line that is a question (D202): it ends on "?" or starts with a question word."""
    line = line.strip()
    return line.endswith("?") or bool(re.match(r"(?:why|how come|what makes|ever)\b", line, re.I))


_PLAIN = {"why", "how", "what", "does", "do", "did", "the", "and", "your", "you", "are", "is", "a", "an", "so",
          "feel", "make", "makes", "can", "this", "that", "it", "its", "of", "to", "in", "on", "with", "when"}


def _echoes(hook: str, said: list[str]) -> bool:
    """The on-screen hook saying what is heard or titled anyway (D202): "Why wet shirts feel cold" over "Why does
    your wet shirt feel cold?". Most of its words, by their first four letters, are in one of `said`."""
    def stems(text: str) -> set[str]:
        return {w[:4] for w in _words(text) if len(w) > 2 and w not in _PLAIN}
    own = stems(hook)
    return len(own) >= 2 and any(len(own & stems(s)) >= max(2, 0.6 * len(own)) for s in said if s)


def _copied(text: str, sources: list[str], run: int = 5) -> list[str]:
    """The quoted example lines in `sources` that the script repeats `run` or more words of in a row (D199; four
    caught plain physics, "the floor pushes up"): quoted shapes and jokes came back word for word ("Metal or wood: which wins?" ended two scripts)."""
    said = _words(text)
    grams = {tuple(said[i:i + run]) for i in range(len(said) - run + 1)}
    out = []
    for quote in (q for src in sources for q in re.findall(r'"([^"\n]{12,120})"', src or "")):
        w = _words(quote)
        if any(tuple(w[i:i + run]) in grams for i in range(len(w) - run + 1)):
            out.append(quote)
    return out


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


def loops_back(script: Script) -> bool:
    """Whether the video's end runs into its start (D197): the user's switch, else when it ends on the loop."""
    return script.loop if script.loop is not None else script.ending == "loop"


def tidy(script: Script) -> Script:
    """Rules the model can bend, put straight: known templates, no diagram first or three
    in a row, an emphasis word that's really in its beat, at most 5 hashtags. A loop ends over the opening's
    footage (D196)."""
    beats = []
    pictures: list[str] = []   # the kind of each picture so far: a held sentence adds none
    held = 0
    before = 0                 # words said before this sentence
    last = len(script.beats) - 1
    for i, beat in enumerate(script.beats):
        before, said_before = before + len(beat.text.split()), before
        v = beat.visual
        if (loops_back(script) and i == last >= 3 and not v.manual and not v.clip
                and beats and beats[0].visual.kind == "stock"):
            # The last line runs into the first, so it plays over the opening shot again (build._loop_back): a
            # drawing or a new example there (soup after lips) was a seam the viewer saw on every loop.
            first = beats[0].visual
            v = v.model_copy(update={"kind": "stock", "template": "", "hold": False, "sketch": None,
                                     "queries": first.queries, "query": first.query, "card": v.card or first.card})
            beat = beat.model_copy(update={"visual": v})
        if v.hold:
            # Held on: only after a drawing, and not for ever (a picture held past ~15 s goes stale).
            if pictures and pictures[-1] == "diagram" and held < MAX_HOLD:
                held += 1
                beats.append(beat.model_copy(update={"emphasis": _emphasis(beat)}))
                continue
            v = v.model_copy(update={"hold": False})
        held, held_before = 0, held
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
        root = next((b for b in reversed(beats) if not b.visual.hold), None)
        if (diagram and pictures[-2:] == ["diagram", "diagram"] and v.template == "sketch" and v.idea.strip()
                and root and root.visual.template == "sketch" and not root.visual.sketch and not root.visual.manual
                and held_before < MAX_HOLD):
            # A third drawing in a row about the same thing (D196): it stays up and builds on the one before, the
            # new part in its idea, instead of footage searched with the sentence's leftover words.
            k = beats.index(root)
            beats[k] = root.model_copy(update={"visual": root.visual.model_copy(update={
                "idea": f'{root.visual.idea.strip()} Then, as the voice says "{beat.text}": {v.idea.strip()}'})})
            held = held_before + 1
            beats.append(beat.model_copy(update={"visual": v.model_copy(update={"hold": True}),
                                                 "emphasis": _emphasis(beat)}))
            continue
        if diagram and (i == 0 or said_before < FIRST_DIAGRAM_WORDS or pictures[-2:] == ["diagram", "diagram"]):
            diagram = False
        if not diagram:
            # Only real searches (D196): the sentence's longest words ("breaths degrees thirty", "surrounding
            # directly current") found a fireplace; with fewer than two, the build writes them for the sentence.
            queries = [q for q in [*v.queries, v.query] if q.strip()]
            v = v.model_copy(update={"kind": "stock", "queries": list(dict.fromkeys(queries))[:3],
                                     "query": queries[0] if queries else "",
                                     "card": v.card or beat.emphasis or _query_from(beat.text)})
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


class _Apart(Review):
    """The check of new words and their diagrams in one call (D183), what it found in each kept apart."""
    diagrams: list[str] = Field(default_factory=list)


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
1. Hook: the first line is about a concrete moment the viewer knows, in words they would use, short enough to read
   in two seconds. The first line already gives the viewer a reason to stay: the obvious answer shown wrong, or a
   number or contradiction that makes the moment strange; a plain question first fails. Only when you are told the
   channel may open with a question does it stay one, and then the reason comes by the second line.
2. First cause early: the real reason starts within about 20 words of the opening line.
3. Ear: every sentence says one thing and is easy to say and hear aloud: numbers as they are said, one unit, no
   symbols, no word a narrator would trip on, nothing pointed back to before it is named.
4. Comparison: one everyday comparison that explains the mechanism, not decoration.
5. Payoff: the question is fully answered by three quarters of the way through, in one short plain line a viewer
   could repeat to a friend.
6. Ending: it ends the way it was asked to end. For a loop, read the last line and then the first line as one:
   they must make one natural sentence, the last line must not sound finished, and it must be about the same
   moment as the first line.
7. No AI habits: no stock phrases, no lists of three (three nouns or adjectives in a row; an escalating three-beat
   joke is fine), no "it's not X, it's Y", no line that only says again what the line before said.
Do not judge whether the jokes are funny, and do not check the facts: others do that. Never ask to remove
what the script was asked to include (its shape, its ending, a running bit). Return ok=true
with no problems if all pass. Otherwise, for each failure, one sentence saying exactly what to change."""


#: For a channel that holds the answer back (D218): CRITIC's 2 and 5 would ask for the cause early.
ANSWER_LATE_REVIEW = ("This channel holds the answer back, so judge 2 and 5 this way instead. 2: by the second line "
                      "the wrong belief is named and shown wrong, and the real cause is not given in the first half. "
                      "5: about two thirds of the way in a small new question opens, and the real cause comes in the "
                      "last third in one short plain line a viewer could repeat to a friend.\n")


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
                 f"shape {v.shape}" if v.shape else "", f"the dot runs {v.move} the curve" if v.move else "",
                 f"values {v.values}" if v.values else "",
                 f"amounts {v.amounts}" if v.amounts else "",
                 f"a sketch of: {v.idea}" if v.idea else "",
                 "drawn with labels " + str([m.text for m in v.sketch.marks if m.text]) if v.sketch else "",
                 f"directions {v.directions}" if v.directions else "", f"equation {v.equation!r}" if v.equation else ""]
        lines.append(f"- Over sentence {i} (\"{b.text}\"): " + ", ".join(p for p in parts if p))
    return "\n".join(lines)


def check(script: Script, diagrams_only: bool = False, apart: bool = False) -> Review:
    """The fact check; `diagrams_only` when the words passed it already (D164): only the pictures are new.
    `apart`: what is wrong in the words and in the diagrams comes back separately (an _Apart), so one call
    checks a rewrite and its first picture plan (D183)."""
    diagrams = _diagrams(script)
    if diagrams_only and not diagrams:
        return Review(ok=True)
    user = f"Title: {script.title}\nScript:\n{script.text}" + (f"\n\nDiagrams shown:\n{diagrams}" if diagrams else "")
    if diagrams_only:
        user += ("\n\nThe words were checked already and stay as they are: check only the diagrams, against "
                 "their sentences and the facts.")
    elif apart and diagrams:
        user += ("\n\nKeep the two apart: problems in the words go in problems, problems in a diagram (one that "
                 "disagrees with its sentence or the facts) go in diagrams, each saying which sentence it is over.")
    schema = _Apart if apart and diagrams else Review
    answer = ask(channels.fill(CHECK), user, schema, temperature=0.0, job="check", keep=True)
    try:
        review = schema.model_validate(json.loads(answer))
    except (ValueError, TypeError):
        review = Review(ok=False, problems=[f"The {channels.check_name().lower()} came back unreadable; read it carefully yourself."])
    return _Apart(**review.model_dump()) if apart else review


def _videos() -> list[dict]:
    from . import store

    return store.videos()


def critique(script: Script, writer: str = "") -> list[str]:
    """What an editor would change (D155), asked of a different AI from the one that wrote it: a model
    goes easy on its own work. Nothing when no other AI answers; the script still goes on."""
    channel = channels.load()
    shape = script.shape if script.shape in channel.shapes else ""
    bit = next((b["how"].replace("{n}", "N") for b in channel.bits if b["name"] == script.bit), "")
    # Only an ending it was asked for (D199): the fallback told the editor "a deadpan two-beat line" after D195
    # made every ending the loop.
    user = ((f"It was asked to end with {ENDINGS[script.ending]}.\n" if script.ending in ENDINGS else "")
            + (f"Its shape, as asked: {shape}\n" if shape else "") + (f"It was asked to include: {bit}\n" if bit else "")
            + ("" if channel.open_surprise else "The channel may open with a question.\n")   # D202
            + (ANSWER_LATE_REVIEW if channel.answer_late else "")   # D218: 2 and 5 judged the other way round
            + "\n"
            f"Title: {script.title}\nScript:\n" + "\n".join(b.text for b in script.beats))
    try:
        answer = ask(channels.fill(CRITIC), user, Review, temperature=0.0, job="critic", keep=True, unlike=writer)
        review = Review.model_validate(json.loads(answer))
    except (CreateError, ValueError, TypeError):
        return []
    return [] if review.ok else review.problems


def write_checked(question: str, angle: str = "", *, take: int = 1, steer: str = "",
                  progress=None, previous: Script | None = None) -> tuple[Script, str]:
    """A script held to the channel's countable rules, an editor's read and the physics check, rewritten
    once with everything they found (D155), and what each said, for the page. `progress(stage, percent)` hears
    each step (D188): the page shows it, and the log gets each step's seconds."""
    from . import ai

    started, took = time.monotonic(), []

    def step(stage: str, pct: float) -> None:
        took.append((stage, time.monotonic()))
        if progress:
            progress(stage, pct)

    ai.misses.clear()
    channel = channels.load()
    step("Writing the script", 5)
    # A note revises the take it names (D193): its shape and ending stay unless the note says otherwise.
    keep = previous if previous and steer.strip() else None
    script = write(question, angle, take=take, steer=steer, previous=previous,
                   shape=keep.shape if keep else None, ending=keep.ending if keep else None,
                   length=keep.length if keep else None)
    writer = ai.last_used
    notes: list[str] = []
    step(f"The editor's read and the {channels.check_name().lower()}", 30)
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
        step("Rewriting with the fixes", 50)
        script = write(question, angle, take=take, steer=steer, feedback=feedback, shape=script.shape,
                       ending=script.ending, bit=bit, draft=script, previous=keep, length=script.length)
        writer = ai.last_used
        step("Planning the pictures", 70)
        # The director plans on the final words, then one check reads the words and the diagrams (D183): the
        # words check used to go beside the director, and the diagrams' check after it took as long.
        *planned, second = _planned_or_note(script, words=True)
        if second is None:   # not checked with the pictures: the words on their own
            second = _check_or_note(script, notes)
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
    if not planned:
        step("Planning the pictures", 70)
    script, pictures = planned or _planned_or_note(script)[:2]
    notes.append(pictures)
    took.append(("", time.monotonic()))
    log.info("create: script for %r written in %.0fs: %s", question[:60], took[-1][1] - started,
             ", ".join(f"{stage} {t2 - t1:.0f}s" for (stage, t1), (_, t2) in pairwise(took)))
    return _signed(script, "\n".join(notes), writer)


def _planned_or_note(script: Script, words: bool = False) -> tuple[Script, str, Review | None]:
    """The pictures planned, or the words as they are with a note saying why not: the words stand, and the
    pictures can be planned again from the page. With `words`, what the check found in the words (_plan)."""
    try:
        return _plan(script, words)
    except CreateError as exc:
        return script, f"Pictures not planned ({exc}): plain footage searches for now; press Plan pictures.", None


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
    planned, note, _ = _plan(script)
    return planned, note


def _plan(script: Script, words: bool = False) -> tuple[Script, str, Review | None]:
    """plan_pictures, and with `words` the new words checked in the same call as the first plan's diagrams
    (D183): after a rewrite, a words check of its own went beside it. Returns what was wrong in the words,
    None when they weren't checked."""
    channel = channels.load()
    said = None
    # How far in each sentence starts, so the director can keep the rules timed in words (no drawing in the
    # first ~9 words: one planned there was turned into footage with a search made of its leftover words).
    starts = [sum(len(b.text.split()) for b in script.beats[:i]) for i in range(len(script.beats))]
    beats = "\n".join(f"{i}. ({w} words in) {b.text}" for i, (b, w) in enumerate(zip(script.beats, starts, strict=True), start=1))
    # The sentences said in the opening words (D196): the second one too, often, which got a drawing that tidy then
    # emptied into a search made of its longest words ("breaths degrees thirty" found a fireplace).
    early = [str(n) for n, w in enumerate(starts, 1) if w < FIRST_DIAGRAM_WORDS]
    opening = (f"Sentence{'s' if len(early) > 1 else ''} {', '.join(early)} {'are' if len(early) > 1 else 'is'} said "
               f"in the first {FIRST_DIAGRAM_WORDS} words: footage only, with three searches each, even for a "
               "number.\n") if channel.drawings else ""
    feedback = ""
    for n in range(2):
        user = (pose_note(channel) + "Plan the pictures for this script: one beat for each numbered sentence, "
                "every sentence kept exactly as written, in order.\n" + opening
                + (LOOP_PICTURE.replace("\n", " ") + "\n" if script.ending == "loop" else "")
                + f"\nTitle: {script.title}\n{beats}\n"
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
        both = words and n == 0
        try:
            review = check(fresh, diagrams_only=not both, apart=both)
        except CreateError as exc:   # the plan stands; the diagrams can be checked from the page
            return fresh, f"Pictures planned; the diagrams weren't checked ({exc}).", said
        if both:
            said = Review(ok=not review.problems, problems=review.problems)
            review = Review(ok=not review.diagrams, problems=review.diagrams)
        if review.ok or not review.problems:
            return fresh, f"Pictures: {channels.check_name()} found no problems in the diagrams.", said
        feedback = "\n".join(f"- {p}" for p in review.problems)
    return fresh, f"Pictures: {channels.check_name()}, still unsure about the diagrams:\n" + feedback, said


def replan(script: Script) -> tuple[Script, str]:
    """New pictures for an approved script, every word kept (its voice is already made)."""
    from . import ai

    ai.misses.clear()
    planned, note = plan_pictures(script)
    return _signed(planned, "Pictures planned again. " + note.removeprefix("Pictures: "))
