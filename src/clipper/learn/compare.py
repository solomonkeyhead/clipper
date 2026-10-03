"""What's working: posts with a change against posts without it (D105).

Clipper has changed how clips open (payoff first, D97), what they show first
(the chosen cover, D104), and TikTok posts now carry the branded-content switch.
Each is judged the same way, so the comparison is fair:

* views at the same age -- 48 hours after posting, read from the sync snapshots
  (a post that young isn't counted yet), not views today, which favour old posts;
* within one platform, never across: TikTok and Instagram numbers don't mix;
* medians, not averages, so one viral post doesn't decide it;
* no verdict until each side has MIN_EACH posts.

Early drop-off is Instagram's skip rate (share who swipe away in the first 3 s),
lower is better, where the platform reports it.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import datetime, timedelta

AGE_HOURS = 48
#: A snapshot this long after the 48-hour mark still stands in for it (syncs are periodic).
SLACK_HOURS = 12
MIN_EACH = 3
#: Within this, two medians are "about the same".
SAME = 0.2


@dataclass(frozen=True)
class PostFacts:
    url: str
    platform: str
    posted_at: datetime | None
    history: tuple[tuple[datetime, float | None, float | None], ...]   # (at, views, skip_rate_pct)
    traits: dict[str, bool] = field(default_factory=dict)
    hook: str = ""
    campaign: str = ""


def parse(when: str | None) -> datetime | None:
    if not when:
        return None
    try:
        return datetime.strptime(when[:16].replace("T", " "), "%Y-%m-%d %H:%M")
    except ValueError:
        return None


def views_at(post: PostFacts, hours: float = AGE_HOURS) -> float | None:
    """Its views `hours` after posting: the last sync by then (or just after)."""
    if post.posted_at is None:
        return None
    target = post.posted_at + timedelta(hours=hours)
    by_then = [(at, v) for at, v, _ in post.history if v is not None and at <= target + timedelta(hours=SLACK_HOURS)]
    if not by_then or max(at for at, _ in by_then) < target - timedelta(hours=SLACK_HOURS):
        return None  # too young, or no sync near the mark
    return float(max(by_then)[1])


def skip_rate(post: PostFacts) -> float | None:
    rates = [s for _, _, s in post.history if s]
    return rates[-1] if rates else None


@dataclass
class Side:
    posts: int
    median_views: float | None
    skip_rate: float | None


@dataclass
class Row:
    platform: str
    yes: Side
    no: Side
    ratio: float | None
    verdict: str


@dataclass
class Comparison:
    key: str
    title: str
    yes_label: str
    no_label: str
    rows: list[Row]


def _side(posts: list[PostFacts]) -> Side:
    views = [v for p in posts if (v := views_at(p)) is not None]
    skips = [s for p in posts if (s := skip_rate(p)) is not None]
    return Side(posts=len(views), median_views=statistics.median(views) if views else None,
                skip_rate=round(statistics.median(skips), 1) if skips else None)


def verdict(yes: Side, no: Side) -> tuple[float | None, str]:
    if yes.posts < MIN_EACH or no.posts < MIN_EACH:
        need = [f"{MIN_EACH - s.posts} more {w}" for s, w in ((yes, "with"), (no, "without")) if s.posts < MIN_EACH]
        return None, f"Not enough yet: {' and '.join(need)}"
    a, b = yes.median_views or 0, no.median_views or 0
    if b == 0:
        return None, "Better" if a > 0 else "No views either way"
    ratio = round(a / b, 2)
    if abs(ratio - 1) <= SAME:
        return ratio, "About the same"
    return ratio, f"{ratio:.1f} times the views" if ratio > 1 else f"{(1 - ratio) * 100:.0f}% fewer views"


TRAITS = [
    ("payoff", "Opens on its payoff", "Opens on the payoff line", "Opens at the start"),
    ("cover", "Chosen cover", "Clipper's cover first", "No chosen cover"),
    ("disclosed", "TikTok branded-content switch", "Switch on", "Switch off"),
    ("edited", "Cut in the editor", "Edited by hand", "As Clipper made it"),
]


def compare(posts: list[PostFacts]) -> list[Comparison]:
    """One comparison per change, each split by platform."""
    out = []
    for key, title, yes_label, no_label in TRAITS:
        rows = []
        for platform in sorted({p.platform for p in posts if key in p.traits}):
            mine = [p for p in posts if p.platform == platform and key in p.traits]
            yes, no = _side([p for p in mine if p.traits[key]]), _side([p for p in mine if not p.traits[key]])
            if yes.posts or no.posts:
                ratio, word = verdict(yes, no)
                rows.append(Row(platform=platform, yes=yes, no=no, ratio=ratio, verdict=word))
        out.append(Comparison(key=key, title=title, yes_label=yes_label, no_label=no_label, rows=rows))
    return out


@dataclass
class HookResult:
    campaign: str
    platform: str
    hook: str
    posts: int
    median_views: float


def hooks(posts: list[PostFacts]) -> list[HookResult]:
    """Each on-screen hook's median views at 48 hours, per campaign and platform, best first."""
    groups: dict[tuple[str, str, str], list[float]] = {}
    for p in posts:
        v = views_at(p)
        if p.hook and v is not None:
            groups.setdefault((p.campaign, p.platform, p.hook), []).append(v)
    found = [HookResult(campaign=c, platform=pl, hook=h, posts=len(vs), median_views=statistics.median(vs))
             for (c, pl, h), vs in groups.items()]
    return sorted(found, key=lambda r: (r.campaign, r.platform, -r.median_views))
