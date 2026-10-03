"""Which edits a campaign allows: the permission gate in front of every edit.

The retention research (docs/DECISIONS.md D59) sorts edits into classes. Container
edits -- cropping to 9:16, trimming the head and tail, loudness normalisation --
are allowed everywhere and never gated. Every other class is decided, in order:

1. an explicit setting in the campaign's `edits:` block (a human's reading of
   the brief) wins;
2. otherwise anything the brief's own wording (`brief_rules`) forbids is off --
   the stricter reading when wording is ambiguous;
3. otherwise the content type's default: scripted scenes keep the studio's own
   edit (no internal cuts), podcasts get pauses and fillers tightened.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..config import CampaignConfig

CLASSES = ("captions", "added_text", "internal_cuts", "re_edit", "visual_effects",
           "audio_additions", "overlays")

#: Brief wording -> the classes it forbids (research permission table).
WORDING: list[tuple[str, tuple[str, ...]]] = [
    (r"\b(do not|don'?t|must not|never)\s+(alter|modify|change)\b.*\b(footage|content|clip|video|ip|property)|"
     r"\bunaltered\b|\bunmodified\b|\b(use|post)\s+(the\s+)?(clips?|footage|videos?)\s+as[- ]is\b",
     ("added_text", "internal_cuts", "re_edit", "visual_effects", "audio_additions", "overlays")),
    (r"\bno\s+re-?edit|\bre-?edited\s+dialogue\b|\bdon'?t\s+(re-?)?edit\s+(the\s+)?dialogue",
     ("internal_cuts", "re_edit", "visual_effects", "audio_additions", "overlays")),
    (r"\bno\s+jump[- ]?cuts?\b", ("internal_cuts",)),
    (r"\b(don'?t|do not)\s+change\s+(the\s+)?meaning|\bout[- ]of[- ]context\b",
     ("re_edit", "overlays")),
    (r"\bno\s+(added\s+|on[- ]screen\s+)?text\b|\bno\s+text\s+overlays?\b", ("added_text", "captions")),
    (r"\bno\s+captions?\b|\bno\s+subtitles?\b", ("captions",)),
    (r"\bkeep\s+(the\s+)?original\s+audio\b|\bno\s+(added\s+)?(music|sound\s*effects|sfx)\b",
     ("audio_additions",)),
    (r"\bno\s+(reaction\s+)?overlays?\b|\bno\s+logos?\b|\bno\s+stickers?\b|\bno\s+b-?roll\b",
     ("overlays",)),
    # Footage only from the campaign: nothing from outside on top of it (D103).
    (r"\bonly\s+(use\s+)?(the\s+)?(provided|official|supplied|approved)\s+(footage|clips|content|assets|videos?)\b|"
     r"\bno\s+(outside|external|third[- ]party)\s+(footage|clips|content|videos?)\b",
     ("overlays", "audio_additions")),
    (r"\bno\s+(zooms?|(visual\s+)?effects|filters?)\b|\bno\s+colou?r\s+(correction|grading|changes?)\b",
     ("visual_effects",)),
]

#: Defaults by content type, for classes neither set nor forbidden.
DEFAULTS = {
    "scripted": {"captions": True, "added_text": True, "internal_cuts": False, "re_edit": False,
                 "visual_effects": True, "audio_additions": False, "overlays": False},
    "podcast": {"captions": True, "added_text": True, "internal_cuts": True, "re_edit": False,
                "visual_effects": True, "audio_additions": False, "overlays": False},
    "other": {"captions": True, "added_text": True, "internal_cuts": True, "re_edit": False,
              "visual_effects": True, "audio_additions": False, "overlays": False},
}


@dataclass(frozen=True)
class Permissions:
    content_type: str
    allowed: dict[str, bool]
    reasons: dict[str, str] = field(default_factory=dict)

    def __getattr__(self, name: str) -> bool:
        try:
            return self.allowed[name]
        except KeyError:
            raise AttributeError(name) from None

    def summary(self) -> str:
        on = [c for c in CLASSES if self.allowed[c]]
        return f"{self.content_type}: " + (", ".join(on) if on else "container edits only")


def content_type(campaign: CampaignConfig) -> str:
    if campaign.content_type:
        return campaign.content_type
    return "scripted" if campaign.scripted else "podcast"


def forbidden_by(text: str) -> dict[str, str]:
    """Class -> the brief wording that forbids it."""
    out: dict[str, str] = {}
    for pattern, classes in WORDING:
        match = re.search(pattern, text or "", re.IGNORECASE)
        if match:
            for cls in classes:
                out.setdefault(cls, match.group(0))
    return out


def teaser_allowed(campaign: CampaignConfig) -> bool:
    """Whether a clip may open with a preview of its payoff line (D97).

    The scene still plays whole and in order, so the scripted default against
    re-editing doesn't stop it; a brief that forbids re-edits or changes in its
    own words ("no re-edits", "unaltered", "out of context"), or a campaign set
    to `re_edit: false`, does."""
    if campaign.edits.re_edit is False:
        return False
    return "re_edit" not in forbidden_by(campaign.brief_rules)


def permissions(campaign: CampaignConfig) -> Permissions:
    kind = content_type(campaign)
    defaults = dict(DEFAULTS[kind])
    # The existing switch for the hook line is the campaign's word on added text.
    defaults["added_text"] = defaults["added_text"] and campaign.hook_overlay
    denied = forbidden_by(campaign.brief_rules)
    allowed, reasons = {}, {}
    for cls in CLASSES:
        explicit = getattr(campaign.edits, cls)
        if explicit is not None:
            allowed[cls], reasons[cls] = explicit, "set in the campaign"
        elif cls in denied:
            allowed[cls], reasons[cls] = False, f'the brief says "{denied[cls]}"'
        else:
            allowed[cls], reasons[cls] = defaults[cls], f"default for {kind}"
    return Permissions(content_type=kind, allowed=allowed, reasons=reasons)
