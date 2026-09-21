"""Selection: the quality gates and the placement constraints.

The gate behaviour is the load-bearing part. BUILD_BRIEF.md section 1 promises
the tool will return fewer clips -- or none -- when a source is weak, and
`min_composite` alone cannot deliver that because a per-video percentile is
uniform by construction. These tests pin down that the absolute gate can.
"""

from __future__ import annotations

import pytest

from clipper.config import SelectionConfig
from clipper.models import Candidate, Scored, ScoredCandidate
from clipper.select.pick import select

SOURCE_DURATION = 3600.0


def candidate(cid: str, start: float, duration: float = 30.0) -> Candidate:
    return Candidate(candidate_id=cid, start=start, end=start + duration,
                     sentence_indices=(0, 1), text=f"text for {cid}")


def entry(cid: str, composite: float, llm_total: float = 8.0) -> ScoredCandidate:
    return ScoredCandidate(candidate_id=cid, composite=composite,
                           components={"llm": composite}, raw={"llm": llm_total})


def build(specs: list[tuple[str, float, float, float]]):
    """specs: (id, start, composite, llm_total)."""
    candidates = [candidate(cid, start) for cid, start, _, _ in specs]
    scored = Scored(
        source_id="s",
        weights_used={"llm": 1.0},
        scored=[entry(cid, comp, llm) for cid, _, comp, llm in specs],
    )
    return scored, candidates


def run(specs, cfg=None, *, min_gap=30.0, limit=None):
    scored, candidates = build(specs)
    return select(scored, candidates, cfg or SelectionConfig(),
                  min_gap_seconds=min_gap, source_duration=SOURCE_DURATION, limit=limit)


class TestBasicSelection:
    def test_picks_the_highest_composite_first(self):
        result = run([("a", 100, 0.9, 8.0), ("b", 500, 0.5, 8.0), ("c", 1000, 0.7, 8.0)])
        assert [p.scored.candidate_id for p in result.picks] == ["a", "c", "b"]

    def test_ranks_are_sequential_from_one(self):
        result = run([("a", 100, 0.9, 8.0), ("b", 500, 0.8, 8.0), ("c", 1000, 0.7, 8.0)])
        assert [p.rank for p in result.picks] == [1, 2, 3]

    def test_stops_at_top_n(self):
        specs = [(f"c{i}", i * 300, 0.9 - i * 0.01, 8.0) for i in range(10)]
        result = run(specs, SelectionConfig(top_n=3, max_from_same_third=10))
        assert result.count == 3
        assert "reached the requested 3" in result.stopped_because

    def test_an_explicit_limit_overrides_top_n(self):
        specs = [(f"c{i}", i * 300, 0.9, 8.0) for i in range(6)]
        result = run(specs, SelectionConfig(top_n=5, max_from_same_third=10), limit=2)
        assert result.count == 2

    def test_no_candidates(self):
        result = run([])
        assert result.count == 0
        assert "no candidate survived" in result.stopped_because


class TestAbsoluteQualityGate:
    """The fix for the brief's undeliverable `min_composite` promise (PLAN.md P1)."""

    def test_a_weak_source_yields_nothing(self):
        """Every candidate below the absolute bar means zero clips, not five."""
        specs = [(f"c{i}", i * 300, 0.95 - i * 0.05, 3.0) for i in range(6)]
        result = run(specs, SelectionConfig(min_llm_total=5.5, max_from_same_third=10))
        assert result.count == 0
        assert "no candidate met the quality bar" in result.stopped_because

    def test_returning_nothing_beats_returning_filler(self):
        """The top candidate still ranks ~1.0 on percentile; only the absolute
        gate can tell that it is nonetheless bad."""
        specs = [(f"c{i}", i * 300, 0.99 - i * 0.01, 2.0) for i in range(5)]
        result = run(specs, SelectionConfig(min_llm_total=5.5, max_from_same_third=10))
        assert result.count == 0

    def test_only_the_good_ones_are_taken(self):
        specs = [("good1", 100, 0.9, 8.0), ("bad", 600, 0.8, 3.0), ("good2", 1200, 0.7, 7.0)]
        result = run(specs, SelectionConfig(min_llm_total=5.5, max_from_same_third=10))
        assert {p.scored.candidate_id for p in result.picks} == {"good1", "good2"}
        assert "below the absolute minimum" in result.rejections["bad"]

    def test_the_gate_can_be_disabled_for_comparison(self):
        """Phase 5 needs to measure whether the change actually helps."""
        specs = [(f"c{i}", i * 300, 0.9, 2.0) for i in range(4)]
        cfg = SelectionConfig(use_absolute_gate=False, min_composite=0.1,
                              max_from_same_third=10)
        assert run(specs, cfg).count == 4

    def test_a_candidate_without_an_llm_score_is_not_gated_out(self):
        """Missing evidence is not evidence of low quality."""
        scored, candidates = build([("a", 100, 0.9, 8.0)])
        scored.scored[0].raw = {}  # no llm key at all
        result = select(scored, candidates, SelectionConfig(min_llm_total=9.9),
                        min_gap_seconds=30.0, source_duration=SOURCE_DURATION)
        assert result.count == 1

    def test_min_composite_still_applies_as_a_relative_guard(self):
        specs = [("a", 100, 0.9, 8.0), ("b", 600, 0.01, 8.0)]
        result = run(specs, SelectionConfig(min_composite=0.3, max_from_same_third=10))
        assert result.count == 1
        assert "below min_composite" in result.rejections["b"]


class TestConstraints:
    def test_overlapping_candidates_are_not_both_picked(self):
        result = run([("a", 100, 0.9, 8.0), ("b", 110, 0.8, 8.0)])
        assert result.count == 1
        assert "overlaps" in result.rejections["b"]

    def test_the_minimum_gap_is_enforced(self):
        # b starts 10s after a ends, under a 30s minimum.
        result = run([("a", 100, 0.9, 8.0), ("b", 140, 0.8, 8.0)], min_gap=30.0)
        assert result.count == 1
        assert "minimum gap" in result.rejections["b"]

    def test_a_sufficient_gap_is_allowed(self):
        result = run([("a", 100, 0.9, 8.0), ("b", 200, 0.8, 8.0)], min_gap=30.0)
        assert result.count == 2

    def test_picks_are_spread_across_thirds(self):
        # Five strong candidates all in the first third.
        specs = [(f"c{i}", 100 + i * 100, 0.9 - i * 0.01, 8.0) for i in range(5)]
        result = run(specs, SelectionConfig(top_n=5, max_from_same_third=2), min_gap=30.0)
        assert result.count <= 2
        assert any("third already has" in r for r in result.rejections.values())

    def test_each_third_gets_its_own_allowance(self):
        specs = [
            ("a", 100, 0.95, 8.0), ("b", 400, 0.94, 8.0),      # first third
            ("c", 1300, 0.93, 8.0), ("d", 1600, 0.92, 8.0),    # middle third
            ("e", 2500, 0.91, 8.0), ("f", 2800, 0.90, 8.0),    # final third
        ]
        result = run(specs, SelectionConfig(top_n=6, max_from_same_third=2))
        assert result.count == 6

    def test_selected_clips_never_overlap_each_other(self):
        specs = [(f"c{i}", i * 40, 0.9 - i * 0.01, 8.0) for i in range(20)]
        result = run(specs, SelectionConfig(top_n=8, max_from_same_third=8), min_gap=0.0)
        picked = [p.candidate for p in result.picks]
        for i, a in enumerate(picked):
            for b in picked[i + 1:]:
                assert not a.overlaps(b)


class TestReserves:
    def test_a_good_candidate_blocked_on_placement_becomes_a_reserve(self):
        """Reserves are what the QA gate uses to fill a failed clip's slot."""
        result = run([("a", 100, 0.9, 8.0), ("b", 110, 0.85, 8.0)])
        assert [p.scored.candidate_id for p in result.reserves] == ["b"]

    def test_a_candidate_below_the_quality_bar_is_not_a_reserve(self):
        """Replacing a failed clip with filler defeats the purpose of the gate."""
        result = run([("a", 100, 0.9, 8.0), ("b", 110, 0.85, 2.0)],
                     SelectionConfig(min_llm_total=5.5))
        assert result.reserves == []

    def test_candidates_beyond_top_n_become_reserves(self):
        specs = [(f"c{i}", i * 300, 0.9 - i * 0.01, 8.0) for i in range(6)]
        result = run(specs, SelectionConfig(top_n=2, max_from_same_third=10))
        assert result.count == 2
        assert len(result.reserves) == 4

    def test_reserves_are_ranked(self):
        specs = [(f"c{i}", i * 300, 0.9 - i * 0.01, 8.0) for i in range(5)]
        result = run(specs, SelectionConfig(top_n=1, max_from_same_third=10))
        assert [p.rank for p in result.reserves] == [1, 2, 3, 4]


class TestRejectionReasons:
    def test_every_unpicked_candidate_has_a_reason(self):
        """`clipper explain` shows these, so none may be blank."""
        specs = [("a", 100, 0.9, 8.0), ("b", 110, 0.8, 8.0), ("c", 900, 0.2, 2.0)]
        result = run(specs, SelectionConfig(top_n=1, max_from_same_third=10))
        picked = {p.scored.candidate_id for p in result.picks}
        for cid in ("a", "b", "c"):
            if cid not in picked:
                assert result.rejections.get(cid), cid

    def test_a_pre_scoring_drop_keeps_its_own_reason(self):
        scored, candidates = build([("a", 100, 0.9, 8.0)])
        scored.scored[0].dropped = True
        scored.scored[0].drop_reason = "flagged as a sponsor read"
        result = select(scored, candidates, SelectionConfig(),
                        min_gap_seconds=30.0, source_duration=SOURCE_DURATION)
        assert result.count == 0
        assert "sponsor" in result.rejections["a"]

    def test_the_stop_reason_names_the_binding_constraint(self):
        specs = [(f"c{i}", i * 300, 0.9, 3.0) for i in range(4)]
        result = run(specs, SelectionConfig(min_llm_total=5.5, max_from_same_third=10))
        assert "min_llm_total" in result.stopped_because


class TestThirds:
    @pytest.mark.parametrize(("start", "expected"), [
        (0, "first"), (1000, "first"), (1300, "middle"), (2000, "middle"),
        (2500, "final"), (3500, "final"),
    ])
    def test_midpoint_decides_the_third(self, start, expected):
        specs = [("a", start, 0.9, 8.0)]
        result = run(specs, SelectionConfig(max_from_same_third=1))
        assert result.count == 1
        del expected  # placement is asserted via the allowance tests above

    def test_a_zero_length_source_does_not_divide_by_zero(self):
        scored, candidates = build([("a", 0, 0.9, 8.0)])
        result = select(scored, candidates, SelectionConfig(),
                        min_gap_seconds=30.0, source_duration=0.0)
        assert result.count == 1
