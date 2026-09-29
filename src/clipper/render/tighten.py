"""Cutting dead air and fillers out of a clip (podcasts; permission `internal_cuts`).

Rules from the retention research (docs/DECISIONS.md D59). Conversational turn
gaps cluster around 200 ms (Stivers et al., PNAS 2009), so longer pauses read as
dead air on a phone:

* a pause after a sentence longer than 0.50 s is cut down to 0.28 s; a pause
  inside a sentence longer than 0.70 s to 0.30 s;
* the pause before the clip's last sentence (the payoff) keeps up to 0.80 s --
  it is part of the joke;
* standalone "um"/"uh" with silence beside them are removed, and so is the first
  of an immediate repeat ("I I think") when there is a gap to cut in;
* never cut where the gap isn't quiet (laughter, a reaction, music), never
  within 40 ms of a word, never after the payoff starts;
* budgets: at most 20% of the clip removed, cut points at least 2.5 s apart,
  no piece shorter than 0.6 s.

Whisper's word times can drift about 100 ms, so every cut is also checked
against the audio: its whole span must be quiet.
"""

from __future__ import annotations

import wave
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path

import numpy as np

from ..models import Word

SENTENCE_GAP, SENTENCE_KEEP = 0.50, 0.28
CLAUSE_GAP, CLAUSE_KEEP = 0.70, 0.30
PAYOFF_KEEP = 0.80
FILLER_KEEP = 0.25
FILLERS = {"um", "uh", "erm", "er", "hmm", "umm", "uhh", "uhm", "mm", "mmm", "ah"}
WORD_MARGIN = 0.04
MAX_REMOVED_SHARE = 0.20
MIN_CUT_SPACING = 2.5
MIN_PIECE = 0.6
#: A gap is quiet when its level (80th percentile of 50 ms windows) is this far
#: below the clip's speech level.
QUIET_BELOW_SPEECH_DB = 18.0


@dataclass(frozen=True)
class Cut:
    start: float   # source seconds, removed from here...
    end: float     # ...to here
    reason: str

    @property
    def length(self) -> float:
        return self.end - self.start


def _bare(text: str) -> str:
    return text.strip().strip(".,!?;:…\"'").lower()


def _ends_sentence(text: str) -> bool:
    return text.strip().endswith((".", "?", "!", "…"))


class Loudness:
    """Short-window loudness of a 16-bit mono WAV, in dBFS per 50 ms."""

    def __init__(self, samples: np.ndarray, rate: int):
        hop = max(1, rate // 20)
        n = len(samples) // hop
        frames = samples[: n * hop].astype(np.float32).reshape(n, hop) / 32768.0
        rms = np.sqrt(np.mean(frames * frames, axis=1)) + 1e-9
        self.db = 20 * np.log10(rms)
        self.step = hop / rate

    @classmethod
    def from_wav(cls, path: Path) -> Loudness:
        with wave.open(str(path)) as f:
            if f.getnchannels() != 1 or f.getsampwidth() != 2:
                raise ValueError(f"{path} is not 16-bit mono")
            rate = f.getframerate()
            data = np.frombuffer(f.readframes(f.getnframes()), dtype=np.int16)
        return cls(data, rate)

    def peak(self, start: float, end: float) -> float:
        a, b = int(start / self.step), max(int(start / self.step) + 1, int(end / self.step))
        window = self.db[a:b]
        return float(window.max()) if len(window) else -120.0

    def level(self, start: float, end: float, percentile: float = 80) -> float:
        """The span's loudness at `percentile` -- robust to a word's tail at its edge.

        Measured on 85 South (a live audience): gaps full of laughter read -18 to
        -29 dB here; the one true dead-air pause -45.6 dB. The loudest instant
        alone read -16 to -21 dB for all of them, so it couldn't tell them apart.
        """
        a, b = int(start / self.step), max(int(start / self.step) + 1, int(end / self.step))
        window = self.db[a:b]
        return float(np.percentile(window, percentile)) if len(window) else -120.0

    def speech_level(self, words: list[Word]) -> float:
        levels = [self.peak(w.start, w.end) for w in words if w.end - w.start >= 0.08]
        return float(np.median(levels)) if levels else -20.0


def plan_cuts(words: list[Word], start: float, end: float,
              loudness: Loudness | None, *, min_length: float = 0.0) -> list[Cut]:
    """The cuts for one clip: dead air and fillers, within every budget above.

    `min_length` is the campaign's minimum: tightening never takes a clip under it.
    """
    inside = [w for w in words if start <= (w.start + w.end) / 2 < end and w.text.strip()]
    if len(inside) < 3:
        return []
    quiet = (loudness.speech_level(inside) - QUIET_BELOW_SPEECH_DB) if loudness else None

    def is_quiet(a: float, b: float) -> bool:
        return quiet is None or loudness.level(a, b) <= quiet

    # The payoff: the last sentence. Nothing is cut from its start on.
    payoff_at = inside[0].start
    for prev, word in pairwise(inside):
        if _ends_sentence(prev.text):
            payoff_at = word.start

    candidates: list[Cut] = []
    skip_next_gap = False
    for i, (prev, word) in enumerate(pairwise(inside)):
        if skip_next_gap:
            skip_next_gap = False
            continue
        gap = word.start - prev.end
        # Standalone fillers: remove the word and the pauses around it.
        after = inside[i + 2] if i + 2 < len(inside) else None
        if (_bare(word.text) in FILLERS and after is not None and word.end <= payoff_at
                and (gap >= 0.08 or after.start - word.end >= 0.08)):
            a = prev.end + min(FILLER_KEEP / 2, max(WORD_MARGIN, gap / 2))
            b = after.start - min(FILLER_KEEP / 2, max(WORD_MARGIN, (after.start - word.end) / 2))
            if b - a > 0.12 and is_quiet(a, word.start) and is_quiet(word.end, b):
                candidates.append(Cut(a, b, f'filler "{word.text.strip()}"'))
                skip_next_gap = True
                continue
        # Stutters: "I I think" -- drop the first when there's a gap to cut in.
        if (_bare(prev.text) == _bare(word.text) and _bare(word.text)
                and word.start - prev.start <= 0.4 and gap >= WORD_MARGIN and i > 0):
            before = inside[i - 1]
            lead = prev.start - before.end
            if lead >= WORD_MARGIN and prev.end <= payoff_at:
                a = prev.start - min(lead / 2, 0.02)
                b = word.start - min(gap / 2, 0.02)
                if b > a and is_quiet(prev.end, word.start):
                    candidates.append(Cut(a, b, f'repeat "{prev.text.strip()}"'))
                    continue
        # Dead air.
        if word.start > payoff_at:
            continue
        if word.start == payoff_at:
            threshold, keep = PAYOFF_KEEP, PAYOFF_KEEP
        elif _ends_sentence(prev.text):
            threshold, keep = SENTENCE_GAP, SENTENCE_KEEP
        else:
            threshold, keep = CLAUSE_GAP, CLAUSE_KEEP
        if gap > threshold:
            a, b = prev.end + keep / 2, word.start - keep / 2
            if b - a > 0.05 and is_quiet(a, b):
                candidates.append(Cut(a, b, f"pause {gap:.2f}s"))

    return _within_budget(candidates, start, end, min_length)


def _within_budget(candidates: list[Cut], start: float, end: float,
                   min_length: float = 0.0) -> list[Cut]:
    # A little headroom over the minimum: QA measures the rendered file.
    budget = min((end - start) * MAX_REMOVED_SHARE, (end - start) - min_length - 0.5)
    if budget <= 0:
        return []
    kept: list[Cut] = []
    for cut in sorted(candidates, key=lambda c: -c.length):
        if sum(c.length for c in kept) + cut.length > budget:
            continue
        if any(abs(cut.start - c.start) < MIN_CUT_SPACING for c in kept):
            continue
        trial = sorted([*kept, cut], key=lambda c: c.start)
        if min(p[1] - p[0] for p in keep_segments(trial, start, end)) < MIN_PIECE:
            continue
        kept.append(cut)
    return sorted(kept, key=lambda c: c.start)


def keep_segments(cuts: list[Cut], start: float, end: float) -> list[tuple[float, float]]:
    """The parts of [start, end] left after `cuts`, in order."""
    pieces, at = [], start
    for cut in sorted(cuts, key=lambda c: c.start):
        if cut.start > at:
            pieces.append((at, cut.start))
        at = max(at, cut.end)
    if end > at:
        pieces.append((at, end))
    return pieces


def remap_words(words: list[Word], segments: list[tuple[float, float]]) -> list[Word]:
    """Words inside `segments`, re-timed onto the joined clip (which starts at 0)."""
    out, offset = [], 0.0
    for a, b in segments:
        for w in words:
            mid = (w.start + w.end) / 2
            if a <= mid < b:
                out.append(Word(start=max(0.0, w.start - a) + offset,
                                end=min(b, w.end) - a + offset, text=w.text,
                                probability=w.probability))
        offset += b - a
    return out
