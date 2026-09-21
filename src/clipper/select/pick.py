"""Greedy non-overlapping selection (BUILD_BRIEF.md section 10).

Sort by composite, then walk down taking clips that satisfy every constraint:
no overlap, a minimum gap, a cap per third of the video, and the quality gates.

The quality gates are where this departs from the brief, deliberately. The brief
stops "when the next candidate falls below `min_composite`", but the composite is
a per-video percentile rank and is therefore uniform by construction -- the top
candidate scores near 1.0 in every video, including one with nothing worth
clipping. A relative threshold cannot express "this source is weak".

So selection applies both:

* `min_composite` -- relative, catches a pathological tail within a video.
* `min_llm_total` -- **absolute**, on the raw 0-10 rubric scale. The prompts
  instruct the model that most clips should score 3-6, so this is a genuine
  across-video judgement and is the gate that lets clipper return two clips, or
  none, when that is the honest answer.

`use_absolute_gate: false` restores the brief's literal behaviour, so Phase 5
can measure whether the change helps rather than assuming it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..config import SelectionConfig
from ..models import Candidate, Scored, ScoredCandidate
from ..utils.logging import get_logger

log = get_logger(__name__)


@dataclass
class Pick:
    """One selected candidate and why it was taken."""

    candidate: Candidate
    scored: ScoredCandidate
    rank: int


@dataclass
class SelectionResult:
    picks: list[Pick] = field(default_factory=list)
    #: Candidates that passed the gates but lost on a constraint, best first.
    #: These are the replacements used when a clip fails the QA gate.
    reserves: list[Pick] = field(default_factory=list)
    stopped_because: str = ""
    #: candidate_id -> why it was not picked, for `clipper explain`.
    rejections: dict[str, str] = field(default_factory=dict)

    @property
    def count(self) -> int:
        return len(self.picks)


def select(
    scored: Scored,
    candidates: list[Candidate],
    cfg: SelectionConfig,
    *,
    min_gap_seconds: float,
    source_duration: float,
    limit: int | None = None,
) -> SelectionResult:
    """Choose which candidates become clips."""
    by_id = {c.candidate_id: c for c in candidates}
    target = min(limit or cfg.top_n, cfg.top_n if limit is None else limit)
    result = SelectionResult()

    ranked = sorted(
        (s for s in scored.scored if not s.dropped),
        key=lambda s: s.composite,
        reverse=True,
    )
    for entry in scored.scored:
        if entry.dropped:
            result.rejections[entry.candidate_id] = entry.drop_reason or "dropped before scoring"

    if not ranked:
        result.stopped_because = "no candidate survived scoring"
        log.warning("nothing to select: %s", result.stopped_because)
        return result

    taken: list[Candidate] = []
    per_third = {0: 0, 1: 0, 2: 0}

    for entry in ranked:
        candidate = by_id.get(entry.candidate_id)
        if candidate is None:  # pragma: no cover - artifacts out of sync
            result.rejections[entry.candidate_id] = "no matching candidate window"
            continue

        gate = _quality_gate(entry, cfg)
        if gate:
            result.rejections[entry.candidate_id] = gate
            continue

        constraint = _constraint_violation(
            candidate, taken, per_third, cfg,
            min_gap_seconds=min_gap_seconds, source_duration=source_duration,
        )
        if constraint:
            # Passed quality but lost on placement: a valid replacement if a
            # picked clip later fails QA.
            result.reserves.append(Pick(candidate, entry, rank=0))
            result.rejections[entry.candidate_id] = constraint
            continue

        if len(result.picks) >= target:
            result.reserves.append(Pick(candidate, entry, rank=0))
            result.rejections[entry.candidate_id] = f"beyond the requested top {target}"
            continue

        taken.append(candidate)
        per_third[_third(candidate, source_duration)] += 1
        result.picks.append(Pick(candidate, entry, rank=len(result.picks) + 1))

    result.stopped_because = _describe_stop(result, ranked, cfg, target)
    for position, pick in enumerate(result.reserves, start=1):
        pick.rank = position

    log.info(
        "selected %d clip(s) of %d requested from %d scored candidate(s); %s",
        result.count, target, len(ranked), result.stopped_because,
    )
    return result


def _quality_gate(entry: ScoredCandidate, cfg: SelectionConfig) -> str:
    """Why this candidate is not good enough, or "" if it is."""
    if cfg.use_absolute_gate:
        llm_total = entry.raw.get("llm")
        if llm_total is not None and llm_total < cfg.min_llm_total:
            return (
                f"LLM rubric total {llm_total:.2f}/10 is below the absolute "
                f"minimum of {cfg.min_llm_total:.2f}"
            )

    if entry.composite < cfg.min_composite:
        return (
            f"composite {entry.composite:.3f} is below min_composite "
            f"{cfg.min_composite:.3f}"
        )
    return ""


def _constraint_violation(
    candidate: Candidate,
    taken: list[Candidate],
    per_third: dict[int, int],
    cfg: SelectionConfig,
    *,
    min_gap_seconds: float,
    source_duration: float,
) -> str:
    """Why this candidate cannot be placed alongside those already taken."""
    for other in taken:
        if candidate.overlaps(other):
            return f"overlaps the already-selected {other.candidate_id}"

    for other in taken:
        gap = max(candidate.start - other.end, other.start - candidate.end)
        if gap < min_gap_seconds:
            return (
                f"only {gap:.0f}s from {other.candidate_id}, under the "
                f"{min_gap_seconds:.0f}s minimum gap"
            )

    third = _third(candidate, source_duration)
    if per_third[third] >= cfg.max_from_same_third:
        return (
            f"the {_third_name(third)} third already has "
            f"{cfg.max_from_same_third} clip(s)"
        )
    return ""


def _third(candidate: Candidate, source_duration: float) -> int:
    """Which third of the source a candidate's midpoint falls in."""
    if source_duration <= 0:
        return 0
    midpoint = (candidate.start + candidate.end) / 2
    return min(2, max(0, int(3 * midpoint / source_duration)))


def _third_name(index: int) -> str:
    return ("first", "middle", "final")[index]


def _describe_stop(
    result: SelectionResult,
    ranked: list[ScoredCandidate],
    cfg: SelectionConfig,
    target: int,
) -> str:
    """A human-readable reason the selection ended where it did.

    Reports the actual distribution of rejection reasons rather than assuming
    one. An earlier version counted quality *and* composite rejections together
    and attributed all of them to `min_llm_total`, which said "27 scored below
    min_llm_total" on a run where only 5 actually had -- and where the real
    binding constraint was clip placement, not quality at all.
    """
    if result.count >= target:
        return f"reached the requested {target} clip(s)"

    buckets = {
        "below the absolute quality bar": 0,
        "below the relative composite threshold": 0,
        "overlapping or too close to a better clip": 0,
        "capped by the per-third spread limit": 0,
        "dropped before scoring": 0,
    }
    for reason in result.rejections.values():
        if "below the absolute minimum" in reason:
            buckets["below the absolute quality bar"] += 1
        elif "below min_composite" in reason:
            buckets["below the relative composite threshold"] += 1
        elif "overlaps" in reason or "minimum gap" in reason:
            buckets["overlapping or too close to a better clip"] += 1
        elif "third already has" in reason:
            buckets["capped by the per-third spread limit"] += 1
        elif "beyond the requested" not in reason:
            buckets["dropped before scoring"] += 1

    breakdown = ", ".join(
        f"{count} {label}" for label, count in
        sorted(buckets.items(), key=lambda kv: -kv[1]) if count
    )
    quality_blocked = buckets["below the absolute quality bar"]

    if result.count == 0 and quality_blocked:
        return (
            f"no candidate met the quality bar of {cfg.min_llm_total:.1f}/10. "
            "Returning nothing beats returning filler"
            + (f" ({breakdown})" if breakdown else "")
        )
    return (
        f"produced {result.count} of {target} requested"
        + (f"; the rest were {breakdown}" if breakdown else "")
    )
