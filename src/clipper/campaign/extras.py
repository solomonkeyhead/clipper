"""A searchable YouTube title and a pinned comment for each clip (D96).

Platform research (D95): on YouTube Shorts the title is what search reads, and
hashtags bring almost no views (0.03% in Metricool's 2026 study); a clip's hook
line ("the most underrated gay show of the 2010s...") is written to stop a
scroll, not to be searched. And a pinned comment carries what a viewer asks
first -- what is this, where do I watch it -- which the Please Like Me brief asks
for in so many words. Both come from the clip's transcript, the campaign's
context and its brief, which outranks everything here. The title gets the
brief's title rules added by campaign/rules.py afterwards.

D185: up to BATCH clips share one call. One call a clip sent the whole brief
each time, and a run's clips used up a free model's daily quota on their own.
"""

from __future__ import annotations

import json
import re

from pydantic import BaseModel

from ..config import CampaignConfig
from ..llm.base import LLMBackend
from ..utils.logging import get_logger

log = get_logger(__name__)

EXTRAS_VERSION = "e2"
TITLE_MIN, TITLE_MAX = 25, 80
PINNED_MAX = 220
BATCH = 8   # clips a call

SYSTEM = """\
You write two short texts for each vertical clip below, cut from a TV show and posted \
for a paid campaign: its YouTube Shorts title and a comment the poster pins under it.

THE BRIEF COMES FIRST. The campaign's rules are given below; follow every one of \
them. If a rule says anything about titles, comments, wording, topics or mentions \
of other platforms, it overrides the guidelines here.

youtube_title (40-70 characters): what someone would type into YouTube search to \
find this moment. Start with the show's name, then who is in it and what happens, \
in plain words: "Please Like Me: Josh tells his mum he's gay", "Chad Powers: Russ \
gets caught lying to the team". Keep it non-sexual even when the scene isn't: name \
the topic without sexual words ("Josh and Tom's most awkward talk", not "...talk \
about sex"), since those words get posts held back. No hashtags, no @mentions \
(they're added for the brief), no emojis, no all-caps, no clickbait the clip \
doesn't deliver.

pinned_comment (under 150 characters): answers "what show is this?" before anyone \
asks. The show's name, the season and episode written out if the source's name \
gives them ("PLM s04 ep3" -> "season 4, episode 3"), and where to watch, exactly as the brief \
or context says it. Casual, one or two short sentences. No links, no hashtags, no \
@mentions unless the brief asks for them, no "like and follow".

Never invent anything the transcript, source name, context or brief don't support: \
no made-up names, plots, seasons or places to watch. If you can't tell who speaks a \
line, don't name them.

Write each clip's texts from its own transcript, and give no two clips the same title.

Return JSON: {"clips": [{"clip": <its number>, "youtube_title": "...", "pinned_comment": "..."}, ...]}, \
one for every clip.\
"""


class Extras(BaseModel):
    youtube_title: str
    pinned_comment: str


class _Written(Extras):
    clip: int


class _Answer(BaseModel):
    clips: list[_Written]


def build_user(clips: list[tuple[str, str, str]], campaign: CampaignConfig, *, brief: str | None = None) -> str:
    """The brief and context once, then each clip: (transcript, on-screen hook, source video's name)."""
    from .audit import brief_text

    parts = [f"THE CAMPAIGN'S BRIEF (follow it over everything):\n{brief_text(campaign, brief) or '(none)'}\n\n"
             f"Context (the show, the people): {campaign.description_context.strip() or '(none)'}"]
    for n, (transcript, hook, source) in enumerate(clips, 1):
        parts.append(f"=== CLIP {n} ===\nThe source video's name: {source.strip() or '(unknown)'}\n"
                     f"The clip's on-screen hook: {hook.strip() or '(none)'}\n"
                     f"Transcript of the clip:\n{transcript.strip()}")
    return "\n\n".join(parts)


def clean(found: Extras, campaign: CampaignConfig | None = None) -> Extras | None:
    """Both texts tidied, or None if either breaks the shape asked for. Hashtags go
    from both; @mentions from the title, where the brief's own are added after.
    A word that gets posts held back (campaign/safety.py) rejects them: the model
    was told so and still wrote "Josh and Tom talk about men and sex"; masked, it
    would read "s*x" in a title, so the clip keeps its hook as the title instead."""
    from .safety import exempt, flagged

    def tidy(text: str, marks: str) -> str:
        text = re.sub(r"\s+", " ", text or "").strip().strip('"').strip()
        return re.sub(rf"(^|\s)[{marks}][\w.]+", "", text).strip()

    title, pinned = tidy(found.youtube_title, "#@"), tidy(found.pinned_comment, "#")
    if not TITLE_MIN <= len(title) <= TITLE_MAX or not pinned or len(pinned) > PINNED_MAX:
        return None
    allowed = exempt(campaign) if campaign is not None else set()
    if any(flagged(w) and w.lower() not in allowed for w in re.findall(r"[A-Za-z]+", f"{title} {pinned}")):
        return None
    return Extras(youtube_title=title, pinned_comment=pinned)


def write(clips: list[tuple[str, str, str]], campaign: CampaignConfig, backends: list[LLMBackend], *,
          brief: str | None = None, cache=None) -> list[Extras | None]:
    """The title and pinned comment for each clip (transcript, on-screen hook, source video's name), BATCH
    clips a call; None for a clip without words, or when no model gave usable ones."""
    from ..transcribe.correct import _ask

    out: list[Extras | None] = [None] * len(clips)
    todo = [k for k, c in enumerate(clips) if c[0].strip()]
    for chunk in (todo[i:i + BATCH] for i in range(0, len(todo), BATCH)):
        user = build_user([clips[k] for k in chunk], campaign, brief=brief)
        answered = _ask(backends, SYSTEM, user, _Answer, cache=cache, prompt_key=f"extras-batch:{EXTRAS_VERSION}")
        if answered is None:
            continue
        try:
            written = _Answer.model_validate(json.loads(answered[0])).clips
        except (ValueError, TypeError) as exc:
            log.info("extras: unusable answer (%s)", str(exc)[:120])
            continue
        for found in written:
            if 1 <= found.clip <= len(chunk):
                out[chunk[found.clip - 1]] = clean(found, campaign)
    return out
