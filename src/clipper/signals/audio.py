"""The audio signal (BUILD_BRIEF.md section 9.2).

Energy, dynamics, onset events and speech rate, computed from the 16 kHz mono
WAV extracted at ingest. Everything is numpy and scipy -- no librosa, which
would pull in numba for features we do not use (docs/DECISIONS.md D5).

The whole file is loaded once into an `AudioAnalysis` and every candidate is
scored against it, so a 60-minute source costs one pass rather than one per
candidate.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

from ..models import Candidate, Transcript
from ..utils.logging import get_logger

log = get_logger(__name__)

FRAME_SECONDS = 0.025
HOP_SECONDS = 0.010
EPS = 1e-10

# Digital silence is -200 dB once EPS is added, which would make the dynamic
# range of any clip containing a pause enormous and meaningless. Real speech
# noise floors sit around -60 dB, so clamp there.
FLOOR_DB = -60.0

# Words per second at which speech feels energetic without being rushed.
# Conversational English sits near 2.5; short-form performs better slightly above.
IDEAL_WORDS_PER_SECOND = 2.8


@dataclass
class AudioAnalysis:
    """Frame-level features for a whole source, computed once."""

    sample_rate: int
    hop: float
    rms_db: np.ndarray          # per frame, decibels
    flux: np.ndarray            # per frame, positive spectral flux
    duration: float

    @property
    def frame_times(self) -> np.ndarray:
        return np.arange(len(self.rms_db)) * self.hop

    def slice_indices(self, start: float, end: float) -> tuple[int, int]:
        lo = max(0, int(start / self.hop))
        hi = min(len(self.rms_db), int(np.ceil(end / self.hop)))
        return lo, max(lo + 1, hi)


def analyse(audio_path: Path | str) -> AudioAnalysis:
    """Compute frame-level energy and spectral flux for a whole file."""
    import soundfile as sf

    data, sample_rate = sf.read(str(audio_path), dtype="float32", always_2d=False)
    if data.ndim > 1:
        data = data.mean(axis=1)
    if data.size == 0:
        raise ValueError(f"{audio_path} contains no samples")

    frame_length = max(2, int(FRAME_SECONDS * sample_rate))
    hop_length = max(1, int(HOP_SECONDS * sample_rate))
    frames = _frame(data, frame_length, hop_length)

    rms = np.sqrt(np.mean(frames.astype(np.float64) ** 2, axis=1) + EPS)
    rms_db = np.maximum(20.0 * np.log10(rms + EPS), FLOOR_DB)

    # Spectral flux: positive frame-to-frame change in the magnitude spectrum.
    # Peaks mark onsets -- laughter, a raised voice, a cut, an impact.
    window = np.hanning(frame_length).astype(np.float32)
    spectra = np.abs(np.fft.rfft(frames * window, axis=1))
    diff = np.diff(spectra, axis=0, prepend=spectra[:1])
    flux = np.sum(np.maximum(diff, 0.0), axis=1)

    return AudioAnalysis(
        sample_rate=sample_rate,
        hop=hop_length / sample_rate,
        rms_db=rms_db.astype(np.float32),
        flux=flux.astype(np.float32),
        duration=len(data) / sample_rate,
    )


def _frame(data: np.ndarray, frame_length: int, hop_length: int) -> np.ndarray:
    """Split a signal into overlapping frames without copying."""
    if len(data) < frame_length:
        data = np.pad(data, (0, frame_length - len(data)))
    count = 1 + (len(data) - frame_length) // hop_length
    strides = (data.strides[0] * hop_length, data.strides[0])
    return np.lib.stride_tricks.as_strided(
        data, shape=(count, frame_length), strides=strides, writeable=False
    )


def features_for(
    candidate: Candidate,
    analysis: AudioAnalysis,
    transcript: Transcript,
    *,
    global_stats: tuple[float, float] | None = None,
) -> dict[str, float]:
    """Audio features for one candidate, all on comparable scales."""
    lo, hi = analysis.slice_indices(candidate.start, candidate.end)
    window_db = analysis.rms_db[lo:hi]
    window_flux = analysis.flux[lo:hi]

    mean_db, std_db = global_stats or (float(analysis.rms_db.mean()),
                                       float(analysis.rms_db.std()) or 1.0)

    energy_z = float((window_db.mean() - mean_db) / (std_db + EPS))

    # Dynamic range across *voiced* frames only. Including silence would make
    # this measure "does the clip contain pauses", which `silence_ratio` already
    # covers; restricting to speech measures delivery -- a flat monologue versus
    # an animated exchange at the same mean level.
    voiced = window_db[window_db > FLOOR_DB + 10.0]
    if voiced.size < 4:
        voiced = window_db
    loud = float(np.percentile(voiced, 90))
    quiet = float(np.percentile(voiced, 10))
    dynamic_range = loud - quiet

    # Onsets, counted as flux peaks above a global percentile. Because that
    # threshold is defined over the whole file, the *absolute* rate is fixed by
    # construction (~15% of frames by definition); what carries information is
    # this window's density relative to the video's own average.
    flux_threshold = float(np.percentile(analysis.flux, 85))
    onset_fraction = float(np.mean(window_flux > flux_threshold))
    global_fraction = 0.15  # by definition of the 85th percentile
    onset_ratio = onset_fraction / global_fraction

    words = transcript.words_between(candidate.start, candidate.end)
    words_per_second = len(words) / max(1e-6, candidate.duration)

    # Variation in pace across the clip: monotone delivery scores low.
    pace_variance = _pace_variance(words, candidate)

    return {
        "energy_z": round(energy_z, 4),
        "dynamic_range_db": round(dynamic_range, 3),
        "onset_ratio": round(onset_ratio, 4),
        "words_per_second": round(words_per_second, 3),
        "pace_variance": round(pace_variance, 4),
        "peak_db": round(loud, 2),
    }


def _pace_variance(words: list, candidate: Candidate) -> float:
    """Standard deviation of words-per-second across 5-second sub-windows."""
    if candidate.duration < 10 or len(words) < 8:
        return 0.0
    edges = np.arange(candidate.start, candidate.end, 5.0)
    rates = []
    for edge in edges:
        in_bin = [w for w in words if edge <= w.start < edge + 5.0]
        rates.append(len(in_bin) / 5.0)
    return float(np.std(rates)) if len(rates) > 1 else 0.0


def raw_score(features: dict[str, float]) -> float:
    """Collapse the features into one raw value for percentile ranking.

    Weights are a transparent starting point, not a fitted model -- Phase 5
    tunes the *signal* weights, and this internal mix is documented in
    docs/TUNING.md as a candidate for tuning if the audio signal underperforms.
    """
    # Energy relative to the video: above-average is better, but only to a point.
    energy = float(np.clip(features.get("energy_z", 0.0), -2.0, 2.0)) / 2.0

    # Dynamic range, normalised over a plausible 5-25 dB span.
    dynamics = float(np.clip((features.get("dynamic_range_db", 0.0) - 5.0) / 20.0, 0.0, 1.0))

    # Onset density relative to the video's own average. 1.0 means "typical for
    # this video"; 2.0 or more means a notably eventful passage.
    onsets = float(np.clip(features.get("onset_ratio", 0.0) / 2.0, 0.0, 1.0))

    # Speech rate: a peak at IDEAL_WORDS_PER_SECOND, falling off either side.
    wps = features.get("words_per_second", 0.0)
    pace = float(np.clip(1.0 - abs(wps - IDEAL_WORDS_PER_SECOND) / IDEAL_WORDS_PER_SECOND, 0.0, 1.0))

    # Some variation in pace is engaging; a lot is rambling.
    variation = float(np.clip(features.get("pace_variance", 0.0) / 1.5, 0.0, 1.0))

    return round(
        0.30 * (energy + 1.0) / 2.0
        + 0.25 * dynamics
        + 0.15 * onsets
        + 0.20 * pace
        + 0.10 * variation,
        5,
    )


def score_candidates(
    candidates: list[Candidate],
    audio_path: Path | str,
    transcript: Transcript,
) -> tuple[dict[str, float], dict[str, dict[str, float]]]:
    """Raw audio scores and per-candidate feature detail."""
    analysis = analyse(audio_path)
    global_stats = (float(analysis.rms_db.mean()), float(analysis.rms_db.std()) or 1.0)

    raws: dict[str, float] = {}
    details: dict[str, dict[str, float]] = {}
    for candidate in candidates:
        features = features_for(candidate, analysis, transcript, global_stats=global_stats)
        details[candidate.candidate_id] = features
        raws[candidate.candidate_id] = raw_score(features)

    log.debug("audio signal computed for %d candidates", len(candidates))
    return raws, details
