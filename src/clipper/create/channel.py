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
    abstract: str = _EXPLAINER.abstract
    hashtags: str = _EXPLAINER.hashtags
    drawings: bool = _EXPLAINER.drawings
    templates: list[str] = Field(default_factory=lambda: list(_EXPLAINER.templates))
    voice: str = "Your own voice: record it in the page, or upload an audio file"
    words_per_second: float = 2.3
    # Real scripts of the channel's own, imitated for voice and rhythm, never copied.
    examples: list[dict] = Field(default_factory=list)   # {"title", "text", "views"}
    # A small logo burned into a corner of every video, if there is one.
    watermark: str = ""
    # The campaign the finished videos are filed under (studio library).
    campaign: str = ""


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
    """A prompt with the channel's {subject}, {expert}, {areas}, {abstract} and {hashtags} filled in."""
    ch = channel or load()
    return (text.replace("{subject}", ch.subject).replace("{expert}", ch.expert).replace("{areas}", ch.areas)
            .replace("{abstract}", ch.abstract).replace("{hashtags}", ch.hashtags))


def examples_block(channel: Channel, limit: int = 5) -> str:
    """The channel's best scripts, for the writer to match in voice and rhythm."""
    ranked = sorted(channel.examples, key=lambda e: -(e.get("views") or 0))[:limit]
    blocks = []
    for e in ranked:
        seen = f", {e['views']} views" if e.get("views") else ""
        blocks.append(f"[{e['title']}{seen}]\n{e['text']}")
    return "\n\n".join(blocks)
