"""Ingest -> transcribe -> segment -> candidates, against real synthesised speech.

These use Windows SAPI to generate speech whose exact wording is known, so the
transcription can be checked against ground truth rather than merely asserted to
be non-empty.

Marked `slow` and `gpu`.
"""

from __future__ import annotations

from itertools import pairwise

import pytest

from clipper.candidates.windows import generate
from clipper.config import Config
from clipper.ingest.download import IngestError, ingest
from clipper.transcribe.segment import segment
from clipper.transcribe.whisper import transcribe

from ..conftest import needs_ffmpeg
from ..fixtures import speech, synthetic

pytestmark = [pytest.mark.slow, needs_ffmpeg]

needs_sapi = pytest.mark.skipif(not speech.sapi_available(), reason="Windows SAPI unavailable")

# `small` keeps the suite fast; large-v3 accuracy is measured separately and
# recorded in docs/VERIFIED.md rather than asserted here.
TEST_MODEL = "small"


@pytest.fixture(scope="module")
def cfg() -> Config:
    base = Config.load()
    return base.model_copy(
        update={"transcription": base.transcription.model_copy(update={"model": TEST_MODEL})}
    )


@pytest.fixture
def dense(media_cache, data_root):
    """A 75-second monologue with realistic conversational cadence."""
    return speech.speech_video(list(speech.DENSE_SCRIPT), name="dense")


@pytest.fixture
def paused(media_cache, data_root):
    """A 40-second monologue with long, exaggerated pauses."""
    return speech.speech_video(list(speech.PAUSED_SCRIPT), name="paused")


def words_of(text: str) -> list[str]:
    return [w.strip(".,!?").lower() for w in text.split()]


@needs_sapi
class TestIngest:
    def test_produces_info_and_analysis_audio(self, dense, data_root):
        info = ingest(str(dense.video))
        assert info.source_id
        assert info.media.duration == pytest.approx(dense.duration, abs=0.5)
        assert info.media.has_audio

        from pathlib import Path

        audio = Path(info.audio_path)
        assert audio.is_file()
        from clipper.ingest.probe import probe

        extracted = probe(audio)
        assert extracted.audio_sample_rate == 16_000
        assert extracted.audio_channels == 1

    def test_a_local_file_has_no_heatmap(self, dense, data_root):
        """The heatmap signal must degrade cleanly for non-YouTube sources."""
        info = ingest(str(dense.video))
        assert info.heatmap is None
        assert not info.has_heatmap

    def test_re_ingesting_reuses_the_cache(self, dense, data_root):
        first = ingest(str(dense.video))
        second = ingest(str(dense.video))
        assert first.source_id == second.source_id
        assert first.audio_path == second.audio_path

    def test_a_missing_file_gives_a_clear_error(self, data_root):
        with pytest.raises(IngestError, match="no such file"):
            ingest("C:/definitely/not/here.mp4")

    def test_a_silent_source_is_rejected_with_a_reason(self, media_cache, data_root):
        """clipper selects moments from speech; silence cannot be clipped."""
        from clipper.render.ffmpeg import run

        video = synthetic.bars_video(duration=4.0, with_audio=False)
        with pytest.raises(IngestError, match="no audio"):
            ingest(str(video))
        del run


@needs_sapi
class TestTranscribe:
    def test_transcribes_the_known_script_accurately(self, dense, cfg, data_root):
        info = ingest(str(dense.video))
        transcript, _stats = transcribe(info, cfg.transcription)

        assert transcript.words
        assert transcript.language == "en"

        got = words_of(transcript.text)
        expected = words_of(dense.transcript_text)
        overlap = len(set(got) & set(expected)) / len(set(expected))
        assert overlap > 0.9, f"only {overlap:.0%} of expected words present"

    def test_word_timestamps_are_ordered_and_bounded(self, dense, cfg, data_root):
        info = ingest(str(dense.video))
        transcript, _ = transcribe(info, cfg.transcription)
        words = transcript.words
        for w in words:
            assert w.end >= w.start
            assert 0 <= w.start <= info.media.duration + 1.0
        for a, b in pairwise(words):
            assert b.start >= a.start - 1e-6

    def test_reports_a_measured_realtime_factor(self, dense, cfg, data_root):
        info = ingest(str(dense.video))
        _, stats = transcribe(info, cfg.transcription, force=True)
        assert stats is not None
        assert stats.realtime_factor > 1.0, "should be faster than realtime on this GPU"
        assert stats.word_count > 0

    def test_the_cache_is_reused_on_a_second_call(self, dense, cfg, data_root):
        info = ingest(str(dense.video))
        transcribe(info, cfg.transcription, force=True)
        _, stats = transcribe(info, cfg.transcription)
        assert stats is None, "a cache hit should not report fresh timing"

    def test_the_cache_is_invalidated_by_a_model_change(self, dense, cfg, data_root):
        """`--model X` must not silently return a transcript made with Y."""
        info = ingest(str(dense.video))
        transcribe(info, cfg.transcription, force=True)
        other = cfg.transcription.model_copy(update={"model": "base"})
        _, stats = transcribe(info, other)
        assert stats is not None, "a different model should force re-transcription"
        assert stats.model == "base"

    def test_words_between_selects_by_midpoint(self, dense, cfg, data_root):
        info = ingest(str(dense.video))
        transcript, _ = transcribe(info, cfg.transcription)
        mid = transcript.words[len(transcript.words) // 2]
        selected = transcript.words_between(mid.start, mid.end + 5.0)
        assert selected
        assert all(mid.start <= (w.start + w.end) / 2 < mid.end + 5.0 for w in selected)


@needs_sapi
class TestSegmentOnRealSpeech:
    def test_finds_about_the_expected_number_of_sentences(self, dense, cfg, data_root):
        info = ingest(str(dense.video))
        transcript, _ = transcribe(info, cfg.transcription)
        sentences = segment(transcript).sentences
        # Exact equality is too brittle -- the transcriber may merge or split
        # one line -- but it must be close.
        assert abs(len(sentences) - dense.expected_sentences) <= 2

    def test_long_pauses_create_paragraph_breaks(self, paused, cfg, data_root):
        info = ingest(str(paused.video))
        transcript, _ = transcribe(info, cfg.transcription)
        sentences = segment(transcript).sentences
        assert max(s.paragraph for s in sentences) >= 1

    def test_sentences_cover_every_word_exactly_once(self, dense, cfg, data_root):
        info = ingest(str(dense.video))
        transcript, _ = transcribe(info, cfg.transcription)
        covered: list[int] = []
        for s in segment(transcript).sentences:
            lo, hi = s.word_indices
            covered.extend(range(lo, hi))
        assert covered == list(range(len(transcript.words)))


@needs_sapi
class TestCandidatesOnRealSpeech:
    def test_generates_sentence_aligned_candidates(self, dense, cfg, data_root):
        info = ingest(str(dense.video))
        transcript, _ = transcribe(info, cfg.transcription)
        sentences = segment(transcript)
        result = generate(transcript, sentences, cfg.candidates,
                          source_duration=info.media.duration)

        assert result.candidates, "the dense script should yield candidates"
        starts = {s.start for s in sentences.sentences}
        ends = {s.end for s in sentences.sentences}
        for c in result.candidates:
            assert c.start in starts
            assert c.end in ends
            assert cfg.candidates.min_seconds <= c.duration <= cfg.candidates.max_seconds
            assert c.silence_ratio <= cfg.candidates.max_silence_ratio

    def test_a_pause_heavy_source_yields_none(self, paused, cfg, data_root):
        """Measured at ~42% silence -- above the 25% filter, so zero is correct.

        This is the behaviour docs/BUILD_BRIEF.md section 1 asks for: returning
        nothing beats returning filler.
        """
        info = ingest(str(paused.video))
        transcript, _ = transcribe(info, cfg.transcription)
        sentences = segment(transcript)
        result = generate(transcript, sentences, cfg.candidates,
                          source_duration=info.media.duration)
        assert result.candidates == []

    def test_candidates_are_persisted_and_reloadable(self, dense, cfg, data_root, tmp_path):
        from clipper.models import Candidates

        info = ingest(str(dense.video))
        transcript, _ = transcribe(info, cfg.transcription)
        result = generate(transcript, segment(transcript), cfg.candidates,
                          source_duration=info.media.duration)
        path = tmp_path / "candidates.json"
        result.save(path)
        assert Candidates.load(path).candidates == result.candidates
