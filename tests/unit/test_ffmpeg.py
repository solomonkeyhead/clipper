"""FFmpeg discovery, Windows filter-path escaping, and encoder selection.

The escaping tests are the important ones: an unescaped drive colon silently
turns ``ass=C:\\x.ass`` into an unparseable filter argument, which is the single
most common way caption rendering breaks on Windows.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from clipper.render import ffmpeg
from clipper.render.ffmpeg import EncoderProbe, FFmpegError, escape_filter_path

from ..conftest import needs_ffmpeg


class TestEscapeFilterPath:
    def test_drive_colon_is_escaped(self):
        assert escape_filter_path(r"C:\tmp\subs.ass") == r"C\:/tmp/subs.ass"

    def test_backslashes_become_forward_slashes(self):
        assert "\\\\" not in escape_filter_path(r"C:\a\b\c.ass")

    def test_spaces_are_left_alone(self):
        """Spaces are fine inside a quoted filter argument; escaping them breaks libass."""
        out = escape_filter_path(r"C:\my videos\clip one.ass")
        assert "my videos" in out
        assert r"clip one.ass" in out

    @pytest.mark.parametrize("char", ["'", "[", "]", ","])
    def test_filter_metacharacters_are_escaped(self, char):
        out = escape_filter_path(Path(f"C:/tmp/a{char}b.ass"))
        assert f"\\{char}" in out

    def test_accepts_path_objects(self):
        assert escape_filter_path(Path(r"C:\tmp\x.ass")) == escape_filter_path(r"C:\tmp\x.ass")


class TestSelectVideoEncoder:
    def test_explicit_libx264_skips_the_probe(self, monkeypatch):
        called = False

        def boom(_name):  # pragma: no cover - must not run
            nonlocal called
            called = True
            raise AssertionError("probe should not be called for an explicit libx264")

        monkeypatch.setattr(ffmpeg, "probe_encoder", boom)
        name, _reason = ffmpeg.select_video_encoder("libx264")
        assert name == "libx264"
        assert not called

    def test_auto_prefers_nvenc_when_it_probes_clean(self, monkeypatch):
        monkeypatch.setattr(ffmpeg, "probe_encoder", lambda n: EncoderProbe(n, True))
        assert ffmpeg.select_video_encoder("auto")[0] == "h264_nvenc"

    def test_auto_falls_back_when_nvenc_will_not_open(self, monkeypatch):
        """A listed-but-unopenable NVENC must not fail the run."""
        detail = (
            "[h264_nvenc @ 0x1] Driver does not support the required nvenc API "
            "version. Required: 13.1 Found: 13.0"
        )
        monkeypatch.setattr(ffmpeg, "probe_encoder", lambda n: EncoderProbe(n, False, detail))
        name, reason = ffmpeg.select_video_encoder("auto")
        assert name == "libx264"
        assert "nvenc api version" in reason.lower()

    def test_explicit_nvenc_raises_rather_than_falling_back(self, monkeypatch):
        """If you asked for the GPU encoder, a silent CPU fallback hides the problem."""
        monkeypatch.setattr(ffmpeg, "probe_encoder", lambda n: EncoderProbe(n, False, "nope"))
        with pytest.raises(FFmpegError, match="h264_nvenc"):
            ffmpeg.select_video_encoder("h264_nvenc")


class TestDiscovery:
    def test_override_pointing_nowhere_is_an_error(self, monkeypatch, tmp_path):
        monkeypatch.setenv("CLIPPER_FFMPEG_DIR", str(tmp_path))
        ffmpeg._find.cache_clear()
        with pytest.raises(ffmpeg.FFmpegNotFound, match="CLIPPER_FFMPEG_DIR"):
            ffmpeg._find("ffmpeg")
        ffmpeg._find.cache_clear()


@needs_ffmpeg
class TestAgainstRealFFmpeg:
    def test_version_line_looks_like_ffmpeg(self):
        assert ffmpeg.version().startswith("ffmpeg version")

    def test_libass_is_present(self):
        """Without libass there are no burned-in captions, so this is load-bearing."""
        assert ffmpeg.has_build_flag("libass")
        assert ffmpeg.has_filter("ass")

    def test_qa_filters_are_present(self):
        for name in ("loudnorm", "silencedetect", "blackdetect", "freezedetect"):
            assert ffmpeg.has_filter(name), name

    def test_unknown_filter_is_not_reported_present(self):
        assert not ffmpeg.has_filter("definitely_not_a_filter")

    def test_failed_command_raises_with_stderr(self):
        with pytest.raises(FFmpegError) as exc:
            ffmpeg.run(["-hide_banner", "-i", "does_not_exist_12345.mp4", "-f", "null", "-"])
        assert exc.value.returncode != 0
        assert exc.value.stderr

    def test_libx264_actually_encodes(self):
        """The fallback encoder must work, since NVENC is unavailable here."""
        assert ffmpeg.probe_encoder("libx264").usable
