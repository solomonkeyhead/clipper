"""Campaign rule enforcement (BUILD_BRIEF.md section 7.2).

Separate from the QA gate because the two answer different questions. QA asks
"is this clip technically sound"; compliance asks "does this clip satisfy the
contract of the campaign being submitted to". A clip can be flawless and still
be non-compliant, and the user needs to see which it is.

Every rule records pass or fail individually so the manifest can show exactly
which one bit.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..config import CampaignConfig
from ..models import ClipPlan
from ..utils.logging import get_logger

log = get_logger(__name__)


@dataclass
class RuleResult:
    name: str
    passed: bool
    detail: str = ""

    @property
    def status(self) -> str:
        return "pass" if self.passed else "fail"


@dataclass
class ComplianceReport:
    clip_id: str
    rules: list[RuleResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(rule.passed for rule in self.rules)

    @property
    def status(self) -> str:
        return "pass" if self.passed else "fail"

    @property
    def failures(self) -> list[RuleResult]:
        return [rule for rule in self.rules if not rule.passed]

    def summary(self) -> str:
        if self.passed:
            return f"pass ({len(self.rules)} rules)"
        return "fail: " + "; ".join(r.detail for r in self.failures)


def check_clip(plan: ClipPlan, campaign: CampaignConfig, *,
               duration: float | None = None) -> ComplianceReport:
    """Validate one finished clip against its campaign's rules."""
    report = ComplianceReport(clip_id=plan.clip_id)
    actual_duration = duration if duration is not None else plan.duration

    report.rules.append(_check_duration(actual_duration, campaign))
    report.rules.append(_check_forbidden_terms(plan.text, campaign))
    report.rules.append(_check_brand_mentions(plan.text, campaign))
    report.rules.append(_check_hashtags(plan, campaign))
    report.rules.append(_check_credit(plan, campaign))
    report.rules += _check_posts(plan, campaign)
    return report


def _check_posts(plan: ClipPlan, campaign: CampaignConfig) -> list[RuleResult]:
    """Each platform's text against every caption rule (campaign/rules.py)."""
    from .rules import NAMES, post_texts

    out = []
    for post in post_texts(plan.hook_text or plan.text[:60], full_caption(plan), plan.hook_text, campaign):
        failed = [c.name + (f" ({c.detail})" if c.detail and "“" not in c.name else "")
                  for c in post.checks if not c.passed]
        out.append(RuleResult(f"post_text:{post.platform}", not failed,
                              "; ".join(failed) or f"{NAMES.get(post.platform, post.platform)}: "
                              f"all {len(post.checks)} checks pass"))
    return out


def _check_duration(duration: float, campaign: CampaignConfig) -> RuleResult:
    low, high = campaign.duration.min_seconds, campaign.duration.max_seconds
    if not (low <= duration <= high):
        return RuleResult(
            "duration", False,
            f"{duration:.1f}s is outside the campaign's {low:.0f}-{high:.0f}s window",
        )
    return RuleResult("duration", True, f"{duration:.1f}s")


def _check_forbidden_terms(text: str, campaign: CampaignConfig) -> RuleResult:
    """Any forbidden term in the clip transcript drops the clip (section 7.2)."""
    if not campaign.forbidden_terms:
        return RuleResult("forbidden_terms", True, "none configured")

    hits = [term for term in campaign.forbidden_terms if _contains(text, term)]
    if hits:
        return RuleResult(
            "forbidden_terms", False,
            f"transcript contains forbidden term(s): {', '.join(sorted(hits))}",
        )
    return RuleResult("forbidden_terms", True,
                      f"none of {len(campaign.forbidden_terms)} terms present")


def _check_brand_mentions(text: str, campaign: CampaignConfig) -> RuleResult:
    brand = campaign.brand_mentions
    if not brand.required:
        return RuleResult("brand_mentions", True, "not required")

    hits = [term for term in brand.terms if _contains(text, term)]
    if not hits:
        return RuleResult(
            "brand_mentions", False,
            f"the campaign requires a brand mention but none of "
            f"{', '.join(brand.terms)} appear in the transcript",
        )
    return RuleResult("brand_mentions", True, f"mentions {', '.join(hits)}")


def _check_hashtags(plan: ClipPlan, campaign: CampaignConfig) -> RuleResult:
    """Required hashtags must be present in the suggested caption.

    clipper does not post, so this checks the caption it hands the user rather
    than anything published. `apply_campaign_caption` guarantees it by
    construction; this verifies that guarantee held.
    """
    if not campaign.required_hashtags:
        return RuleResult("required_hashtags", True, "none configured")

    have = {h.lower() for h in plan.hashtags}
    caption = plan.suggested_caption.lower()
    missing = [
        tag for tag in campaign.required_hashtags
        if tag.lower() not in have and tag.lower() not in caption
    ]
    if missing:
        return RuleResult("required_hashtags", False,
                          f"suggested caption is missing {', '.join(missing)}")
    return RuleResult("required_hashtags", True,
                      f"all {len(campaign.required_hashtags)} present")


def _check_credit(plan: ClipPlan, campaign: CampaignConfig) -> RuleResult:
    if not campaign.required_credit_text.strip():
        return RuleResult("credit", True, "none configured")

    credit = campaign.required_credit_text.strip()
    if campaign.burn_credit_in_video:
        # Burned credits are rendered from the campaign config directly, so the
        # only failure mode is a misconfiguration the config validator already
        # rejects. Recorded so the manifest shows it was required.
        return RuleResult("credit", True, f"burned on screen: {credit!r}")

    if credit.lower() not in plan.suggested_caption.lower():
        return RuleResult("credit", False,
                          f"suggested caption does not contain the required credit {credit!r}")
    return RuleResult("credit", True, f"in caption: {credit!r}")


def _contains(text: str, term: str) -> bool:
    """Whole-word, case-insensitive match.

    Substring matching would make a forbidden term like "ai" fire on "said",
    "again" and "chair", silently deleting good clips.
    """
    term = term.strip()
    if not term:
        return False
    pattern = r"(?<!\w)" + re.escape(term) + r"(?!\w)"
    return re.search(pattern, text, re.IGNORECASE) is not None


def apply_campaign_caption(plan: ClipPlan, campaign: CampaignConfig, *, pick=None) -> ClipPlan:
    """Fold the campaign's required hashtags and credit into the suggested caption.

    Returns an updated copy. Existing hashtags are preserved and deduplicated
    case-insensitively, with the campaign's required ones first. `pick` chooses
    among the brief's own captions (campaign/rotation.py); without it they go
    by the clip's rank.
    """
    tags: list[str] = []
    seen: set[str] = set()
    banned = [*campaign.forbidden_terms, *(r.text for r in campaign.caption_rules if r.must == "avoid")]
    suggested = [] if campaign.only_required_hashtags else list(plan.hashtags)
    for tag in list(campaign.required_hashtags) + suggested:
        cleaned = tag.strip()
        if not cleaned:
            continue
        if not cleaned.startswith("#"):
            cleaned = "#" + cleaned
        if cleaned.lower() in seen:
            continue
        if tag in suggested and any(_contains(cleaned, term) for term in banned):
            continue
        seen.add(cleaned.lower())
        tags.append(cleaned)

    caption = plan.suggested_caption.strip()
    if campaign.only_required_hashtags:
        # The LLM also writes hashtags into the caption text itself -- seen on
        # a real clip: "...unexpected encounter. #comedy #funny #skits".
        caption = " ".join(w for w in caption.split() if not w.startswith("#"))
    if campaign.fixed_captions and campaign.fallback_captions:
        caption = ""  # the brief's own captions only
    if any(_contains(caption, term) for term in banned):
        log.info("%s: the written caption says something the brief bans; using the brief's own",
                 plan.clip_id)
        caption = ""
    if not caption and campaign.fallback_captions:
        caption = (pick(campaign, "caption", campaign.fallback_captions) if pick else
                   campaign.fallback_captions[(plan.rank - 1) % len(campaign.fallback_captions)]).strip()
    from .rules import append, required_texts

    # The tune-in line, the credit and every caption rule for all platforms;
    # per-platform ones are added when posting (campaign/rules.py).
    for text in required_texts(campaign, None):
        if not _contains(caption, text):
            caption = append(caption, text)

    if campaign.censor_flagged_words:  # campaign/safety.py (D82)
        from .safety import clean

        caption = clean(caption, campaign)
        tags = [t for t in tags if clean(t, campaign)]
        return plan.model_copy(update={"suggested_caption": caption, "hashtags": tags,
                                       "hook_text": clean(plan.hook_text, campaign)})
    return plan.model_copy(update={"suggested_caption": caption, "hashtags": tags})


def full_caption(plan: ClipPlan) -> str:
    """The caption as the user would paste it, hashtags included.

    With a description it is three paragraphs -- caption line, description,
    hashtags -- so the first line (and any #ad in it) shows before "more".
    """
    caption = plan.suggested_caption.strip()
    in_text = {w.lower().rstrip(".,!?") for w in caption.split() if w.startswith("#")}
    # A brief's own caption may already carry a required tag (#chadpowers).
    tags = [t for t in plan.hashtags if t.lower() not in in_text]
    description = plan.description.strip()
    if description:
        return "\n\n".join(p for p in (caption, description, " ".join(tags)) if p).strip()
    parts = [caption]
    if tags:
        parts.append(" ".join(tags))
    return "  ".join(p for p in parts if p).strip()
