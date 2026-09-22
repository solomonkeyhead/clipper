"""Filter-graph and command construction.

These tests assert on the *text* of the graph, which is cheap; the integration
tests then prove FFmpeg actually accepts it.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from clipper.config import RenderConfig
from clipper.models import CropKeyframe, CropRect, LayoutPlan
from clipper.render.graph import (
    RenderSpec,
    build_audio_filter,
    build_command,
    build_video_filter,
    output_size,
)


def make_spec(layout: LayoutPlan, **kwargs) -> RenderSpec:
    defaults = dict(
        source=Path("C:/media/source.mp4"),
        output=Path("C:/out/clip.mp4"),
        start=12.5,
        duration=30.0,
        layout=layout,
        ass_path=None,
        fonts_dir=None,
        width=1080,
        height=1920,
        fps=30,
        encoder="libx264",
        loudness_lufs=-14.0,
        true_peak_dbtp=-1.5,
        crf=20,
        nvenc_cq=23,
        x264_preset="veryfast",
        audio_bitrate="192k",
        audio_rate=48_000,
    )
    defaults.update(kwargs)
    return RenderSpec(**defaults)


FOLLOW = LayoutPlan(
    kind="follow_crop", crop_width=608, crop_height=1080,
    keyframes=[CropKeyframe(t=0.0, x=100, y=0)],
)
FIT = LayoutPlan(
    kind="fit_crop", crop_width=900, crop_height=1080,
    keyframes=[CropKeyframe(t=0.0, x=300, y=0)],
)
STACK = LayoutPlan(
    kind="two_speaker_stack", crop_width=1215, crop_height=1080,
    panes=[CropRect(x=0, y=0, width=1215, height=1080),
           CropRect(x=705, y=0, width=1215, height=1080)],
)
BLURRED = LayoutPlan(kind="blurred_fit", crop_width=1920, crop_height=1080)


class TestVideoFilter:
    def test_follow_crop_has_crop_scale_and_output_label(self):
        vf = build_video_filter(make_spec(FOLLOW))
        assert "crop=608:1080:100:0" in vf
        assert "scale=1080:1920" in vf
        assert vf.endswith("[v]")

    def test_framing_never_moves(self):
        """Framing is static per shot: no trajectory, no per-frame commands."""
        for layout in (FOLLOW, FIT):
            assert "sendcmd" not in build_video_filter(make_spec(layout))

    def test_fit_crop_crops_the_region_before_blurring(self):
        """The background is a blur of the framed region, not the whole frame."""
        vf = build_video_filter(make_spec(FIT))
        assert vf.index("crop=900:1080:300:0") < vf.index("split=2")
        assert "gblur" in vf
        assert "overlay=(W-w)/2:(H-h)/2" in vf

    def test_blurred_fit_does_not_crop_the_region(self):
        vf = build_video_filter(make_spec(BLURRED))
        assert vf.startswith("[0:v]split=2")

    def test_stack_produces_two_panes_and_a_vstack(self):
        vf = build_video_filter(make_spec(STACK))
        assert vf.count("crop=1215:1080") == 2
        assert "vstack=inputs=2" in vf
        assert "scale=1080:960" in vf

    def test_stack_panes_sum_to_the_output_height(self):
        vf = build_video_filter(make_spec(STACK, height=1921))
        assert "scale=1080:960" in vf and "scale=1080:961" in vf

    def test_stack_rejects_a_wrong_pane_count(self):
        bad = LayoutPlan(kind="two_speaker_stack", panes=[CropRect(x=0, y=0, width=8, height=8)])
        with pytest.raises(ValueError, match="two panes"):
            build_video_filter(make_spec(bad))

    def test_blurred_fit_blurs_and_darkens_a_background_copy(self):
        vf = build_video_filter(make_spec(BLURRED))
        assert "gblur" in vf
        assert "eq=brightness=" in vf
        assert "overlay=" in vf

    def test_blurred_fit_never_crops_content_away(self):
        """The foreground is scaled to width; cropping it would defeat the point."""
        vf = build_video_filter(make_spec(BLURRED))
        foreground = vf.split("[fg]")[-1]
        assert "scale=1080:-2" in foreground

    def test_blur_scales_with_output_width(self):
        full = build_video_filter(make_spec(BLURRED, width=1080, height=1920))
        draft = build_video_filter(make_spec(BLURRED, width=540, height=960))
        assert _sigma(draft) < _sigma(full)

    def test_every_graph_normalises_fps_sar_and_pixel_format(self):
        for layout in (FOLLOW, STACK, BLURRED):
            vf = build_video_filter(make_spec(layout))
            assert "fps=30" in vf
            assert "setsar=1" in vf
            assert "format=yuv420p" in vf

    def test_unknown_layout_is_rejected(self):
        with pytest.raises(ValueError, match="unknown layout"):
            build_video_filter(make_spec(LayoutPlan.model_construct(kind="spiral")))


class TestAssInGraph:
    def test_ass_is_appended_when_a_subtitle_file_is_given(self):
        vf = build_video_filter(make_spec(FOLLOW, ass_path=Path("C:/w/s.ass")))
        assert "ass=f=" in vf

    def test_windows_path_is_escaped(self):
        """An unescaped drive colon makes the filter argument unparseable."""
        vf = build_video_filter(make_spec(FOLLOW, ass_path=Path(r"C:\w x\s.ass")))
        assert "C\\:/w x/s.ass" in vf

    def test_fonts_dir_is_passed_so_bundled_fonts_are_found(self):
        vf = build_video_filter(
            make_spec(FOLLOW, ass_path=Path("C:/w/s.ass"), fonts_dir=Path("C:/repo/assets/fonts"))
        )
        assert "fontsdir=" in vf

    def test_ass_comes_after_scaling(self):
        """Captions must be burned onto the final geometry, not the crop."""
        vf = build_video_filter(make_spec(FOLLOW, ass_path=Path("C:/w/s.ass")))
        assert vf.index("scale=") < vf.index("ass=f=")


class TestAudioFilter:
    def test_targets_the_configured_loudness(self):
        af = build_audio_filter(make_spec(FOLLOW))
        assert "loudnorm=I=-14.0" in af
        assert "TP=-1.5" in af

    def test_resamples_to_the_configured_rate(self):
        assert "aresample=48000" in build_audio_filter(make_spec(FOLLOW))


class TestCommand:
    def test_seeks_before_the_input_for_a_fast_cut(self):
        cmd = build_command(make_spec(FOLLOW))
        assert cmd.index("-ss") < cmd.index("-i")

    def test_start_and_duration_are_passed(self):
        cmd = build_command(make_spec(FOLLOW, start=12.5, duration=30.0))
        assert cmd[cmd.index("-ss") + 1] == "12.500"
        assert cmd[cmd.index("-t") + 1] == "30.000"

    def test_uses_fps_mode_not_the_removed_vsync_flag(self):
        """FFmpeg 9 removed -vsync; passing it is a hard error."""
        cmd = build_command(make_spec(FOLLOW))
        assert "-fps_mode" in cmd
        assert "-vsync" not in cmd

    def test_faststart_is_set_for_streaming(self):
        cmd = build_command(make_spec(FOLLOW))
        assert cmd[cmd.index("-movflags") + 1] == "+faststart"

    def test_x264_path_uses_crf_and_preset(self):
        cmd = build_command(make_spec(FOLLOW, encoder="libx264", crf=20, x264_preset="veryfast"))
        assert cmd[cmd.index("-c:v") + 1] == "libx264"
        assert cmd[cmd.index("-crf") + 1] == "20"
        assert cmd[cmd.index("-preset") + 1] == "veryfast"

    def test_nvenc_path_uses_cq_not_crf(self):
        cmd = build_command(make_spec(FOLLOW, encoder="h264_nvenc", nvenc_cq=23))
        assert cmd[cmd.index("-c:v") + 1] == "h264_nvenc"
        assert "-cq" in cmd
        assert "-crf" not in cmd

    def test_audio_is_mapped_and_encoded_when_present(self):
        cmd = build_command(make_spec(FOLLOW, has_audio=True))
        assert "[a]" in cmd
        assert cmd[cmd.index("-c:a") + 1] == "aac"

    def test_silent_source_gets_an(self):
        cmd = build_command(make_spec(FOLLOW, has_audio=False))
        assert "-an" in cmd
        assert "-c:a" not in cmd

    def test_output_path_is_last(self):
        spec = make_spec(FOLLOW)
        assert build_command(spec)[-1] == str(spec.output)

    def test_paths_with_spaces_are_passed_as_single_arguments(self):
        """Argument-list invocation means no quoting is needed or wanted."""
        spec = make_spec(FOLLOW, source=Path(r"C:\my videos\a b.mp4"))
        cmd = build_command(spec)
        assert r"C:\my videos\a b.mp4" in cmd


class TestOutputSize:
    def test_full_quality(self):
        assert output_size(RenderConfig(), draft=False) == (1080, 1920)

    def test_draft_is_smaller_and_still_9x16(self):
        w, h = output_size(RenderConfig(), draft=True)
        assert (w, h) == (540, 960)
        assert w / h == pytest.approx(1080 / 1920)


def _sigma(vf: str) -> float:
    return float(vf.split("gblur=sigma=")[1].split(",")[0].split("[")[0])


# --------------------------------------------------------------------------
# per_shot: one framing per shot, concatenated in a single pass
# --------------------------------------------------------------------------


def per_shot(*kinds: str) -> LayoutPlan:
    """A per_shot layout tiling 30 seconds evenly across `kinds`."""
    from clipper.models import LayoutSegment

    step = 30.0 / len(kinds)
    built = {
        "follow_crop": FOLLOW,
        "two_speaker_stack": STACK,
        "fit_crop": FIT,
        "blurred_fit": LayoutPlan(kind="blurred_fit", crop_width=1920,
                                  crop_height=1080),
    }
    return LayoutPlan(kind="per_shot", segments=[
        LayoutSegment(start=i * step, end=(i + 1) * step, layout=built[k])
        for i, k in enumerate(kinds)
    ])


class TestPerShotGraph:
    def test_each_shot_is_trimmed_to_its_own_span(self):
        graph = build_video_filter(make_spec(per_shot("blurred_fit", "follow_crop")))
        assert "trim=start=0.000:end=15.000" in graph
        assert "trim=start=15.000:end=30.000" in graph

    def test_each_shot_restarts_its_timestamps(self):
        """Without this the second branch keeps source PTS and concat stalls."""
        graph = build_video_filter(make_spec(per_shot("blurred_fit", "follow_crop")))
        assert graph.count("setpts=PTS-STARTPTS") == 2

    def test_the_branches_are_concatenated(self):
        graph = build_video_filter(make_spec(per_shot("blurred_fit", "follow_crop")))
        assert "[seg0][seg1]concat=n=2:v=1:a=0" in graph

    def test_every_branch_is_forced_to_square_pixels(self):
        """concat refuses inputs whose SAR differs, and the layouts round
        differently -- measured at 1216:1215 against 10240:10239, which failed
        a real render."""
        graph = build_video_filter(make_spec(per_shot("blurred_fit", "follow_crop")))
        for i in range(2):
            assert f",setsar=1,format=yuv420p[seg{i}]" in graph

    def test_intermediate_labels_do_not_collide(self):
        """Two blurred_fit shots each need their own split labels."""
        graph = build_video_filter(make_spec(per_shot("blurred_fit", "blurred_fit")))
        for label in ("[bgs0]", "[fgs0]", "[bgs1]", "[fgs1]"):
            assert label in graph
        # Every declared label is declared exactly once.
        declared = re.findall(r"\[([a-z]+[a-z0-9]*)\](?=[a-z])", graph)
        assert len(declared) == len(set(declared)), "a label was declared twice"

    def test_captions_are_burned_after_the_concat(self):
        """Burning per branch would apply clip-time subtitles to shot time."""
        spec = make_spec(per_shot("blurred_fit", "follow_crop"),
                         ass_path=Path("C:/work/clip.ass"))
        graph = build_video_filter(spec)
        assert graph.count("ass=") == 1
        assert graph.index("concat=") < graph.index("ass=")

    def test_no_segments_is_rejected(self):
        with pytest.raises(ValueError, match="no segments"):
            build_video_filter(make_spec(LayoutPlan(kind="per_shot")))

    def test_a_single_layout_graph_is_unchanged_by_the_refactor(self):
        """The whole-clip path must still read [0:v] directly."""
        graph = build_video_filter(make_spec(FOLLOW))
        assert graph.startswith("[0:v]")
        assert "trim=" not in graph
        assert "concat=" not in graph


    def test_a_fit_crop_shot_joins_a_follow_crop_shot(self):
        graph = build_video_filter(make_spec(per_shot("fit_crop", "follow_crop")))
        assert "[cut0]crop=900:1080:300:0,split=2[bgs0][fgs0]" in graph
        assert "[cut1]crop=608:1080:100:0" in graph
        assert "[seg0][seg1]concat=n=2" in graph
