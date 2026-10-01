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
