"""Framing the person who is talking.

Reported on real output: a b-roll interview where the player answered and the
frame held the interviewer. Two things were wrong, and both are pinned here:

* the player's face measured 12.5% of frame width, just under the 13% line that
  separates people from reaction-cam overlays, so he was never a candidate;
* nothing asked who was talking.
"""

from __future__ import annotations

import numpy as np
import pytest

from clipper.render.layouts import FaceObservation, build_tracks, choose_layout
from clipper.render.speakers import (
    SPEAKER_MARGIN,
    clear_talker,
    face_patches,
    talking_score,
)

W = 1920
RNG = np.random.default_rng(7)


def patch(seed: int) -> np.ndarray:
    return np.random.default_rng(seed).normal(size=(12, 24)).astype(np.float32)


def face(t: float, x: float, *, mouth, eyes, width: float = 250) -> FaceObservation:
    return FaceObservation(t=t, x=x, y=450, width=width, height=width * 1.2,
                           mouth=mouth, eyes=eyes)


def talker(x: float, n: int = 30, width: float = 250) -> list[FaceObservation]:
    """A mouth that changes every sample while the eyes barely do."""
    base = patch(1)
    return [face(i * 0.2, x, width=width, mouth=patch(100 + i),
                 eyes=base + RNG.normal(scale=0.3, size=base.shape).astype(np.float32))
            for i in range(n)]


def listener(x: float, n: int = 30, width: float = 250) -> list[FaceObservation]:
    """A face that moves a little as a whole -- mouth and eyes alike."""
    mouth, eyes = patch(2), patch(3)
    jitter = lambda p: p + RNG.normal(scale=0.3, size=p.shape).astype(np.float32)  # noqa: E731
    return [face(i * 0.2, x, width=width, mouth=jitter(mouth), eyes=jitter(eyes))
            for i in range(n)]


def two_people(a: list[FaceObservation], b: list[FaceObservation]):
    return [[x, y] for x, y in zip(a, b, strict=True)]


class TestTalkingScore:
    def test_a_talking_mouth_scores_higher_than_a_still_one(self):
        per = two_people(talker(500), listener(1400))
        speaking, quiet = sorted(build_tracks(per, W), key=lambda t: t.median_x)
        assert talking_score(speaking) > talking_score(quiet) * SPEAKER_MARGIN

    def test_head_movement_is_not_talking(self):
        """The failure of the first version: jitter in profile read as talking.
        Moving the whole face -- eyes as much as mouth -- must not score high."""
        base_m, base_e = patch(4), patch(5)
        obs = [face(i * 0.2, 900, mouth=base_m + (i % 2) * 2.0, eyes=base_e + (i % 2) * 2.0)
               for i in range(30)]
        track = build_tracks([[o] for o in obs], W)[0]
        assert talking_score(track) == pytest.approx(1.0, abs=0.05)

    def test_too_few_samples_gives_no_score(self):
        track = build_tracks([[o] for o in talker(500, n=3)], W)[0]
        assert talking_score(track) is None

    def test_samples_far_apart_are_not_compared(self):
        """A face that vanished for a while moved for reasons other than speech."""
        obs = talker(500, n=10)
        spaced = [FaceObservation(t=i * 2.0, x=o.x, y=o.y, width=o.width, height=o.height,
                                  mouth=o.mouth, eyes=o.eyes) for i, o in enumerate(obs)]
        track = build_tracks([[o] for o in spaced], W)[0]
        assert talking_score(track) is None

    def test_missing_patches_give_no_score(self):
        obs = [FaceObservation(t=i * 0.2, x=900, y=450, width=250, height=300)
               for i in range(20)]
        assert talking_score(build_tracks([[o] for o in obs], W)[0]) is None


class TestClearTalker:
    def test_the_talker_is_picked(self):
        tracks = build_tracks(two_people(talker(500), listener(1400)), W)
        found = clear_talker(tracks)
        assert found is not None
        assert found[0].median_x == pytest.approx(500)

    def test_two_people_talking_alike_is_no_clear_talker(self):
        tracks = build_tracks(two_people(talker(500), talker(1400)), W)
        assert clear_talker(tracks) is None

    def test_one_person_is_not_a_choice(self):
        assert clear_talker(build_tracks([[o] for o in talker(500)], W)) is None


def choose(per_sample):
    return choose_layout(per_sample, src_w=W, src_h=1080, out_w=1080, out_h=1920,
                         min_face_ratio=0.5)


class TestFramingTheTalker:
    def test_the_reported_case_frames_the_player(self):
        """Player (talking) 12.5% wide on the left, interviewer 13.6% on the right."""
        per = two_people(talker(560, width=W * 0.125), listener(1500, width=W * 0.136))
        plan = choose(per)
        assert plan.kind in ("follow_crop", "fit_crop")
        x0 = plan.keyframes[0].x
        assert x0 <= 560 <= x0 + plan.crop_width, "the talker is not in frame"
        assert not x0 <= 1500 <= x0 + plan.crop_width
        assert "talking" in plan.reason

    def test_the_interviewer_is_framed_when_he_is_the_one_talking(self):
        per = two_people(listener(560, width=W * 0.125), talker(1500, width=W * 0.136))
        plan = choose(per)
        x0 = plan.keyframes[0].x
        assert x0 <= 1500 <= x0 + plan.crop_width

    def test_without_a_clear_talker_both_are_shown(self):
        per = two_people(talker(560, width=W * 0.14), talker(1500, width=W * 0.14))
        assert choose(per).kind == "two_speaker_stack"

    def test_a_small_overlay_beside_a_speaker_is_still_not_a_person(self):
        """The companion rule must not undo the inset rule: a 7% reaction cam
        next to a 20% speaker is still an overlay."""
        per = two_people(listener(900, width=W * 0.20), talker(1750, width=W * 0.07))
        plan = choose(per)
        x0 = plan.keyframes[0].x
        assert x0 <= 900 <= x0 + plan.crop_width


class TestPatches:
    def test_patches_are_normalised_and_fixed_size(self):
        frame = (np.random.default_rng(0).random((360, 640, 3)) * 255).astype(np.uint8)
        mouth, eyes = face_patches(frame, 200, 80, 120, 150)
        assert mouth.shape == eyes.shape == (12, 24)
        assert abs(float(mouth.mean())) < 1e-3
        assert float(mouth.std()) == pytest.approx(1.0, abs=0.01)

    def test_a_box_off_the_frame_gives_no_patch(self):
        frame = np.zeros((360, 640, 3), np.uint8)
        assert face_patches(frame, 700, 400, 50, 50) == (None, None)
