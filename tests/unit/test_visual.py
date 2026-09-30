"""The "watch it" pass: which moments are watched, how the verdict is used.

No video is encoded: `cut` is replaced, and the mock backend answers.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from clipper import pipeline, runner
from clipper.config import Config, LLMConfig
from clipper.llm.base import MockBackend
from clipper.llm.cache import LLMCache
from clipper.models import Candidate, RubricScores, Signals, SignalValues
from clipper.select.pick import _quality_gate
from clipper.signals import visual
from clipper.signals.combine import combine


def cand(i: int) -> Candidate:
    return Candidate(candidate_id=f"c{i}", start=i * 100.0, end=i * 100.0 + 30.0,
                     sentence_indices=(i, i + 1), text=f"Black or pinto? Black. What are you saying {i}?")


def value(i: int, total: float | None, **kw) -> SignalValues:
    return SignalValues(candidate_id=f"c{i}", llm_total=total, **kw)


def verdict(score: int = 8, payoff: int = 9, **kw) -> str:
    base = dict(hook_strength=score, standalone_clarity=score, payoff=score, emotional_intensity=score,
                quotability=score, ending_completeness=score, visual_payoff=payoff,
                sees="The host glances at the one Black guest.", hook_text="He looked RIGHT at him")
    return json.dumps({**base, **kw})


@pytest.fixture
def no_ffmpeg(monkeypatch):
    cuts = []

    def cut(source, start, end, out):
        cuts.append((start, end))
        return b"fake-mp4"
    monkeypatch.setattr(visual, "cut", cut)
    monkeypatch.setattr(visual, "WORKERS", 1)  # the mock's scripted answers come in order
    return cuts


class TestShortlist:
    def test_best_totals_first_dropped_and_unscored_left_out(self):
        values = [value(0, 4.0), value(1, 7.5), value(2, 9.0, dropped=True), value(3, None), value(4, 6.0)]
        assert visual.shortlist(values, {f"c{i}": cand(i) for i in range(5)}, 2) == (["c1", "c4"], {})

    def test_overlapping_windows_are_watched_once(self):
        a = cand(1)
        b = Candidate(candidate_id="b", start=105.0, end=128.0, sentence_indices=(1, 2), text="same scene")
        values = [value(1, 7.0), SignalValues(candidate_id="b", llm_total=6.9), value(4, 5.0)]
        chosen, same = visual.shortlist(values, {"c1": a, "b": b, "c4": cand(4)}, 2)
        assert chosen == ["c1", "c4"] and same == {"b": "c1"}

    def test_blend_is_half_read_half_watched(self):
        assert visual.blend(5.0, 8.0) == 6.5
        assert visual.blend(5.0, None) == 5.0
        assert visual.blend(None, 7.0) == 7.0


class TestWatch:
    def test_the_video_goes_with_the_transcript(self, data_root, no_ffmpeg):
        backend = MockBackend(responses=[verdict()])
        seen = visual.watch_candidates([cand(1)], [value(1, 6.0)], data_root / "src.mp4", backend,
                                       LLMConfig(), source_id="s")
        assert seen["c1"].visual_payoff == 9 and "glances" in seen["c1"].sees
        request = backend.calls[0]
        assert request.media == [(b"fake-mp4", "video/mp4")] and "Black or pinto?" in request.user
        assert no_ffmpeg == [(100.0, 130.0)]

    def test_a_watched_moment_isnt_watched_twice(self, data_root, no_ffmpeg):
        cache = LLMCache(data_root / "cache")
        args = ([cand(1)], [value(1, 6.0)], data_root / "src.mp4")
        visual.watch_candidates(*args, MockBackend(responses=[verdict()]), LLMConfig(), source_id="s", cache=cache)
        again = MockBackend(responses=[])
        assert visual.watch_candidates(*args, again, LLMConfig(), source_id="s", cache=cache)["c1"]
        assert again.calls == [] and len(no_ffmpeg) == 1

    def test_off_or_unable_means_nothing_is_watched(self, data_root, no_ffmpeg):
        args = ([cand(1)], [value(1, 6.0)], data_root / "src.mp4")
        assert visual.watch_candidates(*args, MockBackend(), LLMConfig(watch_video=False), source_id="s") == {}

        class Blind(MockBackend):
            supports_video = False
        assert visual.watch_candidates(*args, Blind(), LLMConfig(), source_id="s") == {}
        assert no_ffmpeg == []

    def test_a_bad_answer_leaves_the_transcript_score(self, data_root, no_ffmpeg):
        seen = visual.watch_candidates([cand(1)], [value(1, 6.0)], data_root / "src.mp4",
                                       MockBackend(responses=["nonsense"]), LLMConfig(), source_id="s")
        assert seen == {}

    @pytest.mark.watch
    def test_the_verdict_lands_on_the_signal_values(self, data_root, no_ffmpeg):
        info = SimpleNamespace(media=SimpleNamespace(width=1920, path=str(data_root / "src.mp4")),
                               source_id="s")
        values = [value(1, 5.0), value(2, 7.0)]
        pipeline._watch(info, [cand(1), cand(2)], values, Config(),
                        MockBackend(responses=[verdict(score=9), verdict(score=4, payoff=1)]), None)
        # Watched in shortlist order: c2 (7.0) first, then c1.
        assert values[1].watched == pytest.approx(9.0) and values[1].visual_payoff == 9
        assert values[0].watched == pytest.approx(4.0) and values[0].rubric_total == pytest.approx(4.5)


class TestUse:
    def test_ranking_and_the_gate_use_the_blended_total(self):
        cfg = Config()
        values = [value(1, 5.0, watched=8.0), value(2, 6.0)]
        scored = combine(Signals(source_id="s", available=["llm"], values=values), cfg)
        first, second = scored.scored
        assert first.candidate_id == "c1" and first.raw["llm"] == 6.5
        assert first.raw["llm_read"] == 5.0 and first.raw["watched"] == 8.0
        assert "llm_read" not in second.raw
        # 5.0 read alone is under the 5.5 bar; watched, it clears it.
        assert _quality_gate(first, cfg.selection) == ""

    def test_the_watched_hook_wins_when_the_picture_carries_it(self):
        scores = RubricScores(hook_strength=5, standalone_clarity=5, payoff=5, emotional_intensity=5,
                              quotability=5, ending_completeness=5, hook_text="Dinner gets awkward")
        strong = value(1, 5.0, watched=8.0, visual_payoff=8, visual_hook="He looked RIGHT at him")
        weak = value(1, 5.0, watched=8.0, visual_payoff=3, visual_hook="He looked RIGHT at him")
        assert runner._hook(strong, scores) == "He looked RIGHT at him"
        assert runner._hook(weak, scores) == "Dinner gets awkward"
        assert runner._hook(None, None) == ""
