"""Refining a selected clip's start and end (docs/BUILD_BRIEF.md section 10).

Candidate windows are sentence-aligned, which is correct but blunt. A clip that
opens on "So, um, yeah, the thing is..." wastes the three seconds that decide
whether anyone watches. These rules clean that up.

The invariant every rule respects: **never cut into a word.** Padding only
expands into silence, and trimming only moves to another word's start. A clip
that clips a syllable sounds broken in a way viewers notice immediately.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..config import RefineConfig
from ..models import Sentence, Transcript, Word


@dataclass
class RefinedBounds:
    """The adjusted boundaries plus a record of what moved them."""

    start: float
    end: float
    notes: list[str] = field(default_factory=list)
    dropped: bool = False
    drop_reason: str = ""

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


def refine(
    start: float,
    end: float,
    *,
    transcript: Transcript,
    sentences: list[Sentence],
    cfg: RefineConfig,
    min_duration: float,
    max_duration: float,
    source_duration: float,
) -> RefinedBounds:
    """Apply section 10's refinement rules in order.

    Order is load-bearing: filler is trimmed before padding is added, or the
    pre-roll would be measured from the filler's start and then thrown away.
    """
    bounds = RefinedBounds(start=start, end=end)

    _snap_to_sentences(bounds, sentences)
    _trim_leading_filler(bounds, transcript, cfg)
    _extend_to_finish_thought(bounds, sentences, cfg, max_duration)
    _add_padding(bounds, transcript, cfg, source_duration)
    _enforce_duration(bounds, min_duration, max_duration)

    return bounds


def _snap_to_sentences(bounds: RefinedBounds, sentences: list[Sentence]) -> None:
    """Move the start to the nearest sentence start, the end to a sentence end.

    Preference for the end is a sentence that ends on real punctuation: a clip
    that stops at a pause-derived boundary often sounds cut off.
    """
    if not sentences:
        return

    nearest_start = min(sentences, key=lambda s: abs(s.start - bounds.start))
    if abs(nearest_start.start - bounds.start) > 1e-6:
        bounds.notes.append(
            f"snapped start {bounds.start:.2f}s -> {nearest_start.start:.2f}s (sentence)"
        )
        bounds.start = nearest_start.start

    candidates = [s for s in sentences if s.end > bounds.start]
    if not candidates:
        return

    nearest_end = min(candidates, key=lambda s: abs(s.end - bounds.end))

    # The punctuation preference may only pull the end *forward*. Allowing it to
    # search backwards lets a nearby earlier full stop win over the correct
    # boundary and silently drop whole sentences -- e.g. a window ending at
    # 2.70s snapping back to 0.60s because that was the closest punctuated end,
    # shrinking the clip to a fifth of its length. Finishing a thought is a
    # forward operation by definition.
    punctuated = [
        s for s in candidates
        if s.ends_with_terminal_punctuation
        and s.end >= nearest_end.end - 1e-6
        and abs(s.end - bounds.end) <= 3.0
    ]
    chosen = min(punctuated, key=lambda s: abs(s.end - bounds.end)) if punctuated else nearest_end

    if abs(chosen.end - bounds.end) > 1e-6:
        why = "sentence end with punctuation" if punctuated else "sentence end"
        bounds.notes.append(f"snapped end {bounds.end:.2f}s -> {chosen.end:.2f}s ({why})")
        bounds.end = chosen.end


def _trim_leading_filler(bounds: RefinedBounds, transcript: Transcript,
                         cfg: RefineConfig) -> None:
    """Skip filler words in the opening window, to the first content word."""
    if cfg.filler_window <= 0:
        return

    words = [w for w in transcript.words
             if bounds.start - 1e-6 <= w.start < bounds.start + cfg.filler_window]
    if not words:
        return

    fillers = set(cfg.filler_words)
    trimmed_to: Word | None = None
    for word in words:
        if word.normalized in fillers:
            trimmed_to = word
            continue
        break

    if trimmed_to is None:
        return

    # The first content word after the run of filler.
    following = [w for w in transcript.words if w.start > trimmed_to.start]
    if not following:
        return
    new_start = following[0].start
    if new_start >= bounds.end:
        return

    skipped = [w.text for w in words if w.start < new_start]
    bounds.notes.append(
        f"trimmed leading filler ({' '.join(skipped)}): "
        f"{bounds.start:.2f}s -> {new_start:.2f}s"
    )
    bounds.start = new_start


def _extend_to_finish_thought(bounds: RefinedBounds, sentences: list[Sentence],
                              cfg: RefineConfig, max_duration: float) -> None:
    """Extend past an unfinished closing sentence, within limits."""
    if cfg.max_extend_seconds <= 0:
        return

    closing = [s for s in sentences if abs(s.end - bounds.end) < 1e-6]
    if closing and closing[0].ends_with_terminal_punctuation:
        return  # already ends on a finished thought

    later = [s for s in sentences
             if s.end > bounds.end and s.ends_with_terminal_punctuation]
    if not later:
        return

    target = later[0]
    extra = target.end - bounds.end
    if extra > cfg.max_extend_seconds:
        bounds.notes.append(
            f"left ending unfinished: completing it needs {extra:.1f}s, "
            f"over the {cfg.max_extend_seconds:.0f}s limit"
        )
        return
    if (target.end - bounds.start) > max_duration:
        bounds.notes.append(
            f"left ending unfinished: completing it would exceed the "
            f"{max_duration:.0f}s campaign maximum"
        )
        return

    bounds.notes.append(
        f"extended {extra:.2f}s to finish the closing thought "
        f"({bounds.end:.2f}s -> {target.end:.2f}s)"
    )
    bounds.end = target.end


def _add_padding(bounds: RefinedBounds, transcript: Transcript, cfg: RefineConfig,
                 source_duration: float) -> None:
    """Add pre/post-roll, but only into silence -- never over a neighbouring word."""
    before = [w for w in transcript.words if w.end <= bounds.start + 1e-6]
    after = [w for w in transcript.words if w.start >= bounds.end - 1e-6]

    # Pre-roll may not reach back past the previous word's end.
    headroom = bounds.start - before[-1].end if before else bounds.start
    pre = max(0.0, min(cfg.pre_roll, headroom))

    # Post-roll may not reach forward into the next word's start.
    tailroom = after[0].start - bounds.end if after else source_duration - bounds.end
    if after:
        tailroom -= cfg.tail_guard
    post = max(0.0, min(cfg.post_roll, tailroom))

    if pre > 0:
        bounds.start = max(0.0, bounds.start - pre)
    if post > 0:
        bounds.end = min(source_duration, bounds.end + post)
    if pre > 0 or post > 0:
        bounds.notes.append(f"padded -{pre:.2f}s / +{post:.2f}s into silence")


def _enforce_duration(bounds: RefinedBounds, min_duration: float, max_duration: float) -> None:
    """Drop a clip that refinement pushed outside the campaign's bounds."""
    if bounds.duration < min_duration:
        bounds.dropped = True
        bounds.drop_reason = (
            f"after refinement the clip is {bounds.duration:.1f}s, under the "
            f"campaign minimum of {min_duration:.0f}s"
        )
    elif bounds.duration > max_duration:
        bounds.dropped = True
        bounds.drop_reason = (
            f"after refinement the clip is {bounds.duration:.1f}s, over the "
            f"campaign maximum of {max_duration:.0f}s"
        )
