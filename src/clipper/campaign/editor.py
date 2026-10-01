"""Creating and editing campaigns from the Control Center.

A campaign is a YAML file under campaigns/ (see example.yaml). The page edits
it through `CampaignForm`, the fields a person fills in from a brief; anything
else already in the file (forbidden terms, edit permissions, ...) is kept as
it was. Every save is checked by `CampaignConfig` first, and the previous
version is copied to campaigns/.history/ -- a hand-written file's comments are
not carried into the rewritten one, so the old file stays readable there.

`read_brief` fills the form from a pasted brief with the configured LLM. It
takes only what the brief states, and leaves passwords and private links out.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, ValidationError

from ..config import PLATFORMS, CampaignConfig, CaptionRule
from ..utils.logging import get_logger

log = get_logger(__name__)

HISTORY = ".history"


class CampaignError(ValueError):
    """The form can't be saved as it is; the message says what to fix."""


class CampaignForm(BaseModel):
    """What the New campaign / Edit campaign page shows."""

    title: str
    name: str = ""                     # the id; made from the title when empty
    marketplace: str = ""
    campaign_url: str = ""
    reward_per_1k_usd: float | None = None
    min_payout_usd: float | None = None
    max_payout_usd: float | None = None
    deadline: str = ""
    source_authorization: str = ""
    platform_targets: list[str] = Field(default_factory=lambda: ["tiktok", "instagram_reels"])
    content_type: Literal["scripted", "podcast", "other"] = "scripted"
    min_seconds: float = 15
    max_seconds: float = 60
    selection_focus: str = ""
    required_caption_text: str = ""
    required_hashtags: list[str] = Field(default_factory=list)
    only_required_hashtags: bool = False
    required_credit_text: str = ""
    fallback_captions: list[str] = Field(default_factory=list)
    fixed_captions: bool = False
    hook_texts: list[str] = Field(default_factory=list)
    hook_overlay: bool = True
    keep_original_audio: bool = False
    censor_flagged_words: bool = True
    brief_rules: str = ""
    # Mentions, per-platform tags, banned words (config.CaptionRule), and the
    # rules only the poster can follow.
    caption_rules: list[CaptionRule] = Field(default_factory=list)
    posting_rules: list[str] = Field(default_factory=list)
    long_description: bool = True
    description_context: str = ""
    description_keywords: list[str] = Field(default_factory=list)
    max_clips_per_source: int | None = None  # None: no limit
    notes: str = ""


def slugify(title: str) -> str:
    """"Chad Powers S2 (Hulu)" -> "chad-powers-s2-hulu"."""
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return slug[:60].strip("-")


def pretty(name: str) -> str:
    """A readable title for a campaign file that has none: "chad-powers-s2" -> "Chad Powers S2"."""
    words = re.split(r"[-_\s]+", name.strip())
    return " ".join(w.upper() if len(w) <= 2 and any(ch.isdigit() for ch in w) else w.capitalize()
                    for w in words if w)


def title_of(campaign: CampaignConfig) -> str:
    return campaign.title.strip() or pretty(campaign.name)


def default_authorization(form: CampaignForm) -> str:
    where = f" on {form.marketplace}" if form.marketplace else ""
    link = f" ({form.campaign_url})" if form.campaign_url else ""
    return (f"Official footage supplied by the campaign \"{form.title.strip()}\"{where}{link}, "
            "which pays clippers to post it. Only that footage is used.")


def to_form(campaign: CampaignConfig) -> CampaignForm:
    content = campaign.content_type or ("scripted" if campaign.scripted else "podcast")
    return CampaignForm(
        title=title_of(campaign), name=campaign.name, marketplace=campaign.marketplace,
        campaign_url=campaign.campaign_url, reward_per_1k_usd=campaign.reward_per_1k_usd,
        min_payout_usd=campaign.min_payout_usd, max_payout_usd=campaign.max_payout_usd,
        deadline=campaign.deadline, source_authorization=campaign.source_authorization,
        platform_targets=list(campaign.platform_targets), content_type=content,
        min_seconds=campaign.duration.min_seconds, max_seconds=campaign.duration.max_seconds,
        selection_focus=campaign.selection_focus,
        required_caption_text=campaign.required_caption_text,
        required_hashtags=list(campaign.required_hashtags),
        only_required_hashtags=campaign.only_required_hashtags,
        required_credit_text=campaign.required_credit_text,
        fallback_captions=list(campaign.fallback_captions),
        fixed_captions=campaign.fixed_captions, hook_texts=list(campaign.hook_texts),
        hook_overlay=campaign.hook_overlay, keep_original_audio=campaign.keep_original_audio,
        censor_flagged_words=campaign.censor_flagged_words,
        brief_rules=campaign.brief_rules, caption_rules=list(campaign.caption_rules),
        posting_rules=list(campaign.posting_rules), long_description=campaign.long_description,
        description_context=campaign.description_context,
        description_keywords=list(campaign.description_keywords),
        max_clips_per_source=campaign.max_clips_per_source, notes=campaign.notes)


def _lines(items: list[str]) -> list[str]:
    return [i.strip() for i in items if i and i.strip()]


def _hashtags(items: list[str]) -> list[str]:
    out = []
    for item in items:
        for tag in re.split(r"[\s,]+", item.strip()):
            if tag:
                tag = tag if tag.startswith("#") else f"#{tag}"
                if tag.lower() not in {t.lower() for t in out}:
                    out.append(tag)
    return out


def _unique_rules(rules: list[CaptionRule]) -> list[CaptionRule]:
    seen, out = set(), []
    for rule in rules:
        k = (rule.text.lower(), rule.must, rule.place, tuple(sorted(rule.platforms)))
        if k not in seen:
            seen.add(k)
            out.append(rule)
    return out


def merged(form: CampaignForm, existing: dict | None) -> dict:
    """The YAML mapping: the file's own keys, with the form's fields written over them."""
    data = dict(existing or {})
    title = form.title.strip()
    if not title:
        raise CampaignError("give the campaign a name")
    platforms = [p for p in form.platform_targets if p in PLATFORMS]
    if not platforms:
        raise CampaignError("pick at least one platform to post on")
    if form.min_seconds > form.max_seconds:
        raise CampaignError("the shortest clip length is longer than the longest")
    auth = form.source_authorization.strip() or default_authorization(form)
    updates = {
        "name": form.name or slugify(title),
        "title": title,
        "marketplace": form.marketplace.strip(),
        "campaign_url": form.campaign_url.strip(),
        "reward_per_1k_usd": form.reward_per_1k_usd,
        "min_payout_usd": form.min_payout_usd,
        "max_payout_usd": form.max_payout_usd,
        "deadline": form.deadline.strip(),
        "source_authorization": auth,
        "platform_targets": platforms,
        "content_type": form.content_type,
        "scripted": form.content_type == "scripted",
        "duration": {"min_seconds": form.min_seconds, "max_seconds": form.max_seconds},
        "selection_focus": form.selection_focus.strip(),
        "required_caption_text": form.required_caption_text.strip(),
        "required_hashtags": _hashtags(form.required_hashtags),
        "only_required_hashtags": form.only_required_hashtags,
        "required_credit_text": form.required_credit_text.strip(),
        "fallback_captions": _lines(form.fallback_captions),
        "fixed_captions": form.fixed_captions and bool(_lines(form.fallback_captions)),
        "hook_texts": _lines(form.hook_texts),
        "hook_overlay": form.hook_overlay,
        "keep_original_audio": form.keep_original_audio,
        "censor_flagged_words": form.censor_flagged_words,
        "brief_rules": form.brief_rules.strip(),
        "caption_rules": [r.model_dump(mode="json") for r in _unique_rules(form.caption_rules)],
        "posting_rules": _lines(form.posting_rules),
        "long_description": form.long_description,
        "description_context": form.description_context.strip(),
        "description_keywords": _lines(form.description_keywords),
        "max_clips_per_source": (max(1, min(500, int(form.max_clips_per_source)))
                                 if form.max_clips_per_source else None),
        "notes": form.notes.strip(),
    }
    data.update(updates)
    # A credit burned into the video needs its text; drop the flag with the text.
    if not updates["required_credit_text"]:
        data["burn_credit_in_video"] = False
    return data


def validate(data: dict) -> CampaignConfig:
    try:
        return CampaignConfig.model_validate(data)
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(p) for p in first.get("loc", ()))
        message = str(first.get("msg", "")).removeprefix("Value error, ")
        raise CampaignError(f"{where}: {message}" if where else message) from None


class _Block(str):
    """A multi-line string, written as a YAML block (`|`) so it stays readable."""


def _block(dumper: yaml.SafeDumper, value: _Block):
    return dumper.represent_scalar("tag:yaml.org,2002:str", value, style="|")


class _Dumper(yaml.SafeDumper):
    pass


_Dumper.add_representer(_Block, _block)


def dump(data: dict) -> str:
    def blocks(value):
        if isinstance(value, str) and "\n" in value:
            return _Block(value.rstrip())
        if isinstance(value, dict):
            return {k: blocks(v) for k, v in value.items()}
        if isinstance(value, list):
            return [blocks(v) for v in value]
        return value

    header = ("# Written by the Control Center (New campaign / Edit campaign).\n"
              "# Earlier versions are kept in campaigns/.history/.\n")
    body = yaml.dump({k: blocks(v) for k, v in data.items() if v is not None},
                     Dumper=_Dumper, sort_keys=False, allow_unicode=True, width=88)
    return header + body


def path_for(folder: Path, name: str) -> Path | None:
    """The file that defines campaign `name` (its file name need not match)."""
    direct = folder / f"{name}.yaml"
    if direct.exists():
        return direct
    for path in sorted(folder.glob("*.yaml")):
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError):
            continue
        if raw.get("name") == name:
            return path
    return None


def _back_up(path: Path) -> None:
    history = path.parent / HISTORY
    history.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    (history / f"{path.stem}.{stamp}.yaml").write_bytes(path.read_bytes())


def create(folder: Path, form: CampaignForm) -> CampaignConfig:
    """Write a new campaign file. The id comes from the title and must be free."""
    form = form.model_copy(update={"name": slugify(form.title)})
    if not form.name:
        raise CampaignError("the name needs at least one letter or number")
    if path_for(folder, form.name) is not None:
        raise CampaignError(f"there's already a campaign called {form.title.strip()!r}; "
                            "pick another name")
    data = merged(form, None)
    campaign = validate(data)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{form.name}.yaml").write_text(dump(data), encoding="utf-8")
    log.info("created campaign %s", form.name)
    return campaign


def update(folder: Path, name: str, form: CampaignForm) -> CampaignConfig:
    """Rewrite campaign `name` from the form; its id never changes, only its title."""
    path = path_for(folder, name)
    if path is None:
        raise CampaignError(f"no campaign {name!r}")
    existing = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    data = merged(form.model_copy(update={"name": name}), existing)
    campaign = validate(data)
    _back_up(path)
    path.write_text(dump(data), encoding="utf-8")
    log.info("updated campaign %s", name)
    return campaign


def add_caption_rule(folder: Path, name: str, rule: dict) -> CampaignConfig:
    """Add one caption rule (config.CaptionRule) to campaign `name`; a rule it
    already has is not added twice."""
    path = path_for(folder, name)
    if path is None:
        raise CampaignError(f"no campaign {name!r}")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    rules = list(data.get("caption_rules") or [])
    clean = CaptionRule.model_validate(rule).model_dump(mode="json")
    same = ("text", "must", "place", "platforms")
    if not any({k: CaptionRule.model_validate(r).model_dump(mode="json")[k] for k in same}
               == {k: clean[k] for k in same} for r in rules):
        rules.append(clean)
    data["caption_rules"] = rules
    campaign = validate(data)
    _back_up(path)
    path.write_text(dump(data), encoding="utf-8")
    log.info("campaign %s: added caption rule %r", name, clean["text"])
    return campaign


# --------------------------------------------------------------------------
# Reading a pasted brief
# --------------------------------------------------------------------------

class BriefCaptionRule(BaseModel):
    text: str = ""
    must: str = "include"
    place: str = "caption"
    platforms: list[str] = Field(default_factory=list)
    quote: str = ""


class BriefFields(BaseModel):
    """What the LLM may fill in. Empty string / 0 / [] means the brief doesn't say."""

    title: str = ""
    marketplace: str = ""
    campaign_url: str = ""
    reward_per_1k_usd: float = 0
    min_payout_usd: float = 0
    max_payout_usd: float = 0
    deadline: str = ""
    platforms: list[str] = Field(default_factory=list)
    content_type: str = ""
    min_seconds: float = 0
    max_seconds: float = 0
    selection_focus: str = ""
    required_caption_text: str = ""
    required_hashtags: list[str] = Field(default_factory=list)
    only_required_hashtags: bool = False
    required_credit_text: str = ""
    approved_captions: list[str] = Field(default_factory=list)
    on_screen_text: list[str] = Field(default_factory=list)
    keep_original_audio: bool = False
    edit_rules: str = ""
    caption_rules: list[BriefCaptionRule] = Field(default_factory=list)
    posting_rules: list[str] = Field(default_factory=list)
    description_context: str = ""
    description_keywords: list[str] = Field(default_factory=list)
    other_rules: list[str] = Field(default_factory=list)


BRIEF_SYSTEM = """You read a paid clipping campaign's brief (from Whop Content Rewards, Vyro or \
similar) and fill in a form for a tool that cuts the campaign's footage into short vertical clips.

Rules:
- Take only what the brief states. Never invent a rate, a hashtag, a caption or a rule. \
Leave a field empty ("" / 0 / []) when the brief doesn't say.
- Never copy passwords, access codes, Google Drive / Dropbox / footage-folder links, \
Discord invites or anyone's contact details into any field.
- title: a short name for the campaign, e.g. "Chad Powers S2" (the show, creator or brand, \
plus season if given).
- marketplace: "Content Rewards" for Whop Content Rewards, "Vyro" for Vyro, else the platform named.
- reward_per_1k_usd: pay per 1,000 views in US dollars (e.g. "$2.50 CPM" -> 2.5). \
min_payout_usd / max_payout_usd: the minimum payout and the maximum per post, if given.
- deadline: YYYY-MM-DD if the brief gives an end date.
- platforms: any of "tiktok", "instagram_reels", "youtube_shorts", "x" (X / Twitter) that the \
brief allows.
- content_type: "scripted" for TV shows and films, "podcast" for podcasts, interviews, \
streams and talking-head creators, "other" otherwise.
- min_seconds / max_seconds: the clip length the brief asks for.
- selection_focus: what the clips should be about, in the brief's own terms, as 1-4 plain \
sentences, including what it says will be rejected.
- required_caption_text: text every caption must contain, e.g. "#ad" or a tune-in line.
- required_hashtags: hashtags the brief requires, each starting with #. \
only_required_hashtags: true if the brief forbids other hashtags.
- required_credit_text: a credit line the brief requires (e.g. "Source: @creator").
- approved_captions: captions the brief supplies, word for word.
- on_screen_text: text-on-screen hook lines the brief supplies, word for word.
- keep_original_audio: true if the brief requires the original audio.
- edit_rules: the brief's own words about editing (cuts, zooms, music, text, audio), quoted.
- caption_rules: EVERY other rule about text a post must or must not contain: @mentions or \
tags of an account, a phrase, link or hashtag required only on some platforms, words or \
topics that are banned. One entry each: "text" is exactly what must appear (or must not), \
"must" is "include" or "avoid", "place" is "title" when the brief says the YouTube title, \
else "caption", "platforms" lists the platforms it is limited to ([] for all), "quote" is \
the brief's sentence word for word. Example: "On YouTube, tag @JoshThomasChannel in the \
title" -> {"text": "@JoshThomasChannel", "must": "include", "place": "title", \
"platforms": ["youtube_shorts"], "quote": "On YouTube, tag @JoshThomasChannel in the title."}. \
Don't repeat what required_caption_text, required_hashtags or required_credit_text hold.
- posting_rules: rules only the person posting can follow (likes and comments on, \
disclosure or paid-partnership toggles, pinned comments, how long posts stay up, no \
boosting, one account per platform), one short line each.
- description_context: 1-3 factual sentences on what the show/creator is and who is in it, \
from the brief only.
- description_keywords: search terms the brief uses (show name, cast, creator), at most 10.
- other_rules: every other rule worth remembering (eligibility, audience, payout \
conditions), one short line each.
- Every rule in the brief must land in exactly one field. Drop none."""


def read_brief(text: str, backend) -> CampaignForm:
    """Fill a form from a pasted brief with `backend` (an LLMBackend)."""
    import json

    from ..llm.base import LLMRequest

    text = text.strip()
    if len(text) < 40:
        raise CampaignError("paste the whole brief (it looks too short)")
    response = backend.complete(LLMRequest(system=BRIEF_SYSTEM, user=text[:30_000],
                                           temperature=0.0, response_schema=BriefFields))
    raw = response.text.strip()
    raw = re.sub(r"^```(?:json)?|```$", "", raw).strip()
    try:
        found = BriefFields.model_validate(json.loads(raw))
    except (ValueError, ValidationError) as exc:
        raise CampaignError(f"couldn't read the brief ({exc.__class__.__name__}); "
                            "fill the form in yourself") from None
    return form_from_brief(found)


def form_from_brief(found: BriefFields) -> CampaignForm:
    content = found.content_type if found.content_type in ("scripted", "podcast", "other") \
        else "scripted"
    platforms = [p for p in found.platforms if p in PLATFORMS] or ["tiktok", "instagram_reels"]
    form = CampaignForm(
        title=found.title.strip(), marketplace=found.marketplace.strip(),
        campaign_url=found.campaign_url.strip() if _public_link(found.campaign_url) else "",
        reward_per_1k_usd=found.reward_per_1k_usd or None,
        min_payout_usd=found.min_payout_usd or None,
        max_payout_usd=found.max_payout_usd or None,
        deadline=found.deadline.strip() if re.fullmatch(r"\d{4}-\d{2}-\d{2}", found.deadline.strip())
        else "",
        platform_targets=platforms, content_type=content,
        min_seconds=found.min_seconds or 15, max_seconds=found.max_seconds or 60,
        selection_focus=found.selection_focus.strip(),
        required_caption_text=found.required_caption_text.strip(),
        required_hashtags=_hashtags(found.required_hashtags),
        only_required_hashtags=found.only_required_hashtags,
        required_credit_text=found.required_credit_text.strip(),
        fallback_captions=_lines(found.approved_captions),
        fixed_captions=bool(_lines(found.approved_captions)),
        hook_texts=_lines(found.on_screen_text),
        keep_original_audio=found.keep_original_audio,
        brief_rules=found.edit_rules.strip(),
        caption_rules=_caption_rules(found.caption_rules),
        posting_rules=_lines(found.posting_rules),
        long_description=True,
        description_context=found.description_context.strip(),
        description_keywords=_lines(found.description_keywords)[:10],
        notes="\n".join(f"- {r.strip().lstrip('-• ').strip()}" for r in found.other_rules
                        if r.strip()))
    if form.min_seconds > form.max_seconds:
        form.min_seconds, form.max_seconds = form.max_seconds, form.min_seconds
    return form


def _caption_rules(found: list[BriefCaptionRule]) -> list[CaptionRule]:
    """The reader's caption rules that make sense; a malformed one is dropped, not guessed at."""
    out = []
    for r in found:
        try:
            out.append(CaptionRule(
                text=r.text, must=r.must if r.must in ("include", "avoid") else "include",
                place=r.place if r.place in ("caption", "title") else "caption",
                platforms=tuple(p for p in r.platforms if p in PLATFORMS), quote=r.quote.strip()))
        except ValidationError:
            continue
    return _unique_rules(out)


def _public_link(url: str) -> bool:
    """A campaign page, not a footage folder or an invite."""
    url = url.strip().lower()
    private = ("drive.google", "dropbox", "mega.nz", "wetransfer", "discord.gg", "discord.com",
               "frame.io", "box.com", "onedrive", "sharepoint")
    return url.startswith("http") and not any(p in url for p in private)
