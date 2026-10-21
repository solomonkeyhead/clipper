"""Speech recognition through Groq (D230): chunking, stitching, the mapping to our words, retries, the usage log."""

from __future__ import annotations

import httpx
import numpy as np
import pytest

from clipper.config import TranscriptionConfig
from clipper.llm import usage
from clipper.transcribe import groq
from clipper.transcribe.whisper import TranscriptionError


@pytest.fixture(autouse=True)
def env(data_root, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    monkeypatch.setattr(groq, "_sleep", lambda s: sleeps.append(s))
    sleeps.clear()


sleeps: list[float] = []


def serve(monkeypatch, handler) -> list[httpx.Request]:
    seen: list[httpx.Request] = []

    def wrapped(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    monkeypatch.setattr(groq, "_client", lambda: httpx.Client(transport=httpx.MockTransport(wrapped)))
    return seen


def reply(words, *, language="english", segments=None, status=200, headers=None):
    segments = segments if segments is not None else [{"start": 0.0, "end": 9999.0, "avg_logprob": -0.1,
                                                      "no_speech_prob": 0.01}]
    body = {"language": language, "duration": 1.0, "text": " ".join(w[0] for w in words), "segments": segments,
            "words": [{"word": t, "start": s, "end": e} for t, s, e in words]}
    return httpx.Response(status, json=body, headers=headers)


def cfg(**kw) -> TranscriptionConfig:
    return TranscriptionConfig(backend="groq", **kw)


def encode(start, length):
    return b"x", "c.flac", "audio/flac"


class TestPlan:
    def test_short_audio_is_one_request(self):
        assert groq.plan(120.0) == [(0.0, 120.0)]

    def test_chunks_overlap_and_the_last_reaches_the_end(self):
        spans = groq.plan(1500.0)
        assert spans[0] == (0.0, 606.0)
        assert [s for s, _ in spans] == [0.0, 600.0, 1200.0]
        assert spans[-1][0] + spans[-1][1] == 1500.0

    def test_no_tiny_tail_request(self):
        """Audio ending inside the last overlap is covered by the chunk before it."""
        assert groq.plan(1203.0) == [(0.0, 606.0), (600.0, 603.0)]


class TestStitching:
    def test_overlap_words_appear_once_at_the_middle_of_the_overlap(self, monkeypatch):
        # Chunk 0 is 0-606 s, chunk 1 is 600-1000 s; the cut is at 603 s.
        # Each chunk hears "boundary" (at 602 and, relative to its own start, 2 s) and "after".
        def handler(request):
            if len(seen) == 1:
                return reply([("one", 10.0, 10.5), ("late", 600.0, 600.5), ("boundary", 601.5, 602.0),
                              ("early", 604.0, 604.5)])
            return reply([("late", 0.0, 0.5), ("boundary", 1.5, 2.0), ("early", 4.0, 4.5), ("two", 100.0, 100.5)])

        seen = serve(monkeypatch, handler)
        words, language = groq.transcribe_chunks(1000.0, encode, cfg())
        assert [w.text for w in words] == ["one", "late", "boundary", "early", "two"]
        assert [round(w.start, 1) for w in words] == [10.0, 600.0, 601.5, 604.0, 700.0]
        assert language == "en"
        assert len(seen) == 2

    def test_the_language_found_in_the_first_chunk_is_sent_for_the_rest(self, monkeypatch):
        seen = serve(monkeypatch, lambda r: reply([("hallo", 1.0, 1.5)], language="german"))
        groq.transcribe_chunks(1000.0, encode, cfg())
        assert b'name="language"' not in seen[0].content
        assert b'name="language"' in seen[1].content and b"\r\n\r\nde\r\n" in seen[1].content


class TestMapping:
    def test_request_fields(self, monkeypatch):
        seen = serve(monkeypatch, lambda r: reply([("hi", 0.0, 0.4)]))
        groq.transcribe_chunks(30.0, encode, cfg(language="en"), prompt="Vocabulary: Newton.")
        req = seen[0]
        assert req.url == "https://api.groq.com/openai/v1/audio/transcriptions"
        assert req.headers["authorization"] == "Bearer test-key"
        body = req.content
        for needle in (b"whisper-large-v3-turbo", b"verbose_json", b"timestamp_granularities[]", b"word", b"segment",
                       b"Vocabulary: Newton.", b'name="language"'):
            assert needle in body

    def test_words_become_our_words(self, monkeypatch):
        serve(monkeypatch, lambda r: reply([(" Hello", 0.5, 0.9), ("", 1.0, 1.1), ("world.", 1.0, 0.9)],
                                           segments=[{"start": 0, "end": 2, "avg_logprob": -0.5, "no_speech_prob": 0.1}]))
        words, _ = groq.transcribe_chunks(10.0, encode, cfg())
        assert [(w.text, w.start, w.end) for w in words] == [("Hello", 0.5, 0.9), ("world.", 1.0, 1.0)]
        assert words[0].probability == pytest.approx(0.6065, abs=1e-3)

    def test_a_silent_segment_is_dropped(self, monkeypatch):
        """No VAD on Groq: Whisper's invented "Thank you." in silence is filtered by its own no-speech mark."""
        segments = [{"start": 0, "end": 3, "avg_logprob": -0.2, "no_speech_prob": 0.02},
                    {"start": 3, "end": 6, "avg_logprob": -1.8, "no_speech_prob": 0.9}]
        serve(monkeypatch, lambda r: reply([("real", 1.0, 1.4), ("Thank", 4.0, 4.3), ("you.", 4.3, 4.6)],
                                           segments=segments))
        words, _ = groq.transcribe_chunks(10.0, encode, cfg())
        assert [w.text for w in words] == ["real"]

    def test_samples_go_up_as_wav(self, monkeypatch):
        seen = serve(monkeypatch, lambda r: reply([("hi", 0.1, 0.3)]))
        words, _ = groq.transcribe_samples(np.zeros(16000 * 3, np.float32), 16000, cfg())
        assert words[0].text == "hi"
        assert b'filename="chunk.wav"' in seen[0].content and b"RIFF" in seen[0].content


class TestRetries:
    def test_429_waits_the_time_groq_gave_then_succeeds(self, monkeypatch):
        answers = iter([httpx.Response(429, headers={"retry-after": "7"}, json={"error": {"message": "slow"}}),
                        httpx.Response(503), reply([("ok", 0.0, 0.2)])])
        serve(monkeypatch, lambda r: next(answers))
        words, _ = groq.transcribe_chunks(30.0, encode, cfg())
        assert [w.text for w in words] == ["ok"]
        assert sleeps == [7.0, 2.0]

    def test_a_network_error_is_retried(self, monkeypatch):
        answers = iter([httpx.ConnectError("down"), reply([("ok", 0.0, 0.2)])])

        def handler(request):
            a = next(answers)
            if isinstance(a, Exception):
                raise a
            return a

        serve(monkeypatch, handler)
        assert groq.transcribe_chunks(30.0, encode, cfg())[0][0].text == "ok"

    def test_it_gives_up_with_a_plain_message(self, monkeypatch):
        seen = serve(monkeypatch, lambda r: httpx.Response(500))
        with pytest.raises(TranscriptionError, match="gave up after 6 tries"):
            groq.transcribe_chunks(30.0, encode, cfg())
        assert len(seen) == groq.RETRIES

    def test_a_bad_key_stops_at_once_with_groqs_words(self, monkeypatch):
        seen = serve(monkeypatch, lambda r: httpx.Response(401, json={"error": {"message": "Invalid API Key"}}))
        with pytest.raises(TranscriptionError, match="GROQ_API_KEY"):
            groq.transcribe_chunks(30.0, encode, cfg())
        assert len(seen) == 1

    def test_no_key_is_said_before_any_request(self, monkeypatch):
        monkeypatch.delenv("GROQ_API_KEY")
        seen = serve(monkeypatch, lambda r: reply([]))
        with pytest.raises(TranscriptionError, match="GROQ_API_KEY is not set"):
            groq.transcribe_chunks(30.0, encode, cfg())
        assert not seen


class TestUsage:
    def test_each_chunk_is_recorded_in_audio_seconds(self, monkeypatch):
        serve(monkeypatch, lambda r: reply([("a", 1.0, 1.2)]))
        groq.transcribe_chunks(700.0, encode, cfg())   # 606 s + 100 s
        (row,) = usage.summary(0.0)
        assert (row["job"], row["model"]) == ("transcribe", "whisper-large-v3-turbo")
        assert row["audio_seconds"] == pytest.approx(706.0)
        assert row["usd"] == pytest.approx(706 / 3600 * 0.04, rel=1e-3)

    def test_under_ten_seconds_is_billed_ten(self, monkeypatch):
        serve(monkeypatch, lambda r: reply([("a", 0.1, 0.2)]))
        groq.transcribe_chunks(3.0, encode, cfg())
        assert usage.summary(0.0)[0]["audio_seconds"] == pytest.approx(10.0)

    def test_a_spent_budget_sends_nothing(self, monkeypatch, data_root):
        (data_root / "plan.json").write_text('{"ai_budget_usd": 0.0, "period_start": "2026-01-01T00:00:00+00:00"}')
        seen = serve(monkeypatch, lambda r: reply([]))
        with pytest.raises(usage.BudgetExceeded):
            groq.transcribe_chunks(30.0, encode, cfg())
        assert not seen


class TestSelected:
    def test_local_unless_told_otherwise(self, monkeypatch):
        monkeypatch.delenv("CLIPPER_HOSTED", raising=False)
        monkeypatch.delenv("CLIPPER_HOST", raising=False)
        assert not groq.selected(TranscriptionConfig())

    def test_hosted_with_a_key_uses_groq(self, monkeypatch):
        monkeypatch.setenv("CLIPPER_HOSTED", "1")
        assert groq.selected(TranscriptionConfig())
        assert not groq.selected(TranscriptionConfig(backend="local"))
        monkeypatch.delenv("GROQ_API_KEY")
        assert not groq.selected(TranscriptionConfig())

    def test_config_wins_over_the_host(self, monkeypatch):
        monkeypatch.delenv("CLIPPER_HOSTED", raising=False)
        monkeypatch.delenv("CLIPPER_HOST", raising=False)
        assert groq.selected(TranscriptionConfig(backend="groq"))
