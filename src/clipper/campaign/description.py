"""Searchable post descriptions, for campaigns with `long_description`.

TikTok's own advice is that a longer description helps a post get found. This
writes 2-3 plain sentences about what happens in the clip, from its transcript,
with the show's name and the campaign's keywords where they genuinely fit. It
sits between the caption line and the hashtags (compliance.full_caption).

Faithfulness matters more than keywords: a description that promises what the
clip doesn't show is the same fault as an invented hook (llm/prompts.py), so the
model gets the transcript and the brief's context and is told to use nothing
else. Any failure leaves the clip with its short caption.
"""

from __future__ import annotations

import json
import re

from pydantic import BaseModel

from ..config import CampaignConfig
from ..llm.base import LLMBackend, LLMRequest
from ..utils.logging import get_logger

log = get_logger(__name__)

DESCRIPTION_VERSION = "d2"
MIN_CHARS, MAX_CHARS = 80, 450

SYSTEM = """\
You write the description that goes under a short vertical clip from a TV show or
film, posted on TikTok and Instagram. People find clips by searching, so the
description says plainly what happens in this clip.

Write 2 or 3 sentences, 150-350 characters in total:
- Name the show, and the characters involved if the context or transcript names them.
- Say what happens and what is at stake in this clip, in present tense.
- Use search terms from the keyword list only where they truly fit this clip.
- Describe only what the transcript says or makes certain. The context is
  background for names and the premise; do not add a setting (practice, after
  the game, at a party), a secret, a feeling or a stake the transcript doesn't
  state, and do not reveal how the story ends.
- The transcript has no speaker names. Never say who says a line or who
  confesses, admits or confronts, unless the line itself makes it certain
  (e.g. someone is addressed by name). Prefer "Ricky and Russ" as a pair.
- No hashtags, no @mentions, no emojis, no questions to the viewer, no calls to
  action ("follow for more", "watch till the end"), and no quote longer than 6 words.
- Don't repeat the caption line you are given; add to it.

Return JSON: {"description": "..."}\
"""


class _Description(BaseModel):
    description: str


def build_user(transcript: str, campaign: CampaignConfig, caption_line: str) -> str:
    keywords = ", ".join(campaign.description_keywords) or "(none)"
    return (f"Context: {campaign.description_context.strip() or '(none)'}\n"
            f"Keywords: {keywords}\n"
            f"Caption line already posted above: {caption_line.strip()}\n\n"
            f"Transcript of the clip:\n{transcript.strip()}")


def clean(text: str) -> str | None:
    """The description, tidied, or None if it breaks the rules above."""
    text = re.sub(r"\s+", " ", (text or "")).strip().strip('"').strip()
    if re.search(r"(^|\s)[#@]\w", text):
        text = re.sub(r"(^|\s)[#@]\w+", "", text).strip()
    if not MIN_CHARS <= len(text) <= MAX_CHARS:
        return None
    return text


def describe(transcript: str, campaign: CampaignConfig, caption_line: str,
             backends: list[LLMBackend], *, cache=None) -> str:
    """A description for one clip, or "" if the model gave nothing usable."""
    if not campaign.long_description or not transcript.strip():
        return ""
    user = build_user(transcript, campaign, caption_line)
    for backend in backends:
        key = None
        if cache is not None:
            key = cache.key(backend=backend.name, model=backend.cache_model(),
                            prompt_key=f"description:{DESCRIPTION_VERSION}", payload=user)
            entry = cache.get(key)
            if entry is not None:
                found = _parse(entry.text)
                if found:
                    return found
        try:
            response = backend.complete(LLMRequest(system=SYSTEM, user=user, temperature=0.2,
                                                   response_schema=_Description))
        except Exception as exc:  # a description must never fail a render
            log.warning("description: %s did not answer (%s)", backend.describe(), str(exc)[:160])
            continue
        found = _parse(response.text)
        if found:
            if cache is not None and key is not None:
                cache.put(key, text=response.text, model=response.model)
            return found
        log.info("description from %s was unusable: %r", backend.describe(), response.text[:120])
    return ""


def _parse(text: str) -> str | None:
    try:
        return clean(json.loads(text).get("description", ""))
    except (ValueError, AttributeError):
        return None
