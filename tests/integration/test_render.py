"""End-to-end renders against synthetic video.

These actually invoke FFmpeg and inspect the resulting MP4 with ffprobe, so they
prove the filter graphs are accepted and produce conformant output -- something
the unit tests, which only check graph text, cannot.

Marked `slow`: `pytest -m "not slow"` skips them.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from clipper.config import Config
from clipper.ingest.probe import measure_loudness, probe
from clipper.models import ClipPlan, CropKeyframe, CropRect, LayoutPlan, Word
from clipper.render import captions as cap
from clipper.render.clip import render_clip

from ..conftest import needs_ffmpeg
from ..fixtures import synthetic

pytestmark = [pytest.mark.slow, needs_ffmpeg]

CLIP_SECONDS = 6.0


def sample_words(count: int = 14, start: float = 0.5, step: float = 0.38) -> list[Word]:
    """Speech-like word timings covering most of the clip."""
    vocab = ["this", "is", "the", "part", "where", "everything", "changed.",
             "nobody", "expected", "it", "to", "work", "at", "all."]
    return [
        Word(start=start + i * step, end=start + i * step + step * 0.85,
             text=vocab[i % len(vocab)])
        for i in range(count)
    ]


def make_plan(layout: LayoutPlan, **kwargs) -> ClipPlan:
    defaults = dict(
        clip_id="001_test", candidate_id="c0", rank=1,
        start=1.0, end=1.0 + CLIP_SECONDS,
        text="this is the part where everything changed",
        hook_text="Nobody expected this",
        caption_style="bold_pop",
        layout=layout,
    )
    defaults.update(kwargs)
    return ClipPlan(**defaults)


@pytest.fixture(scope="module")
def cfg() -> Config:
    return Config.load()


def render(tmp_path: Path, source: Path, plan: ClipPlan, cfg: Config, **kwargs):
    media = probe(source)
    return render_clip(
        source=source, media=media, plan=plan, words=sample_words(),
        config=cfg, work_dir=tmp_path / "work", output=tmp_path / "out" / "clip.mp4",
        **kwargs,
    )


class TestFollowCrop:
    def test_renders_conformant_vertical_video(self, tmp_path, media_cache, cfg):
        source, track = synthetic.moving_face_video(duration=10.0)
        keyframes = [
            CropKeyframe(
                t=round(i * 0.2, 3),
                x=int(max(0, min(1920 - 608, track.x_at(1.0 + i * 0.2) - 304))),
                y=0,
            )
            for i in range(int(CLIP_SECONDS / 0.2) + 1)
        ]
        layout = LayoutPlan(kind="follow_crop", crop_width=608, crop_height=1080,
                            keyframes=keyframes, face_ratio=1.0)

        result = render(tmp_path, source, make_plan(layout), cfg)

        assert result.output.exists()
        info = probe(result.output)
        assert (info.width, info.height) == (1080, 1920)
        assert info.fps == pytest.approx(30, abs=0.2)
        assert info.duration == pytest.approx(CLIP_SECONDS, abs=0.35)
        assert info.has_audio
        assert info.video_codec == "h264"

    def test_writes_a_sendcmd_script_it_actually_uses(self, tmp_path, media_cache, cfg):
        source, _ = synthetic.moving_face_video(duration=10.0)
        layout = LayoutPlan(
            kind="follow_crop", crop_width=608, crop_height=1080,
            keyframes=[CropKeyframe(t=0.0, x=100, y=0), CropKeyframe(t=3.0, x=900, y=0)],
        )
        result = render(tmp_path, source, make_plan(layout), cfg)
        assert result.sendcmd_path is not None
        assert "crop x 900" in result.sendcmd_path.read_text(encoding="utf-8")
        assert "sendcmd" in " ".join(result.command)

    def test_the_crop_actually_moves(self, tmp_path, media_cache, cfg):
        """The strongest available proof that sendcmd drove the crop.

        The fixture's disc slides left to right on a dark field. If the crop
        tracked it, the disc stays near the centre of the output; if sendcmd did
        nothing, it drifts out of frame.
        """
        source, track = synthetic.moving_face_video(duration=10.0)
        keyframes = [
            CropKeyframe(
                t=round(i * 0.2, 3),
                x=int(max(0, min(1920 - 608, track.x_at(1.0 + i * 0.2) - 304))),
                y=0,
            )
            for i in range(int(CLIP_SECONDS / 0.2) + 1)
        ]
        layout = LayoutPlan(kind="follow_crop", crop_width=608, crop_height=1080,
                            keyframes=keyframes)
        plan = make_plan(layout, hook_text="", caption_style="bold_pop")
        result = render(tmp_path, source, plan, cfg)

        early = _bright_centroid_x(result.output, 0.5, tmp_path)
        late = _bright_centroid_x(result.output, CLIP_SECONDS - 0.7, tmp_path)
        assert early is not None and late is not None, "disc missing from the output"
        # Both near the middle of the 1080-wide frame: the camera followed.
        assert abs(early - 540) < 260, f"disc off-centre early at x={early}"
        assert abs(late - 540) < 260, f"disc off-centre late at x={late}"


class TestTwoSpeakerStack:
    def test_renders_and_stacks(self, tmp_path, media_cache, cfg):
        source = synthetic.two_faces_video(duration=10.0)
        layout = LayoutPlan(
            kind="two_speaker_stack", crop_width=1215, crop_height=1080,
            panes=[CropRect(x=0, y=0, width=1215, height=1080),
                   CropRect(x=705, y=0, width=1215, height=1080)],
            face_ratio=1.0,
        )
        result = render(tmp_path, source, make_plan(layout), cfg)
        info = probe(result.output)
        assert (info.width, info.height) == (1080, 1920)
        assert info.duration == pytest.approx(CLIP_SECONDS, abs=0.35)

    def test_no_sendcmd_for_a_static_stack(self, tmp_path, media_cache, cfg):
        source = synthetic.two_faces_video(duration=10.0)
        layout = LayoutPlan(
            kind="two_speaker_stack", crop_width=1215, crop_height=1080,
            panes=[CropRect(x=0, y=0, width=1215, height=1080),
                   CropRect(x=705, y=0, width=1215, height=1080)],
        )
        result = render(tmp_path, source, make_plan(layout), cfg)
        assert result.sendcmd_path is None


class TestBlurredFit:
    def test_renders_with_no_black_bars(self, tmp_path, media_cache, cfg):
        """The blurred fill exists precisely so there are no letterbox bars."""
        source = synthetic.bars_video(duration=10.0)
        layout = LayoutPlan(kind="blurred_fit", crop_width=1920, crop_height=1080)
        result = render(tmp_path, source, make_plan(layout), cfg)

        info = probe(result.output)
        assert (info.width, info.height) == (1080, 1920)

        top = _mean_luma(result.output, 2.0, tmp_path, region="top")
        assert top > 8, f"top band is black (mean luma {top}); the fill did not render"


class TestCaptionsAndAudio:
    def test_ass_file_is_written_and_within_bounds(self, tmp_path, media_cache, cfg):
        source = synthetic.bars_video(duration=10.0)
        layout = LayoutPlan(kind="blurred_fit", crop_width=1920, crop_height=1080)
        result = render(tmp_path, source, make_plan(layout), cfg)

        assert result.ass_path is not None and result.ass_path.exists()
        events = cap.parse_event_times(result.ass_path.read_text(encoding="utf-8"))
        assert events
        for start, end, _ in events:
            assert start >= -1e-6
            assert end <= CLIP_SECONDS + 1e-6

    def test_loudness_lands_near_the_target(self, tmp_path, media_cache, cfg):
        """loudnorm is single-pass here, so tolerance is deliberately wide."""
        source = synthetic.bars_video(duration=10.0)
        layout = LayoutPlan(kind="blurred_fit", crop_width=1920, crop_height=1080)
        result = render(tmp_path, source, make_plan(layout), cfg)

        loudness = measure_loudness(result.output)
        assert loudness["i"] == pytest.approx(cfg.render.loudness_lufs, abs=3.0)
        assert loudness["tp"] <= -0.5

    def test_silent_source_still_renders(self, tmp_path, media_cache, cfg):
        source = synthetic.silent_video(duration=10.0)
        layout = LayoutPlan(kind="blurred_fit", crop_width=1920, crop_height=1080)
        result = render(tmp_path, source, make_plan(layout), cfg)
        assert probe(result.output).duration == pytest.approx(CLIP_SECONDS, abs=0.35)


class TestDraftMode:
    def test_draft_is_smaller_and_faster(self, tmp_path, media_cache, cfg):
        source = synthetic.bars_video(duration=10.0)
        layout = LayoutPlan(kind="blurred_fit", crop_width=1920, crop_height=1080)

        full = render(tmp_path / "full", source, make_plan(layout), cfg)
        draft = render(tmp_path / "draft", source, make_plan(layout), cfg, draft=True)

        draft_info = probe(draft.output)
        assert (draft_info.width, draft_info.height) == (540, 960)
        assert draft.output.stat().st_size < full.output.stat().st_size


class TestWindowsPaths:
    def test_renders_from_a_directory_containing_spaces(self, tmp_path, media_cache, cfg):
        """The section 17 hazard: a drive colon plus spaces in the ass filter path."""
        spaced = tmp_path / "my source videos" / "work dir"
        spaced.mkdir(parents=True)

        source = synthetic.bars_video(duration=10.0)
        copied = spaced / "a source clip.mp4"
        copied.write_bytes(source.read_bytes())

        layout = LayoutPlan(kind="blurred_fit", crop_width=1920, crop_height=1080)
        media = probe(copied)
        result = render_clip(
            source=copied, media=media, plan=make_plan(layout), words=sample_words(),
            config=cfg, work_dir=spaced / "work files",
            output=spaced / "out put" / "the clip.mp4",
        )
        assert result.output.exists()
        assert probe(result.output).width == 1080

    def test_non_ascii_caption_text_survives(self, tmp_path, media_cache, cfg):
        source = synthetic.bars_video(duration=10.0)
        layout = LayoutPlan(kind="blurred_fit", crop_width=1920, crop_height=1080)
        words = [Word(start=0.5, end=1.2, text="café"), Word(start=1.3, end=2.0, text="naïve"),
                 Word(start=2.1, end=2.8, text="日本語")]
        media = probe(source)
        result = render_clip(
            source=source, media=media, plan=make_plan(layout), words=words,
            config=cfg, work_dir=tmp_path / "w", output=tmp_path / "o" / "c.mp4",
        )
        assert result.output.exists()
        assert "café" in result.ass_path.read_text(encoding="utf-8").upper().lower()


class TestValidation:
    def test_a_plan_without_a_layout_is_rejected(self, tmp_path, media_cache, cfg):
        source = synthetic.bars_video(duration=10.0)
        with pytest.raises(ValueError, match="no layout"):
            render(tmp_path, source, make_plan(None), cfg)


# --------------------------------------------------------------------------
# frame inspection helpers
# --------------------------------------------------------------------------


def _load_frame(video: Path, t: float, tmp_path: Path):
    import numpy as np

    png = synthetic.extract_frame(video, t, tmp_path / f"probe_{t:.2f}.png")
    import cv2

    img = cv2.imread(str(png), cv2.IMREAD_GRAYSCALE)
    return np.asarray(img) if img is not None else None


def _bright_centroid_x(video: Path, t: float, tmp_path: Path) -> float | None:
    """Horizontal centroid of the brightest region, i.e. where the disc is."""
    import numpy as np

    frame = _load_frame(video, t, tmp_path)
    if frame is None:
        return None
    # The disc is far brighter than the 0x202030 background.
    mask = frame > 150
    if mask.sum() < 500:
        return None
    return float(np.argwhere(mask)[:, 1].mean())


def _mean_luma(video: Path, t: float, tmp_path: Path, *, region: str) -> float:
    frame = _load_frame(video, t, tmp_path)
    if frame is None:
        return 0.0
    h = frame.shape[0]
    band = frame[: h // 6] if region == "top" else frame[-h // 6 :]
    return float(band.mean())
