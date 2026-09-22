"""Faces that are overlay insets must not drive the framing.

Two real sources motivated this, with opposite right answers:

* A chess video: a webcam inset beside a board. The board is a *separate*
  content region, so the two get stacked (`content_stack`).
* A reaction video: two small circular reaction cams over full-frame footage.
  There is no separate content region -- the footage *is* the whole frame -- so
  cropping to either cam, or stacking both, would discard everything the clip is
  about.

Before this rule the second case chose `two_speaker_stack` and would have blown
two tiny circular avatars up to fill the output.
"""

from __future__ import annotations

import pytest

from clipper.render.layouts import (
    INSET_FACE_WIDTH_RATIO,
    FaceObservation,
    choose_layout,
)

FRAME_W, FRAME_H = 1920, 1080


def samples(count: int, faces_per_frame: list[tuple[float, float, float]]):
    """Build `count` identical samples, each holding the given faces."""
    return [
        [FaceObservation(t=i * 0.2, x=x, y=y, width=w, height=w * 1.3)
         for x, y, w in faces_per_frame]
        for i in range(count)
    ]


def choose(per_sample, **kwargs):
    defaults = dict(
        src_w=FRAME_W, src_h=FRAME_H, out_w=1080, out_h=1920, duration=25.0,
        pan_smoothing=0.12, max_pan_speed=0.25, min_face_ratio=0.5,
    )
    defaults.update(kwargs)
    return choose_layout(per_sample, **defaults)


class TestInsetFacesAreNotTheSubject:
    def test_two_tiny_reaction_cams_do_not_become_a_two_shot(self):
        """The measured failure: 6-8% wide circular cams over full-frame footage."""
        per_sample = samples(100, [(150, 120, 130), (1770, 120, 130)])
        plan = choose(per_sample)
        assert plan.kind == "blurred_fit"
        assert "inset overlays" in plan.reason

    def test_one_tiny_inset_does_not_become_a_follow_crop(self):
        per_sample = samples(100, [(1600, 400, 130)])
        assert choose(per_sample).kind == "blurred_fit"

    def test_a_normal_talking_head_still_follow_crops(self):
        """The rule must not break the ordinary case."""
        per_sample = samples(100, [(960, 450, FRAME_W * 0.25)])
        assert choose(per_sample).kind == "follow_crop"

    def test_a_genuine_two_shot_still_stacks(self):
        per_sample = samples(100, [(500, 450, FRAME_W * 0.18),
                                   (1400, 450, FRAME_W * 0.18)])
        assert choose(per_sample).kind == "two_speaker_stack"

    @pytest.mark.parametrize("ratio", [0.04, 0.07, 0.10, 0.12])
    def test_anything_under_the_threshold_is_an_inset(self, ratio):
        per_sample = samples(80, [(960, 400, FRAME_W * ratio)])
        assert choose(per_sample).kind == "blurred_fit"

    @pytest.mark.parametrize("ratio", [0.15, 0.20, 0.30])
    def test_anything_over_the_threshold_is_the_subject(self, ratio):
        per_sample = samples(80, [(960, 400, FRAME_W * ratio)])
        assert choose(per_sample).kind == "follow_crop"

    def test_the_threshold_matches_the_screen_share_detector(self):
        """The two gates must agree, or a source can fall between them."""
        from clipper.render.regions import PIP_FACE_WIDTH_RATIO

        assert INSET_FACE_WIDTH_RATIO == PIP_FACE_WIDTH_RATIO

    def test_the_median_decides_not_a_single_outlier(self):
        """One frame where the detector latched onto something large must not
        flip a whole clip out of the inset rule."""
        per_sample = samples(60, [(1600, 400, 120)])
        per_sample[30] = [FaceObservation(t=6.0, x=960, y=450, width=800, height=900)]
        assert choose(per_sample).kind == "blurred_fit"

    def test_no_faces_is_unaffected(self):
        assert choose([[] for _ in range(40)]).kind == "blurred_fit"

    def test_a_vertical_source_is_still_short_circuited_first(self):
        per_sample = samples(40, [(540, 900, 100)])
        plan = choose(per_sample, src_w=1080, src_h=1920)
        assert plan.kind == "blurred_fit"
        assert "vertical" in plan.reason
