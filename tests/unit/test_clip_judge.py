"""Claude judges the moments, Gemini fills in what it can't (D139)."""

from __future__ import annotations

from types import SimpleNamespace

from clipper import pipeline
from clipper.config import Config
from clipper.llm.base import LLMConfigError
from clipper.llm.claude_code import _json_in
from clipper.signals.llm import LLMSignalResult


def items(*ids):
    return [SimpleNamespace(candidate_id=i) for i in ids]


def fake_scoring(monkeypatch, answers):
    """score_candidates answered per backend name: a set of ids it scores, or an exception."""
    asked = []

    def score(cands, backend, cfg, **kw):
        asked.append((backend.name, [c.candidate_id for c in cands]))
        got = answers[backend.name]
        if isinstance(got, Exception):
            raise got
        out = LLMSignalResult()
        for c in cands:
            if c.candidate_id in got:
                out.scores[c.candidate_id], out.totals[c.candidate_id] = ("a", "b"), 7.0
            else:
                out.unscored.append(c.candidate_id)
        return out

    monkeypatch.setattr(pipeline.llm_signal, "score_candidates", score)
    monkeypatch.setattr(pipeline, "_few_shot_examples", lambda config: None)
    return asked


judge, gemini = (SimpleNamespace(name=n, describe=lambda n=n: n) for n in ("claude_code", "gemini"))


def test_the_judge_scores_and_gemini_fills_only_the_gaps(monkeypatch):
    asked = fake_scoring(monkeypatch, {"claude_code": {"c1"}, "gemini": {"c2"}})
    got = pipeline._judged(items("c1", "c2"), Config(), gemini, judge, None)
    assert asked == [("claude_code", ["c1", "c2"]), ("gemini", ["c2"])]
    assert set(got.totals) == {"c1", "c2"} and got.unscored == []


def test_no_claude_or_claude_not_set_up_means_gemini_judges_everything(monkeypatch):
    asked = fake_scoring(monkeypatch, {"claude_code": LLMConfigError("not logged in"), "gemini": {"c1"}})
    assert set(pipeline._judged(items("c1"), Config(), gemini, judge, None).totals) == {"c1"}
    assert set(pipeline._judged(items("c1"), Config(), gemini, None, None).totals) == {"c1"}
    assert [name for name, _ in asked] == ["claude_code", "gemini", "gemini"]


def test_a_list_answer_wrapped_in_words_or_a_fence_still_parses():
    assert _json_in('Here you go:\n```json\n[{"a": 1}, {"a": 2}]\n```', "[") == '[{"a": 1}, {"a": 2}]'


def test_a_rerun_leaves_out_moments_already_rated_not_good(data_root):
    """D141: rerunning Love and Justice offered 3 of the 4 moments rated 1 star."""
    from clipper import runner
    from clipper.studio import db

    with db.connect() as con:
        for clip_id, start, end, rating in (("a", 100.0, 130.0, 1), ("b", 200.0, 230.0, 5)):
            row = db.upsert_clip(con, {"campaign": "c", "source_id": "src", "clip_id": clip_id,
                                       "start_s": start, "end_s": end, "file": f"{clip_id}.mp4"})
            db.set_rating(con, row, rating, [])
    moments = {"m1": (105.0, 135.0), "m2": (125.0, 160.0), "m3": (200.0, 230.0)}
    outcome = SimpleNamespace(
        info=SimpleNamespace(source_id="src"),
        candidates=SimpleNamespace(candidates=[SimpleNamespace(candidate_id=k, start=a, end=b)
                                               for k, (a, b) in moments.items()]),
        scored=SimpleNamespace(scored=[SimpleNamespace(candidate_id=k) for k in moments]))
    runner._skip_rejected(outcome)
    # m1 is mostly the 1-star clip; m2 only grazes it; m3 is the 5-star one.
    assert [s.candidate_id for s in outcome.scored.scored] == ["m2", "m3"]


def test_the_paid_api_key_never_judges_moments_unless_allowed(monkeypatch):
    """D142: with a key set, judging still runs on the Claude plan, not the billed API."""
    from clipper.llm import claude_code

    made = []
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    monkeypatch.setattr(claude_code, "cli", lambda: "claude")
    monkeypatch.setattr(pipeline, "_claude_judge_on", lambda: True)
    monkeypatch.setattr(pipeline, "create_backend", lambda name, **kw: made.append(name) or name)
    config = Config()
    config = config.model_copy(update={"llm": config.llm.model_copy(update={"judge_model": "claude-opus-5-5"})})
    assert pipeline.judge_backend(config) == "claude_code"
    allowed = config.model_copy(update={"llm": config.llm.model_copy(update={"paid_api_jobs": ["sketch", "judge"]})})
    assert pipeline.judge_backend(allowed) == "anthropic" and made == ["claude_code", "anthropic"]
