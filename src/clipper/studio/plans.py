"""Plans: which features an account has.

Planned as paid tiers for a hosted Clipper (D62, D63): Free (clipping, stats),
Research (adds the Ask chat), Pro (everything, and later auto-posting). Nothing
is billed; a local install is on Pro, and the setting exists so the tiers can
be tried and the checks sit in the right places.
"""

from __future__ import annotations

from fastapi import HTTPException

from . import db

PLANS = ["free", "research", "pro"]
PLAN_NAMES = {"free": "Free", "research": "Research", "pro": "Pro"}
#: feature -> the lowest plan that has it
FEATURES = {"research": "research", "unlimited_clips": "pro"}
#: Clips a video can give below Pro (D71): enough to try, not to mine a 4 GB bank.
CLIP_CAP = 10
#: Videos one "Make clips" may queue (D72). A hosted Clipper meters minutes a
#: month; this only stops one click queuing hours of footage on a free account.
BATCH_LIMIT = {"free": 3, "research": 10, "pro": None}


def current() -> str:
    with db.connect() as con:
        plan = db.settings(con).get("plan", "pro")
    return plan if plan in PLANS else "free"


def has(feature: str, plan: str | None = None) -> bool:
    plan = plan or current()
    return PLANS.index(plan) >= PLANS.index(FEATURES[feature])


def clip_count(top: int | None, campaign_cap: int | None, plan: str | None = None) -> int | None:
    """The count a job may ask for on this plan: Pro unchanged, others at most CLIP_CAP."""
    if has("unlimited_clips", plan):
        return top
    return min(top or campaign_cap or CLIP_CAP, CLIP_CAP)


def batch_limit(plan: str | None = None) -> int | None:
    """Videos one request may queue on this plan; None: no limit."""
    return BATCH_LIMIT[plan or current()]


def require(feature: str) -> None:
    if not has(feature):
        needed = PLAN_NAMES[FEATURES[feature]]
        raise HTTPException(402, f"This is part of the {needed} plan.")
