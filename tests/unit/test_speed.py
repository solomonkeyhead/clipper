"""Clipping faster (D75): resting models, a shared request budget, two jobs at once."""

from __future__ import annotations

import logging
import threading
import time
from types import SimpleNamespace

import pytest

from clipper.llm.base import LLMBackend, LLMError, LLMRequest, LLMResponse, RateLimiter


class Flaky(LLMBackend):
    name = "flaky"

    def __init__(self, **kw):
        super().__init__(**kw)
        self.calls = 0

    def _complete(self, request):
        self.calls += 1
        raise LLMError("503 high demand")


class Fine(LLMBackend):
    name = "fine"

    def _complete(self, request):
        return LLMResponse(text="ok", model="m")


@pytest.fixture(autouse=True)
def _no_resting():
    LLMBackend._resting.clear()
    yield
    LLMBackend._resting.clear()


def test_a_try_first_model_rests_after_failing():
    first = Flaky(model="preview", max_retries=0, requests_per_minute=100_000)
    req = LLMRequest(system="s", user="u")
    with pytest.raises(LLMError):
        first.complete(req)
    with pytest.raises(LLMError, match="resting"):
        first.complete(req)
    assert first.calls == 1  # the second clip didn't wait on it again


def test_the_main_model_never_rests():
    main = Flaky(model="main", max_retries=1, requests_per_minute=100_000)
    main._backoff = lambda attempt: 0.0
    for _ in range(2):
        with pytest.raises(LLMError, match="failed after"):
            main.complete(LLMRequest(system="s", user="u"))
    assert main.calls == 4


def test_backends_share_one_request_budget():
    a, b = Fine(requests_per_minute=7), Fine(requests_per_minute=7)
    assert a.limiter is b.limiter is RateLimiter.shared("fine", 7)


def test_two_jobs_run_at_once_and_keep_their_own_progress(monkeypatch, data_root):
    from clipper import runner
    from clipper.studio import jobs

    both_running = threading.Barrier(2, timeout=10)
    log = logging.getLogger("clipper.runner")

    def fake_run(source, **kwargs):
        log.info("selected 1 clip(s) of 1 requested")
        both_running.wait()  # deadlocks unless the two jobs overlap
        log.info("001_%s accepted: pass", source)
        return SimpleNamespace(accepted=[1], rejected=[], selection_note="", report={})

    monkeypatch.setattr(runner, "run", fake_run)
    runner_ = jobs.JobRunner(lambda *a, **k: None)
    campaign = SimpleNamespace(name="c")
    a = runner_.submit(campaign, "a.mp4", 1)
    b = runner_.submit(campaign, "b.mp4", 1)
    for _ in range(200):
        if a.status == b.status == "done":
            break
        time.sleep(0.05)
    assert a.status == b.status == "done"
    assert a.stage == b.stage == "Made 1 clip"


def test_a_job_clipper_closed_on_is_listed_as_stopped_after_a_restart(monkeypatch, data_root):
    """D175: it was kept only when it finished, so one cut off by a crash or a closed window vanished."""
    from clipper import runner
    from clipper.studio import jobs

    started, hold = threading.Event(), threading.Event()

    def fake_run(source, **kwargs):
        started.set()
        hold.wait(10)
        return SimpleNamespace(accepted=[], rejected=[], selection_note="", report={})

    monkeypatch.setattr(runner, "run", fake_run)
    before = jobs.JobRunner(lambda *a, **k: None)
    job = before.submit(SimpleNamespace(name="c"), "a.mp4", 1)
    assert started.wait(10)
    after = jobs.JobRunner(lambda *a, **k: None)     # what a restart finds
    hold.set()
    kept = next(j for j in after.list() if j["id"] == job.id)
    assert kept["status"] == "failed" and kept["stage"] == "Stopped" and "again" in kept["message"]
    assert not [j for j in after.list() if j["status"] in ("queued", "running")]   # /api/quit isn't blocked by it


class OutOfQuota(LLMBackend):
    """Gemini's free tier on 2026-10-08: "retry in 20h25m", and each call asked again (D168)."""
    name = "quota"

    def __init__(self, wait, **kw):
        super().__init__(**kw)
        self.calls, self.wait = 0, wait

    def _complete(self, request):
        from clipper.llm.base import RateLimited

        self.calls += 1
        raise RateLimited("429", retry_after=self.wait)


def test_a_day_out_of_quota_rests_even_the_main_model_and_says_until_when():
    main = OutOfQuota(73524.0, model="day", max_retries=1, requests_per_minute=100_000)
    req = LLMRequest(system="s", user="u")
    with pytest.raises(LLMError):
        main.complete(req)
    with pytest.raises(LLMError, match="out of its quota until"):
        main.complete(req)
    assert main.calls == 1   # not twice a call, and not again until the quota is back


def test_a_minute_rate_limit_rests_a_try_first_model_a_minute_not_fifteen(monkeypatch):
    first = OutOfQuota(20.0, model="minute", max_retries=0, requests_per_minute=100_000, max_wait=10)
    with pytest.raises(LLMError):
        first.complete(LLMRequest(system="s", user="u"))
    rest = LLMBackend._resting[("quota", "minute")] - time.monotonic()
    assert 50 < rest <= 60


def test_each_model_has_its_own_request_budget():
    a, b = Fine(model="x", requests_per_minute=7), Fine(model="y", requests_per_minute=7)
    assert a.limiter is not b.limiter and a.limiter is Fine(model="x", requests_per_minute=7).limiter


def test_gemini_reads_how_long_its_quota_is_out():
    from clipper.llm.gemini import _quota_line, _retry_delay

    msg = ("429 RESOURCE_EXHAUSTED. {'error': {'code': 429, 'message': 'You exceeded your current quota... \n* Quota "
           "exceeded for metric: generativelanguage.googleapis.com/generate_content_free_tier_requests, limit: 20, "
           "model: gemini-3.8-flash\nPlease retry in 20h25m24.2s.', 'details': [{'quotaId': "
           "'GenerateRequestsPerDayPerProjectPerModel-FreeTier'}, {'@type': 'type.googleapis.com/google.rpc.RetryInfo', "
           "'retryDelay': '73524s'}]}}")
    assert _retry_delay(msg) == 73524.0 and _retry_delay('"retryDelay": "36s"') == 36.0 and _retry_delay("503") is None
    assert _quota_line(msg) == "429 limit: 20, model: gemini-3.8-flash a day"


def test_the_fact_check_and_the_editors_read_are_asked_at_once(data_root, monkeypatch):
    """D168: two calls that don't need each other no longer wait in line."""
    import json

    from clipper.create import script as scripts

    both, seen = threading.Barrier(2, timeout=10), set()

    def ask(system, user, schema, **kw):
        if schema.__name__ == "WordsScript":
            return json.dumps({"lines": ["Why does your lift make you heavier?"] * 3, "title": "t"})
        if schema.__name__ == "Plan":
            return json.dumps({"beats": [{"text": "x"}] * 3})
        if kw.get("job") in ("check", "critic") and kw.get("job") not in seen:
            seen.add(kw["job"])
            both.wait()   # deadlocks unless the first check and the editor's read are asked together
        return json.dumps({"ok": True, "problems": []})

    monkeypatch.setattr(scripts, "ask", ask)
    out, _ = scripts.write_checked("Why does the lift make you heavier?")
    assert len(out.beats) == 3


def test_a_full_disk_is_said_plainly():
    """D176: ffmpeg's "No space left on device" reached the page as its raw output."""
    import errno

    from clipper.studio.jobs import failure

    assert failure(OSError(errno.ENOSPC, "x")).startswith("The disk is full")
    assert failure(RuntimeError("ffmpeg exited -28:\nError writing trailer: No space left on device")).startswith("The disk is full")
    assert failure(RuntimeError("x" * 500)) == "x" * 400


def test_a_setting_this_version_does_not_know_is_ignored_not_fatal(data_root, caplog):
    """D177: a key in the user's config.yaml the running code didn't know failed 13 jobs in a row."""
    from clipper.config import Config

    (data_root / "config.yaml").write_text("llm:\n  not_a_setting_yet: [a]\n  create_claude_only: true\nmade_up: 1\n",
                                           encoding="utf-8")
    config = Config.load()
    assert config.llm.create_claude_only is True
    assert "not_a_setting_yet" in caplog.text and "made_up" in caplog.text


def test_the_best_model_is_not_asked_twice_after_a_timeout_and_then_rests():
    """D188: an overloaded Gemini 3.8 Flash (one retry of its own) made every call of a script wait two 120 s
    timeouts on it again; it now gives up after one and sits out the next calls."""
    from clipper.llm.base import Resting

    calls = []

    class Slow(LLMBackend):
        name = "slow-d188"

        def _complete(self, request):
            calls.append(1)
            raise LLMError("Gemini request failed: The read operation timed out")

    b = Slow(model="m", max_retries=1, max_wait=10.0, requests_per_minute=100_000)
    with pytest.raises(LLMError):
        b.complete(LLMRequest(system="s", user="u"))
    assert len(calls) == 1                     # no second 120 s wait on the same model
    with pytest.raises(Resting):
        b.complete(LLMRequest(system="s", user="u"))
    assert len(calls) == 1                     # resting: the next model is asked at once
    Slow._resting.pop(("slow-d188", "m"), None)
