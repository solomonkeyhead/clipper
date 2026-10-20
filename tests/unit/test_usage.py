"""What AI calls cost, the budget stop, effort per job and no personal Claude plan on a server (D227)."""

from __future__ import annotations

import json
import sqlite3
import time
from types import SimpleNamespace

import pytest

from clipper.llm import usage
from clipper.llm.base import LLMConfigError, LLMRequest, MockBackend


def _rows() -> list[tuple]:
    with sqlite3.connect(usage.db_path()) as db:
        return db.execute("SELECT provider, model, job, ref, input_tokens, output_tokens, cache_read, cache_write, "
                          "usd FROM ai_calls").fetchall()


class TestPrices:
    def test_tokens_and_cache_are_priced_per_million(self):
        usd = usage.record("anthropic", "claude-opus-5-5", "sketch", input_tokens=1_000_000, output_tokens=100_000,
                           cache_read=1_000_000, cache_write=100_000)
        assert usd == pytest.approx(4.0 + 2.0 + 0.2 + 0.5)
        assert _rows()[0][:3] == ("anthropic", "claude-opus-5-5", "sketch")

    def test_audio_is_priced_per_hour(self):
        assert usage.record("groq", "whisper-large-v3-turbo", "transcribe", audio_seconds=1800) == pytest.approx(0.02)

    def test_an_unknown_model_costs_its_providers_dearest(self):
        assert usage.record("gemini", "gemini-9-flash", "", input_tokens=1_000_000) == pytest.approx(0.75)
        assert usage.record("claude_code", "claude-next", "", output_tokens=1_000_000) == pytest.approx(20.0)
        assert usage.record("ollama", "llama", "", input_tokens=1_000_000) == 0.0

    def test_a_tag_names_every_call_inside_it(self):
        with usage.tag("video:33"):
            usage.record("gemini", "gemini-3.8-flash", "script", input_tokens=10)
        usage.record("gemini", "gemini-3.8-flash", "script", input_tokens=10)
        assert [r[3] for r in _rows()] == ["video:33", None]
        (row,) = usage.summary(0)
        assert (row["job"], row["calls"], row["input_tokens"]) == ("script", 2, 20)

    def test_a_failed_write_never_raises(self, monkeypatch):
        monkeypatch.setattr(usage, "_connect", lambda: (_ for _ in ()).throw(sqlite3.OperationalError("disk full")))
        assert usage.record("anthropic", "claude-haiku-5-5", "x", input_tokens=1_000_000) == pytest.approx(0.1)


class TestBudget:
    def _plan(self, root, budget):
        (root / "plan.json").write_text(json.dumps({"plan": "free", "period_start": "2026-10-01T00:00:00Z",
                                                    "period_end": "2026-11-01T00:00:00Z", "ai_budget_usd": budget}))

    def test_no_plan_no_limit(self, data_root):
        usage.record("anthropic", "claude-opus-5-5", "", output_tokens=10_000_000)
        usage.before_call("sketch")

    def test_a_spent_budget_stops_the_call_before_it_is_sent(self, data_root):
        self._plan(data_root, 1.0)
        usage.before_call("sketch")   # nothing spent yet
        usage.record("anthropic", "claude-opus-5-5", "sketch", output_tokens=50_000)   # $1.00
        backend = MockBackend()
        with pytest.raises(usage.BudgetExceeded, match="used up"):
            backend.complete(LLMRequest(system="s", user="u", job="sketch"))
        assert backend.calls == []

    def test_spending_before_the_period_doesnt_count(self, data_root):
        self._plan(data_root, 1.0)
        usage.record("anthropic", "claude-opus-5-5", "sketch", output_tokens=500_000)
        with sqlite3.connect(usage.db_path()) as db:
            db.execute("UPDATE ai_calls SET at = ?", (time.time() - 90 * 86400,))
        usage.before_call("sketch")
        assert usage.spent_usd(0) == pytest.approx(10.0)

    def test_an_unreadable_plan_stops_too(self, data_root):
        (data_root / "plan.json").write_text("{not json")
        with pytest.raises(usage.BudgetExceeded):
            usage.before_call("sketch")

    def test_create_doesnt_pass_a_spent_budget_on_to_another_model(self, data_root, monkeypatch):
        from clipper.create import ai

        self._plan(data_root, 0.0)
        first, second = MockBackend(model="a"), MockBackend(model="b")
        monkeypatch.setattr(ai, "_ordered", lambda config, job, tier: ([first, second], [], "", False))
        with pytest.raises(usage.BudgetExceeded):
            ai.ask("s", "u", None, temperature=0.0, job="sketch")
        assert first.calls == [] and second.calls == []


class TestAnthropic:
    def _backend(self, sent):
        from clipper.llm.anthropic_backend import AnthropicBackend

        class Messages:
            def create(self, **kw):
                sent.update(kw)
                return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text="ok")],
                                       usage=SimpleNamespace(input_tokens=100, output_tokens=20,
                                                             cache_read_input_tokens=1000,
                                                             cache_creation_input_tokens=None))

        backend = AnthropicBackend(api_key="test", requests_per_minute=100_000, model="claude-sonnet-5-5")
        backend._client = SimpleNamespace(messages=Messages())
        return backend

    def test_the_jobs_effort_is_sent_and_the_usage_recorded(self, data_root):
        sent: dict = {}
        self._backend(sent).complete(LLMRequest(system="s", user="u", job="judge_a"))
        assert sent["output_config"] == {"effort": "low"}
        assert _rows() == [("anthropic", "claude-sonnet-5-5", "judge_a", None, 100, 20, 1000, 0,
                            pytest.approx((100 * 2 + 20 * 10 + 1000 * 0.2) / 1e6))]

    def test_an_unnamed_job_gets_the_models_own_effort(self, data_root):
        sent: dict = {}
        self._backend(sent).complete(LLMRequest(system="s", user="u"))
        assert "output_config" not in sent


class TestClaudeCode:
    def test_usage_and_effort_through_the_json_output(self, data_root, monkeypatch):
        from clipper.llm import claude_code

        monkeypatch.setattr(claude_code, "cli", lambda: "claude")
        seen = {}

        def run(args, **kw):
            seen["args"] = args
            return SimpleNamespace(returncode=0, stderr="", stdout=json.dumps({
                "is_error": False, "result": "the answer",
                "usage": {"input_tokens": 7, "output_tokens": 300, "cache_read_input_tokens": 5000,
                          "cache_creation_input_tokens": 2000}}))

        monkeypatch.setattr(claude_code.subprocess, "run", run)
        backend = claude_code.ClaudeCodeBackend(requests_per_minute=100_000)
        reply = backend.complete(LLMRequest(system="s", user="u", job="sketch"))
        assert reply.text == "the answer" and reply.output_tokens == 300
        assert seen["args"][seen["args"].index("--effort") + 1] == "medium"
        assert _rows()[0][:8] == ("claude_code", "claude-opus-5-5", "sketch", None, 7, 300, 5000, 2000)


class TestGemini:
    def test_cached_and_thinking_tokens(self, monkeypatch):
        from clipper.llm.gemini import GeminiBackend

        response = SimpleNamespace(text='{"ok": true}', usage_metadata=SimpleNamespace(
            prompt_token_count=1000, cached_content_token_count=200, candidates_token_count=50,
            thoughts_token_count=30))
        backend = GeminiBackend(api_key="test", model="gemini-3.8-flash", requests_per_minute=100_000)
        backend._client = SimpleNamespace(models=SimpleNamespace(generate_content=lambda **kw: response))
        reply = backend.complete(LLMRequest(system="s", user="u", job="script"))
        assert (reply.prompt_tokens, reply.output_tokens) == (1000, 80)
        assert _rows()[0][:8] == ("gemini", "gemini-3.8-flash", "script", None, 800, 80, 200, 0)


class TestHosted:
    @pytest.fixture
    def hosted(self, monkeypatch, tmp_path):
        monkeypatch.undo()   # the suite's stand-in for claude_code.cli: this test needs the real one
        monkeypatch.setattr(usage, "db_path", lambda: tmp_path / "usage.sqlite")
        monkeypatch.setenv("CLIPPER_HOSTED", "1")
        from clipper.llm import claude_code

        monkeypatch.setattr(claude_code, "_said_hosted", False)
        return monkeypatch

    def test_no_claude_code_on_a_server(self, hosted):
        from clipper.llm import claude_code

        assert claude_code.cli() is None
        with pytest.raises(LLMConfigError):
            claude_code.ClaudeCodeBackend()

    def test_every_create_job_may_use_the_key(self, hosted, data_root):
        from clipper.config import Config
        from clipper.create import ai

        hosted.setenv("ANTHROPIC_API_KEY", "test")
        config = Config.load()
        assert "script" not in config.llm.paid_api_jobs
        assert ai._default_backends(config, job="script")[0].name == "anthropic"
        assert "claude_code" not in ai.misses

    def test_the_judge_uses_the_key(self, hosted, data_root):
        from clipper.config import Config
        from clipper.pipeline import judge_backend

        hosted.setenv("ANTHROPIC_API_KEY", "test")
        judge = judge_backend(Config.load())
        assert judge is not None and judge.name == "anthropic"


def test_tiers_pick_their_models(data_root):
    from clipper.config import Config
    from clipper.create import ai

    config = Config.load()
    assert ai._tier_model(config, "") is None   # create_model
    assert ai._tier_model(config, "cheap") == config.llm.create_cheap_model == "claude-haiku-5-5"
    assert ai._tier_model(config, "review") == "claude-sonnet-5-5"
    assert ai._tier_model(config, "quick") == config.llm.create_quick_model
    with pytest.raises(ValueError):
        ai._tier_model(config, "fancy")


def test_the_new_settings_dont_redo_cached_stages():
    from clipper.config import Config
    from clipper.pipeline import stage_keys

    config = Config()
    changed = config.model_copy(update={"llm": config.llm.model_copy(update={"effort": {}, "review_model": "x"})})
    assert stage_keys(config, None) == stage_keys(changed, None)
