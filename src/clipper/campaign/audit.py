"""A second reader: each clip's post texts checked against the brief's own words (D81).

campaign/rules.py enforces, exactly, the rules the campaign file states. This
catches the ones it doesn't -- a rule the brief reader missed or a user never
typed in, like "On YouTube, tag @JoshThomasChannel in the title", which once
went unnoticed on 40 clips. The model reads the brief as pasted (or, without
one, what the campaign file records of it beyond its structured rules) and
each platform's title, caption and on-screen text, and lists every rule about
that text that is broken, quoting the rule.

Small models raise false alarms on rules like these (an @mention called an
extra hashtag; an example caption read as mandatory), so the model is told
which rules code already checks exactly and to leave them alone, and each
problem it reports is put to it again as one focused yes/no question before
it is shown. A "missing" text that is already there is dropped regardless.

A problem whose fix is adding text comes back with that text, so one click can
make it a caption rule for every clip (studio/rulecheck.py).

D185: up to AUDIT_BATCH clips are read in one call; a call a clip sent the whole
brief (up to 30,000 characters) each time.
"""

from __future__ import annotations

import hashlib
import json
import re

from pydantic import BaseModel, Field

from ..config import CampaignConfig
from ..llm.base import ContentBlocked, LLMBackend, LLMRequest, miss_level
from ..utils.logging import get_logger
from .compliance import _contains
from .rules import NAMES, PostText

log = get_logger(__name__)

AUDIT_VERSION = "audit-v3"
AUDIT_BATCH = 6   # clips a call

SYSTEM = """\
You check short videos' posts against the paid campaign brief they are for, before they are \
posted. A mistake can get a post rejected and unpaid, so be exact.

Find every rule in the brief about a post's TEXT: the caption, the YouTube title, the \
on-screen text, @mentions and tags, a disclosure, a required phrase or link, banned words \
or topics, language, emojis. Check each against each clip's post on each platform below, \
every clip on its own. A rule that names a platform applies only there.

Skip the rules listed under ALREADY CHECKED: code checks those exactly. Skip rules about \
anything that is not in the text: the footage, editing, length, eligibility, audience, \
payouts, posting schedules and account settings. Example captions are suggestions, not \
requirements, unless the brief says only they may be used.

Report only clear breaks of a rule the brief states, not style, suggestions or guesses. \
Read exactly: a hashtag starts with #; an @mention is not a hashtag. Quote the rule word \
for word from the brief. When adding text would fix it, put exactly that text in "add" \
(e.g. "@JoshThomasChannel"), else "".

Return JSON: {"problems": [{"clip": <its number>, "rule": "...", "platform": "tiktok" | \
"instagram_reels" | "youtube_shorts" | "all", "where": "caption" | "title" | "on-screen text", \
"problem": "...", "add": "..."}]} -- an empty list when every post follows every rule."""

CONFIRM = """\
A campaign brief has this rule. Does the post text below clearly break it? Answer only \
from the rule's own words and the text as written. A hashtag starts with #; an @mention \
is not a hashtag. If the rule doesn't apply to this text, or the text follows it, the \
answer is no.

Return JSON: {"breaks": true | false, "why": "one short sentence"}"""


class Refused(RuntimeError):
    """Every model refused to read the post (a provider's own content filter)."""


class Problem(BaseModel):
    rule: str
    platform: str = "all"
    where: str = "caption"
    problem: str = ""
    add: str = ""


class _Found(Problem):
    clip: int = 1


class _Answer(BaseModel):
    problems: list[_Found] = Field(default_factory=list)


class _Verdict(BaseModel):
    breaks: bool = False
    why: str = ""


def brief_text(campaign: CampaignConfig, pasted: str | None) -> str:
    """The brief as pasted, or what the campaign file records of it beyond `code_checked`."""
    if pasted and pasted.strip():
        return pasted.strip()
    lines = [f"Post on: {', '.join(NAMES.get(p, p) for p in campaign.platform_targets)}.",
             campaign.brief_rules, *campaign.posting_rules, campaign.notes]
    return "\n".join(x.strip() for x in lines if x and x.strip())


def code_checked(campaign: CampaignConfig) -> list[str]:
    """The rules campaign/rules.py enforces and checks exactly."""
    out = []
    if campaign.required_hashtags:
        out.append(f"Required hashtags: {' '.join(campaign.required_hashtags)}")
    if campaign.only_required_hashtags:
        out.append("No hashtags other than the required ones (and those in the brief's own captions)")
    if campaign.required_caption_text:
        out.append(f"Every caption includes: {campaign.required_caption_text}")
    if campaign.required_credit_text:
        out.append(f"Credit: {campaign.required_credit_text}")
    if campaign.forbidden_terms:
        out.append(f"Banned words: {', '.join(campaign.forbidden_terms)}")
    for r in campaign.caption_rules:
        where = ", ".join(NAMES.get(p, p) for p in r.platforms) or "every platform"
        out.append(f"{'Includes' if r.must == 'include' else 'Never says'} {r.text!r} in the {r.place} "
                   f"on {where}" + (f" (the brief: {r.quote})" if r.quote else ""))
    if campaign.fallback_captions:
        out.append("The brief's own captions may be used as they are: " + " | ".join(campaign.fallback_captions))
    out.append("Each platform's length and hashtag limits")
    return out


def _posts(posts: list[PostText], hook: str) -> list[str]:
    shown = [f"ON-SCREEN TEXT (all platforms): {hook.strip() or '(none)'}"]
    for post in posts:
        part = f"--- {post.platform} ---\n"
        if post.title:
            part += f"TITLE: {post.title}\n"
        part += f"CAPTION:\n{post.caption}"
        shown.append(part)
    return shown


def build_user(brief: str, posts: list[PostText], hook: str, checked: list[str]) -> str:
    """One clip's texts with the brief: what its stored result is keyed on (`key`)."""
    return build_batch(brief, [(posts, hook)], checked, numbered=False)


def build_batch(brief: str, clips: list[tuple[list[PostText], str]], checked: list[str], numbered: bool = True) -> str:
    """The brief once, then each clip's (texts, on-screen text)."""
    shown = [f"BRIEF:\n{brief[:30_000]}", "ALREADY CHECKED (skip these):\n" + "\n".join(f"- {c}" for c in checked)]
    for n, (posts, hook) in enumerate(clips, 1):
        shown += ([f"=== CLIP {n} ==="] if numbered else []) + _posts(posts, hook)
    return "\n\n".join(shown)


def key(user: str) -> str:
    """What the clip's stored result was checked against; a change means check again."""
    return hashlib.sha256(f"{AUDIT_VERSION}\n{user}".encode()).hexdigest()[:24]


def audit(clips: list[tuple[list[PostText], str]], brief: str, checked: list[str], backends: list[LLMBackend], *,
          cache=None) -> list[tuple[list[Problem] | None, bool]]:
    """For each clip (its texts, its on-screen text): the problems found and confirmed ([] for none, None
    when no model gave a usable answer), and whether the models' own filters refused to read it. A batch
    the filters refuse is read in halves, so only the clip they object to goes unread."""
    out = []
    for i in range(0, len(clips), AUDIT_BATCH):
        out += _audit(clips[i:i + AUDIT_BATCH], brief, checked, backends, cache)
    return out


def _audit(chunk, brief, checked, backends, cache) -> list[tuple[list[Problem] | None, bool]]:
    try:
        found = _ask(SYSTEM, build_batch(brief, chunk, checked), backends, cache,
                     lambda text: _parse(text, chunk), _Answer)
    except Refused:
        if len(chunk) == 1:
            return [([], True)]
        half = len(chunk) // 2
        return _audit(chunk[:half], brief, checked, backends, cache) + _audit(chunk[half:], brief, checked, backends, cache)
    if found is None:
        return [(None, False)] * len(chunk)
    return [(_confirmed(problems, posts, hook, backends, cache), False) for problems, (posts, hook) in zip(found, chunk, strict=True)]


def _confirmed(found: list[Problem], posts: list[PostText], hook: str, backends, cache) -> list[Problem]:
    confirmed = []
    for problem in found:
        verdict = _ask(CONFIRM, _confirm_user(problem, posts, hook), backends, cache, _verdict, _Verdict)
        # Unanswered: shown, since a missed rule costs more than a second look.
        if verdict is None or verdict.breaks:
            confirmed.append(problem)
        else:
            log.info("rule check: dropped %r on a second look (%s)", problem.problem, verdict.why)
    return confirmed


def _ask(system: str, user: str, backends: list[LLMBackend], cache, parse, schema):
    refused = False
    for backend in backends:
        cache_key = None
        if cache is not None:
            cache_key = cache.key(backend=backend.name, model=backend.cache_model(),
                                  prompt_key=f"{AUDIT_VERSION}:{schema.__name__}", payload=user)
            entry = cache.get(cache_key)
            if entry is not None:
                found = parse(entry.text)
                if found is not None:
                    return found
                cache.forget(cache_key)
        try:
            response = backend.complete(LLMRequest(system=system, user=user, temperature=0.0,
                                                   response_schema=schema))
        except ContentBlocked as exc:
            refused = True
            log.info("rule check: %s refused to read it (%s)", backend.describe(), str(exc)[:160])
            continue
        except Exception as exc:  # a check that couldn't run says so; it never blocks
            log.log(miss_level(exc), "rule check: %s did not answer (%s)", backend.describe(), str(exc)[:160])
            continue
        found = parse(response.text)
        if found is not None:
            if cache is not None and cache_key is not None:
                cache.put(cache_key, text=response.text, model=response.model)
            return found
        log.info("rule check from %s was unusable: %r", backend.describe(), response.text[:120])
    if refused and system is SYSTEM:
        raise Refused("the AI's content filter wouldn't read this post")
    return None


def _json(text: str):
    return json.loads(re.sub(r"^```(?:json)?|```$", "", (text or "").strip()).strip())


def _verdict(text: str) -> _Verdict | None:
    try:
        return _Verdict.model_validate(_json(text))
    except (ValueError, TypeError):
        return None


def _confirm_user(problem: Problem, posts: list[PostText], hook: str) -> str:
    mine = [p for p in posts if problem.platform in ("all", p.platform)] or posts
    texts = []
    for post in mine:
        if problem.where == "on-screen text":
            texts.append(f"ON-SCREEN TEXT: {hook or '(none)'}")
        elif problem.where == "title" and post.title:
            texts.append(f"{NAMES.get(post.platform, post.platform)} TITLE: {post.title}")
        else:
            texts.append(f"{NAMES.get(post.platform, post.platform)} CAPTION:\n{post.caption}")
    return f"RULE: {problem.rule}\n\nPOST TEXT:\n" + "\n\n".join(dict.fromkeys(texts))


def _parse(text: str, chunk: list[tuple[list[PostText], str]]) -> list[list[Problem]] | None:
    """Each clip's problems, from an answer about the clips in `chunk`."""
    try:
        answer = _Answer.model_validate(_json(text))
    except (ValueError, TypeError):
        return None
    out: list[list[Problem]] = [[] for _ in chunk]
    for found in answer.problems:
        if not 1 <= found.clip <= len(chunk) or not found.rule.strip():
            continue
        posts = chunk[found.clip - 1][0]
        problem = Problem(**found.model_dump(exclude={"clip"}))
        if problem.platform not in {p.platform for p in posts}:
            problem.platform = "all"
        if problem.add.strip() and _already_there(problem, posts):
            continue  # the model missed it; the text is there
        out[found.clip - 1].append(problem)
    return out


def _already_there(problem: Problem, posts: list[PostText]) -> bool:
    mine = [p for p in posts if problem.platform in ("all", p.platform)]
    text = problem.add.strip()
    return bool(mine) and all(_contains(p.title if problem.where == "title" and p.title else
                                        f"{p.title}\n{p.caption}", text) for p in mine)
