"""Normalising and combining the signals (BUILD_BRIEF.md section 9.5).

    component_i = percentile_rank(raw_i)          # per video, per signal
    composite   = sum(w_i * component_i) / sum(w_i)
    composite  *= soft_penalties

Percentile ranking is the right tool for *combining* signals: raw LLM totals
(0-10), audio scores (0-1) and bias-corrected heatmap values (small, signed) are
not otherwise comparable, and ranking makes them so without any scaling
assumptions.

It is the wrong tool for *gating* quality, and the brief conflates the two. A
per-video percentile is uniform by construction, so the best candidate scores
near 1.0 whether the video is excellent or worthless, and a `min_composite`
threshold can never fire on a weak source. Selection therefore gates on the
absolute LLM total instead -- see `select/pick.py` and PLAN.md P1.
"""

from __future__ import annotations

import numpy as np

from ..config import Config
from ..models import Scored, ScoredCandidate, Signals, SignalValues
from ..utils.logging import get_logger

log = get_logger(__name__)

SIGNAL_NAMES = ("llm", "heatmap", "audio", "text")


def percentile_rank(values: dict[str, float]) -> dict[str, float]:
    """Map raw values onto [0, 1] by rank within this video.

    Ties share the midpoint of the ranks they span, so N identical values all
    receive the same score rather than an arbitrary ordering. With a single
    value the rank is 0.5: one data point carries no information about whether
    it is good, and 1.0 would assert that it is.
    """
    if not values:
        return {}
    if len(values) == 1:
        return {next(iter(values)): 0.5}

    keys = list(values)
    array = np.array([values[k] for k in keys], dtype=np.float64)

    if np.allclose(array, array[0]):
        return dict.fromkeys(keys, 0.5)

    order = array.argsort()
    ranks = np.empty(len(array), dtype=np.float64)
    ranks[order] = np.arange(len(array), dtype=np.float64)

    # Average the ranks of tied values.
    unique, inverse, counts = np.unique(array, return_inverse=True, return_counts=True)
    if len(unique) < len(array):
        sums = np.zeros(len(unique))
        np.add.at(sums, inverse, ranks)
        ranks = (sums / counts)[inverse]

    return {k: float(r / (len(array) - 1)) for k, r in zip(keys, ranks, strict=True)}


def renormalize_weights(configured: dict[str, float], available: list[str]) -> dict[str, float]:
    """Restrict weights to the signals that exist, then renormalise to sum to 1.

    A source with no heatmap must not be scored as though its heatmap component
    were zero -- that would penalise every local file by 20% of the composite.
    """
    usable = {k: v for k, v in configured.items() if k in available and v > 0}
    total = sum(usable.values())
    if total <= 0:
        # Every configured signal is unavailable. Fall back to equal weights over
        # whatever we do have, so the pipeline degrades rather than dividing by zero.
        if not available:
            return {}
        log.warning(
            "none of the configured signals are available; falling back to equal "
            "weights over %s", ", ".join(available),
        )
        return {name: 1.0 / len(available) for name in available}
    return {k: v / total for k, v in usable.items()}


def soft_penalties(values: SignalValues) -> tuple[float, list[str]]:
    """Multiplicative penalties short of a hard drop (section 9.5).

    Kept multiplicative and mild: these express "less confident", not "reject".
    Rejection is the hard-drop path in `signals/llm.py`.
    """
    factor = 1.0
    reasons: list[str] = []
    opinions = [s for s in (values.llm_a, values.llm_b) if s is not None]

    if any(s.policy_risk == "low" for s in opinions):
        factor *= 0.95
        reasons.append("moderate policy risk (-5%)")

    if opinions:
        mean_hook = sum(s.hook_strength for s in opinions) / len(opinions)
        if mean_hook <= 3:
            factor *= 0.85
            reasons.append(f"weak hook (mean {mean_hook:.1f}/10, -15%)")
        elif mean_hook <= 5:
            factor *= 0.95
            reasons.append(f"soft hook (mean {mean_hook:.1f}/10, -5%)")

        mean_ending = sum(s.ending_completeness for s in opinions) / len(opinions)
        if mean_ending <= 3:
            factor *= 0.92
            reasons.append(f"unfinished ending (mean {mean_ending:.1f}/10, -8%)")

    # Only one prompt returned a usable answer: the score rests on half the
    # evidence it should, so trust it slightly less.
    if len(opinions) == 1:
        factor *= 0.97
        reasons.append("only one prompt returned a score (-3%)")

    return round(factor, 4), reasons


def combine(signals: Signals, config: Config) -> Scored:
    """Turn raw signal values into ranked composites."""
    values = {v.candidate_id: v for v in signals.values}
    if not values:
        return Scored(source_id=signals.source_id, weights_used={}, scored=[])

    raws: dict[str, dict[str, float]] = {}
    for name in SIGNAL_NAMES:
        if name not in signals.available:
            continue
        attribute = "llm_total" if name == "llm" else name
        collected = {
            cid: getattr(v, attribute)
            for cid, v in values.items()
            if getattr(v, attribute) is not None and not v.dropped
        }
        if collected:
            raws[name] = collected

    available = list(raws)
    weights = renormalize_weights(config.weights.as_dict(), available)
    components = {name: percentile_rank(raw) for name, raw in raws.items()}

    log.info(
        "combining %d candidates over %s (weights: %s)",
        len(values), ", ".join(available) or "no signals",
        ", ".join(f"{k}={v:.2f}" for k, v in weights.items()) or "none",
    )

    scored: list[ScoredCandidate] = []
    for cid, value in values.items():
        if value.dropped:
            scored.append(ScoredCandidate(
                candidate_id=cid, composite=0.0, dropped=True,
                drop_reason=value.drop_reason,
                raw=_raw_snapshot(value),
            ))
            continue

        present = {n: components[n][cid] for n in available if cid in components[n]}
        if not present:
            scored.append(ScoredCandidate(
                candidate_id=cid, composite=0.0, dropped=True,
                drop_reason="no signal produced a value for this candidate",
                raw=_raw_snapshot(value),
            ))
            continue

        # Renormalise again per candidate: a candidate the LLM failed to score
        # must be ranked on the signals it does have, not penalised for the gap.
        local_weights = renormalize_weights(config.weights.as_dict(), list(present))
        base = sum(local_weights.get(n, 0.0) * c for n, c in present.items())

        penalty, reasons = soft_penalties(value)
        scored.append(ScoredCandidate(
            candidate_id=cid,
            composite=round(base * penalty, 5),
            components={k: round(v, 5) for k, v in present.items()},
            raw=_raw_snapshot(value),
            penalty=penalty,
            penalty_reasons=reasons,
        ))

    scored.sort(key=lambda s: s.composite, reverse=True)
    return Scored(source_id=signals.source_id, weights_used=weights, scored=scored)


def _raw_snapshot(value: SignalValues) -> dict[str, float]:
    """The pre-normalisation numbers, kept so `explain` can show its working."""
    snapshot: dict[str, float] = {}
    for name, attribute in (("llm", "llm_total"), ("audio", "audio"),
                            ("heatmap", "heatmap"), ("text", "text")):
        raw = getattr(value, attribute)
        if raw is not None:
            snapshot[name] = round(float(raw), 5)
    return snapshot
