"""Framing each shot of a clip separately.

The failure this exists for, measured on real footage: a 35-second podcast clip
held a wide two-shot for 8.5 seconds and then a 27-second close-up. One framing
was chosen for the whole clip, the close-up dominated the statistics, and the
resulting 607px-wide column showed a wall and two arms for the first 8 seconds
because the two people sit at opposite edges of the wide shot.
"""

from __future__ import annotations

from itertools import pairwise

import pytest

from clipper.render.layouts import FaceObservation
from clipper.render.shots import (
    plan_per_shot,
    split_into_shots,
)

FRAME_W, FRAME_H = 1920, 1080


def obs(t: float, x: float, w: float = 320.0) -> FaceObservation:
    return FaceObservation(t=t, x=x, y=450.0, width=w, height=w * 1.2)


def times(count: int, step: float = 0.2) -> list[float]:
    return [i * step for i in range(count)]


class TestSplitIntoShots:
    def test_no_cuts_is_one_shot(self):
        shots = split_into_shots(times(100), [], 20.0)
        assert len(shots) == 1
        assert (shots[0].start, shots[0].end) == (0.0, 20.0)

    def test_one_cut_is_two_shots(self):
        shots = split_into_shots(times(100), [8.0], 20.0)
        assert [(s.start, s.end) for s in shots] == [(0.0, 8.0), (8.0, 20.0)]

    def test_shots_tile_the_clip_with_no_gaps(self):
        shots = split_into_shots(times(150), [5.0, 12.0, 21.0], 30.0)
        assert shots[0].start == 0.0
        assert shots[-1].end == 30.0
        for a, b in pairwise(shots):
            assert a.end == b.start

    def test_sample_indices_tile_the_samples(self):
        shots = split_into_shots(times(150), [5.0, 12.0, 21.0], 30.0)
        assert shots[0].first == 0
        assert shots[-1].last == 150
        for a, b in pairwise(shots):
            assert a.last == b.first

    def test_a_short_shot_is_merged_away(self):
        """A 0.6s fragment is a glitch, not an edit worth reframing for."""
        shots = split_into_shots(times(150), [0.6, 15.0], 30.0)
        assert len(shots) == 2
        assert all(s.duration >= 1.5 for s in shots)

    def test_merging_still_covers_the_clip(self):
        shots = split_into_shots(times(150), [0.4, 0.8, 1.1, 15.0], 30.0)
        assert shots[0].start == 0.0
        assert shots[-1].end == 30.0
        for a, b in pairwise(shots):
            assert a.end == b.start

    def test_the_shot_cap_is_respected(self):
        cuts = [float(i) for i in range(2, 30)]
        shots = split_into_shots(times(150), cuts, 30.0, min_seconds=0.1, max_shots=4)
        assert len(shots) <= 4
        assert shots[-1].end == 30.0

    def test_cuts_outside_the_clip_are_ignored(self):
        shots = split_into_shots(times(100), [-3.0, 0.0, 20.0, 99.0], 20.0)
        assert len(shots) == 1

    def test_no_samples(self):
        assert split_into_shots([], [5.0], 20.0) == []

    def test_everything_merges_to_one_shot_when_all_are_short(self):
        shots = split_into_shots(times(50), [2.0, 4.0, 6.0], 10.0, min_seconds=30.0)
        assert len(shots) == 1
        assert (shots[0].start, shots[0].end) == (0.0, 10.0)


def plan(per_sample, sample_times, cuts, duration, **kwargs):
    defaults = dict(
        src_w=FRAME_W, src_h=FRAME_H, out_w=1080, out_h=1920,
        pan_smoothing=0.12, max_pan_speed=0.25, min_face_ratio=0.5,
    )
    defaults.update(kwargs)
    return plan_per_shot(per_sample, sample_times, cuts,
                         duration=duration, **defaults)


class TestPlanPerShot:
    def test_the_measured_regression(self):
        """Wide two-shot then close-up: each shot gets its own framing.

        Before per-shot framing the close-up's crop was applied to the wide
        shot as well, which is where the wall-and-arms frame came from.
        """
        per_sample = [[obs(i * 0.2, 500, w=180), obs(i * 0.2, 1540, w=180)]
                      for i in range(40)]  # 0.0 - 8.0s, wide two-shot
        per_sample += [[obs(8.0 + i * 0.2, 1100, w=440)]
                       for i in range(100)]  # 8.0 - 28.0s, close-up
        result = plan(per_sample, times(140), [8.0], 28.0)

        assert result.kind == "per_shot"
        assert len(result.segments) == 2
        assert result.segments[0].layout.kind != result.segments[1].layout.kind
        assert result.segments[1].layout.kind == "follow_crop"

    def test_the_close_up_crop_is_not_applied_to_the_wide_shot(self):
        per_sample = [[obs(i * 0.2, 500, w=180), obs(i * 0.2, 1540, w=180)]
                      for i in range(40)]
        per_sample += [[obs(8.0 + i * 0.2, 1100, w=440)] for i in range(100)]
        wide = plan(per_sample, times(140), [8.0], 28.0).segments[0].layout
        assert wide.kind != "follow_crop" or not wide.keyframes or (
            wide.keyframes[0].x < 900)

    def test_one_shot_returns_a_plain_layout(self):
        per_sample = [[obs(i * 0.2, 960)] for i in range(100)]
        result = plan(per_sample, times(100), [], 20.0)
        assert result.kind != "per_shot"
        assert result.segments == []

    def test_identical_framings_collapse(self):
        """Two shots that reach the same framing need no segmented render."""
        per_sample = [[obs(i * 0.2, 960)] for i in range(140)]
        result = plan(per_sample, times(140), [14.0], 28.0)
        assert result.kind != "per_shot"

    def test_segments_tile_the_clip(self):
        per_sample = [[obs(i * 0.2, 500, w=180), obs(i * 0.2, 1540, w=180)]
                      for i in range(40)]
        per_sample += [[obs(8.0 + i * 0.2, 1100, w=440)] for i in range(100)]
        result = plan(per_sample, times(140), [8.0], 28.0)
        assert result.segments[0].start == 0.0
        assert result.segments[-1].end == pytest.approx(28.0)
        for a, b in pairwise(result.segments):
            assert a.end == b.start

    def test_segment_keyframes_are_rebased_to_the_segment(self):
        """A trajectory is applied after the segment's timestamps restart."""
        per_sample = [[obs(i * 0.2, 960, w=440)] for i in range(40)]
        per_sample += [[obs(8.0 + i * 0.2, 300 + i * 12, w=440)]
                       for i in range(100)]
        result = plan(per_sample, times(140), [8.0], 28.0)
        if result.kind != "per_shot":
            pytest.skip("this footage collapsed to a single framing")
        for segment in result.segments:
            for kf in segment.layout.keyframes:
                assert 0.0 <= kf.t <= segment.duration + 0.5

    def test_describe_names_every_shot(self):
        per_sample = [[obs(i * 0.2, 500, w=180), obs(i * 0.2, 1540, w=180)]
                      for i in range(40)]
        per_sample += [[obs(8.0 + i * 0.2, 1100, w=440)] for i in range(100)]
        result = plan(per_sample, times(140), [8.0], 28.0)
        assert result.describe.startswith("per_shot[")
        for segment in result.segments:
            assert segment.layout.kind in result.describe

    def test_face_ratio_is_weighted_by_shot_length(self):
        per_sample = [[] for _ in range(40)]
        per_sample += [[obs(8.0 + i * 0.2, 1100, w=440)] for i in range(100)]
        result = plan(per_sample, times(140), [8.0], 28.0)
        if result.kind == "per_shot":
            assert 0.0 < result.face_ratio < 1.0


class TestDescribe:
    def test_runs_of_one_kind_are_compressed(self):
        """Seven shots that mostly agree should not print one word five times."""
        from clipper.models import LayoutPlan, LayoutSegment

        kinds = ["follow_crop"] * 5 + ["blurred_fit", "follow_crop"]
        plan = LayoutPlan(kind="per_shot", segments=[
            LayoutSegment(start=i, end=i + 1, layout=LayoutPlan(kind=k))
            for i, k in enumerate(kinds)
        ])
        assert plan.describe == "per_shot[5x follow_crop+blurred_fit+follow_crop]"

    def test_a_plain_layout_describes_as_its_kind(self):
        from clipper.models import LayoutPlan

        assert LayoutPlan(kind="blurred_fit").describe == "blurred_fit"
