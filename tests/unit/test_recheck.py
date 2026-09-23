"""The audio recheck that decides whether a caption fix was actually said.

The Whisper call itself is exercised against real audio in docs/VERIFIED.md;
these pin the decision made from its output, using the transcriptions it
actually produced on the real cases.
"""

from __future__ import annotations

import pytest

from clipper.config import TranscriptionConfig
from clipper.models import Word
from clipper.transcribe.recheck import AudioRecheck, _is_loop


def transcript(text: str, start: float = 10.0) -> list[Word]:
    return [Word(start=start + i * 0.3, end=start + i * 0.3 + 0.25, text=f" {t}")
            for i, t in enumerate(text.split())]


class Scripted(AudioRecheck):
    """Returns a fixed transcription instead of running Whisper."""

    def __init__(self, heard: str | None):
        super().__init__("unused.wav", TranscriptionConfig())
        self.heard = heard

    def _transcribe(self, word, replacement):
        return self.heard


def target(words: list[Word], text: str) -> Word:
    return next(w for w in words if w.text.strip().strip(".,") == text)


class TestSaid:
    def test_a_replacement_the_audio_supports_is_accepted(self):
        words = transcript("I heard the picture from Los Angeles Dodgers.")
        check = Scripted("I heard the pitcher from")
        assert check.said(target(words, "picture"), "pitcher", words)

    def test_a_replacement_the_audio_does_not_support_is_refused(self):
        """Measured: nudged toward "bite", Whisper wrote "...from the sand flies"."""
        words = transcript("They get leishmaniasis from the bot. And the sand flies")
        check = Scripted("They get leishmaniasis from the sand flies because while")
        assert not check.said(target(words, "bot"), "bite", words)

    def test_hearing_the_original_again_is_a_refusal(self):
        words = transcript("they're going to lay bot flies on there. And so")
        check = Scripted("And so they're going to lay bot flies on there and so")
        assert not check.said(target(words, "there"), "their", words)

    def test_a_common_word_already_in_the_window_is_not_evidence(self):
        """Measured: "to" was already in the window ("lead you to a river"),
        so its mere presence proved nothing. The original must also go."""
        words = transcript("follow a tributary through the river and it'll lead you to a river")
        check = Scripted("Again, you just follow a tributary through the river and "
                         "it'll lead you to a river.")
        assert not check.said(target(words, "through"), "to", words)

    def test_a_hallucination_loop_is_no_evidence(self):
        words = transcript("Monkey never clap because")
        check = Scripted(None)
        assert not check.said(target(words, "clap"), "cramp", words)

    def test_a_failure_to_listen_keeps_the_original(self):
        class Broken(AudioRecheck):
            def _transcribe(self, word, replacement):
                raise RuntimeError("CUDA out of memory")

        words = transcript("I heard the picture from")
        check = Broken("unused.wav", TranscriptionConfig())
        assert not check.said(target(words, "picture"), "pitcher", words)


class TestLoops:
    def test_the_measured_loop_is_detected(self):
        assert _is_loop("Los Angeles Dodgers. " * 30)

    @pytest.mark.parametrize("text", [
        "From Los Angeles Dodgers. Monkey never cramp.",
        "the bot flies, the bot flies in their nose",
    ])
    def test_ordinary_speech_is_not_a_loop(self, text):
        assert not _is_loop(text)
