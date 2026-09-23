"""A reserve standing in for a rejected clip must not duplicate a kept one.

Measured on two real TV episodes: after a clip failed QA, its replacement was
taken straight off the reserve list and overlapped an accepted clip by 45s and
35s -- near-duplicates, which the campaign's rules forbid on one account.
"""

from __future__ import annotations

from types import SimpleNamespace

from clipper.models import Candidate, ScoredCandidate
from clipper.runner import _next_reserve
from clipper.select.pick import Pick


def pick(cid: str, start: float, end: float) -> Pick:
    return Pick(candidate=Candidate(candidate_id=cid, start=start, end=end,
                                    sentence_indices=(0, 1), text="x"),
                scored=ScoredCandidate(candidate_id=cid, composite=0.5), rank=1)


def accepted(start: float, end: float):
    return SimpleNamespace(plan=SimpleNamespace(start=start, end=end))


def result(*clips):
    return SimpleNamespace(accepted=list(clips))


class TestNextReserve:
    def test_the_measured_case_is_skipped(self):
        """Accepted 511-566; the first reserve 522-571 shares 45s of it."""
        spare = [pick("c029", 522, 571), pick("c040", 200, 250)]
        chosen = _next_reserve(spare, result(accepted(511, 566)), [], 30)
        assert chosen.candidate.candidate_id == "c040"

    def test_the_minimum_gap_is_respected(self):
        spare = [pick("near", 580, 620), pick("far", 700, 740)]
        chosen = _next_reserve(spare, result(accepted(511, 566)), [], 30)
        assert chosen.candidate.candidate_id == "far"

    def test_queued_picks_count_too(self):
        spare = [pick("clash", 100, 140), pick("ok", 400, 440)]
        chosen = _next_reserve(spare, result(), [pick("queued", 110, 150)], 30)
        assert chosen.candidate.candidate_id == "ok"

    def test_the_chosen_reserve_is_removed_from_the_list(self):
        spare = [pick("clash", 520, 560), pick("ok", 100, 140)]
        _next_reserve(spare, result(accepted(511, 566)), [], 30)
        assert [p.candidate.candidate_id for p in spare] == ["clash"]

    def test_none_when_every_reserve_clashes(self):
        spare = [pick("a", 520, 560), pick("b", 530, 570)]
        assert _next_reserve(spare, result(accepted(511, 566)), [], 30) is None
