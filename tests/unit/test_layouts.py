"""Crop geometry and camera smoothing.

The invariants that matter: a crop never leaves the source (green bands), the
camera never exceeds its speed limit (lurching), and a face keeps headroom.
"""

from __future__ import annotations

from itertools import pairwise

import pytest
from hypothesis import given
from hypothesis import strategies as st

from clipper.render import layouts
from clipper.render.layouts import (
    FaceObservation,
    choose_layout,
    clamp_crop_origin,
    crop_origin_for_face,
    crop_size_for_aspect,
    plan_follow_crop,
    smooth_trajectory,
)

HD = (1920, 1080)
VERTICAL = (1080, 1920)


def face_at(t: float, x: float, y: float = 480, size: float = 200) -> FaceObservation:
    return FaceObservation(t=t, x=x, y=y, width=size, height=size)


class TestCropSize:
    def test_16x9_to_9x16_is_height_limited(self):
        w, h = crop_size_for_aspect(1920, 1080, 1080, 1920)
        assert h == 1080, "should use the full source height"
        assert w == pytest.approx(1080 * 9 / 16, abs=2)

    def test_result_has_the_output_aspect(self):
        w, h = crop_size_for_aspect(1920, 1080, 1080, 1920)
        assert w / h == pytest.approx(1080 / 1920, rel=0.01)

    def test_never_exceeds_the_source(self):
        for src in [(1920, 1080), (1280, 720), (640, 480), (1080, 1920)]:
            w, h = crop_size_for_aspect(*src, 1080, 1920)
            assert w <= src[0] and h <= src[1]

    def test_dimensions_are_even_for_yuv420p(self):
        """Odd dimensions make libx264 refuse or silently round."""
        for src in [(1921, 1081), (1919, 1079), (1280, 721)]:
            w, h = crop_size_for_aspect(*src, 1080, 1920)
            assert w % 2 == 0 and h % 2 == 0, src

    def test_vertical_source_is_width_limited(self):
        w, h = crop_size_for_aspect(1080, 1920, 1080, 1920)
        assert (w, h) == (1080, 1920)

    def test_rejects_nonpositive(self):
        with pytest.raises(ValueError, match="positive"):
            crop_size_for_aspect(0, 1080, 1080, 1920)

    @given(
        st.integers(min_value=64, max_value=4096),
        st.integers(min_value=64, max_value=4096),
    )
    def test_always_fits_inside_the_source(self, src_w, src_h):
        w, h = crop_size_for_aspect(src_w, src_h, 1080, 1920)
        assert 0 < w <= src_w and 0 < h <= src_h


class TestClamping:
    def test_left_edge(self):
        assert clamp_crop_origin(-500, 0, 608, 1080, *HD) == (0, 0)

    def test_right_edge(self):
        x, _ = clamp_crop_origin(9999, 0, 608, 1080, *HD)
        assert x == 1920 - 608

    def test_inside_is_untouched(self):
        assert clamp_crop_origin(400, 0, 608, 1080, *HD) == (400, 0)

    def test_crop_larger_than_source_pins_to_origin(self):
        assert clamp_crop_origin(50, 50, 4000, 4000, *HD) == (0, 0)

    @given(
        st.floats(min_value=-10_000, max_value=10_000, allow_nan=False),
        st.floats(min_value=-10_000, max_value=10_000, allow_nan=False),
    )
    def test_never_escapes_the_source(self, x, y):
        cw, ch = 608, 1080
        cx, cy = clamp_crop_origin(x, y, cw, ch, *HD)
        assert 0 <= cx <= 1920 - cw
        assert 0 <= cy <= 1080 - ch


class TestFaceFraming:
    def test_face_is_horizontally_centred(self):
        cw, ch = crop_size_for_aspect(*HD, *VERTICAL)
        x, _ = crop_origin_for_face(960, 480, cw, ch, *HD)
        assert x + cw / 2 == pytest.approx(960, abs=1)

    def test_face_sits_above_centre_leaving_headroom(self):
        """Eyeline high in frame; a dead-centred face looks wrong.

        Uses a source with vertical slack: on a 16:9 source the 9:16 crop takes
        the full height, so there is nowhere to shift and the anchor cannot
        apply. That clamped case is covered separately below.
        """
        src_w, src_h = 1000, 2000
        cw, ch = crop_size_for_aspect(src_w, src_h, 1080, 1920)
        assert ch < src_h, "fixture must leave vertical slack for this to mean anything"

        _, y = crop_origin_for_face(500, 900, cw, ch, src_w, src_h)
        face_position = (900 - y) / ch
        assert face_position < 0.5
        assert face_position == pytest.approx(layouts.FACE_VERTICAL_ANCHOR, abs=0.02)

    def test_no_vertical_slack_means_no_headroom_shift(self):
        """On 16:9 the 9:16 crop is full-height, so y is pinned to 0."""
        cw, ch = crop_size_for_aspect(*HD, *VERTICAL)
        assert ch == 1080
        _, y = crop_origin_for_face(960, 300, cw, ch, *HD)
        assert y == 0

    def test_face_near_the_edge_still_yields_a_legal_crop(self):
        cw, ch = crop_size_for_aspect(*HD, *VERTICAL)
        for face_x in (0, 30, 1890, 1920):
            x, y = crop_origin_for_face(face_x, 480, cw, ch, *HD)
            assert 0 <= x <= 1920 - cw
            assert 0 <= y <= 1080 - ch


class TestSmoothing:
    def test_empty_input(self):
        assert smooth_trajectory([], alpha=0.2, max_step=100) == []

    def test_constant_target_converges_and_stays(self):
        targets = [(i * 0.2, 500.0) for i in range(30)]
        out = smooth_trajectory(targets, alpha=0.3, max_step=1e9)
        assert out[-1] == pytest.approx(500.0, abs=1.0)

    def test_lags_a_step_change_rather_than_snapping(self):
        """Instant response to a detector jump reads as a lurch."""
        targets = [(0.0, 0.0), (0.2, 1000.0)]
        out = smooth_trajectory(targets, alpha=0.2, max_step=1e9)
        assert out[1] < 1000.0

    def test_speed_limit_is_enforced(self):
        targets = [(i * 0.2, 0.0 if i == 0 else 5000.0) for i in range(10)]
        out = smooth_trajectory(targets, alpha=1.0, max_step=100.0)
        for prev, cur in pairwise(out):
            assert abs(cur - prev) <= 100.0 * 0.2 + 1e-6

    def test_initial_position_is_honoured(self):
        out = smooth_trajectory([(0.0, 100.0)], alpha=0.5, max_step=1e9, initial=0.0)
        assert out[0] == pytest.approx(50.0)

    def test_rejects_bad_alpha(self):
        for alpha in (0.0, -0.1, 1.5):
            with pytest.raises(ValueError, match="alpha"):
                smooth_trajectory([(0.0, 1.0)], alpha=alpha, max_step=10)

    @given(st.lists(st.floats(min_value=0, max_value=1920, allow_nan=False),
                    min_size=2, max_size=60))
    def test_output_stays_within_the_input_range(self, values):
        """Smoothing interpolates; it must never overshoot past the extremes."""
        targets = [(i * 0.2, v) for i, v in enumerate(values)]
        out = smooth_trajectory(targets, alpha=0.3, max_step=1e9)
        assert min(out) >= min(values) - 1e-6
        assert max(out) <= max(values) + 1e-6


class TestPlanFollowCrop:
    def _plan(self, faces, **kwargs):
        return plan_follow_crop(
            faces, src_w=1920, src_h=1080, out_w=1080, out_h=1920,
            duration=kwargs.pop("duration", 10.0),
            pan_smoothing=kwargs.pop("pan_smoothing", 0.15),
            max_pan_speed=kwargs.pop("max_pan_speed", 0.25),
            **kwargs,
        )

    def test_no_faces_falls_back_to_a_centred_hold(self):
        plan = self._plan([])
        assert plan.kind == "follow_crop"
        assert len(plan.keyframes) == 1
        assert plan.face_ratio == 0.0
        assert "centred" in plan.reason

    def test_keyframes_are_all_legal_crops(self):
        faces = [face_at(i * 0.2, 200 + i * 30) for i in range(50)]
        plan = self._plan(faces)
        for kf in plan.keyframes:
            assert 0 <= kf.x <= 1920 - plan.crop_width
            assert 0 <= kf.y <= 1080 - plan.crop_height

    def test_trajectory_follows_the_face(self):
        """A face crossing left to right must drag the crop the same way."""
        faces = [face_at(i * 0.2, 300 + i * 25) for i in range(40)]
        plan = self._plan(faces)
        assert plan.keyframes[-1].x > plan.keyframes[0].x

    def test_a_static_face_collapses_to_few_keyframes(self):
        """Hundreds of identical entries would bloat the sendcmd script."""
        faces = [face_at(i * 0.2, 960) for i in range(50)]
        plan = self._plan(faces)
        assert len(plan.keyframes) < 20

    def test_first_keyframe_is_at_time_zero(self):
        """Without t=0 the crop holds its default origin until the first cue."""
        faces = [face_at(1.0 + i * 0.2, 400 + i * 20) for i in range(20)]
        plan = self._plan(faces)
        assert plan.keyframes[0].t == 0.0

    def test_pan_speed_limit_holds_across_keyframes(self):
        faces = [face_at(0.0, 200), face_at(0.2, 1800), face_at(0.4, 200)]
        plan = self._plan(faces, max_pan_speed=0.1, pan_smoothing=1.0)
        limit = 0.1 * 1920 * 0.2
        for a, b in zip(plan.keyframes, plan.keyframes[1:], strict=False):
            if b.t > a.t:
                assert abs(b.x - a.x) <= limit + 1.5

    def test_scene_cut_allows_an_immediate_jump(self):
        """Easing across a hard cut looks like a mistake, so tracking restarts."""
        faces = [face_at(i * 0.2, 300) for i in range(5)]
        faces += [face_at(1.0 + i * 0.2, 1600) for i in range(5)]
        smooth = self._plan(faces, pan_smoothing=0.15)
        cut = self._plan(faces, pan_smoothing=0.15, scene_cuts=[1.0])
        assert cut.keyframes[-1].x > smooth.keyframes[-1].x


class TestChooseLayout:
    def _choose(self, samples, **kwargs):
        return choose_layout(
            samples, src_w=kwargs.pop("src_w", 1920), src_h=kwargs.pop("src_h", 1080),
            out_w=1080, out_h=1920, duration=10.0,
            pan_smoothing=0.15, max_pan_speed=0.25,
            min_face_ratio=kwargs.pop("min_face_ratio", 0.5), **kwargs,
        )

    def test_vertical_source_is_never_cropped(self):
        plan = self._choose([[face_at(0, 540)]] * 20, src_w=1080, src_h=1920)
        assert plan.kind == "blurred_fit"
        assert "vertical" in plan.reason

    def test_no_faces_gives_blurred_fit(self):
        plan = self._choose([[] for _ in range(20)])
        assert plan.kind == "blurred_fit"

    def test_sparse_faces_give_blurred_fit(self):
        samples = [[face_at(i * 0.2, 960)] if i % 5 == 0 else [] for i in range(20)]
        plan = self._choose(samples)
        assert plan.kind == "blurred_fit"
        assert "20%" in plan.reason

    def test_one_persistent_face_gives_follow_crop(self):
        samples = [[face_at(i * 0.2, 800 + i * 5)] for i in range(30)]
        plan = self._choose(samples)
        assert plan.kind == "follow_crop"

    def test_two_separated_faces_give_a_stack(self):
        samples = [[face_at(i * 0.2, 480), face_at(i * 0.2, 1440)] for i in range(30)]
        plan = self._choose(samples)
        assert plan.kind == "two_speaker_stack"
        assert len(plan.panes) == 2
        assert plan.panes[0].x < plan.panes[1].x

    def test_two_close_detections_are_not_a_two_shot(self):
        """Two boxes on one person's face must not trigger a split screen."""
        samples = [[face_at(i * 0.2, 940), face_at(i * 0.2, 980)] for i in range(30)]
        plan = self._choose(samples)
        assert plan.kind == "follow_crop"

    def test_a_brief_second_face_does_not_trigger_a_stack(self):
        samples = [[face_at(i * 0.2, 480)] for i in range(24)]
        samples += [[face_at(5.0, 480), face_at(5.0, 1440)] for _ in range(6)]
        plan = self._choose(samples)
        assert plan.kind == "follow_crop"

    def test_stack_panes_are_legal_crops(self):
        samples = [[face_at(i * 0.2, 300), face_at(i * 0.2, 1700)] for i in range(30)]
        plan = self._choose(samples)
        for pane in plan.panes:
            assert 0 <= pane.x <= 1920 - pane.width
            assert 0 <= pane.y <= 1080 - pane.height

    def test_no_samples_at_all(self):
        plan = self._choose([])
        assert plan.kind == "blurred_fit"
        assert "no frames" in plan.reason


class TestFaceCentric:
    def test_qa_only_enforces_faces_for_face_layouts(self):
        from clipper.models import LayoutPlan

        assert LayoutPlan(kind="follow_crop").is_face_centric
        assert LayoutPlan(kind="two_speaker_stack").is_face_centric
        assert not LayoutPlan(kind="blurred_fit").is_face_centric
