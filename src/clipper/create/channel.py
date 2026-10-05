"""Who the channel is: its persona, the rules its scripts follow, and real scripts
of its own to imitate (data/create/channel.json, one channel for now).

The first profile is the German Professor's, measured from its own nine Shorts
(2026-10-03): 79-129 words, spoken at 2.2-2.5 words a second (35-55 s); a "why
does this happen to you?" question first; a myth-bust or a dry framing ("You say
centrifugal force. I say careful."); the physics as a short chain of causes
("Lower temperature means lower motion. Lower motion means slower diffusion.");
one vivid everyday analogy; a two-beat deadpan ending. Its best performers were
things viewers feel in their own body (a car turning, pasta at altitude, an
elevator, hearing loss), its weakest about objects (batteries, torque, seatbelts).
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from ..paths import data_root, ensure

PROFESSOR_PERSONA = """You write as the German Professor ("Marshal"): a friendly, funny
German physics professor. Warm, precise, deadpan. Gentle academic shade at
everyday misconceptions, never mean. Occasional personal asides ("I call it...",
"Newton calls this inertia. Your stomach calls this nausea."). Spoken English,
short sentences, the rhythm of someone explaining with a raised eyebrow."""

PROFESSOR_RULES = [
    "80 to 125 words in all: it is read aloud at about 2.3 words a second, 35 to 55 seconds.",
    "Open with ONE question about something the viewer has felt or seen in daily life, "
    "ideally in their own body (\"Why do you feel heavier when the elevator starts going up?\"). "
    "At most 14 words.",
    "Second: a myth-bust or a dry framing of what people think (\"You say centrifugal force. "
    "I say careful.\", \"Congratulations, you've met acceleration.\").",
    "Then the physics as a short chain of causes, 3 to 5 steps, each its own short sentence "
    "(\"Lower temperature means lower motion. Lower motion means slower diffusion.\").",
    "At most one formula, said in words (\"force equals mass times acceleration\").",
    "One vivid everyday analogy (\"like a thief sprinting off with your wallet\").",
    "End on a deadpan two-beat punchline that ties back to the opening "
    "(\"Physics made you fat for one second. Then it apologized.\").",
    "Every sentence at most 16 words. No lists, no emojis, no hashtags, no \"in this video\", "
    "no \"let's dive in\", no greeting, no call to subscribe.",
    "The physics must be right. Simplify, never misstate: name the real mechanism.",
]


class Channel(BaseModel):
    name: str = "German Professor"
    handle: str = "@German.Professor"
    niche: str = "everyday physics: why the physical world does what it does to you"
    persona: str = PROFESSOR_PERSONA
    rules: list[str] = Field(default_factory=lambda: list(PROFESSOR_RULES))
    # What the channel teaches, so the checker, topic planner, footage and sketch prompts speak its
    # field, not just physics (D133). The defaults are the German Professor's: its prompts read
    # exactly as before.
    subject: str = "physics"
    expert: str = "a physics professor"
    areas: str = "mechanics, heat, sound, light, electricity, fluids and pressure, materials"
    voice: str = "ElevenLabs, voice \"Marshal - friendly, funny professor\""
    words_per_second: float = 2.3
    # Real scripts of the channel's own, imitated for voice and rhythm, never copied.
    examples: list[dict] = Field(default_factory=list)   # {"title", "text", "views"}
    # A small logo burned into a corner of every video, if there is one.
    watermark: str = ""
    # The campaign the finished videos are filed under (studio library).
    campaign: str = "german-professor"


def fill(text: str, channel: Channel | None = None) -> str:
    """A prompt with the channel's {subject}, {expert} and {areas} filled in (D133)."""
    ch = channel or load()
    return text.replace("{subject}", ch.subject).replace("{expert}", ch.expert).replace("{areas}", ch.areas)


def path() -> Path:
    return ensure(data_root() / "create") / "channel.json"


def load() -> Channel:
    p = path()
    if p.is_file():
        return Channel.model_validate_json(p.read_text(encoding="utf-8"))
    return Channel()


def save(channel: Channel) -> None:
    path().write_text(channel.model_dump_json(indent=1), encoding="utf-8")


def examples_block(channel: Channel, limit: int = 5) -> str:
    """The channel's best scripts, for the writer to match in voice and rhythm."""
    ranked = sorted(channel.examples, key=lambda e: -(e.get("views") or 0))[:limit]
    blocks = []
    for e in ranked:
        seen = f", {e['views']} views" if e.get("views") else ""
        blocks.append(f"[{e['title']}{seen}]\n{e['text']}")
    return "\n\n".join(blocks)
