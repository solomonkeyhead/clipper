"""Splitting a clip into shots and framing each one on its own.

Why this exists, from a measured failure on real footage:

A 35-second clip of an edited podcast contained two shots -- a wide two-shot
with a b-roll box between the speakers, then a 27-second close-up of one of
them. The pipeline chose *one* framing for the whole clip. The close-up
dominated the face statistics, so the clip was cropped to a 607px-wide column
in the middle of the frame. That column is correct for the close-up and is the
worst possible choice for the wide shot, where both people sit at the far left
and far right: the output showed a wall, a book and two disembodied arms.

No single crop can serve both shots, because the source editor deliberately
changed the composition. So the fix is not a better global crop -- it is to stop
choosing globally. Each shot is framed on its own, and the framing changes only
where the source already cuts, which is invisible to the viewer because there is
a cut there anyway.

This also means the output cuts between speakers whenever the source does, which
is the effect a continuously panning camera was reaching for and kept failing at
(see `layouts.choose_layout`). The source editor already decided who to look at;
following their cuts is both better-looking and far cheaper than guessing.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import pairwise

from ..models import LayoutPlan, LayoutSegment
from .layouts import FaceObservation, choose_layout

# A shot shorter than this is merged into a neighbour. Below roughly a second
# and a half a framing change reads as a glitch rather than as an edit, and the
# face scan has too few samples (7 at the default 5 fps) to decide anything.
MIN_SHOT_SECONDS = 1.5

# An upper bound on segments, so a heavily cut source cannot produce a filter
# graph with dozens of branches. The shortest shots merge until this is met.
MAX_SHOTS = 8


@dataclass(frozen=True)
class Shot:
    """A span of one clip that holds a single camera setup."""

    start: float
    end: float
    first: int  # index into the sample arrays, inclusive
    last: int  # exclusive

    @property
    def duration(self) -> float:
        return self.end - self.start

    @property
    def samples(self) -> int:
        return self.last - self.first


def split_into_shots(
    sample_times: list[float],
    scene_cuts: list[float],
    duration: float,
    *,
    min_seconds: float = MIN_SHOT_SECONDS,
    max_shots: int = MAX_SHOTS,
) -> list[Shot]:
    """Group sampled instants into shots at the detected cuts.

    Short shots are merged away first, then the shortest remaining ones, until
    at most `max_shots` are left. Merging is into whichever neighbour is
    shorter, which keeps the surviving shots closer to equal length rather than
    growing one of them without bound.
    """
    if not sample_times:
        return []

    boundaries = sorted({c for c in scene_cuts if 0.0 < c < duration})
    shots: list[Shot] = []
    edges = [0.0, *boundaries, duration]
    for start, end in pairwise(edges):
        first = _first_index_at_or_after(sample_times, start)
        last = _first_index_at_or_after(sample_times, end)
        if last > first:
            shots.append(Shot(start=start, end=end, first=first, last=last))

    if not shots:
        return [Shot(start=0.0, end=duration, first=0, last=len(sample_times))]

    # Make the first shot start at 0 and the last end at the clip's end, so the
    # segments tile the clip exactly with no gap for the renderer to fall into.
    shots[0] = Shot(start=0.0, end=shots[0].end, first=0, last=shots[0].last)
    shots[-1] = Shot(start=shots[-1].start, end=duration,
                     first=shots[-1].first, last=len(sample_times))

    shots = _merge_while(shots, lambda s: min(s, key=lambda x: x.duration)
                         if any(x.duration < min_seconds for x in s) else None)
    shots = _merge_while(shots, lambda s: min(s, key=lambda x: x.duration)
                         if len(s) > max_shots else None)
    return shots


def _merge_while(shots: list[Shot], pick) -> list[Shot]:
    """Repeatedly merge the shot `pick` selects into its shorter neighbour."""
    while len(shots) > 1:
        target = pick(shots)
        if target is None:
            return shots
        i = shots.index(target)
        if i == 0:
            j = 1
        elif i == len(shots) - 1:
            j = i - 1
        else:
            j = i - 1 if shots[i - 1].duration <= shots[i + 1].duration else i + 1
        lo, hi = sorted((i, j))
        merged = Shot(start=shots[lo].start, end=shots[hi].end,
                      first=shots[lo].first, last=shots[hi].last)
        shots = [*shots[:lo], merged, *shots[hi + 1:]]
    return shots


def _first_index_at_or_after(times: list[float], value: float) -> int:
    for i, t in enumerate(times):
        if t >= value:
            return i
    return len(times)


def plan_per_shot(
    faces_per_sample: list[list[FaceObservation]],
    sample_times: list[float],
    scene_cuts: list[float],
    *,
    src_w: int,
    src_h: int,
    out_w: int,
    out_h: int,
    duration: float,
    min_shot_seconds: float = MIN_SHOT_SECONDS,
    max_shots: int = MAX_SHOTS,
    **layout_kwargs,
) -> LayoutPlan:
    """Frame each shot separately, or fall back to one framing for the clip.

    Returns an ordinary single-layout `LayoutPlan` when the clip turns out to
    hold one shot, or when every shot independently reaches the same framing --
    there is no reason to pay for a segmented filter graph to render what is in
    fact one composition.
    """
    shots = split_into_shots(sample_times, scene_cuts, duration,
                             min_seconds=min_shot_seconds, max_shots=max_shots)

    def whole_clip() -> LayoutPlan:
        return choose_layout(
            faces_per_sample, src_w=src_w, src_h=src_h, out_w=out_w, out_h=out_h,
            duration=duration, scene_cuts=scene_cuts, **layout_kwargs,
        )

    if len(shots) <= 1:
        return whole_clip()

    segments: list[LayoutSegment] = []
    for shot in shots:
        plan = choose_layout(
            faces_per_sample[shot.first:shot.last],
            src_w=src_w, src_h=src_h, out_w=out_w, out_h=out_h,
            duration=shot.duration,
            # Cuts are what separate the shots, so by construction there are
            # none left inside one.
            scene_cuts=[],
            **layout_kwargs,
        )
        segments.append(LayoutSegment(
            start=round(shot.start, 3), end=round(shot.end, 3),
            layout=_rebase(plan, shot.start),
        ))

    if _all_identical(segments):
        return whole_clip()

    face_ratio = sum(s.layout.face_ratio * (s.end - s.start) for s in segments)
    kinds = ", ".join(_kind_summary(segments))
    return LayoutPlan(
        kind="per_shot",
        segments=segments,
        face_ratio=face_ratio / duration if duration > 0 else 0.0,
        reason=f"{len(segments)} shots framed separately ({kinds})",
    )


def _rebase(plan: LayoutPlan, offset: float) -> LayoutPlan:
    """Shift a segment's keyframe times so they start at zero.

    Each segment is trimmed and its timestamps reset before its own filter
    chain runs, so a trajectory planned against clip time would be applied at
    the wrong moment -- or, for a segment starting after the clip's end of the
    trajectory, never applied at all.
    """
    if not plan.keyframes:
        return plan
    shifted = [kf.model_copy(update={"t": round(max(0.0, kf.t - offset), 3)})
               for kf in plan.keyframes]
    return plan.model_copy(update={"keyframes": shifted})


def _all_identical(segments: list[LayoutSegment]) -> bool:
    """True when every segment reached the same framing, geometry included."""
    first = segments[0].layout
    return all(
        s.layout.kind == first.kind
        and s.layout.panes == first.panes
        and s.layout.crop_width == first.crop_width
        and s.layout.crop_height == first.crop_height
        and [(kf.x, kf.y) for kf in s.layout.keyframes]
        == [(kf.x, kf.y) for kf in first.keyframes]
        for s in segments[1:]
    )


def _kind_summary(segments: list[LayoutSegment]) -> list[str]:
    """`['2x blurred_fit', 'follow_crop']` -- readable in a manifest."""
    out: list[str] = []
    for segment in segments:
        kind = segment.layout.kind
        if out and out[-1].endswith(kind):
            count = int(out[-1].split("x ")[0]) + 1 if "x " in out[-1] else 2
            out[-1] = f"{count}x {kind}"
        else:
            out.append(kind)
    return out
