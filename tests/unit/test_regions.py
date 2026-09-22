"""Screen-share region detection and the stacked layout.

The case this exists for: a webcam inset over gameplay, a board or a slide deck.
Cropping to the face there discards the subject of the video.
"""

from __future__ import annotations

import numpy as np
import pytest

from clipper.models import CropRect
from clipper.render import regions
from clipper.render.layouts import plan_content_stack

FRAME_W, FRAME_H = 1920, 1080


def build_map(cells: np.ndarray, *, background: float | None = None) -> regions.ContentMap:
    """Build a ContentMap directly from a luminance grid."""
    if background is None:
        background = regions._background_tone(cells)
    return regions.ContentMap(
        score=regions._normalise(np.abs(cells - background)),
        luminance=cells,
        background=background,
        frame_width=FRAME_W,
        frame_height=FRAME_H,
    )


def screen_share_frame() -> np.ndarray:
    """A dark frame with a bright content block left and a webcam block right.

    Mirrors the real layout measured on a chess video: board on the left, an
    inset webcam on the right, black elsewhere.
    """
    grid = np.full((regions.GRID_HEIGHT, regions.GRID_WIDTH), 10.0)
    grid[2:52, 2:56] = 170.0    # content block (left)
    grid[8:32, 60:92] = 120.0   # webcam block (right)
    return grid


class TestBackgroundTone:
    def test_it_comes_from_the_border_not_the_whole_frame(self):
        """A large flat content area used to win the histogram and be
        misclassified as the background, inverting the detection."""
        grid = np.full((54, 96), 10.0)
        grid[2:52, 2:80] = 200.0  # content covering most of the frame
        assert regions._background_tone(grid) < 60, "the border is the background"

    def test_a_light_background(self):
        grid = np.full((54, 96), 240.0)
        grid[2:52, 2:60] = 30.0
        assert regions._background_tone(grid) > 180

    def test_a_uniform_frame(self):
        assert regions._background_tone(np.full((54, 96), 128.0)) == pytest.approx(128, abs=10)

    def test_a_tiny_grid_falls_back_to_the_global_mode(self):
        assert regions._background_tone(np.full((2, 2), 90.0)) == pytest.approx(90, abs=10)


class TestPictureInPictureDetection:
    def test_a_small_face_is_an_inset_webcam(self):
        """Measured on real footage: a facecam was 7.2% of frame width."""
        assert regions.is_picture_in_picture(138, 1920)

    def test_a_talking_head_face_is_not(self):
        assert not regions.is_picture_in_picture(1920 * 0.25, 1920)

    def test_the_boundary(self):
        ratio = regions.PIP_FACE_WIDTH_RATIO
        assert regions.is_picture_in_picture(1920 * (ratio - 0.01), 1920)
        assert not regions.is_picture_in_picture(1920 * (ratio + 0.01), 1920)

    def test_a_zero_width_frame_does_not_divide_by_zero(self):
        assert not regions.is_picture_in_picture(100, 0)


class TestTrimSparseEdges:
    def test_a_solid_block_is_unchanged(self):
        mask = np.zeros((20, 20), dtype=bool)
        mask[5:15, 5:15] = True
        assert regions._trim_sparse_edges(mask) == (5, 5, 14, 14)

    def test_a_thin_bridge_is_trimmed_away(self):
        """A caption bridging to the content used to drag the box out with it."""
        mask = np.zeros((20, 20), dtype=bool)
        mask[5:15, 5:12] = True   # the real block
        mask[5, 12:19] = True     # a one-row strip reaching right
        _x0, _y0, x1, _y1 = regions._trim_sparse_edges(mask)
        assert x1 < 18, "the sparse strip should have been trimmed off"

    def test_an_empty_mask(self):
        assert regions._trim_sparse_edges(np.zeros((10, 10), dtype=bool)) is None


class TestDetectLayoutRegions:
    def test_finds_both_regions_on_a_screen_share_frame(self):
        content_map = build_map(screen_share_frame())
        # A face inside the right-hand webcam block.
        result = regions.detect_layout_regions(content_map, 1500, 400, 138, 182)
        assert result is not None
        webcam, content = result
        assert webcam.x > content.x, "the webcam sits to the right of the content"
        assert content.width * content.height > webcam.width * webcam.height

    def test_the_webcam_region_contains_the_face(self):
        content_map = build_map(screen_share_frame())
        webcam, _ = regions.detect_layout_regions(content_map, 1500, 400, 138, 182)
        assert webcam.x <= 1500 <= webcam.x + webcam.width
        assert webcam.y <= 400 <= webcam.y + webcam.height

    def test_a_large_face_is_not_treated_as_a_webcam(self):
        """A talking head that fills the frame should follow-crop instead."""
        content_map = build_map(screen_share_frame())
        assert regions.detect_layout_regions(content_map, 1500, 400, 600, 700) is None

    def test_a_single_content_blob_is_not_a_split_layout(self):
        grid = np.full((regions.GRID_HEIGHT, regions.GRID_WIDTH), 10.0)
        grid[10:40, 20:70] = 180.0  # one block, containing the face
        result = regions.detect_layout_regions(build_map(grid), 900, 500, 138, 182)
        assert result is None

    def test_no_content_map(self):
        assert regions.detect_layout_regions(None, 100, 100, 50, 50) is None

    def test_a_face_outside_every_blob(self):
        content_map = build_map(screen_share_frame())
        assert regions.detect_layout_regions(content_map, 5, 5, 138, 182) is None

    def test_works_on_a_light_background(self):
        """A slide deck is dark content on white, the inverse of gameplay."""
        grid = np.full((regions.GRID_HEIGHT, regions.GRID_WIDTH), 240.0)
        grid[2:52, 2:56] = 40.0
        grid[8:32, 60:92] = 90.0
        result = regions.detect_layout_regions(build_map(grid), 1500, 400, 138, 182)
        assert result is not None, "detection must not assume a dark background"


class TestPlanContentStack:
    WEBCAM = CropRect(x=1160, y=180, width=740, height=480)
    CONTENT = CropRect(x=20, y=0, width=1120, height=1080)

    def _plan(self, **kwargs):
        defaults = dict(src_w=FRAME_W, src_h=FRAME_H, out_w=1080, out_h=1920)
        defaults.update(kwargs)
        return plan_content_stack(self.WEBCAM, self.CONTENT, **defaults)

    def test_produces_a_content_stack(self):
        plan = self._plan()
        assert plan.kind == "content_stack"
        assert len(plan.panes) == 2
        assert len(plan.pane_heights) == 2

    def test_pane_heights_sum_to_the_output_height(self):
        """The filter graph rejects a split that does not, so this is load-bearing."""
        for share in (0.3, 0.45, 0.58, 0.7, 0.9):
            plan = self._plan(content_share=share)
            assert sum(plan.pane_heights) == 1920, share

    def test_content_is_the_first_pane(self):
        plan = self._plan()
        assert plan.panes[0] == self.CONTENT
        assert plan.panes[1] == self.WEBCAM

    def test_neither_pane_collapses(self):
        for share in (0.05, 0.5, 0.99):
            plan = self._plan(content_share=share)
            assert all(h > 200 for h in plan.pane_heights), (share, plan.pane_heights)

    def test_the_split_follows_the_content_aspect(self):
        """A square board and a wide banner should not get the same height."""
        square = plan_content_stack(
            self.WEBCAM, CropRect(x=0, y=0, width=1080, height=1080),
            src_w=FRAME_W, src_h=FRAME_H, out_w=1080, out_h=1920)
        wide = plan_content_stack(
            self.WEBCAM, CropRect(x=0, y=0, width=1600, height=400),
            src_w=FRAME_W, src_h=FRAME_H, out_w=1080, out_h=1920)
        assert square.pane_heights[0] > wide.pane_heights[0]

    def test_pane_heights_are_even(self):
        """Odd dimensions make libx264 refuse under yuv420p."""
        plan = self._plan()
        assert plan.pane_heights[0] % 2 == 0 or plan.pane_heights[1] % 2 == 0

    def test_it_is_face_centric_for_qa(self):
        """The speaker is still on screen, so losing the face is still wrong."""
        assert self._plan().is_face_centric

    def test_rejects_a_zero_sized_output(self):
        with pytest.raises(ValueError, match="positive"):
            self._plan(out_w=0)


class TestContentStackFilterGraph:
    def test_builds_a_vstack_of_two_cropped_panes(self):
        from pathlib import Path

        from clipper.render.graph import RenderSpec, build_video_filter

        plan = plan_content_stack(
            CropRect(x=1160, y=180, width=740, height=480),
            CropRect(x=20, y=0, width=1120, height=1080),
            src_w=FRAME_W, src_h=FRAME_H, out_w=1080, out_h=1920)

        spec = RenderSpec(
            source=Path("s.mp4"), output=Path("o.mp4"), start=0.0, duration=10.0,
            layout=plan, ass_path=None, fonts_dir=None, width=1080, height=1920,
            fps=30, encoder="libx264", loudness_lufs=-14.0, true_peak_dbtp=-1.5,
            crf=20, nvenc_cq=23, x264_preset="veryfast", audio_bitrate="192k",
            audio_rate=48000,
        )
        vf = build_video_filter(spec)
        assert "crop=1120:1080:20:0" in vf
        assert "crop=740:480:1160:180" in vf
        assert "vstack=inputs=2" in vf
        assert vf.endswith("[v]")

    def test_mismatched_pane_heights_are_rejected(self):
        from pathlib import Path

        from clipper.models import LayoutPlan
        from clipper.render.graph import RenderSpec, build_video_filter

        bad = LayoutPlan(
            kind="content_stack",
            panes=[CropRect(x=0, y=0, width=100, height=100),
                   CropRect(x=0, y=0, width=100, height=100)],
            pane_heights=[100, 100],  # does not sum to 1920
        )
        spec = RenderSpec(
            source=Path("s.mp4"), output=Path("o.mp4"), start=0.0, duration=10.0,
            layout=bad, ass_path=None, fonts_dir=None, width=1080, height=1920,
            fps=30, encoder="libx264", loudness_lufs=-14.0, true_peak_dbtp=-1.5,
            crf=20, nvenc_cq=23, x264_preset="veryfast", audio_bitrate="192k",
            audio_rate=48000,
        )
        with pytest.raises(ValueError, match="do not sum"):
            build_video_filter(spec)
