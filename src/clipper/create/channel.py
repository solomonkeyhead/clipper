"""Who each channel is: its persona, the rules its scripts follow, and real scripts of its
own to imitate. Any number of channels, one file each (data/create/channels/<slug>.json), made
from a niche pack (create/packs.py) and then the channel's own.

The first profile was the German Professor's, measured from its own nine Shorts (2026-10-03):
79-129 words, spoken at 2.2-2.5 words a second (35-55 s); a "why does this happen to you?"
question first; a myth-bust or a dry framing ("You say centrifugal force. I say careful.");
the physics as a short chain of causes; one vivid everyday analogy; a two-beat deadpan ending.
Its best performers were things viewers feel in their own body (a car turning, pasta at
altitude, an elevator, hearing loss), its weakest about objects (batteries, torque, seatbelts).
It is now the `physics` pack, and Marc's channel is a file like any other (D146).
"""

from __future__ import annotations

import contextlib
import contextvars
import json
import re
from pathlib import Path

from pydantic import BaseModel, Field

from ..paths import data_root, ensure
from . import packs

_EXPLAINER = packs.EXPLAINER


class Channel(BaseModel):
    slug: str = ""                       # the file's name; fixed once made, even if the channel is renamed
    name: str = "My channel"
    handle: str = ""
    niche: str = ""
    pack: str = packs.DEFAULT
    persona: str = _EXPLAINER.persona
    rules: list[str] = Field(default_factory=lambda: list(_EXPLAINER.rules))
    # What the channel teaches, so the checker, topic planner, footage and sketch prompts speak its
    # field (D133).
    subject: str = _EXPLAINER.subject
    expert: str = _EXPLAINER.expert
    areas: str = _EXPLAINER.areas
    # Standing steering for the idea planner, in the owner's words (D153): what ideas should lean toward,
    # and what they must never be about. Both outrank the planner's own proportions.
    idea_focus: str = ""
    idea_avoid: str = ""
    # The same for scripts: what they should do, and never do, on every script written.
    script_focus: str = ""
    script_avoid: str = ""
    abstract: str = _EXPLAINER.abstract
    hashtags: str = _EXPLAINER.hashtags
    drawings: bool = _EXPLAINER.drawings
    templates: list[str] = Field(default_factory=lambda: list(_EXPLAINER.templates))
    voice: str = "Your own voice: record it in the page, or upload an audio file"
    words_per_second: float = 2.3
    # What scripts are held to in code, the shapes they take in turn, and what ideas may be about (D155).
    # The plain limits, for a channel whose owner wrote its own rules (a made channel takes its pack's): the
    # lenient ones every pack had before D164 tightened the starter packs.
    words: list[int] = Field(default_factory=lambda: [80, 125])
    sentence_max: int = 16
    hook_max: int = 14
    hook_you: bool = False
    shapes: list[str] = Field(default_factory=list)
    scope: str = ""
    # Real scripts of the channel's own, imitated for voice and rhythm, never copied.
    examples: list[dict] = Field(default_factory=list)   # {"title", "text", "views"}
    # A small logo burned into a corner of every video, if there is one.
    watermark: str = ""
    # D156: the channel's character (an image; one with a transparent background is used as it is, any other is cut
    # to a circle), shown at the start, and one of `reactions` at the punchline; a sign-off line for the last second;
    # the chalkboard's colours (create/diagrams.PALETTES); music and chalk sounds by default (each video can differ).
    character: str = ""
    reactions: list[str] = Field(default_factory=list)
    presenter: str = ""                  # the character presenting the first drawing, for a moment (D157)
    poses: dict[str, str] = Field(default_factory=dict)   # name -> picture: the writer tags sentences with these (D158)
    signoff: str = ""
    board: str = "slate"
    music: bool = False
    sfx: bool = False
    jokes: list[str] = Field(default_factory=list)
    bits: list[dict] = Field(default_factory=list)
    # The campaign the finished videos are filed under (studio library).
    campaign: str = ""


#: The fields a channel file from before D155 lacks, taken from its pack when read.
LIMITS = ("words", "sentence_max", "hook_max", "hook_you", "shapes", "scope", "jokes", "bits", "signoff")


def _pack_limits(p: packs.Pack) -> dict:
    return {k: getattr(p, k) for k in LIMITS}


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "channel"


def make(pack: str, name: str, handle: str = "", niche: str = "", **own) -> Channel:
    """A new channel from a niche pack: its persona, rules, subject and diagrams, named for this channel."""
    p = packs.get(pack)
    slug = own.pop("slug", "") or slugify(name)
    return Channel(slug=slug, name=name, handle=handle, niche=niche or p.subject, pack=p.key,
                   persona=p.persona.replace("{name}", name), rules=list(p.rules), subject=p.subject,
                   expert=p.expert, areas=p.areas, abstract=p.abstract, hashtags=p.hashtags,
                   drawings=p.drawings, templates=list(p.templates), words_per_second=p.words_per_second,
                   **_pack_limits(p),
                   campaign=own.pop("campaign", "") or slug, **own)


# ---------------------------------------------------------------- which channel

#: The channel a request is working on (set by `use`); the page's switcher sets it per call, a build
#: thread inherits it. Without it: the one chosen in Create, else the first.
_current: contextvars.ContextVar[str] = contextvars.ContextVar("create_channel", default="")


def set_current(slug: str) -> None:
    """Work on `slug` for the rest of this request or thread (each has its own copy of the setting)."""
    _current.set(slug or "")


@contextlib.contextmanager
def use(slug: str):
    token = _current.set(slug)
    try:
        yield
    finally:
        _current.reset(token)


def channels_dir() -> Path:
    return ensure(data_root() / "create" / "channels")


def path(slug: str = "") -> Path:
    return channels_dir() / f"{slug or active_slug()}.json"


def _legacy() -> Path:
    return data_root() / "create" / "channel.json"


def _read(file: Path) -> Channel:
    raw = json.loads(file.read_text(encoding="utf-8"))
    if "pack" not in raw:   # a channel from before packs: it was the physics one (D146)
        old = packs.PHYSICS
        raw = {"pack": old.key, "abstract": old.abstract, "hashtags": old.hashtags, "drawings": old.drawings,
               "templates": list(old.templates), **raw}
    pack = packs.get(raw["pack"])
    if pack.key == "physics" and raw.get("rules") in packs.PHYSICS_RULES_BEFORE:
        # Never edited by its owner: moved to the new rules, with the limits that go with them (D155).
        raw = {**raw, "rules": list(packs.PHYSICS_RULES), **_pack_limits(pack)}
    if raw.get("rules") == pack.rules:   # a channel edited away from its pack's rules keeps the plain limits
        raw = {**_pack_limits(pack), **raw}
    channel = Channel.model_validate(raw)
    if not channel.slug:
        channel.slug = file.stem
    if not channel.campaign:
        channel.campaign = channel.slug
    return channel


def _migrate() -> None:
    """The one channel.json of before becomes channels/<slug>.json, and the videos and ideas made so
    far belong to it."""
    legacy = _legacy()
    if not legacy.is_file() or any(channels_dir().glob("*.json")):
        return
    old = json.loads(legacy.read_text(encoding="utf-8"))
    channel = _read_legacy(old)
    save(channel)
    legacy.replace(legacy.with_suffix(".json.migrated"))
    from ..studio import db

    with db.connect() as con:
        con.execute("UPDATE create_videos SET channel=? WHERE channel=''", (channel.slug,))
        con.execute("UPDATE create_topics SET channel=? WHERE channel=''", (channel.slug,))
        db.set_setting(con, "create_channel", channel.slug)


def _read_legacy(raw: dict) -> Channel:
    tmp = channels_dir() / "_legacy.json"
    tmp.write_text(json.dumps({**raw, "slug": slugify(raw.get("name", "channel"))}), encoding="utf-8")
    try:
        return _read(tmp)
    finally:
        tmp.unlink(missing_ok=True)


def all_channels() -> list[Channel]:
    _migrate()
    return [_read(f) for f in sorted(channels_dir().glob("*.json"))]


def active_slug() -> str:
    """The channel being worked on: this request's, else the one chosen in Create, else the first."""
    if _current.get():
        return _current.get()
    from ..studio import db

    try:
        with db.connect() as con:
            chosen = db.settings(con).get("create_channel", "")
    except Exception:  # no library yet
        chosen = ""
    have = [f.stem for f in sorted(channels_dir().glob("*.json"))]
    return chosen if chosen in have else (have[0] if have else "")


def load(slug: str = "") -> Channel:
    _migrate()
    slug = slug or active_slug()
    file = channels_dir() / f"{slug}.json"
    return _read(file) if slug and file.is_file() else make(packs.DEFAULT, "My channel")


def save(channel: Channel) -> None:
    if not channel.slug:
        channel.slug = slugify(channel.name)
    if not channel.campaign:
        channel.campaign = channel.slug
    (channels_dir() / f"{channel.slug}.json").write_text(channel.model_dump_json(indent=1), encoding="utf-8")


def delete(slug: str) -> None:
    (channels_dir() / f"{slug}.json").unlink(missing_ok=True)


def check_name(channel: Channel | None = None) -> str:
    """What the fact check is called on this channel's page and notes: "Physics check" on a physics
    channel (as it always was), "Fact check" on any other."""
    return "Physics check" if (channel or load()).pack == "physics" else "Fact check"


def fill(text: str, channel: Channel | None = None) -> str:
    """A prompt with the channel's {subject}, {expert}, {areas}, {abstract}, {hashtags} and {scope} filled in."""
    ch = channel or load()
    return (text.replace("{subject}", ch.subject).replace("{expert}", ch.expert).replace("{areas}", ch.areas)
            .replace("{abstract}", ch.abstract).replace("{hashtags}", ch.hashtags)
            .replace("{scope}", ch.scope or ch.subject))


def steering(what: str, focus: str = "", avoid: str = "", note: str = "", skipped: list[str] | None = None,
             yields: str = "", never: str = "Never") -> str:
    """What the owner wants, said first and said to win (D153): the standing focus and exclusions, this
    request's own note, and the ideas they skipped as examples of what missed. `yields` names what still
    applies (a script's length and format). Empty when there is nothing to say, so an unsteered request
    is exactly what it was."""
    lines = []
    if focus.strip():
        lines.append(f"Lean toward: {focus.strip()}")
    if avoid.strip():
        lines.append(f"{never}: {avoid.strip()}")
    if note.strip():
        lines.append(f"For this one in particular: {note.strip()}")
    if skipped:
        lines.append("The owner skipped these as off target; make none like them:\n"
                     + "\n".join(f"- {q}" for q in skipped[-25:]))
    if not lines:
        return ""
    return (f"THE OWNER'S STEERING for these {what}. It outranks the proportions, style and examples in your "
            f"instructions{yields}:\n" + "\n".join(lines) + "\n\n")


def examples_block(channel: Channel, limit: int = 5) -> str:
    """The channel's best scripts, for the writer to match in voice and rhythm."""
    ranked = sorted(channel.examples, key=lambda e: -(e.get("views") or 0))[:limit]
    blocks = []
    for e in ranked:
        seen = f", {e['views']} views" if e.get("views") else ""
        blocks.append(f"[{e['title']}{seen}]\n{e['text']}")
    return "\n\n".join(blocks)
