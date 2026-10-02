"""A searchable YouTube title and a pinned comment for each clip (D96).

Platform research (D95): on YouTube Shorts the title is what search reads, and
hashtags bring almost no views (0.03% in Metricool's 2026 study); a clip's hook
line ("the most underrated gay show of the 2010s...") is written to stop a
scroll, not to be searched. And a pinned comment carries what a viewer asks
first -- what is this, where do I watch it -- which the Please Like Me brief asks
for in so many words. Both come from one call per clip, from its transcript,
the campaign's context and its brief, which outranks everything here. The
title gets the brief's title rules added by campaign/rules.py afterwards.
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

SYSTEM = """\
You write two short texts for a vertical clip from a TV show, posted for a paid \
campaign: its YouTube Shorts title and a comment the poster pins under it.

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

Return JSON: {"youtube_title": "...", "pinned_comment": "..."}\
"""


class Extras(BaseModel):
    youtube_title: str
    pinned_comment: str


def build_user(transcript: str, campaign: CampaignConfig, *, hook: str = "", source: str = "",
               brief: str | None = None) -> str:
    from .audit import brief_text

    return (f"THE CAMPAIGN'S BRIEF (follow it over everything):\n{brief_text(campaign, brief) or '(none)'}\n\n"
            f"Context (the show, the people): {campaign.description_context.strip() or '(none)'}\n"
            f"The source video's name: {source.strip() or '(unknown)'}\n"
            f"The clip's on-screen hook: {hook.strip() or '(none)'}\n\n"
            f"Transcript of the clip:\n{transcript.strip()}")


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


def write(transcript: str, campaign: CampaignConfig, backends: list[LLMBackend], *, hook: str = "",
          source: str = "", brief: str | None = None, cache=None) -> Extras | None:
    """The title and pinned comment for one clip, or None if no model gave usable ones."""
    from ..transcribe.correct import _ask

    if not transcript.strip():
        return None
    user = build_user(transcript, campaign, hook=hook, source=source, brief=brief)
    answered = _ask(backends, SYSTEM, user, Extras, cache=cache, prompt_key=f"extras:{EXTRAS_VERSION}")
    if answered is None:
        return None
    try:
        return clean(Extras.model_validate(json.loads(answered[0])), campaign)
    except (ValueError, TypeError) as exc:
        log.info("extras: unusable answer (%s)", str(exc)[:120])
        return None
