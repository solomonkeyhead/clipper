"""The QA gate's individual checks and its pass/warn/fail policy.

The policy matters as much as the thresholds: a check that *fails* causes the
clip to be discarded and replaced by a lower-scoring one, so failing should be
reserved for output that is actually broken rather than merely imperfect.
"""

from __future__ import annotations

import pytest

from clipper.config import QAConfig
from clipper.models import ClipPlan, LayoutPlan, QACheck, QAReport, Word
from clipper.qa import checks
from clipper.qa.checks import QAContext

QA = QAConfig()


def plan(**overrides) -> ClipPlan:
    base = dict(clip_id="001", candidate_id="c0", rank=1, start=10.0, end=40.0,
                text="some text")
    base.update(overrides)
    return ClipPlan(**base)


class FakeMedia:
    def __init__(self, **kw):
        self.duration = kw.get("duration", 30.0)
        self.width = kw.get("width", 1080)
        self.height = kw.get("height", 1920)
        self.fps = kw.get("fps", 30.0)
        self.has_audio = kw.get("has_audio", True)
        self.audio_codec = "aac"
        self.audio_channels = 2
        self.audio_sample_rate = 48000


class TestReportStatus:
    def test_all_passing(self):
        report = QAReport(clip_id="c", file="f", checks=[
            QACheck(name="a", status="pass"), QACheck(name="b", status="pass")])
        assert report.status == "pass"
        assert report.failures == []

    def test_a_warning_does_not_fail_the_clip(self):
        report = QAReport(clip_id="c", file="f", checks=[
            QACheck(name="a", status="pass"), QACheck(name="b", status="warn")])
        assert report.status == "warn"

    def test_any_failure_fails_the_clip(self):
        report = QAReport(clip_id="c", file="f", checks=[
            QACheck(name="a", status="pass"), QACheck(name="b", status="warn"),
            QACheck(name="c", status="fail")])
        assert report.status == "fail"
        assert [c.name for c in report.failures] == ["c"]


class TestDuration:
    def _context(self, low=15.0, high=60.0) -> QAContext:
        return QAContext(plan=plan(), words=[], duration_bounds=(low, high))

    def test_in_range(self):
        assert checks._check_duration(30.0, self._context()).status == "pass"

    def test_too_short_fails(self):
        result = checks._check_duration(10.0, self._context())
        assert result.status == "fail"
        assert "minimum" in result.detail

    def test_too_long_fails(self):
        result = checks._check_duration(90.0, self._context())
        assert result.status == "fail"
        assert "maximum" in result.detail


class TestResolutionAndFps:
    def test_correct_resolution(self):
        context = QAContext(plan=plan(), words=[])
        assert checks._check_resolution(FakeMedia(), context).status == "pass"

    def test_wrong_resolution_fails(self):
        context = QAContext(plan=plan(), words=[])
        result = checks._check_resolution(FakeMedia(width=1920, height=1080), context)
        assert result.status == "fail"
        assert "1920x1080" in result.detail

    def test_fps_drift_only_warns(self):
        """A frame rate a little off is playable; discarding the clip is worse."""
        context = QAContext(plan=plan(), words=[])
        assert checks._check_fps(FakeMedia(fps=25.0), context).status == "warn"

    def test_fps_within_tolerance_passes(self):
        context = QAContext(plan=plan(), words=[])
        assert checks._check_fps(FakeMedia(fps=29.97), context).status == "pass"


class TestAudioPresence:
    def test_present(self):
        assert checks._check_audio_present(FakeMedia()).status == "pass"

    def test_missing_fails(self):
        result = checks._check_audio_present(FakeMedia(has_audio=False))
        assert result.status == "fail"


class TestSilenceChecks:
    def test_within_the_limit(self):
        spans = [(5.0, 8.0)]
        assert checks._check_silence_ratio(spans, 30.0, QA).status == "pass"

    def test_over_the_limit_fails(self):
        spans = [(0.0, 20.0)]
        result = checks._check_silence_ratio(spans, 30.0, QA)
        assert result.status == "fail"
        assert result.measured == pytest.approx(20 / 30)

    def test_no_silence(self):
        assert checks._check_silence_ratio([], 30.0, QA).status == "pass"

    def test_zero_duration_warns_rather_than_dividing_by_zero(self):
        assert checks._check_silence_ratio([], 0.0, QA).status == "warn"

    def test_lead_silence_only_warns(self):
        """Whisper word timestamps lead the audio, so this is common and
        cosmetic; failing would swap the clip for a worse one."""
        result = checks._check_lead_silence([(0.0, 2.0)], QA)
        assert result.status == "warn"

    def test_lead_silence_allows_the_configured_pre_roll(self):
        spans = [(0.0, 0.4)]
        strict = checks._check_lead_silence(spans, QA, pre_roll=0.0)
        lenient = checks._check_lead_silence(spans, QA, pre_roll=0.15)
        assert strict.status == "warn"
        assert lenient.status == "pass"

    def test_a_clean_start_passes(self):
        assert checks._check_lead_silence([(10.0, 12.0)], QA).status == "pass"

    def test_trailing_dead_air_warns(self):
        result = checks._check_trail_silence([(25.0, 30.0)], 30.0, QA)
        assert result.status == "warn"


class TestBlackAndFreeze:
    def test_no_black_passes(self):
        assert checks._check_black_frames([], QA).status == "pass"

    def test_black_frames_fail(self):
        result = checks._check_black_frames([(0.0, 2.0)], QA)
        assert result.status == "fail"

    def test_a_still_passage_only_warns(self):
        """A held shot or a slide is content, not a broken render."""
        result = checks._check_frozen_frames(4.0, 60.0, QA)
        assert result.status == "warn"

    def test_a_stalled_render_fails(self):
        result = checks._check_frozen_frames(25.0, 60.0, QA)
        assert result.status == "fail"
        assert "stalled" in result.detail

    def test_brief_stillness_passes(self):
        assert checks._check_frozen_frames(1.5, 60.0, QA).status == "pass"

    def test_the_same_absolute_freeze_is_judged_against_clip_length(self):
        """5s frozen is nothing in a minute and fatal in ten seconds."""
        assert checks._check_frozen_frames(5.0, 60.0, QA).status == "warn"
        assert checks._check_frozen_frames(5.0, 10.0, QA).status == "fail"


class TestFaceRatio:
    def test_sufficient_faces(self):
        assert checks._check_face_ratio(0.8, QA).status == "pass"

    def test_insufficient_faces_fails(self):
        result = checks._check_face_ratio(0.2, QA)
        assert result.status == "fail"
        assert "20%" in result.detail

    def test_only_enforced_for_face_centric_layouts(self):
        """A blurred fit deliberately has no face; checking it would be absurd."""
        assert not LayoutPlan(kind="blurred_fit").is_face_centric
        assert LayoutPlan(kind="follow_crop").is_face_centric


class TestCaptionChecks:
    def _ass(self, events: list[tuple[float, float, str]]) -> str:
        lines = ["[Events]",
                 "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"]
        from clipper.utils.timecode import to_ass

        for start, end, text in events:
            lines.append(f"Dialogue: 0,{to_ass(start)},{to_ass(end)},Caption,,0,0,0,,{text}")
        return "\n".join(lines)

    def _context(self, events, words) -> QAContext:
        return QAContext(plan=plan(start=10.0, end=40.0), words=words,
                         ass_text=self._ass(events))

    def test_no_events_fails(self):
        context = QAContext(plan=plan(), words=[], ass_text="[Events]\n")
        results = checks._check_captions(context, QA)
        assert results[0].status == "fail"

    def test_events_inside_the_clip_pass(self):
        words = [Word(start=11.0, end=11.5, text="hello")]
        results = self._context([(0.5, 2.0, "HELLO")], words)
        results = checks._check_captions(results, QA)
        assert all(r.status == "pass" for r in results), [r.detail for r in results]

    def test_an_event_past_the_clip_end_fails(self):
        words = [Word(start=11.0, end=11.5, text="hello")]
        context = self._context([(0.5, 2.0, "HELLO"), (100.0, 105.0, "LATE")], words)
        results = checks._check_captions(context, QA)
        assert any(r.name == "caption_bounds" and r.status == "fail" for r in results)

    def test_uncovered_words_fail(self):
        """Every spoken word must fall inside some caption event."""
        words = [Word(start=10.5 + i, end=11.0 + i, text=f"w{i}") for i in range(20)]
        context = self._context([(0.5, 1.0, "W0")], words)
        results = checks._check_captions(context, QA)
        assert any(r.name == "caption_coverage" and r.status == "fail" for r in results)

    def test_coverage_is_timing_based_not_text_based(self):
        """Profanity masking legitimately rewrites caption text."""
        words = [Word(start=11.0, end=11.5, text="shit")]
        context = self._context([(0.5, 2.0, "S**T")], words)
        results = checks._check_captions(context, QA)
        assert all(r.status == "pass" for r in results)


class TestDetectionParsing:
    def test_black_spans(self):
        stderr = "[blackdetect] black_start:1.5 black_end:2.75 black_duration:1.25"
        assert checks._parse_black(stderr) == [(1.5, 2.75)]

    def test_freeze_total(self):
        stderr = ("[freezedetect] lavfi.freezedetect.freeze_start: 2.0\n"
                  "[freezedetect] lavfi.freezedetect.freeze_end: 5.5\n")
        assert checks._parse_freeze(stderr) == pytest.approx(3.5)

    def test_an_unclosed_freeze_is_not_counted(self):
        stderr = "[freezedetect] lavfi.freezedetect.freeze_start: 2.0\n"
        assert checks._parse_freeze(stderr) == 0.0

    def test_silence_spans(self):
        stderr = ("[silencedetect] silence_start: 1.0\n"
                  "[silencedetect] silence_end: 3.0\n")
        assert checks._parse_silence(stderr, 30.0) == [(1.0, 3.0)]

    def test_an_unclosed_silence_runs_to_the_end(self):
        stderr = "[silencedetect] silence_start: 25.0\n"
        assert checks._parse_silence(stderr, 30.0) == [(25.0, 30.0)]

    def test_a_negative_silence_start_is_clamped(self):
        """silencedetect can report a small negative start."""
        stderr = "[silencedetect] silence_start: -0.02\nsilence_end: 1.0\n"
        assert checks._parse_silence(stderr, 30.0)[0][0] == 0.0

    def test_empty_stderr(self):
        assert checks._parse_black("") == []
        assert checks._parse_freeze("") == 0.0
        assert checks._parse_silence("", 30.0) == []


class TestSummarize:
    def test_passing(self):
        report = QAReport(clip_id="c", file="f",
                          checks=[QACheck(name="a", status="pass")])
        assert "pass" in checks.summarize(report)

    def test_failing_lists_the_reasons(self):
        report = QAReport(clip_id="c", file="f", checks=[
            QACheck(name="a", status="fail", detail="too short")])
        assert "too short" in checks.summarize(report)

    def test_warning_is_distinguished_from_passing_cleanly(self):
        report = QAReport(clip_id="c", file="f", checks=[
            QACheck(name="a", status="warn", detail="a bit quiet")])
        assert "warning" in checks.summarize(report)
