"""A post's text for each platform, with every brief rule enforced and checked (D81).

A clip has one stored caption, but a brief can ask different things of each
platform ("On YouTube, tag @JoshThomasChannel in the title"), and YouTube has a
title field the others don't. So the text to paste is worked out per platform,
from the stored caption and the campaign's rules as they are now: a rule added
or changed later applies to every clip not yet posted, without re-rendering.

`enforce` adds whatever a rule requires and is missing, and drops hashtags a
brief forbids. `check` then verifies every rule one by one, with the brief's
words where it has them, including the platforms' own limits. Nothing here
removes words a rule bans: that changes what the caption says, so it fails the
check and the user decides.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..config import CampaignConfig, CaptionRule
from . import safety
from .compliance import RuleResult, _contains

NAMES = {"tiktok": "TikTok", "instagram_reels": "Instagram", "youtube_shorts": "YouTube", "x": "X"}
#: Platforms' own limits on a post's text.
#: X counts 280 for an account without Premium.
CAPTION_MAX = {"tiktok": 4000, "instagram_reels": 2200, "youtube_shorts": 5000, "x": 280}
TITLE_MAX = 100
INSTAGRAM_MAX_HASHTAGS = 30
#: YouTube refuses a title or description containing these.
YOUTUBE_FORBIDDEN = ("<", ">")


@dataclass
class PostText:
    platform: str          # a campaign platform id: tiktok, instagram_reels, youtube_shorts
    caption: str           # TikTok/Instagram caption, YouTube description
    title: str = ""        # YouTube's title
    checks: list[RuleResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(c.passed for c in self.checks)


def has_title(platform: str) -> bool:
    return platform == "youtube_shorts"


def applies(rule: CaptionRule, platform: str | None) -> bool:
    """`platform` None: the stored caption, which carries only the every-platform rules."""
    return not rule.platforms if platform is None else (not rule.platforms or platform in rule.platforms)


def in_title(rule: CaptionRule, platform: str | None) -> bool:
    return rule.place == "title" and platform is not None and has_title(platform)


# -- the caption's parts ------------------------------------------------------

def _is_tags(text: str) -> bool:
    words = text.split()
    return bool(words) and all(w.startswith("#") for w in words)


def parts(caption: str) -> tuple[str, str, str]:
    """(caption line, description, hashtags), the layout compliance.full_caption writes."""
    caption = (caption or "").strip()
    if "\n\n" in caption:
        paras = [p.strip() for p in caption.split("\n\n") if p.strip()]
        if len(paras) > 1 and _is_tags(paras[-1]):
            return paras[0], "\n\n".join(paras[1:-1]), paras[-1]
        return paras[0], "\n\n".join(paras[1:]), ""
    line, sep, tags = caption.partition("  ")
    if sep and _is_tags(tags):
        return line.strip(), "", tags.strip()
    return caption, "", ""


def join(line: str, description: str, tags: str) -> str:
    if description:
        return "\n\n".join(p for p in (line, description, tags) if p)
    return "  ".join(p for p in (line, tags) if p)


def append(line: str, text: str) -> str:
    """Text added to the end of a caption line, reading as its own sentence."""
    line = line.strip()
    if not line:
        return text
    last = line.split()[-1]
    if not line.endswith((".", "!", "?", "…", ":")) and not last.startswith(("#", "@")):
        line += "."  # "...@chadpowershulu." reads wrong, "That escalated quickly Watch..." too
    return f"{line} {text}"


def _tag(word: str) -> str:
    return word.lower().rstrip(".,!?:;)")


def _tags_in(text: str) -> set[str]:
    return {t.lower() for t in re.findall(r"#\w+", text or "")}


def allowed_tags(campaign: CampaignConfig) -> set[str]:
    """Hashtags a brief that bans others still allows: its own, in any text it supplies."""
    texts = [campaign.required_caption_text, campaign.required_credit_text,
             *campaign.fallback_captions, *(r.text for r in campaign.caption_rules if r.must == "include")]
    return {t.lower() for t in campaign.required_hashtags} | {t for x in texts for t in _tags_in(x)}


def required_texts(campaign: CampaignConfig, platform: str | None) -> list[str]:
    """Text the caption itself must contain on `platform`."""
    out = [campaign.required_caption_text.strip()]
    if not campaign.burn_credit_in_video:
        out.append(campaign.required_credit_text.strip())
    out += [r.text for r in campaign.caption_rules
            if r.must == "include" and applies(r, platform) and not in_title(r, platform)]
    return [t for t in out if t]


# -- enforcing ------------------------------------------------------------------

def enforce(caption: str, campaign: CampaignConfig, platform: str | None = None) -> str:
    """The caption with everything the rules require on `platform` (None: on every platform)."""
    if campaign.censor_flagged_words:
        caption = safety.clean(caption, campaign)
    line, description, tags = parts(caption)
    if campaign.only_required_hashtags:
        allowed = allowed_tags(campaign)
        line = " ".join(w for w in line.split() if not w.startswith("#") or _tag(w) in allowed)
        tags = " ".join(t for t in tags.split() if _tag(t) in allowed)
    for text in required_texts(campaign, platform):
        if not _contains(join(line, description, tags), text):
            line = append(line, text)
    # The required hashtags first, in the brief's order, unless the line already has one.
    words = tags.split()
    have = {w.lower(): w for w in words}
    required = [have.get(t.lower(), t) for t in campaign.required_hashtags
                if t.lower() in have or not _contains(join(line, description, ""), t)]
    tags = " ".join(required + [w for w in words if w.lower() not in
                                {t.lower() for t in campaign.required_hashtags}])
    text = join(line, description, tags)
    if platform == "x":
        text = _fit_x(line, tags, campaign)
    if platform == "youtube_shorts":
        text = _no_angles(text)
    return text


def _fit_x(line: str, tags: str, campaign: CampaignConfig) -> str:
    """An X post: the caption line and its hashtags, no description, and the
    optional hashtags dropped from the end until it fits 280 characters."""
    required = {t.lower() for t in campaign.required_hashtags}
    words = tags.split()
    while len(join(line, "", " ".join(words))) > CAPTION_MAX["x"]:
        optional = [i for i, w in enumerate(words) if w.lower() not in required]
        if not optional:
            break
        words.pop(optional[-1])
    return join(line, "", " ".join(words))


def _no_angles(text: str) -> str:
    return text.replace("<", "‹").replace(">", "›")  # noqa: RUF001 -- the look-alikes YouTube accepts


def youtube_title(name: str, caption: str, campaign: CampaignConfig) -> str:
    """The Short's title: the clip's name (its hook), then anything a rule wants in
    the title, cut at a word so the whole fits YouTube's 100 characters."""
    base = re.sub(r"\s+", " ", name or parts(caption)[0]).strip()
    if campaign.censor_flagged_words:
        base = safety.clean(base, campaign)
    needed = [r.text for r in campaign.caption_rules
              if r.must == "include" and applies(r, "youtube_shorts") and in_title(r, "youtube_shorts")]
    suffix = " ".join(t for t in needed if not _contains(base, t))
    room = TITLE_MAX - (len(suffix) + 1 if suffix else 0)
    if len(base) > room:
        base = base[:max(room - 1, 0)].rsplit(" ", 1)[0].rstrip(" ,.;:-") + "…"
    return _no_angles(f"{base} {suffix}".strip())


def post_texts(name: str, caption: str, hook: str, campaign: CampaignConfig) -> list[PostText]:
    """What to paste on each platform the campaign posts on, checked."""
    out = []
    for platform in campaign.platform_targets:
        post = PostText(platform=platform, caption=enforce(caption, campaign, platform),
                        title=youtube_title(name, caption, campaign) if has_title(platform) else "")
        post.checks = check(post, campaign, hook=hook)
        out.append(post)
    return out


# -- checking ---------------------------------------------------------------------

def check(post: PostText, campaign: CampaignConfig, *, hook: str = "") -> list[RuleResult]:
    """Every rule the brief sets on this post's text, one result each."""
    caption, title, platform = post.caption, post.title, post.platform
    everything = f"{title}\n{caption}"
    results: list[RuleResult] = []
    for tag in campaign.required_hashtags:
        results.append(RuleResult(f"Has {tag}", _contains(caption, tag)))
    required = campaign.required_caption_text.strip()
    if required:
        results.append(RuleResult(f"Includes “{required}”", _contains(caption, required)))
    credit = campaign.required_credit_text.strip()
    if credit and not campaign.burn_credit_in_video:
        results.append(RuleResult(f"Credits “{credit}”", _contains(caption, credit)))
    for rule in campaign.caption_rules:
        if not applies(rule, platform):
            continue
        where = "Title" if in_title(rule, platform) else "Caption"
        text = title if where == "Title" else caption
        if rule.must == "include":
            results.append(RuleResult(f"{where} includes “{rule.text}”", _contains(text, rule.text), rule.quote))
        else:
            # A banned word is banned anywhere in the post.
            results.append(RuleResult(f"Doesn't say “{rule.text}”", not _contains(everything, rule.text),
                                      rule.quote))
    banned = [t for t in campaign.forbidden_terms if _contains(f"{everything}\n{hook}", t)]
    if campaign.forbidden_terms:
        results.append(RuleResult("None of the brief's banned words", not banned, ", ".join(banned)))
    if campaign.only_required_hashtags:
        extra = sorted(_tags_in(everything) - allowed_tags(campaign))
        results.append(RuleResult("No hashtags beyond the brief's", not extra, ", ".join(extra)))
    if campaign.censor_flagged_words:
        risky = sorted({w for w in re.findall(r"[A-Za-z]+", everything)
                        if safety.flagged(w) and w.lower() not in safety.exempt(campaign)})
        results.append(RuleResult("No words that get posts flagged", not risky, ", ".join(risky)))
    results.append(_limits(post))
    return results


def _limits(post: PostText) -> RuleResult:
    name = NAMES.get(post.platform, post.platform)
    problems = []
    limit = CAPTION_MAX.get(post.platform)
    if limit and len(post.caption) > limit:
        problems.append(f"the caption is {len(post.caption)} characters (most {limit})")
    if post.platform == "instagram_reels" and len(_tags_in(post.caption)) > INSTAGRAM_MAX_HASHTAGS:
        problems.append(f"more than {INSTAGRAM_MAX_HASHTAGS} hashtags")
    if has_title(post.platform):
        if not post.title.strip():
            problems.append("no title")
        elif len(post.title) > TITLE_MAX:
            problems.append(f"the title is {len(post.title)} characters (most {TITLE_MAX})")
        if any(c in post.title + post.caption for c in YOUTUBE_FORBIDDEN):
            problems.append("YouTube doesn't allow < or >")
    return RuleResult(f"Fits {name}'s limits", not problems, "; ".join(problems))


def summary(posts: list[PostText]) -> list[str]:
    """Every failed check, named with its platform when the platforms differ."""
    failed: dict[str, list[str]] = {}
    for post in posts:
        for c in post.checks:
            if not c.passed:
                failed.setdefault(c.name + (f": {c.detail}" if c.detail and "“" not in c.name else ""),
                                  []).append(NAMES.get(post.platform, post.platform))
    many = len(posts) > 1
    return [f"{name} ({', '.join(where)})" if many and len(where) < len(posts) else name
            for name, where in failed.items()]
