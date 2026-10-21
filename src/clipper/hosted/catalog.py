"""What each plan and pack gives (D228), and the plan.json the gateway writes for each user's Clipper.

The prices are the launch pricing. `ai_budget_usd` is a safety net for the AI spend inside Clipper
(llm.usage.before_call): about 1.5x what a plan costs us at full use, plus 1.5x the cost of each pack bought.
Every manual control is on every plan (D120): plans differ only in volume, watermark, retention, accounts,
seats and queue speed.
"""

from __future__ import annotations

import calendar
import json
import os
import time
from dataclasses import dataclass
from datetime import UTC, datetime

from . import data_dir, users


@dataclass(frozen=True)
class Plan:
    name: str
    price: float                 # $ a month
    price_yearly: float | None   # $ a month, billed yearly
    minutes: int
    shorts: int
    shorts_once: bool
    retention_days: int
    accounts_per_platform: int
    seats: int
    watermark_after: int | None  # None: never
    queue: str
    ai_budget_usd: float


@dataclass(frozen=True)
class Pack:
    name: str
    title: str
    price: float
    minutes: int
    shorts: int
    ai_budget_usd: float


PLANS = {p.name: p for p in (
    Plan("free", 0, None, 60, 1, True, 7, 1, 1, 3, "normal", 2.0),
    Plan("starter", 15, 12, 300, 3, False, 30, 1, 1, None, "normal", 10.0),
    # "Several accounts per platform": 5 on Pro, 20 on Agency (5 seats), a number to revisit with use.
    Plan("pro", 29, 23, 600, 8, False, 90, 5, 1, None, "fast", 20.0),
    Plan("agency", 79, 63, 1800, 20, False, 180, 20, 5, None, "fastest", 55.0),
)}
PACKS = {p.name: p for p in (
    # One campaign: no subscription needed, no watermark on those clips, kept 30 days.
    Pack("campaign", "One campaign", 5, 120, 0, 2.5),
    Pack("minutes", "300 minutes", 10, 300, 0, 6.0),
    Pack("shorts", "5 Shorts", 7.5, 0, 5, 4.5),
)}
REDRAWS_PER_SHORT = 5
#: AI budget for credits given by hand (comps), at the packs' rates.
USD_PER_MINUTE, USD_PER_SHORT = PACKS["minutes"].ai_budget_usd / 300, PACKS["shorts"].ai_budget_usd / 5


def iso(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse(stamp: str) -> datetime:
    return datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)


def day(stamp: str | None) -> str:
    """"20 October 2026", for the pages."""
    if not stamp:
        return ""
    dt = parse(stamp)
    return f"{dt.day} {dt:%B %Y}"


def add_month(dt: datetime, n: int = 1) -> datetime:
    months = dt.month - 1 + n
    year, month = dt.year + months // 12, months % 12 + 1
    return dt.replace(year=year, month=month, day=min(dt.day, calendar.monthrange(year, month)[1]))


def allowance_period(user: dict, now: float | None = None) -> tuple[str, str]:
    """The month the allowance counts in. A yearly plan's billing period is a year, but its minutes and Shorts
    are a month's (pricing: "$/mo yearly"), so this is the month of that year we are in (D234)."""
    if not user["period_start"] or not user["period_end"]:
        return user["period_start"], user["period_end"]
    start, end = parse(user["period_start"]), parse(user["period_end"])
    now_dt = datetime.fromtimestamp(now or time.time(), UTC)
    k = 0
    while add_month(start, k + 1) <= now_dt and add_month(start, k + 1) < end:
        k += 1
    return iso(add_month(start, k)), iso(min(add_month(start, k + 1), end))


def first_period(now: float | None = None) -> tuple[str, str]:
    """Signup day (midnight UTC) to the same day next month."""
    day = datetime.fromtimestamp(now or time.time(), UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    return iso(day), iso(add_month(day))


def roll(user: dict, now: float | None = None) -> dict:
    """A user Paddle doesn't bill (Free, or a plan given by hand) gets a new month when the last one ends;
    paid periods come from Paddle (E2). A canceled plan ends at its period's end and an unpaid one after
    its grace (billing.GRACE_DAYS), even when Paddle's own event is late or lost (D234)."""
    now_dt = datetime.fromtimestamp(now or time.time(), UTC)
    ended = user["status"] == "canceled" and user["period_end"] and parse(user["period_end"]) <= now_dt
    unpaid = user["status"] == "past_due" and user.get("grace_until") and parse(user["grace_until"]) <= now_dt
    if ended or unpaid:
        start, end = first_period(now)
        return users.update(user["id"], plan="free", status="active", paddle_subscription_id=None,
                            period_start=start, period_end=end, grace_until=None)
    if user["paddle_subscription_id"] or not user["period_end"]:
        return user
    start, end = parse(user["period_start"]), parse(user["period_end"])
    if end > now_dt:
        return user
    while end <= now_dt:
        start, end = end, add_month(end)
    return users.update(user["id"], period_start=iso(start), period_end=iso(end))


def credit_totals(user_id: int) -> tuple[dict, float, bool]:
    """Credits as given (minutes, shorts), the AI budget they add, and whether a campaign pack is among them.

    ponytail: totals of everything ever bought, not what is left: Clipper reports use (E3), and then the
    remaining credits replace these totals.
    """
    total, budget, campaign = {"minutes": 0, "shorts": 0}, 0.0, False
    for row in users.credits(user_id):
        total["minutes"] += row["minutes"]
        total["shorts"] += row["shorts"]
        pack = PACKS.get(row["source"].removeprefix("pack:"))
        budget += pack.ai_budget_usd if pack else row["minutes"] * USD_PER_MINUTE + row["shorts"] * USD_PER_SHORT
        campaign = campaign or row["source"] == "pack:campaign"
    return total, budget, campaign


def plan_json(user: dict) -> dict:
    """The contract's plan.json for this user (shared contracts, "plan.json")."""
    plan = PLANS.get(user["plan"], PLANS["free"])
    credit, extra_budget, campaign = credit_totals(user["id"])
    watermark, retention = plan.watermark_after, plan.retention_days
    if plan.name == "free" and campaign:
        # The campaign pack's clips have no watermark and are kept 30 days. ponytail: lifts the whole
        # account while the pack is held; per-clip once Clipper reports which minutes a clip used (E3).
        watermark, retention = None, max(retention, 30)
    start, end = allowance_period(user)
    return {
        "plan": plan.name,
        "status": user["status"] if user["status"] in ("active", "past_due", "canceled") else "active",
        "period_start": start,
        "period_end": end,
        "limits": {"minutes": plan.minutes, "shorts": plan.shorts, "shorts_once": plan.shorts_once,
                   "redraws_per_short": REDRAWS_PER_SHORT, "retention_days": retention,
                   "accounts_per_platform": plan.accounts_per_platform, "seats": plan.seats,
                   "watermark_after": watermark, "queue": plan.queue},
        "credits": credit,
        "ai_budget_usd": round(plan.ai_budget_usd + extra_budget, 2),
        "billing_url": "/billing",
    }


def write_plan(user: dict) -> None:
    """`<data>/plan.json`, replaced in one step so Clipper never reads half a file."""
    path = data_dir(user["id"]) / "plan.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"plan.json.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(plan_json(user), indent=1), encoding="utf-8")
    tmp.replace(path)


def refresh(user: dict, now: float | None = None) -> dict:
    """Roll the period if it ended and rewrite plan.json: after signup, a plan or credit change, and before
    the user's Clipper starts. E2 calls this after every billing change."""
    user = roll(user, now)
    write_plan(user)
    return user
