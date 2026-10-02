"""Choosing the line a clip opens on (candidates/opening.py, D93)."""

from __future__ import annotations

import json
from types import SimpleNamespace

from clipper.candidates import opening
from clipper.models import Candidate, Sentence


def _sentences(lines: list[tuple[float, float, str]]) -> list[Sentence]:
    out, previous_end = [], 0.0
    for i, (start, end, text) in enumerate(lines):
        out.append(Sentence(index=i, start=start, end=end, text=text, word_indices=(i, i + 1),
                            gap_before=start - previous_end))
        previous_end = end
    return out


SENTS = _sentences([
    (0.0, 2.0, "Should I just pee on him?"),
    (2.3, 3.0, "Yeah, I"),
    (3.1, 4.0, "vote yes."),
    (4.2, 6.0, "Right, yep, I'm saying yes."),
    (6.2, 9.0, "You said I should!"),
    (9.2, 30.0, "I'm not sure that I thought it through!"),
])


def _candidate(lo: int, hi: int = 6, scene_start: float | None = None) -> Candidate:
    return Candidate(candidate_id="c1", start=SENTS[lo].start, end=SENTS[hi - 1].end,
                     sentence_indices=(lo, hi), text="", scene_start=scene_start, scene_end=30.0)


class _Backend:
    name = "fake"

    def __init__(self, answer):
        self.answer, self.asked = answer, []

    def cache_model(self):
        return "fake"

    def describe(self):
        return "fake"

    def complete(self, request):
        self.asked.append(request.user)
        return SimpleNamespace(text=json.dumps(self.answer), model="fake")


def test_lines_that_continue_an_earlier_one_are_never_offered():
    found = opening.options(_candidate(2), SENTS, min_seconds=10, max_seconds=60)
    assert found is not None
    assert 2 not in found.allowed  # "vote yes." continues "Yeah, I"
    assert 0 in found.allowed and 1 in found.allowed


def test_the_ai_can_move_a_clip_back_to_its_setup():
    backend = _Backend([{"clip": 1, "start_line": 0}])
    starts = opening.choose([_candidate(2)], SENTS, [backend], min_seconds=10, max_seconds=60)
    assert starts == {"c1": opening.Opening(0)}
    assert "CURRENT START" in backend.asked[0] and "before the clip" in backend.asked[0]


def test_without_an_answer_a_mid_sentence_start_goes_back_to_its_sentence():
    starts = opening.choose([_candidate(2)], SENTS, [], min_seconds=10, max_seconds=60)
    assert starts == {"c1": opening.Opening(1)}


def test_a_choice_outside_the_allowed_lines_is_ignored():
    backend = _Backend([{"clip": 1, "start_line": 5}])  # would leave a 21s clip under 25s
    starts = opening.choose([_candidate(0)], SENTS, [backend], min_seconds=25, max_seconds=60)
    assert starts == {}


def test_the_clip_keeps_within_the_longest_allowed():
    found = opening.options(_candidate(3), SENTS, min_seconds=10, max_seconds=27)
    assert found is not None and min(found.allowed) == 3  # line 1 would make it 27.7s


def test_reaching_back_across_a_camera_cut_needs_the_talk_to_run_on():
    # A cut at 4.1s with only 0.2s of quiet: still one exchange.
    found = opening.options(_candidate(3, scene_start=4.1), SENTS, min_seconds=10, max_seconds=60)
    assert 1 in found.allowed
    moved = opening.moved(_candidate(3, scene_start=4.1), 1, SENTS)
    assert moved.start == 2.3 and moved.scene_start < 2.3 and moved.sentence_indices == (1, 6)


def test_a_payoff_far_enough_in_is_marked_to_open_on():
    # Line 5 ("I'm not sure that I thought it through!") is 20.8s long here: too long to tease.
    sents = [*SENTS[:5], Sentence(index=5, start=9.2, end=11.0, text="I'm not sure that I thought it through!",
                                  word_indices=(5, 6), gap_before=0.2)]
    clip = Candidate(candidate_id="c1", start=0.0, end=11.0, sentence_indices=(0, 6), text="")
    pick = SimpleNamespace(candidate=clip)
    opening.apply([pick], sents, [_Backend([{"clip": 1, "start_line": 0, "payoff_line": 5, "payoff_strength": 9}])],
                  min_seconds=5, max_seconds=60)
    assert pick.candidate.payoff == (9.2, 11.0)


def test_a_payoff_too_early_too_long_or_turned_off_is_dropped():
    def tease(answer, payoff=True, max_seconds=60):
        answer = {"payoff_strength": 9, **answer}
        found = opening.choose([_candidate(0)], SENTS, [_Backend([answer])], min_seconds=10,
                               max_seconds=max_seconds, payoff=payoff)
        return found.get("c1", opening.Opening(0)).payoff

    assert tease({"clip": 1, "start_line": 0, "payoff_line": 3}) == 3      # 1.8s long, 4.2s in: fits
    assert tease({"clip": 1, "start_line": 0, "payoff_line": 5}) is None   # 20.8s long: not a teaser
    assert tease({"clip": 1, "start_line": 0, "payoff_line": 1}) is None   # 2.3s in: the clip opens on it
    assert tease({"clip": 1, "start_line": 0, "payoff_line": 4}, payoff=False) is None
    assert tease({"clip": 1, "start_line": 0, "payoff_line": 4}) == 4
    assert tease({"clip": 1, "start_line": 0, "payoff_line": 4}, max_seconds=31) is None  # 30s + 2.8s
    assert tease({"clip": 1, "start_line": 0, "payoff_line": 4, "payoff_strength": 6}) is None  # not strong enough


def test_apply_moves_the_pick_in_place():
    pick = SimpleNamespace(candidate=_candidate(2))
    n = opening.apply([pick], SENTS, [_Backend([{"clip": 1, "start_line": 0}])],
                      min_seconds=10, max_seconds=60)
    assert n == 1 and pick.candidate.start == 0.0 and "pee on him" in pick.candidate.text
