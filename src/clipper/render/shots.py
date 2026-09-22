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
from .overlays import OverlayBox, persistent_regions

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
    overlays_per_sample: list[list[OverlayBox]] | None = None,
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
    overlays = overlays_per_sample or [[] for _ in faces_per_sample]
    shots = split_into_shots(sample_times, scene_cuts, duration,
                             min_seconds=min_shot_seconds, max_shots=max_shots)

    # A graphic on screen for only part of a long shot should widen only that
    # part. Otherwise a 27-second close-up with a 6-second insert is framed wide
    # for all 27 seconds.
    extra = [t for shot in shots
             for t in _graphic_boundaries(shot, overlays, sample_times,
                                          min_seconds=min_shot_seconds)]
    if extra:
        shots = split_into_shots(sample_times, sorted([*scene_cuts, *extra]), duration,
                                 min_seconds=min_shot_seconds, max_shots=max_shots)

    def frame(first: int, last: int) -> LayoutPlan:
        return choose_layout(
            faces_per_sample[first:last], src_w=src_w, src_h=src_h,
            out_w=out_w, out_h=out_h,
            overlays=persistent_regions(overlays[first:last]), **layout_kwargs,
        )

    if len(shots) <= 1:
        return frame(0, len(faces_per_sample))

    segments = [
        LayoutSegment(start=round(shot.start, 3), end=round(shot.end, 3),
                      layout=frame(shot.first, shot.last))
        for shot in shots
    ]
    if _all_identical(segments):
        return frame(0, len(faces_per_sample))

    face_ratio = sum(s.layout.face_ratio * (s.end - s.start) for s in segments)
    plan = LayoutPlan(
        kind="per_shot",
        segments=segments,
        face_ratio=face_ratio / duration if duration > 0 else 0.0,
    )
    return plan.model_copy(update={
        "reason": f"{len(segments)} shots framed separately ({plan.describe})"})


def _graphic_boundaries(
    shot: Shot,
    overlays: list[list[OverlayBox]],
    sample_times: list[float],
    *,
    min_seconds: float,
) -> list[float]:
    """Times inside a shot where a recurring graphic appears or disappears.

    Only shots long enough to hold two sub-shots are split. Presence is
    smoothed first -- detections are intermittent (the animated Dumbo card was
    found in 5 of 19 samples), so a raw on/off signal would flicker.
    """
    if shot.duration < 2 * min_seconds:
        return []
    window = overlays[shot.first:shot.last]
    regions = persistent_regions(window)
    if not regions:
        return []

    def covered(box: OverlayBox) -> bool:
        return any(_share_inside(box, r) >= 0.5 for r in regions if r.kind == box.kind)

    present = [any(covered(b) for b in boxes) for boxes in window]
    step = shot.duration / max(1, len(window))
    # Bridge gaps shorter than a shot could be; a graphic that drops out of
    # detection for a second has not left the screen.
    gap = max(1, round(min_seconds / step))
    present = _close_gaps(present, gap)

    times = sample_times[shot.first:shot.last]
    return [times[i] for i in range(1, len(present)) if present[i] != present[i - 1]]


def _share_inside(box: OverlayBox, region: OverlayBox) -> float:
    ix = max(0.0, min(box.right, region.right) - max(box.x, region.x))
    iy = max(0.0, min(box.bottom, region.bottom) - max(box.y, region.y))
    area = box.width * box.height
    return ix * iy / area if area > 0 else 0.0


def _close_gaps(flags: list[bool], gap: int) -> list[bool]:
    """Fill runs of False no longer than `gap` that lie between two Trues."""
    out = list(flags)
    trues = [i for i, f in enumerate(flags) if f]
    for a, b in pairwise(trues):
        if 1 < b - a <= gap + 1:
            for i in range(a + 1, b):
                out[i] = True
    return out


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
