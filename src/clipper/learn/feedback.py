"""Learning from the user's ratings: does the score work, and what should change?

The Control Center asks for a 1-5 rating on each clip, with optional reasons
("weak hook", "needs context", ...). Three things come out of them:

* **A report** -- does Clipper's score agree with the user's ratings, and with
  the views the posts got? Rank correlations, and a table of score bands.
* **Rubric weights** -- the six rubric scores roll up into one total with fixed
  weights (hook 30%, clarity 20%, ...). Once enough clips are rated, each weight
  moves toward how well that dimension tracked the ratings, blended with the
  defaults by how much evidence there is. Scoring picks them up on the next run
  (no model calls are repeated: the total is recomputed from cached scores).
* **Taste** -- a short block for the scoring prompt with a few clips the user
  rated highly and low, and the reasons they gave most often.

Both are applied only while the "Learn from my ratings" setting is on.
"""

from __future__ import annotations

import json
import statistics
from dataclasses import dataclass, field

from ..studio.db import REASONS

RUBRIC = ["hook_strength", "standalone_clarity", "payoff", "emotional_intensity",
          "quotability", "ending_completeness"]
RUBRIC_LABELS = {"hook_strength": "Hook", "standalone_clarity": "Makes sense alone",
                 "payoff": "Payoff", "emotional_intensity": "Emotion",
                 "quotability": "Quotable", "ending_completeness": "Ending"}
#: Ratings before weights move at all; below this, noise dominates.
MIN_FOR_WEIGHTS = 8
#: Evidence weight: with n ratings the learnt weights count n / (n + PRIOR).
PRIOR = 20
MAX_SHARE = 0.6
MIN_FOR_AGREEMENT = 5
MIN_FOR_TASTE = 3
BANDS = [("Under 6", None, 6.0), ("6 to 7", 6.0, 7.0), ("7 to 8", 7.0, 8.0), ("8 and up", 8.0, None)]


def _ranks(values: list[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        for k in range(i, j + 1):
            ranks[order[k]] = (i + j) / 2 + 1
        i = j + 1
    return ranks


def spearman(x: list[float], y: list[float]) -> float | None:
    """Rank correlation, -1..1; None when it can't be computed."""
    if len(x) < 3 or len(set(x)) < 2 or len(set(y)) < 2:
        return None
    rx, ry = _ranks(x), _ranks(y)
    mx, my = statistics.fmean(rx), statistics.fmean(ry)
    cov = sum((a - mx) * (b - my) for a, b in zip(rx, ry, strict=True))
    vx = sum((a - mx) ** 2 for a in rx)
    vy = sum((b - my) ** 2 for b in ry)
    return round(cov / (vx * vy) ** 0.5, 3) if vx and vy else None


def verdict(rho: float | None, n: int, minimum: int = MIN_FOR_AGREEMENT) -> str:
    if rho is None or n < minimum:
        return "not enough yet"
    if rho >= 0.5:
        return "strong"
    if rho >= 0.3:
        return "moderate"
    if rho >= 0.1:
        return "weak"
    return "none" if rho > -0.1 else "backwards"


@dataclass
class Rated:
    """One clip as the learner sees it."""

    id: int
    campaign: str
    title: str
    rating: int | None
    reasons: list[str]
    score: float | None
    rubric: dict[str, float]
    text: str
    views: float | None = None
    rated_at: str = ""


def from_rows(rows: list[dict], views: dict[int, float] | None = None) -> list[Rated]:
    """Clips (db rows) with their stored scores, ratings and total views."""
    out = []
    for row in rows:
        scores = json.loads(row.get("scores") or "{}")
        out.append(Rated(
            id=row["id"], campaign=row["campaign"], title=row.get("title") or "",
            rating=row.get("rating"), reasons=json.loads(row.get("reasons") or "[]"),
            score=scores.get("score"), rubric=scores.get("rubric") or {},
            text=scores.get("text") or "", views=(views or {}).get(row["id"]),
            rated_at=row.get("rated_at") or ""))
    return out


def learned_weights(clips: list[Rated], defaults: dict[str, float]) -> tuple[dict[str, float], int]:
    """Rubric weights moved toward what the ratings favour. Returns (weights, n used)."""
    rated = [c for c in clips if c.rating is not None and all(k in c.rubric for k in RUBRIC)]
    n = len(rated)
    if n < MIN_FOR_WEIGHTS:
        return dict(defaults), n
    ratings = [float(c.rating) for c in rated]
    implied = {}
    for dim in RUBRIC:
        rho = spearman([c.rubric[dim] for c in rated], ratings)
        # A dimension that tracks the ratings gains weight; one that runs against
        # them loses most of it, but never all (the evidence is small).
        implied[dim] = defaults[dim] * max(0.25, 1.0 + (rho or 0.0))
    total_default = sum(defaults.values())
    scale = total_default / sum(implied.values())
    share = min(MAX_SHARE, n / (n + PRIOR))
    return ({d: round((1 - share) * defaults[d] + share * implied[d] * scale, 4) for d in RUBRIC},
            n)


def total(rubric: dict[str, float], weights: dict[str, float]) -> float | None:
    if not all(k in rubric for k in weights):
        return None
    den = sum(weights.values())
    return round(sum(weights[k] * rubric[k] for k in weights) / den, 2) if den else None


def taste(clips: list[Rated], campaign: str | None = None) -> str:
    """The prompt block: rated examples (this campaign's first) and common reasons."""
    rated = [c for c in clips if c.rating is not None]
    if len(rated) < MIN_FOR_TASTE:
        return ""

    def pick(cond, limit=4):
        chosen = sorted((c for c in rated if cond(c)),
                        key=lambda c: (c.campaign != campaign, -abs(c.rating - 3), c.rated_at),
                        reverse=False)
        return chosen[:limit]

    def line(c: Rated) -> str:
        why = ", ".join(REASONS.get(r, r).lower() for r in c.reasons)
        text = " ".join(c.text.split())[:220]
        return (f'- rated {c.rating}/5: "{c.title}"' + (f" ({why})" if why else "")
                + (f"\n  {text}" if text else ""))

    liked = pick(lambda c: c.rating >= 4)
    disliked = pick(lambda c: c.rating <= 2)
    parts = []
    if liked:
        parts.append("Clips they rated highly:\n" + "\n".join(line(c) for c in liked))
    if disliked:
        parts.append("Clips they rated low:\n" + "\n".join(line(c) for c in disliked))
    counts: dict[str, int] = {}
    for c in rated:
        for r in c.reasons:
            counts[r] = counts.get(r, 0) + 1
    common = sorted(counts.items(), key=lambda kv: -kv[1])[:5]
    if common:
        parts.append("Reasons they give most: " + ", ".join(
            f"{REASONS.get(r, r).lower()} ({n})" for r, n in common))
    return "\n".join(parts)


@dataclass
class Report:
    rated: int = 0
    unrated: int = 0
    scored_and_rated: int = 0
    agreement: float | None = None
    agreement_verdict: str = "not enough yet"
    with_views: int = 0
    views_agreement: float | None = None
    views_verdict: str = "not enough yet"
    rating_vs_views: float | None = None
    bands: list[dict] = field(default_factory=list)
    dimensions: list[dict] = field(default_factory=list)
    reasons: list[dict] = field(default_factory=list)
    taste: str = ""
    weights_n: int = 0


def report(clips: list[Rated], defaults: dict[str, float], *, active: bool = True) -> Report:
    rated = [c for c in clips if c.rating is not None]
    both = [c for c in rated if c.score is not None]
    viewed = [c for c in clips if c.score is not None and c.views is not None]
    out = Report(rated=len(rated), unrated=sum(1 for c in clips if c.rating is None),
                 scored_and_rated=len(both), with_views=len(viewed))
    out.agreement = spearman([c.score for c in both], [float(c.rating) for c in both])
    out.agreement_verdict = verdict(out.agreement, len(both))
    out.views_agreement = spearman([c.score for c in viewed], [c.views for c in viewed])
    out.views_verdict = verdict(out.views_agreement, len(viewed))
    rated_viewed = [c for c in rated if c.views is not None]
    out.rating_vs_views = spearman([float(c.rating) for c in rated_viewed],
                                   [c.views for c in rated_viewed])
    for label, low, high in BANDS:
        members = [c for c in clips if c.score is not None
                   and (low is None or c.score >= low) and (high is None or c.score < high)]
        ratings = [c.rating for c in members if c.rating is not None]
        views = [c.views for c in members if c.views is not None]
        out.bands.append({"label": label, "clips": len(members),
                          "avg_rating": round(statistics.fmean(ratings), 1) if ratings else None,
                          "rated": len(ratings),
                          "median_views": statistics.median(views) if views else None})
    weights, out.weights_n = learned_weights(clips, defaults)
    rubric_rated = [c for c in rated if all(k in c.rubric for k in RUBRIC)]
    for dim in RUBRIC:
        rho = spearman([c.rubric[dim] for c in rubric_rated], [float(c.rating) for c in rubric_rated])
        out.dimensions.append({"key": dim, "label": RUBRIC_LABELS[dim], "default": defaults[dim],
                               "learned": weights[dim] if active else defaults[dim],
                               "agreement": rho})
    counts: dict[str, int] = {}
    for c in rated:
        for r in c.reasons:
            counts[r] = counts.get(r, 0) + 1
    out.reasons = [{"key": k, "label": REASONS.get(k, k), "count": n}
                   for k, n in sorted(counts.items(), key=lambda kv: -kv[1])]
    out.taste = taste(clips)
    return out
