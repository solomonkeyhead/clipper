"""One click to make a flagged clip follow its brief (D91).

Clipper already adds what rules require and masks flagged words by itself
(campaign/rules.py, campaign/safety.py). What's left on a flagged clip needs the
caption to *say* something different -- a banned word in its description, a
phrase the brief forbids, a caption too long for X -- so the AI rewrites the
caption line and description to follow the broken rules, keeping their meaning
and tone; hashtags stay as they are. The result is checked again like any caption.
"""

from __future__ import annotations

import json
import re

from pydantic import BaseModel

from ..config import CampaignConfig
from ..llm.base import LLMBackend, LLMRequest
from ..utils.logging import get_logger
from .rules import join, parts

log = get_logger(__name__)

SYSTEM = """\
You fix the caption of a short video so it follows the campaign rules it breaks. \
Change as little as you can: keep its meaning, tone, length and any @mentions, and \
don't add hashtags. If a rule bans a word or topic, rephrase around it. If it's too \
long, shorten it. Keep any text the rules require exactly as written.

Return JSON: {"line": "the caption's first line", "description": "the paragraph under it, \
or \\"\\" if there was none"}"""


class _Fixed(BaseModel):
    line: str
    description: str = ""


def rewrite(caption: str, problems: list[str], campaign: CampaignConfig,
            backends: list[LLMBackend]) -> str | None:
    """The caption rewritten to follow `problems` (one rule each), hashtags kept; None if no model could."""
    line, description, tags = parts(caption)
    user = ("RULES IT BREAKS:\n" + "\n".join(f"- {p}" for p in problems)
            + (f"\n\nREQUIRED TEXT (keep exactly): {campaign.required_caption_text}" if campaign.required_caption_text else "")
            + f"\n\nCAPTION LINE:\n{line}\n\nDESCRIPTION:\n{description or '(none)'}")
    for backend in backends:
        try:
            response = backend.complete(LLMRequest(system=SYSTEM, user=user, temperature=0.2,
                                                   response_schema=_Fixed))
            fixed = _Fixed.model_validate(json.loads(re.sub(r"^```(?:json)?|```$", "", response.text.strip())))
        except Exception as exc:  # the next model, or the user edits it
            log.warning("caption fix: %s gave nothing usable (%s)", backend.describe(), str(exc)[:160])
            continue
        if fixed.line.strip():
            return join(fixed.line.strip(), fixed.description.strip() if description else "", tags)
    return None
