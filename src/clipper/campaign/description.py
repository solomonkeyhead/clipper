"""Post descriptions written to be found and to get people talking (D92).

The paragraph between a clip's caption line and its hashtags
(compliance.full_caption), for campaigns with `long_description`. It used to
recount the scene, which does nothing a viewer who just watched it needs. What
the platforms' own search and their creators' guides point to instead
(researched 2026-10-01):

* Search reads the caption: TikTok indexes caption text alongside on-screen
  text and speech, and favours captions of about 150-300 characters with the
  topic's words up front; Instagram ranks keyword-rich captions over hashtags
  and shows ~125 characters before "more"; YouTube indexes a Short's first
  description lines. So the first sentence carries the words people search --
  the show, the people in it, what the moment is about -- in plain language.
* A description adds what the clip can't show: the premise, why the moment
  lands, where to watch -- not a retelling of what was just seen.
* Comments, shares and saves rank posts, so it may end with one specific
  question or "send this to..." tied to this moment, never generic bait.

The campaign's brief comes first: its rules are in the prompt and outrank every
guideline here, and the result is enforced and checked like any caption
(campaign/rules.py). Faithfulness still matters more than keywords: nothing the
transcript and brief don't support. Any failure leaves the clip its caption line.
"""

from __future__ import annotations

import json
import re

from pydantic import BaseModel

from ..config import CampaignConfig
from ..llm.base import LLMBackend, LLMRequest, miss_level
from ..utils.logging import get_logger

log = get_logger(__name__)

DESCRIPTION_VERSION = "d5"
MIN_CHARS, MAX_CHARS = 80, 400

SYSTEM = """\
You write the description under a short vertical clip from a TV show, film or creator, \
posted on TikTok, Instagram Reels and YouTube Shorts for a paid campaign. It goes under \
a caption line that's already written. Its jobs: get the clip found in search, and get \
viewers to comment, share and go watch the show.

THE BRIEF COMES FIRST. The campaign's rules are given below. Follow every one of them \
exactly; if a rule says anything about captions, descriptions, wording, topics, \
mentions of other platforms or calls to action, it overrides the guidelines here.

How to write it (2-3 short sentences, 150-300 characters in total):
1. Open with a natural sentence that carries the words people would type to find this: \
the show's name and, where the context or transcript names them, the people in it, plus \
what this moment is about in plain search terms ("Josh's first boyfriend", "a mum and son \
road trip"). A real sentence, never a list of keywords, with the show's name in its \
first 60 characters. About the first 125 characters show \
before "more", so make them count.
2. Add what the clip can't show on its own: the premise, why this moment lands, or \
where to watch, as the brief puts it. Don't retell the scene the viewer just watched.
3. End with ONE short, specific question or share prompt tied to this moment, unless \
the brief forbids calls to action: something a viewer can answer from their own life \
("Who else had a friend like Tom?") or a person to send it to ("Send this to the friend \
who always takes the biggest one"). Never generic or like-for-like bait ("like and \
follow", "follow for part 2", "comment JOSH to...", "comment below", "watch till the end", \
"thoughts?"): TikTok keeps posts that bargain for engagement off its For You feed.

Use the keyword list where it truly fits; never stuff it. Write like a fan telling a \
friend about the show, in the voice of the caption line above it: casual, specific, no \
press-release words ("hilarious exchange", "must-watch", "award-winning" unless the \
brief says it).

Examples of the shape (not to copy):
- "Josh Thomas in Please Like Me, freaking out that his new boyfriend is too hot to be \
real. The whole show is free on YouTube. Who else has done this?"
- "Rose and Josh's mum-and-son road trip in Please Like Me is the most honest thing TV \
has done about caring for a parent. Send this to your mum."

Never:
- state anything the transcript or context doesn't support: no invented setting, \
secret, feeling, stake, quote or ending, and no spoilers beyond this clip;
- say who says a line unless the line itself makes it certain (a name is used);
- repeat the caption line or the on-screen hook;
- use hashtags, @mentions, emojis or links (the caption adds what the brief requires);
- quote more than 6 words.

Return JSON: {"description": "..."}\
"""


class _Description(BaseModel):
    description: str


def build_user(transcript: str, campaign: CampaignConfig, caption_line: str, *,
               hook: str = "", brief: str | None = None) -> str:
    """The prompt's facts: the brief's rules first, then the clip."""
    from .audit import brief_text, code_checked

    keywords = ", ".join(campaign.description_keywords) or "(none)"
    rules = brief_text(campaign, brief)
    checked = "\n".join(f"- {c}" for c in code_checked(campaign))
    return (f"THE CAMPAIGN'S BRIEF (follow it over everything):\n{rules or '(none)'}\n\n"
            f"Rules Clipper adds to the caption itself:\n{checked}\n\n"
            f"What the clips should be about: {campaign.selection_focus.strip() or '(not said)'}\n"
            f"Context (the show, the people): {campaign.description_context.strip() or '(none)'}\n"
            f"Keywords: {keywords}\n\n"
            f"Caption line above it: {caption_line.strip()}\n"
            f"On-screen hook: {hook.strip() or '(none)'}\n\n"
            f"Transcript of the clip:\n{transcript.strip()}")


def clean(text: str) -> str | None:
    """The description, tidied, or None if it breaks the rules above."""
    text = re.sub(r"\s+", " ", (text or "")).strip().strip('"').strip()
    if re.search(r"(^|\s)[#@]\w", text):
        text = re.sub(r"(^|\s)[#@]\w+", "", text).strip()
    if not MIN_CHARS <= len(text) <= MAX_CHARS:
        return None
    return text


def pasted_brief(campaign: str) -> str | None:
    """The campaign's brief as the user pasted it, if they did (studio/db.py)."""
    try:
        from ..studio import db

        with db.connect() as con:
            return (db.brief(con, campaign) or {}).get("text")
    except Exception as exc:  # no library: the campaign file's rules still go in
        log.debug("no pasted brief for %s: %s", campaign, exc)
        return None


def describe(transcript: str, campaign: CampaignConfig, caption_line: str,
             backends: list[LLMBackend], *, cache=None, hook: str = "", brief: str | None = None) -> str:
    """A description for one clip, or "" if the model gave nothing usable.
    `brief` is the brief as pasted, when there is one (studio/db.py)."""
    if not campaign.long_description or not transcript.strip():
        return ""
    user = build_user(transcript, campaign, caption_line, hook=hook, brief=brief)
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
            log.log(miss_level(exc), "description: %s did not answer (%s)", backend.describe(), str(exc)[:160])
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
