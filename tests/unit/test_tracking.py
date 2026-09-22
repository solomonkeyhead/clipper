"""Face tracking and group framing.

The bug these exist for: `_dominant_face` used to pick the largest face in each
frame independently, with no memory. With two people on screen the target
teleported between them -- measured on real footage at 10 side-flips across 68%
of frame width in a 36-second clip. The speed limit then stopped the camera ever
arriving at either, so it sat between them showing *neither* face.
"""

from __future__ import annotations

import pytest

from clipper.render.layouts import (
    FaceObservation,
    build_tracks,
    choose_layout,
    plan_group_crop,
)

FRAME_W, FRAME_H = 1920, 1080


def obs(t: float, x: float, y: float = 450, w: float = 300) -> FaceObservation:
    return FaceObservation(t=t, x=x, y=y, width=w, height=w * 1.2)


def choose(per_sample, **kwargs):
    defaults = dict(
        src_w=FRAME_W, src_h=FRAME_H, out_w=1080, out_h=1920, duration=30.0,
        pan_smoothing=0.12, max_pan_speed=0.25, min_face_ratio=0.5,
    )
    defaults.update(kwargs)
    return choose_layout(per_sample, **defaults)


class TestBuildTracks:
    def test_one_person_is_one_track(self):
        per_sample = [[obs(i * 0.2, 900 + i)] for i in range(50)]
        tracks = build_tracks(per_sample, FRAME_W)
        assert len(tracks) == 1
        assert len(tracks[0].observations) == 50

    def test_two_people_are_two_tracks(self):
        per_sample = [[obs(i * 0.2, 500), obs(i * 0.2, 1400)] for i in range(50)]
        tracks = build_tracks(per_sample, FRAME_W)
        assert len(tracks) == 2
        assert all(len(t.observations) == 50 for t in tracks)

    def test_tracks_keep_their_own_identity(self):
        """The core fix: each track stays with one person."""
        per_sample = [[obs(i * 0.2, 500), obs(i * 0.2, 1400)] for i in range(50)]
        left, right = sorted(build_tracks(per_sample, FRAME_W), key=lambda t: t.median_x)
        assert left.median_x == pytest.approx(500, abs=30)
        assert right.median_x == pytest.approx(1400, abs=30)

    def test_alternating_detections_do_not_merge(self):
        """The real failure mode: the detector finds one person, then the other."""
        per_sample = []
        for i in range(60):
            per_sample.append([obs(i * 0.2, 500 if i % 2 == 0 else 1400)])
        tracks = build_tracks(per_sample, FRAME_W)
        assert len(tracks) == 2
        positions = sorted(t.median_x for t in tracks)
        assert positions[0] == pytest.approx(500, abs=50)
        assert positions[1] == pytest.approx(1400, abs=50)

    def test_a_person_moving_slowly_stays_one_track(self):
        per_sample = [[obs(i * 0.2, 500 + i * 5)] for i in range(60)]
        assert len(build_tracks(per_sample, FRAME_W)) == 1

    def test_tracks_are_ordered_by_length(self):
        per_sample = [[obs(i * 0.2, 500)] for i in range(50)]
        per_sample += [[obs(10 + i * 0.2, 500), obs(10 + i * 0.2, 1500)] for i in range(10)]
        tracks = build_tracks(per_sample, FRAME_W)
        assert len(tracks[0].observations) >= len(tracks[1].observations)

    def test_no_faces(self):
        assert build_tracks([[] for _ in range(20)], FRAME_W) == []

    def test_extent_is_trimmed_against_outliers(self):
        """One stray detection must not force the camera to pan to reach it."""
        per_sample = [[obs(i * 0.2, 900)] for i in range(50)]
        per_sample[25] = [obs(5.0, 1850)]
        track = build_tracks(per_sample, FRAME_W)[0]
        left, right = track.extent()
        assert right < 1400, "the outlier should be trimmed out of the extent"


class TestNoTargetSwinging:
    def test_two_alternating_people_never_produce_a_panning_crop(self):
        """The exact regression: the camera used to swing and show neither."""
        per_sample = [[obs(i * 0.2, 500 if i % 3 else 1400)] for i in range(80)]
        plan = choose(per_sample)
        moves = sum(1 for a, b in zip(plan.keyframes, plan.keyframes[1:], strict=False)
                    if a.x != b.x or a.y != b.y)
        assert moves == 0, "the camera must not chase between two people"

    def test_a_single_seated_speaker_gets_a_static_frame(self):
        """Small head movement inside the crop should produce no motion at all."""
        per_sample = [[obs(i * 0.2, 900 + (i % 7) * 8)] for i in range(80)]
        plan = choose(per_sample)
        assert plan.kind == "follow_crop"
        assert len(plan.keyframes) == 1, "a barely-moving subject needs one keyframe"


class TestGroupFraming:
    def test_two_close_subjects_share_one_static_crop(self):
        per_sample = [[obs(i * 0.2, 900), obs(i * 0.2, 1080)] for i in range(60)]
        plan = choose(per_sample)
        assert plan.kind == "follow_crop"
        assert len(plan.keyframes) == 1
        assert "subjects" in plan.reason

    def test_the_group_crop_contains_both_subjects(self):
        per_sample = [[obs(i * 0.2, 900), obs(i * 0.2, 1080)] for i in range(60)]
        plan = choose(per_sample)
        kf = plan.keyframes[0]
        assert kf.x <= 900 <= kf.x + plan.crop_width
        assert kf.x <= 1080 <= kf.x + plan.crop_width

    def test_subjects_too_far_apart_do_not_share_a_crop(self):
        tracks = build_tracks(
            [[obs(i * 0.2, 200), obs(i * 0.2, 1700)] for i in range(40)], FRAME_W)
        assert plan_group_crop(tracks, src_w=FRAME_W, src_h=FRAME_H,
                               out_w=1080, out_h=1920) is None

    def test_far_apart_subjects_stack_instead(self):
        per_sample = [[obs(i * 0.2, 300), obs(i * 0.2, 1600)] for i in range(60)]
        assert choose(per_sample).kind == "two_speaker_stack"

    def test_no_tracks(self):
        assert plan_group_crop([], src_w=FRAME_W, src_h=FRAME_H,
                               out_w=1080, out_h=1920) is None


class TestSubjectFiltering:
    def test_overlay_insets_do_not_count_as_extra_subjects(self):
        """One real speaker plus two reaction cams is one subject, not three."""
        per_sample = [
            [obs(i * 0.2, 1040, w=330),   # the speaker
             obs(i * 0.2, 150, w=130),    # overlay
             obs(i * 0.2, 1770, w=130)]   # overlay
            for i in range(80)
        ]
        plan = choose(per_sample)
        assert plan.kind == "follow_crop"
        assert "one subject" in plan.reason

    def test_only_overlays_keeps_the_whole_frame(self):
        per_sample = [[obs(i * 0.2, 150, w=130), obs(i * 0.2, 1770, w=130)]
                      for i in range(80)]
        plan = choose(per_sample)
        assert plan.kind == "blurred_fit"
        assert "overlay inset" in plan.reason

    def test_a_subject_present_too_rarely_keeps_the_whole_frame(self):
        per_sample = [[obs(i * 0.2, 150, w=130)] for i in range(80)]
        per_sample[:10] = [[obs(i * 0.2, 960, w=400)] for i in range(10)]
        plan = choose(per_sample)
        assert plan.kind == "blurred_fit"
        assert "only" in plan.reason


class TestInconsistentSubject:
    def test_a_subject_that_keeps_changing_keeps_the_whole_frame(self):
        """Cuts between three camera setups: no single crop serves them."""
        per_sample = []
        for i in range(90):
            x = (300, 960, 1650)[(i // 30) % 3]
            per_sample.append([obs(i * 0.2, x, w=320)])
        plan = choose(per_sample)
        assert plan.kind == "blurred_fit"
