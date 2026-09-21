"""Percentile normalisation, weight renormalisation, penalties and combination."""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from clipper.config import Config, WeightsConfig
from clipper.models import RubricScores, Signals, SignalValues
from clipper.signals.combine import (
    combine,
    percentile_rank,
    renormalize_weights,
    soft_penalties,
)


def rubric(**overrides) -> RubricScores:
    base = dict(
        hook_strength=6, standalone_clarity=6, payoff=6,
        emotional_intensity=6, quotability=6, ending_completeness=6,
    )
    base.update(overrides)
    return RubricScores(**base)


class TestPercentileRank:
    def test_orders_values_into_zero_to_one(self):
        ranks = percentile_rank({"a": 1.0, "b": 2.0, "c": 3.0})
        assert ranks["a"] == pytest.approx(0.0)
        assert ranks["b"] == pytest.approx(0.5)
        assert ranks["c"] == pytest.approx(1.0)

    def test_is_scale_invariant(self):
        """Ranking is what makes incomparable signals comparable."""
        small = percentile_rank({"a": 0.001, "b": 0.002, "c": 0.003})
        large = percentile_rank({"a": 100.0, "b": 200.0, "c": 300.0})
        assert small == large

    def test_handles_negative_values(self):
        """Bias-corrected heatmap values are signed."""
        ranks = percentile_rank({"a": -5.0, "b": 0.0, "c": 5.0})
        assert ranks["a"] < ranks["b"] < ranks["c"]

    def test_ties_share_a_rank(self):
        ranks = percentile_rank({"a": 1.0, "b": 1.0, "c": 2.0})
        assert ranks["a"] == ranks["b"]
        assert ranks["c"] > ranks["a"]

    def test_all_equal_values_all_score_the_midpoint(self):
        assert percentile_rank({"a": 5.0, "b": 5.0, "c": 5.0}) == {"a": 0.5, "b": 0.5, "c": 0.5}

    def test_a_single_value_is_the_midpoint_not_the_top(self):
        """One data point says nothing about whether it is good."""
        assert percentile_rank({"only": 42.0}) == {"only": 0.5}

    def test_empty(self):
        assert percentile_rank({}) == {}

    @settings(max_examples=50, deadline=None)
    @given(st.lists(st.floats(min_value=-1e6, max_value=1e6, allow_nan=False),
                    min_size=1, max_size=40))
    def test_output_is_always_in_range_and_order_preserving(self, values):
        data = {f"c{i}": v for i, v in enumerate(values)}
        ranks = percentile_rank(data)
        assert all(0.0 <= r <= 1.0 for r in ranks.values())
        for a in data:
            for b in data:
                if data[a] < data[b]:
                    assert ranks[a] <= ranks[b]


class TestRenormalizeWeights:
    def test_all_signals_present_normalises_to_one(self):
        weights = renormalize_weights(WeightsConfig().as_dict(),
                                      ["llm", "heatmap", "audio", "text"])
        assert sum(weights.values()) == pytest.approx(1.0)

    def test_a_missing_signal_is_redistributed_not_zeroed(self):
        """A local file with no heatmap must not lose 20% of its composite."""
        weights = renormalize_weights(WeightsConfig().as_dict(), ["llm", "audio", "text"])
        assert "heatmap" not in weights
        assert sum(weights.values()) == pytest.approx(1.0)
        assert weights["llm"] == pytest.approx(0.50 / 0.80)

    def test_relative_proportions_are_preserved(self):
        full = renormalize_weights(WeightsConfig().as_dict(), ["llm", "audio", "text"])
        assert full["audio"] == pytest.approx(full["text"])
        assert full["llm"] / full["audio"] == pytest.approx(0.50 / 0.15)

    def test_a_zero_weighted_signal_is_excluded(self):
        configured = {"llm": 1.0, "audio": 0.0, "text": 0.0, "heatmap": 0.0}
        assert renormalize_weights(configured, ["llm", "audio", "text"]) == {"llm": 1.0}

    def test_no_available_signals(self):
        assert renormalize_weights(WeightsConfig().as_dict(), []) == {}

    def test_falls_back_to_equal_weights_when_nothing_configured_is_available(self):
        configured = {"llm": 1.0, "heatmap": 0.0, "audio": 0.0, "text": 0.0}
        weights = renormalize_weights(configured, ["audio", "text"])
        assert weights == {"audio": 0.5, "text": 0.5}


class TestSoftPenalties:
    def _values(self, a=None, b=None) -> SignalValues:
        return SignalValues(candidate_id="c", llm_a=a, llm_b=b)

    def test_no_opinions_means_no_penalty(self):
        factor, reasons = soft_penalties(self._values())
        assert factor == 1.0
        assert reasons == []

    def test_healthy_scores_are_not_penalised(self):
        factor, reasons = soft_penalties(self._values(rubric(), rubric()))
        assert factor == 1.0
        assert reasons == []

    def test_a_weak_hook_is_penalised(self):
        factor, reasons = soft_penalties(
            self._values(rubric(hook_strength=2), rubric(hook_strength=2))
        )
        assert factor < 1.0
        assert any("hook" in r for r in reasons)

    def test_low_policy_risk_is_penalised_not_dropped(self):
        factor, reasons = soft_penalties(
            self._values(rubric(policy_risk="low"), rubric(policy_risk="low"))
        )
        assert 0.9 < factor < 1.0
        assert any("policy" in r for r in reasons)

    def test_a_single_opinion_is_trusted_slightly_less(self):
        both, _ = soft_penalties(self._values(rubric(), rubric()))
        one, reasons = soft_penalties(self._values(rubric(), None))
        assert one < both
        assert any("one prompt" in r for r in reasons)

    def test_penalties_compound(self):
        factor, reasons = soft_penalties(
            self._values(rubric(hook_strength=1, ending_completeness=1, policy_risk="low"),
                         rubric(hook_strength=1, ending_completeness=1, policy_risk="low"))
        )
        assert factor < 0.8
        assert len(reasons) >= 3

    def test_penalties_never_go_negative_or_above_one(self):
        for scores in (rubric(hook_strength=0, ending_completeness=0, policy_risk="low"),
                       rubric(hook_strength=10, ending_completeness=10)):
            factor, _ = soft_penalties(self._values(scores, scores))
            assert 0.0 < factor <= 1.0


class TestCombine:
    def _signals(self, count: int = 5, *, available=("llm", "audio", "text")) -> Signals:
        values = []
        for i in range(count):
            values.append(SignalValues(
                candidate_id=f"c{i:03d}",
                llm_a=rubric(), llm_b=rubric(),
                llm_total=float(i),
                audio=float(count - i) / 10,
                text=0.5,
            ))
        return Signals(source_id="s", available=list(available), values=values)

    def test_produces_one_entry_per_candidate(self):
        scored = combine(self._signals(5), Config())
        assert len(scored.scored) == 5

    def test_results_are_sorted_by_composite(self):
        scored = combine(self._signals(6), Config())
        composites = [s.composite for s in scored.scored]
        assert composites == sorted(composites, reverse=True)

    def test_records_the_weights_actually_used(self):
        scored = combine(self._signals(4), Config())
        assert set(scored.weights_used) == {"llm", "audio", "text"}
        assert sum(scored.weights_used.values()) == pytest.approx(1.0)

    def test_raw_values_are_kept_for_explain(self):
        scored = combine(self._signals(3), Config())
        for entry in scored.scored:
            assert "llm" in entry.raw
            assert "audio" in entry.raw

    def test_the_absolute_llm_total_survives_into_raw(self):
        """Selection gates on this, so it must not be lost in normalisation."""
        scored = combine(self._signals(5), Config())
        totals = sorted(e.raw["llm"] for e in scored.scored)
        assert totals == [0.0, 1.0, 2.0, 3.0, 4.0]

    def test_a_dropped_candidate_carries_its_reason(self):
        signals = self._signals(3)
        signals.values[1].dropped = True
        signals.values[1].drop_reason = "flagged as a sponsor read"
        scored = combine(signals, Config())
        dropped = [s for s in scored.scored if s.dropped]
        assert len(dropped) == 1
        assert "sponsor" in dropped[0].drop_reason

    def test_a_candidate_missing_one_signal_is_ranked_on_the_rest(self):
        """A failed LLM call must not zero a candidate that has other signals."""
        signals = self._signals(4)
        signals.values[0].llm_total = None
        scored = combine(signals, Config())
        entry = next(s for s in scored.scored if s.candidate_id == "c000")
        assert not entry.dropped
        assert "llm" not in entry.components
        assert entry.composite > 0

    def test_a_candidate_with_no_signals_at_all_is_dropped(self):
        signals = self._signals(3)
        signals.values[0].llm_total = None
        signals.values[0].audio = None
        signals.values[0].text = None
        scored = combine(signals, Config())
        entry = next(s for s in scored.scored if s.candidate_id == "c000")
        assert entry.dropped

    def test_empty_signals(self):
        scored = combine(Signals(source_id="s"), Config())
        assert scored.scored == []

    def test_composite_is_always_in_range(self):
        scored = combine(self._signals(8), Config())
        for entry in scored.scored:
            assert 0.0 <= entry.composite <= 1.0

    def test_penalties_are_applied_to_the_composite(self):
        clean = self._signals(4)
        penalised = self._signals(4)
        for value in penalised.values:
            value.llm_a = rubric(hook_strength=1)
            value.llm_b = rubric(hook_strength=1)

        top_clean = combine(clean, Config()).scored[0]
        top_penalised = combine(penalised, Config()).scored[0]
        assert top_penalised.penalty < 1.0
        assert top_penalised.composite < top_clean.composite
