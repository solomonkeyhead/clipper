"""Scene-cut detection.

The bug these exist for: cuts were detected from a hue/saturation histogram of
the *whole* frame, which carries no spatial information. A podcast cutting from
a wide two-shot to a close-up of the same person in the same room barely moves
that histogram -- measured at 0.752 correlation on a real cut, well inside the
"no cut" range. So the cut was missed, the clip was treated as one shot, and one
framing was stretched across two compositions it could not both serve.

Comparing a grid of per-tile luminance histograms scores the same real cut at
0.512. Measured across both real sources (docs/VERIFIED.md, 2026-09-22): an
unedited single-camera capture never drops below 0.977 over 827 sample pairs,
so the 0.70 threshold has a wide margin on both sides.
"""

from __future__ import annotations

import numpy as np
import pytest

from clipper.render.faces import SCENE_CUT_CORRELATION, _histogram, _is_cut


def frame(fill: int = 0) -> np.ndarray:
    return np.full((180, 320, 3), fill, dtype=np.uint8)


def with_block(fill: int, box: tuple[int, int, int, int], value: int) -> np.ndarray:
    """A frame with a rectangle of `value` drawn on a `fill` background."""
    img = frame(fill)
    x0, y0, x1, y1 = box
    img[y0:y1, x0:x1] = value
    return img


class TestTiledHistogram:
    def test_one_histogram_per_tile(self):
        from clipper.render.faces import SCENE_GRID

        assert len(_histogram(frame(120))) == SCENE_GRID * SCENE_GRID

    def test_an_identical_frame_is_not_a_cut(self):
        a = _histogram(with_block(40, (20, 20, 120, 140), 200))
        assert not _is_cut(a, a)

    def test_the_same_content_moved_across_the_frame_is_a_cut(self):
        """The exact missed case: the colours are identical, the layout is not.

        A whole-frame colour histogram cannot tell these apart at all, because
        the two frames contain precisely the same pixels in a different place.
        """
        left = with_block(40, (10, 20, 130, 160), 220)
        right = with_block(40, (190, 20, 310, 160), 220)
        assert _is_cut(_histogram(left), _histogram(right))

    def test_a_small_movement_is_not_a_cut(self):
        a = with_block(40, (100, 40, 200, 140), 200)
        b = with_block(40, (106, 44, 206, 144), 200)
        assert not _is_cut(_histogram(a), _histogram(b))

    def test_a_graphic_appearing_in_one_corner_is_not_a_cut(self):
        """A lower-third or a reaction inset changes one tile completely.

        This is why the *mean* tile correlation is used rather than the
        minimum: the source adds and removes overlays constantly, and a new
        framing on each of them would be unwatchable.
        """
        plain = with_block(40, (60, 30, 260, 150), 150)
        overlaid = plain.copy()
        overlaid[0:44, 0:80] = 255
        assert not _is_cut(_histogram(plain), _histogram(overlaid))

    def test_a_hard_cut_to_different_footage_is_a_cut(self):
        assert _is_cut(_histogram(frame(20)), _histogram(frame(230)))

    def test_no_previous_frame_is_not_a_cut(self):
        assert not _is_cut([], _histogram(frame(50)))

    def test_mismatched_lengths_are_not_a_cut(self):
        current = _histogram(frame(50))
        assert not _is_cut(current[:4], current)

    def test_the_threshold_is_in_the_measured_gap(self):
        """Between the podcast's real cuts and the static capture's floor."""
        assert 0.6 <= SCENE_CUT_CORRELATION <= 0.8


class TestCutsFeedShots:
    @pytest.mark.parametrize("cut_at", [4.0, 9.0, 15.0])
    def test_a_detected_cut_becomes_a_shot_boundary(self, cut_at):
        from clipper.render.shots import split_into_shots

        shots = split_into_shots([i * 0.2 for i in range(100)], [cut_at], 20.0)
        assert [s.end for s in shots[:-1]] == [cut_at]


class TestCutRefinement:
    """A cut is detected by a sample up to 0.2s after it; it must be placed on
    the exact frame. Reported on real output: frames of the new shot were
    rendered with the previous shot's crop -- a flash of a chair and a lap."""

    FPS = 30.0

    def refine(self, first_new: int):
        """Six frames between two samples; the shot changes at `first_new`."""
        from clipper.render.faces import _refine_cut

        old = with_block(40, (10, 20, 130, 160), 220)
        new = with_block(40, (190, 20, 310, 160), 220)
        between = [((i + 1) / self.FPS, old if i + 1 < first_new else new)
                   for i in range(5)]
        return _refine_cut(_histogram(old), between, _histogram(new), 6 / self.FPS,
                           size=(320, 180), fps=self.FPS)

    @pytest.mark.parametrize("first_new", [1, 2, 3, 4, 5, 6])
    def test_the_cut_lands_just_before_the_first_new_frame(self, first_new):
        t = self.refine(first_new)
        assert t == pytest.approx((first_new - 0.5) / self.FPS)

    def test_with_nothing_in_between_it_stays_on_the_sample(self):
        from clipper.render.faces import _refine_cut

        h = _histogram(frame(50))
        assert _refine_cut(h, [], h, 1.0, size=(320, 180), fps=30.0) == pytest.approx(
            1.0 - 0.5 / 30.0)
