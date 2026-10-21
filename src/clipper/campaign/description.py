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
    return _brief_part(campaign, brief) + _clip_part(transcript, caption_line, hook) + _language_note(campaign)


def _brief_part(campaign: CampaignConfig, brief: str | None) -> str:
    """What every clip of the campaign shares: sent once for a batch (D232)."""
    from .audit import brief_text, code_checked

    keywords = ", ".join(campaign.description_keywords) or "(none)"
    rules = brief_text(campaign, brief)
    checked = "\n".join(f"- {c}" for c in code_checked(campaign))
    return (f"THE CAMPAIGN'S BRIEF (follow it over everything):\n{rules or '(none)'}\n\n"
            f"Rules Clipper adds to the caption itself:\n{checked}\n\n"
            f"What the clips should be about: {campaign.selection_focus.strip() or '(not said)'}\n"
            f"Context (the show, the people): {campaign.description_context.strip() or '(none)'}\n"
            f"Keywords: {keywords}\n\n")


def _clip_part(transcript: str, caption_line: str, hook: str) -> str:
    return (f"Caption line above it: {caption_line.strip()}\n"
            f"On-screen hook: {hook.strip() or '(none)'}\n\n"
            f"Transcript of the clip:\n{transcript.strip()}")


def _language_note(campaign: CampaignConfig) -> str:
    """Nothing for English; for another language, the instruction to write the description in it (D147)."""
    from ..config import LANGUAGES

    name = LANGUAGES.get(campaign.language)
    return f"\n\nThe footage is in {name}: write the description in {name}." if name and campaign.language != "en" else ""


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
    # Every model's kept answer first: a batch (describe_many) may have been answered by the fallback.
    if (found := _kept(user, backends, cache)) is not None:
        return found
    for backend in backends:
        key = _key(backend, user, cache) if cache is not None else None
        try:
            response = backend.complete(LLMRequest(system=SYSTEM, user=user, temperature=0.2,
                                                   response_schema=_Description, job="description"))
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


def _key(backend: LLMBackend, user: str, cache) -> str:
    return cache.key(backend=backend.name, model=backend.cache_model(),
                     prompt_key=f"description:{DESCRIPTION_VERSION}", payload=user)


def _kept(user: str, backends: list[LLMBackend], cache) -> str | None:
    if cache is None:
        return None
    for backend in backends:
        entry = cache.get(_key(backend, user, cache))
        if entry is not None and (found := _parse(entry.text)):
            return found
    return None


#: Clips described in one call (D232): the brief, most of the prompt, goes once for all of them.
BATCH = 8
BATCH_SYSTEM = SYSTEM.removesuffix('Return JSON: {"description": "..."}') + (
    "You are given several clips of this campaign, each with its own caption line, hook and transcript. "
    "Write one description for each, from that clip's own facts only.\n\n"
    'Return JSON: {"descriptions": [{"clip": 1, "description": "..."}, ...]}, one per clip.')


class _Numbered(BaseModel):
    clip: int
    description: str


class _Batch(BaseModel):
    descriptions: list[_Numbered]


def describe_many(clips: list[tuple[str, str, str]], campaign: CampaignConfig, backends: list[LLMBackend], *,
                  cache, brief: str | None = None) -> None:
    """Several clips' descriptions asked BATCH at a time with the brief sent once (D232), like the caption
    fixes (D185). Each answer is kept as that clip's own question's would be, so `describe` finds it and asks
    nothing; one that breaks the rules (`clean`), a failed batch or a lone clip is left to its own call.
    `clips`: (transcript, caption line, hook) as `describe` will be given them."""
    if not campaign.long_description or cache is None:
        return
    head, note = _brief_part(campaign, brief), _language_note(campaign)
    todo: dict[str, str] = {}   # the clip's own question -> its part of the batch
    for transcript, line, hook in clips:
        part = _clip_part(transcript, line, hook)
        user = head + part + note
        if transcript.strip() and user not in todo and _kept(user, backends, cache) is None:
            todo[user] = part
    asked = list(todo.items())
    for chunk in (asked[i:i + BATCH] for i in range(0, len(asked), BATCH)):
        if len(chunk) < 2:
            continue
        message = head + "\n\n".join(f"=== CLIP {n} ===\n{part}" for n, (_, part) in enumerate(chunk, 1)) + note
        for backend in backends:
            try:
                response = backend.complete(LLMRequest(system=BATCH_SYSTEM, user=message, temperature=0.2,
                                                       response_schema=_Batch, job="description"))
                got = {d.clip: clean(d.description)
                       for d in _Batch.model_validate_json(response.text).descriptions}
            except Exception as exc:  # each clip still asks on its own
                log.log(miss_level(exc), "descriptions: %s did not answer a batch (%s)", backend.describe(),
                        str(exc)[:160])
                continue
            for n, (user, _) in enumerate(chunk, 1):
                if got.get(n):
                    cache.put(_key(backend, user, cache), text=json.dumps({"description": got[n]}),
                              model=response.model)
            break
    if asked:
        log.info("descriptions: %d clip(s) asked in batches of up to %d", len(asked), BATCH)
