"""Crop geometry and static framing.

The invariants that matter: a crop never leaves the source (green bands), the
frame holds the whole face for the whole shot, and graphics beside the speaker
are kept rather than cut.
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
)
from clipper.render.overlays import OverlayBox

HD = (1920, 1080)
VERTICAL = (1080, 1920)


# 320px in a 1920-wide frame is 16.7%, comfortably above the threshold below
# which a face is treated as an overlay inset rather than the subject. The
# earlier default of 200px (10.4%) now reads as an inset, which is correct
# behaviour but not what these tests are about.
def face_at(t: float, x: float, y: float = 480, size: float = 320) -> FaceObservation:
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


def choose(samples, **kwargs):
    return choose_layout(
        samples, src_w=kwargs.pop("src_w", 1920), src_h=kwargs.pop("src_h", 1080),
        out_w=1080, out_h=1920,
        min_face_ratio=kwargs.pop("min_face_ratio", 0.5), **kwargs,
    )


def holds_every_face(plan, samples) -> bool:
    """True if every detected face lies wholly inside the planned frame."""
    if plan.kind == "blurred_fit":
        return True
    x0 = plan.keyframes[0].x
    x1 = x0 + plan.crop_width
    return all(x0 <= f.x - f.width / 2 and f.x + f.width / 2 <= x1
               for sample in samples for f in sample)


class TestStaticFraming:
    """The panning camera is gone; every shot gets one frame that holds it all.

    The regression these guard: a panning crop trailed a speaker who leaned
    across the shot, and the face was fully in frame in only 12 of 27 samples.
    """

    def test_a_still_face_fills_the_frame(self):
        plan = choose([[face_at(i * 0.2, 960)] for i in range(30)])
        assert plan.kind == "follow_crop"
        assert len(plan.keyframes) == 1

    def test_a_moving_face_is_held_by_a_wider_frame_not_followed(self):
        """The 003 regression: face centres swinging across ~340px."""
        xs = [1290, 1110, 1127, 1114, 1070, 1026, 1006, 1024, 1031, 1027, 1013, 1027,
              1086, 1170, 1200, 1174, 1168, 1129, 1099, 1059, 1072, 1031, 972, 953,
              1044, 1157, 1297]
        samples = [[FaceObservation(t=i * 0.2, x=x, y=450, width=380, height=440)]
                   for i, x in enumerate(xs)]
        plan = choose(samples)
        assert plan.kind == "fit_crop"
        assert len(plan.keyframes) == 1
        assert plan.crop_width > 608
        assert holds_every_face(plan, samples)

    @given(st.lists(st.floats(min_value=300, max_value=1620, allow_nan=False),
                    min_size=25, max_size=60),
           st.floats(min_value=260, max_value=420))
    def test_every_face_is_in_frame_bar_the_trimmed_extremes(self, xs, width):
        """Wherever a lone subject goes, the frame holds it.

        The one allowance is the trim that ignores stray detections: at most
        `EXTENT_TRIM` of samples at each extreme may fall outside. That is the
        whole guarantee, stated exactly -- before this change the allowance was
        20% per side plus however far a lagging camera trailed.
        """
        samples = [[FaceObservation(t=i * 0.2, x=x, y=450, width=width, height=width)]
                   for i, x in enumerate(xs)]
        plan = choose(samples)
        if plan.kind == "blurred_fit":
            return
        x0, x1 = plan.keyframes[0].x, plan.keyframes[0].x + plan.crop_width
        outside = sum(1 for (f,) in samples
                      if f.x - f.width / 2 < x0 or f.x + f.width / 2 > x1)
        allowed = 2 * int(len(samples) * layouts.EXTENT_TRIM)
        # A face far enough from the rest starts its own track and is not the
        # subject at all, so it is not held either.
        gate = layouts.TRACK_MATCH_DISTANCE * 1920
        strays = sum(1 for a, b in pairwise(xs) if abs(a - b) > gate)
        assert outside <= allowed + strays

    def test_the_frame_never_leaves_the_source(self):
        samples = [[face_at(i * 0.2, 1880)] for i in range(30)]
        plan = choose(samples)
        assert plan.keyframes[0].x >= 0
        assert plan.keyframes[0].x + plan.crop_width <= 1920


class TestGraphicsAreKept:
    """Reported with screenshots: a card sliced at the frame edge, a subtitle
    bar cut to its middle 608px. A graphic on screen must end up in frame."""

    CARD = OverlayBox(x=81, y=75, width=597, height=489, kind="card")
    SUBTITLE = OverlayBox(x=399, y=996, width=1128, height=54, kind="text")

    def test_a_card_beside_the_speaker_widens_the_frame(self):
        samples = [[face_at(i * 0.2, 905, size=330)] for i in range(20)]
        plan = choose(samples, overlays=[self.CARD])
        assert plan.kind == "fit_crop"
        x0 = plan.keyframes[0].x
        assert x0 <= self.CARD.x and self.CARD.right <= x0 + plan.crop_width
        assert holds_every_face(plan, samples)

    def test_a_subtitle_bar_is_kept_whole(self):
        samples = [[face_at(i * 0.2, 1189, size=390)] for i in range(30)]
        plan = choose(samples, overlays=[self.SUBTITLE])
        x0 = plan.keyframes[0].x
        assert x0 <= self.SUBTITLE.x
        assert self.SUBTITLE.right <= x0 + plan.crop_width

    def test_without_graphics_the_same_shot_fills_the_frame(self):
        samples = [[face_at(i * 0.2, 905, size=330)] for i in range(20)]
        assert choose(samples).kind == "follow_crop"

    def test_a_graphic_prevents_a_stack_that_would_drop_it(self):
        """A stack shows two faces and nothing else."""
        samples = [[face_at(i * 0.2, 480), face_at(i * 0.2, 1440)] for i in range(30)]
        assert choose(samples).kind == "two_speaker_stack"
        plan = choose(samples, overlays=[self.SUBTITLE])
        assert plan.kind in ("fit_crop", "blurred_fit")

    def test_a_graphic_spanning_the_frame_keeps_the_whole_frame(self):
        wide = OverlayBox(x=20, y=900, width=1880, height=60, kind="text")
        samples = [[face_at(i * 0.2, 960)] for i in range(20)]
        assert choose(samples, overlays=[wide]).kind == "blurred_fit"


class TestChooseLayout:
    def _choose(self, samples, **kwargs):
        return choose(samples, **kwargs)

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

    def test_one_persistent_face_gets_a_subject_frame(self):
        samples = [[face_at(i * 0.2, 800 + i * 5)] for i in range(30)]
        plan = self._choose(samples)
        assert plan.kind in ("follow_crop", "fit_crop")
        assert holds_every_face(plan, samples)

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
        assert LayoutPlan(kind="fit_crop").is_face_centric
        assert LayoutPlan(kind="two_speaker_stack").is_face_centric
        assert not LayoutPlan(kind="blurred_fit").is_face_centric
