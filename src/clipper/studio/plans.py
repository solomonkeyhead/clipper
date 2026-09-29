"""Plans: which features an account has.

Planned as paid tiers for a hosted Clipper: Free (clipping, stats), Research
(the Research section: chat, niche radar, saved), Pro (Research, and the chat may
change things in Clipper -- create campaigns, start clip jobs, add hook lines --
after the user confirms). Nothing is billed; a local install is on Pro, and the
setting exists so the tiers can be tried and the checks sit in the right places.
"""

from __future__ import annotations

from fastapi import HTTPException

from . import db

PLANS = ["free", "research", "pro"]
PLAN_NAMES = {"free": "Free", "research": "Research", "pro": "Pro"}
#: feature -> the lowest plan that has it
FEATURES = {"research": "research", "research_actions": "pro"}


def current() -> str:
    with db.connect() as con:
        plan = db.settings(con).get("plan", "pro")
    return plan if plan in PLANS else "free"


def has(feature: str, plan: str | None = None) -> bool:
    plan = plan or current()
    return PLANS.index(plan) >= PLANS.index(FEATURES[feature])


def require(feature: str) -> None:
    if not has(feature):
        needed = PLAN_NAMES[FEATURES[feature]]
        raise HTTPException(402, f"This is part of the {needed} plan.")
