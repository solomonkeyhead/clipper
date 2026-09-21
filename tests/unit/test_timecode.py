"""Timecode conversions. Off-by-a-centisecond here is off-by-a-frame on screen."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from clipper.utils import timecode as tc


class TestAss:
    @pytest.mark.parametrize(
        ("seconds", "expected"),
        [
            (0.0, "0:00:00.00"),
            (0.5, "0:00:00.50"),
            (1.0, "0:00:01.00"),
            (61.23, "0:01:01.23"),
            (3600.0, "1:00:00.00"),
            (3661.99, "1:01:01.99"),
        ],
    )
    def test_known_values(self, seconds, expected):
        assert tc.to_ass(seconds) == expected

    def test_negative_clamps_to_zero(self):
        """A pre-roll that ran past the start must not emit a negative stamp."""
        assert tc.to_ass(-1.5) == "0:00:00.00"

    def test_rounds_to_nearest_centisecond(self):
        assert tc.to_ass(1.005) == "0:00:01.00" or tc.to_ass(1.005) == "0:00:01.01"
        assert tc.to_ass(1.994) == "0:00:01.99"

    def test_carry_across_the_minute(self):
        """59.999 must become 1:00, not 0:60."""
        assert tc.to_ass(59.999) == "0:01:00.00"

    @given(st.floats(min_value=0, max_value=9 * 3600, allow_nan=False))
    def test_roundtrips_within_a_centisecond(self, seconds):
        assert abs(tc.from_ass(tc.to_ass(seconds)) - seconds) <= 0.005 + 1e-9


class TestOtherFormats:
    def test_srt_uses_comma_and_milliseconds(self):
        assert tc.to_srt(61.234) == "00:01:01,234"

    def test_ffmpeg_form_is_zero_padded_hours(self):
        assert tc.to_ffmpeg(61.234) == "00:01:01.234"

    def test_slug_omits_hours_when_short(self):
        assert tc.to_slug_timestamp(754) == "12m34s"
        assert tc.to_slug_timestamp(3754) == "1h02m34s"

    def test_format_duration_switches_units(self):
        assert tc.format_duration(8.4) == "8.4s"
        assert tc.format_duration(201) == "3m21s"
        assert tc.format_duration(3720) == "1h02m"


class TestSnapToFrame:
    def test_nearest(self):
        assert tc.snap_to_frame(1.017, 30) == pytest.approx(1.0 / 30 * 31, abs=1e-9)

    def test_floor_never_moves_a_start_later(self):
        """Flooring a start keeps the first spoken syllable inside the clip."""
        for t in (1.0, 1.01, 1.033, 5.5):
            assert tc.snap_to_frame(t, 30, mode="floor") <= t + 1e-9

    def test_ceil_never_moves_an_end_earlier(self):
        for t in (1.0, 1.01, 1.033, 5.5):
            assert tc.snap_to_frame(t, 30, mode="ceil") >= t - 1e-9

    def test_already_on_a_boundary_is_unchanged(self):
        exact = 90 / 30
        for mode in ("nearest", "floor", "ceil"):
            assert tc.snap_to_frame(exact, 30, mode=mode) == pytest.approx(exact)

    def test_negative_clamps_to_zero(self):
        assert tc.snap_to_frame(-0.5, 30) == 0.0

    def test_rejects_bad_input(self):
        with pytest.raises(ValueError, match="fps"):
            tc.snap_to_frame(1.0, 0)
        with pytest.raises(ValueError, match="mode"):
            tc.snap_to_frame(1.0, 30, mode="sideways")

    @given(
        st.floats(min_value=0, max_value=7200, allow_nan=False),
        st.sampled_from([24, 25, 30, 50, 60]),
    )
    def test_result_is_always_on_a_frame_boundary(self, seconds, fps):
        snapped = tc.snap_to_frame(seconds, fps)
        assert abs(snapped * fps - round(snapped * fps)) < 1e-6
