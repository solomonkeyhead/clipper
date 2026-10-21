"""What clipping an hour costs in AI, cut without changing what is judged (D232)."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from clipper import pipeline, runner
from clipper.campaign import description
from clipper.config import CampaignConfig, Config, LLMConfig
from clipper.llm import usage
from clipper.llm.base import LLMConfigError, LLMError, MockBackend
from clipper.llm.cache import LLMCache
from clipper.models import Candidate, SignalValues
from clipper.signals import visual
from clipper.signals.llm import score_candidates

REPO = Path(__file__).parents[2]
SAMPLE = CampaignConfig.load(REPO / "tests" / "fixtures" / "sample-show.yaml")
GOOD = ("On Sample Show, one lead confronts the other on the bench before the last game, and "
        "he admits he stayed for her. A quiet Ricky and Russ moment from a streaming romance.")


def cand(i: int, quiet: bool = False) -> Candidate:
    return Candidate(candidate_id=f"c{i}", start=i * 100.0, end=i * 100.0 + 30.0, sentence_indices=(i, i + 1),
                     text="" if quiet else f"Why did nobody tell me the secret {i}?", quiet=quiet)


class TestSecondOpinion:
    def test_prompt_b_goes_to_the_second_model_and_a_stays(self):
        a, b = MockBackend(model="opus"), MockBackend(model="haiku")
        result = score_candidates([cand(1), cand(2)], a, LLMConfig(), cache=LLMCache(enabled=False), second=b)
        assert {r.job for r in a.calls} == {"judge_a"} and {r.job for r in b.calls} == {"judge_b"}
        assert all(x is not None and y is not None for x, y in result.scores.values())

    def test_without_one_the_judge_answers_both(self):
        a = MockBackend()
        score_candidates([cand(1)], a, LLMConfig(), cache=LLMCache(enabled=False))
        assert [r.job for r in a.calls] == ["judge_a", "judge_b"]

    def test_a_second_model_that_cant_run_leaves_b_to_the_judge(self):
        class Broken(MockBackend):
            def _complete(self, request):
                raise LLMConfigError("not logged in")

        a = MockBackend()
        score_candidates([cand(1)], a, LLMConfig(), cache=LLMCache(enabled=False), second=Broken())
        assert [r.job for r in a.calls] == ["judge_a", "judge_b"]

    def test_the_second_backend_follows_the_judges_rules_with_its_own_model(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
        monkeypatch.setenv("CLIPPER_HOSTED", "1")   # hosted: the API, never a plan
        monkeypatch.setattr(pipeline, "create_backend", lambda name, **kw: (name, kw["model"]))
        config = Config()
        config = config.model_copy(update={"llm": config.llm.model_copy(update={"judge_model": "claude-opus-5-5"})})
        assert pipeline.judge_backend(config, model="claude-haiku-5-5") == ("anthropic", "claude-haiku-5-5")
        assert pipeline.judge_backend(config) == ("anthropic", "claude-opus-5-5")


class TestWatch:
    def test_at_most_watch_moments_twice_the_cap_at_least_the_shortlist(self):
        llm = LLMConfig()
        assert (runner.watch_size(500, llm), runner.watch_size(10, llm), runner.watch_size(3, llm)) == (15, 15, 12)

    def test_quiet_moments_count_inside_the_shortlist(self, data_root, monkeypatch):
        monkeypatch.setattr(visual, "cut", lambda *a: b"mp4")
        verdict = json.dumps(dict.fromkeys(visual.RUBRIC, 6) | {"visual_payoff": 3})
        backend = MockBackend(responses=[verdict] * 5)
        items = [cand(1), cand(2), cand(3), cand(4, quiet=True)]
        values = [SignalValues(candidate_id=f"c{i}", llm_total=float(i)) for i in (1, 2, 3)]
        seen = visual.watch_candidates(items, values, data_root / "s.mp4", backend,
                                       LLMConfig(watch_shortlist=3), source_id="s")
        assert sorted(seen) == ["c2", "c3", "c4"] and len(backend.calls) == 3

    def test_the_clip_watched_has_no_sound(self, tmp_path, monkeypatch):
        args = []

        def run(a, timeout):
            args.extend(a)
            Path(a[-1]).write_bytes(b"x")
        monkeypatch.setattr(visual.ffmpeg, "run", run)
        visual.cut(tmp_path / "s.mp4", 10.0, 20.0, tmp_path / "o.mp4")
        assert "-an" in args and "-c:a" not in args
        assert "without sound" in visual.SYSTEM


class TestDescriptions:
    clips = tuple((f"You can't be incriminated, said number {i}.", f"line {i} #ad", "") for i in range(3))

    def test_a_batch_sends_the_brief_once_and_each_clip_finds_its_own(self, data_root):
        cache = LLMCache(data_root / "cache")
        answer = {"descriptions": [{"clip": n, "description": f"{GOOD} ({n})"} for n in (1, 2, 3)]}
        batch = MockBackend(responses=[json.dumps(answer)])
        description.describe_many(self.clips, SAMPLE, [batch], cache=cache)
        assert len(batch.calls) == 1 and batch.calls[0].user.count("THE CAMPAIGN'S BRIEF") == 1
        silent = MockBackend(responses=[])
        silent._complete = lambda request: pytest.fail("asked again")
        got = [description.describe(t, SAMPLE, line, [silent, batch], cache=cache, hook=h)
               for t, line, h in self.clips]
        assert got == [f"{GOOD} ({n})" for n in (1, 2, 3)]

    def test_a_failed_batch_leaves_each_clip_to_its_own_call(self, data_root):
        class Down(MockBackend):
            def _complete(self, request):
                raise LLMError("busy")

        cache = LLMCache(data_root / "cache")
        description.describe_many(self.clips, SAMPLE, [Down(max_retries=0)], cache=cache)
        own = MockBackend(responses=[json.dumps({"description": GOOD})])
        text, line, _ = self.clips[0]
        assert description.describe(text, SAMPLE, line, [own], cache=cache) == GOOD and len(own.calls) == 1

    def test_one_clip_alone_is_not_batched(self, data_root):
        backend = MockBackend()
        description.describe_many(self.clips[:1], SAMPLE, [backend], cache=LLMCache(data_root / "cache"))
        assert backend.calls == []


def test_hosted_caption_fixes_go_straight_to_the_scoring_model(monkeypatch):
    monkeypatch.setattr(runner, "create_backend", lambda name=None, **kw: SimpleNamespace(model=kw["model"]))
    config = Config()
    config = config.model_copy(update={"llm": config.llm.model_copy(update={"correction_model": "flash"})})
    monkeypatch.setattr(runner, "build_backend", lambda config, override=None: SimpleNamespace(model="lite", name="g"))
    assert [b.model for b in runner._correction_backends(config, None)] == ["flash", "lite"]
    monkeypatch.setenv("CLIPPER_HOSTED", "1")
    assert [b.model for b in runner._correction_backends(config, None)] == ["lite"]


class TestPinnedTaste:
    def config(self, taste: str, focus: str = "") -> Config:
        config = Config()
        return config.model_copy(update={"llm": config.llm.model_copy(
            update={"user_taste": taste, "campaign_focus": focus})})

    def test_a_rerun_keeps_the_first_taste_until_the_campaign_changes(self, tmp_path):
        first = runner.pinned_taste(self.config("likes A"), SAMPLE, tmp_path, set())
        assert first.llm.user_taste == "likes A"
        again = runner.pinned_taste(self.config("likes A and B"), SAMPLE, tmp_path, set())
        assert again.llm.user_taste == "likes A"
        refocused = runner.pinned_taste(self.config("likes A and B", focus="only the finale"), SAMPLE, tmp_path, set())
        assert refocused.llm.user_taste == "likes A and B"
        forced = runner.pinned_taste(self.config("likes C"), SAMPLE, tmp_path, {"signals", "combine"})
        assert forced.llm.user_taste == "likes C"


def test_watch_threads_carry_the_jobs_usage_tag(data_root, monkeypatch):
    monkeypatch.setattr(visual, "cut", lambda *a: b"mp4")
    seen = []

    class Tagged(MockBackend):
        def _complete(self, request):
            seen.append(usage._ref.get())
            return super()._complete(request)

    verdict = json.dumps(dict.fromkeys(visual.RUBRIC, 6) | {"visual_payoff": 3})
    values = [SignalValues(candidate_id=f"c{i}", llm_total=5.0) for i in (1, 2)]
    with usage.tag("source:abc"):
        visual.watch_candidates([cand(1), cand(2)], values, data_root / "s.mp4", Tagged(responses=[verdict] * 2),
                                LLMConfig(), source_id="s")
    assert seen == ["source:abc", "source:abc"]


def _judge_eval():
    spec = importlib.util.spec_from_file_location("judge_eval", REPO / "scripts" / "cost" / "judge_eval.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestJudgeEval:
    def test_a_candidate_takes_the_rating_of_the_clip_it_mostly_lies_in(self):
        ev = _judge_eval()
        got = ev.match_ratings([("a", 0, 30), ("b", 25, 55), ("c", 100, 130)], [(0, 32, 5.0), (40, 60, 1.0)])
        assert got == {"a": 5.0, "b": 1.0}

    def test_agreement_is_rank_correlation_and_top_k_overlap(self):
        ev = _judge_eval()
        ratings = {"a": 5.0, "b": 4.0, "c": 2.0, "d": 1.0}
        assert ev.agreement({"a": 8.0, "b": 7.0, "c": 4.0, "d": 3.0}, ratings, k=2) == (1.0, 1.0, 4)
        rho, top, n = ev.agreement({"a": 3.0, "b": 4.0, "c": 7.0, "d": 8.0}, ratings, k=2)
        assert (rho, top, n) == (-1.0, 0.0, 4)
        assert ev.top_k_overlap({"a": 9.0, "c": 8.0, "b": 1.0}, ratings, k=2) == 0.5
        assert ev.top_k_overlap({}, ratings, k=2) is None
