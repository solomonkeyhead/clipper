"""The "most replayed" heatmap signal (BUILD_BRIEF.md section 9.3).

YouTube's heatmap is replay density, normalised per video with its maximum
pinned near 1.0. Two biases make the raw values misleading:

1. **Intro inflation.** The opening seconds of nearly every video are replayed
   heavily -- people restart, scrub back to the beginning, or land there from a
   link. This is an artifact of navigation, not of the content being good.
2. **Linked-timestamp spikes.** A moment linked from a comment or description
   accumulates replays for reasons unrelated to its intrinsic quality.

Subtracting a rolling median removes the slow component of both, leaving local
prominence: how much a moment stands out from its own neighbourhood. That is
much closer to the question we actually care about.

Availability is not guaranteed -- local files and campaign footage have no
heatmap at all -- so the signal is optional and its weight is renormalised away
when absent.
"""

from __future__ import annotations

import numpy as np

from ..models import Candidate, SourceInfo
from ..utils.logging import get_logger

log = get_logger(__name__)

RESAMPLE_HZ = 1.0
"""Heatmap segments are coarse; 1 Hz is finer than the underlying data."""

DEFAULT_DETREND_WINDOW = 180.0
"""Rolling median window in seconds. The brief suggests about three minutes."""


def resample(heatmap: list[dict[str, float]], duration: float,
             *, hz: float = RESAMPLE_HZ) -> np.ndarray:
    """Resample heatmap segments onto a uniform time grid."""
    if not heatmap or duration <= 0:
        return np.zeros(0, dtype=np.float32)

    samples = max(1, int(np.ceil(duration * hz)))
    grid = np.zeros(samples, dtype=np.float64)
    counts = np.zeros(samples, dtype=np.float64)

    for segment in heatmap:
        start = max(0.0, float(segment["start_time"]))
        end = min(duration, float(segment["end_time"]))
        if end <= start:
            continue
        lo = int(start * hz)
        hi = min(samples, max(lo + 1, int(np.ceil(end * hz))))
        grid[lo:hi] += float(segment["value"])
        counts[lo:hi] += 1.0

    # Where segments overlap, average rather than sum.
    filled = counts > 0
    grid[filled] /= counts[filled]

    # Gaps between segments are interpolated rather than left at zero, which
    # would otherwise read as "nobody watched this".
    if filled.any() and not filled.all():
        indices = np.arange(samples)
        grid[~filled] = np.interp(indices[~filled], indices[filled], grid[filled])

    return grid.astype(np.float32)


def rolling_median(values: np.ndarray, window_samples: int) -> np.ndarray:
    """Centred rolling median, with the edges handled by reflection.

    Reflection rather than zero-padding: padding with zeros at the start would
    depress the baseline exactly where intro inflation lives, which is the
    opposite of what this correction is for.
    """
    if values.size == 0:
        return values
    window_samples = max(1, min(int(window_samples), values.size))
    if window_samples % 2 == 0:
        window_samples += 1
    half = window_samples // 2

    padded = np.pad(values, half, mode="reflect")
    strides = (padded.strides[0], padded.strides[0])
    windows = np.lib.stride_tricks.as_strided(
        padded, shape=(values.size, window_samples), strides=strides, writeable=False
    )
    return np.median(windows, axis=1).astype(np.float32)


def bias_correct(values: np.ndarray, *, window_seconds: float = DEFAULT_DETREND_WINDOW,
                 hz: float = RESAMPLE_HZ) -> np.ndarray:
    """Remove the slow trend, leaving local prominence."""
    if values.size == 0:
        return values
    baseline = rolling_median(values, int(window_seconds * hz))
    return (values - baseline).astype(np.float32)


def build(info: SourceInfo, *, window_seconds: float = DEFAULT_DETREND_WINDOW
          ) -> tuple[np.ndarray, np.ndarray] | None:
    """Return (raw, corrected) heatmap curves, or None when unavailable."""
    if not info.heatmap:
        return None
    raw = resample(info.heatmap, info.media.duration)
    if raw.size == 0:
        return None
    return raw, bias_correct(raw, window_seconds=window_seconds)


def features_for(candidate: Candidate, corrected: np.ndarray, raw: np.ndarray,
                 *, hz: float = RESAMPLE_HZ) -> dict[str, float]:
    """Mean and peak corrected heatmap value inside one window."""
    lo = max(0, int(candidate.start * hz))
    hi = min(corrected.size, max(lo + 1, int(np.ceil(candidate.end * hz))))
    if lo >= corrected.size:
        return {"mean": 0.0, "peak": 0.0, "raw_mean": 0.0}

    window = corrected[lo:hi]
    return {
        "mean": round(float(window.mean()), 5),
        "peak": round(float(window.max()), 5),
        "raw_mean": round(float(raw[lo:hi].mean()), 5),
    }


def raw_score(features: dict[str, float]) -> float:
    """Blend mean and peak prominence into one raw value.

    Mean-weighted, because a clip is watched in full: a single replayed instant
    inside an otherwise ignored stretch is a worse bet than a consistently
    replayed passage.
    """
    return round(0.7 * features.get("mean", 0.0) + 0.3 * features.get("peak", 0.0), 5)


def score_candidates(
    candidates: list[Candidate],
    info: SourceInfo,
    *,
    window_seconds: float = DEFAULT_DETREND_WINDOW,
) -> tuple[dict[str, float], dict[str, dict[str, float]]] | None:
    """Raw heatmap scores per candidate, or None when there is no heatmap."""
    curves = build(info, window_seconds=window_seconds)
    if curves is None:
        log.info("no heatmap for this source; that signal is unavailable")
        return None

    raw, corrected = curves
    raws: dict[str, float] = {}
    details: dict[str, dict[str, float]] = {}
    for candidate in candidates:
        features = features_for(candidate, corrected, raw)
        details[candidate.candidate_id] = features
        raws[candidate.candidate_id] = raw_score(features)

    log.debug("heatmap signal computed for %d candidates", len(candidates))
    return raws, details
