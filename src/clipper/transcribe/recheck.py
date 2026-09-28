"""Checking a proposed caption fix against the audio.

A text-only judge cannot tell a *misheard* word from a *misspoken* one: both
look wrong on the page. Measured on real clips: every text check tried accepted
"leishmaniasis from the bot" -> "bite" -- the speaker said "bot", and "bite" is
merely what is true -- in some or all runs. Only the audio knows what was said.

The check: re-transcribe a few seconds around the word with Whisper, nudged
toward the replacement (it is given as vocabulary). If the speaker said the
replacement, the nudge tips Whisper into writing it in place of the original. If
they did not, Whisper keeps hearing the original. Measured on the real cases
(docs/VERIFIED.md, 2026-09-22):

    picture -> pitcher   heard "I heard the pitcher from"            fix
    clap    -> cramp     heard "Monkey never cramp."                 fix
    bot     -> bite      heard "...leishmaniasis from the sand flies" no fix
    there   -> their     heard "...lay bot flies on there"           no fix

It is a veto, not a judge: words that sound nearly identical ("pickies" and
"piques") pass it either way, and are left to the text classifier upstream.

Two details decided by measurement:

* The window is 2.0s before to 1.5s after the word. A tighter 1.2s/0.8s window
  sent Whisper into a repetition loop ("Los Angeles Dodgers" thirty times),
  which is its known failure on very short clips.
* The test is "the replacement replaced the original", not "the replacement
  appears": a common word like "to" is usually somewhere in the window already.
"""

from __future__ import annotations

import re
import wave
from pathlib import Path

import numpy as np

from ..config import TranscriptionConfig
from ..models import Word
from ..utils.logging import get_logger

log = get_logger(__name__)

WINDOW_BEFORE = 2.0
WINDOW_AFTER = 1.5

# A transcription repeating one three-word phrase this often is a Whisper
# hallucination loop and says nothing about the audio.
LOOP_REPEATS = 4


class AudioRecheck:
    """Re-listens to the source around a word. Loads Whisper only when first used."""

    def __init__(self, audio_path: Path | str, cfg: TranscriptionConfig):
        self.audio_path = Path(audio_path)
        self.cfg = cfg
        self._model = None
        self._audio: np.ndarray | None = None
        self._rate = 16_000

    def said(self, word: Word, replacement: str, transcript: list[Word]) -> bool:
        """True only if the audio supports `replacement` over the transcribed word.

        Any failure to check -- no audio, Whisper unavailable, a hallucination
        loop -- returns False, so the original stays. `transcript` is the full
        word list, used to count what was already transcribed in the window.
        """
        try:
            heard = self._transcribe(word, replacement)
        except Exception as exc:  # a caption fix must never fail a render
            log.warning("audio recheck unavailable (%s); caption fix not applied", exc)
            return False
        if heard is None:
            return False

        start, end = word.start - WINDOW_BEFORE, word.end + WINDOW_AFTER
        before = _tokens(" ".join(w.text for w in transcript
                                  if start <= (w.start + w.end) / 2 < end))
        after = _tokens(heard)
        original = _tokens(word.text)
        wanted = _tokens(replacement)
        if not original or not wanted:
            return False
        gained = _count(after, wanted) > _count(before, wanted)
        lost = _count(after, original) < _count(before, original)
        log.debug("audio recheck %r -> %r: heard %r (gained=%s lost=%s)",
                  word.text.strip(), replacement, heard, gained, lost)
        return gained and lost

    def words_between(self, start: float, end: float) -> list[Word] | None:
        """Transcribe `start`-`end` on its own; None if it looped or failed.

        A whole-episode pass drops quiet dialogue under loud crowd audio -- the
        Chad Powers Ep 6 field scene came out as 7 words of about 40 -- while the
        same 36 seconds transcribed alone come out whole. VAD stays on so a long
        wordless look is not filled with hallucinated "Thank you."s, and nothing
        is carried over from earlier text, which is what lets one bad guess
        swallow the lines after it.
        """
        try:
            model = self._load_model()
            audio = self._load_audio()
            a = max(0, int(start * self._rate))
            b = min(len(audio), int(end * self._rate))
            segments, _ = model.transcribe(
                audio[a:b], language=self.cfg.language if self.cfg.language != "auto" else None,
                beam_size=5, temperature=0.0, word_timestamps=True,
                condition_on_previous_text=False, vad_filter=True,
            )
            words = [Word(start=start + float(w.start),
                          end=start + max(float(w.end), float(w.start)),
                          text=w.word.strip(),
                          probability=float(getattr(w, "probability", 1.0) or 1.0))
                     for s in segments for w in (s.words or []) if w.word.strip()]
        except Exception as exc:  # a better transcript must never fail a render
            log.warning("re-transcribing %.1f-%.1fs failed (%s); keeping the episode's",
                        start, end, exc)
            return None
        if _is_loop(" ".join(w.text for w in words)):
            log.info("re-transcribing %.1f-%.1fs looped; keeping the episode's", start, end)
            return None
        return words

    def _transcribe(self, word: Word, replacement: str) -> str | None:
        model = self._load_model()
        audio = self._load_audio()
        a = max(0, int((word.start - WINDOW_BEFORE) * self._rate))
        b = min(len(audio), int((word.end + WINDOW_AFTER) * self._rate))
        segments, _ = model.transcribe(
            audio[a:b], language="en", beam_size=5, temperature=0.0,
            initial_prompt=f"Vocabulary: {replacement.strip()}.",
            condition_on_previous_text=False, vad_filter=False,
        )
        text = " ".join(s.text.strip() for s in segments)
        if _is_loop(text):
            log.info("audio recheck for %r looped; treating as no evidence",
                     word.text.strip())
            return None
        return text

    def _load_model(self):
        if self._model is None:
            from .whisper import load_model

            self._model, *_ = load_model(self.cfg)
        return self._model

    def _load_audio(self) -> np.ndarray:
        if self._audio is None:
            with wave.open(str(self.audio_path)) as f:
                if f.getnchannels() != 1 or f.getsampwidth() != 2:
                    raise ValueError(f"{self.audio_path} is not 16-bit mono")
                self._rate = f.getframerate()
                raw = f.readframes(f.getnframes())
            self._audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
        return self._audio


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", text.lower())


def _count(haystack: list[str], needle: list[str]) -> int:
    n = len(needle)
    return sum(1 for i in range(len(haystack) - n + 1) if haystack[i:i + n] == needle)


def _is_loop(text: str) -> bool:
    words = _tokens(text)
    grams = [tuple(words[i:i + 3]) for i in range(len(words) - 2)]
    return any(grams.count(g) >= LOOP_REPEATS for g in set(grams))
