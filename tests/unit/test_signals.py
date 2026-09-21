"""The three non-LLM signals: text structure, heatmap correction, and audio."""

from __future__ import annotations

import numpy as np
import pytest

from clipper.models import Candidate, MediaInfo, SourceInfo
from clipper.signals import heatmap as hm
from clipper.signals import text as txt


def candidate(text: str, start: float = 0.0, duration: float = 30.0) -> Candidate:
    return Candidate(candidate_id="c", start=start, end=start + duration,
                     sentence_indices=(0, 1), text=text)


def features(text: str) -> dict[str, float]:
    return txt.features_for(candidate(text))


class TestTextFeatures:
    def test_every_feature_is_a_unit_value(self):
        for text in ["Why does this happen?", "So um yeah anyway.", "",
                     "That was the moment it all changed."]:
            for name, value in features(text).items():
                assert 0.0 <= value <= 1.0, f"{name}={value} for {text!r}"

    def test_an_empty_candidate_scores_zero(self):
        assert all(v == 0.0 for v in features("").values())

    def test_a_question_mark_opener(self):
        assert features("Why do most projects fail? Because nobody wrote it down.")[
            "opens_with_question"] == 1.0

    def test_a_question_shaped_opener_without_the_mark(self):
        value = features("How you handle that moment decides everything.")["opens_with_question"]
        assert 0.0 < value < 1.0

    def test_a_statement_opener(self):
        assert features("The build finally shipped last Tuesday.")["opens_with_question"] == 0.0

    def test_numeric_claims_are_detected(self):
        assert features("I lost 40,000 dollars in one afternoon.")["has_numeric_claim"] == 1.0
        assert features("I lost a lot of money that day.")["has_numeric_claim"] == 0.0

    def test_contrast_words_are_detected(self):
        assert features("I planned for months, but the launch still failed.")[
            "has_contrast"] == 1.0
        assert features("I planned for months and the launch went fine.")[
            "has_contrast"] == 0.0

    def test_a_dangling_pronoun_opener_is_not_self_contained(self):
        """The clearest textual sign a window was cut out of its context."""
        assert features("That was the moment everything changed.")[
            "first_sentence_self_contained"] == 0.0

    def test_a_concrete_opener_is_self_contained(self):
        assert features("Most founders quit in the second year.")[
            "first_sentence_self_contained"] == 1.0

    def test_filler_heavy_openings_score_low(self):
        clean = features("Most founders quit in the second year of building.")
        filler = features("So um like you know I mean basically it was kind of fine.")
        assert clean["low_opening_filler"] > filler["low_opening_filler"]

    def test_setup_payoff_markers_are_detected(self):
        assert features(
            "Here's the reason it failed. The problem was nobody owned the deploy."
        )["setup_payoff"] > 0.5

    def test_uniform_sentence_lengths_score_lower_than_varied(self):
        uniform = features("One two three four. Five six seven eight. Nine ten "
                           "eleven twelve. Four more words here.")
        varied = features("It failed. The reason was that nobody had written down "
                          "who owned the deploy step on release day. Nobody.")
        assert varied["sentence_rhythm"] >= uniform["sentence_rhythm"]

    def test_direct_address_is_detected(self):
        assert features("You are going to want to write this down for yourself.")[
            "direct_address"] > 0.0


class TestTextRawScore:
    def test_is_a_unit_value(self):
        for text in ["", "Why?", "Most founders quit in the second year."]:
            assert 0.0 <= txt.raw_score(features(text)) <= 1.0

    def test_a_strong_opener_outscores_a_rambling_one(self):
        strong = txt.raw_score(features(
            "Why do 90% of side projects die? Because the next step was never "
            "written down. Here's the fix that doubled my completion rate."
        ))
        weak = txt.raw_score(features(
            "So um, that was, you know, kind of what I was saying about it earlier."
        ))
        assert strong > weak

    def test_missing_features_do_not_crash(self):
        assert txt.raw_score({}) == 0.0


class TestHeatmapResample:
    def test_maps_segments_onto_a_uniform_grid(self):
        segments = [{"start_time": 0.0, "end_time": 10.0, "value": 0.5},
                    {"start_time": 10.0, "end_time": 20.0, "value": 1.0}]
        grid = hm.resample(segments, 20.0)
        assert grid.size == 20
        assert grid[0] == pytest.approx(0.5)
        assert grid[15] == pytest.approx(1.0)

    def test_gaps_are_interpolated_not_left_at_zero(self):
        """Zero would read as 'nobody watched this', which is not what a gap means."""
        segments = [{"start_time": 0.0, "end_time": 5.0, "value": 1.0},
                    {"start_time": 15.0, "end_time": 20.0, "value": 1.0}]
        grid = hm.resample(segments, 20.0)
        assert grid[10] > 0.5

    def test_overlapping_segments_are_averaged(self):
        segments = [{"start_time": 0.0, "end_time": 10.0, "value": 0.0},
                    {"start_time": 0.0, "end_time": 10.0, "value": 1.0}]
        assert hm.resample(segments, 10.0)[5] == pytest.approx(0.5)

    def test_empty_input(self):
        assert hm.resample([], 100.0).size == 0
        assert hm.resample([{"start_time": 0, "end_time": 1, "value": 1}], 0).size == 0


class TestHeatmapBiasCorrection:
    def test_removes_a_slow_trend(self):
        """Intro inflation is a slow component; local prominence is not."""
        t = np.arange(600, dtype=np.float32)
        ramp = np.linspace(1.0, 0.2, 600).astype(np.float32)
        corrected = hm.bias_correct(ramp, window_seconds=180.0)
        assert abs(float(corrected.mean())) < 0.05
        del t

    def test_a_local_spike_survives_correction(self):
        values = np.full(600, 0.5, dtype=np.float32)
        values[300:310] = 1.0
        corrected = hm.bias_correct(values, window_seconds=180.0)
        assert corrected[305] > 0.4
        assert abs(corrected[100]) < 0.05

    def test_intro_inflation_is_flattened(self):
        values = np.full(600, 0.3, dtype=np.float32)
        values[:30] = 1.0  # the classic restart spike
        corrected = hm.bias_correct(values, window_seconds=180.0)
        assert corrected[:30].mean() < values[:30].mean()

    def test_a_flat_curve_corrects_to_about_zero(self):
        corrected = hm.bias_correct(np.full(400, 0.7, dtype=np.float32))
        assert np.allclose(corrected, 0.0, atol=1e-5)

    def test_empty_input(self):
        assert hm.bias_correct(np.zeros(0, dtype=np.float32)).size == 0

    def test_the_output_length_matches_the_input(self):
        for size in (1, 5, 100, 601):
            values = np.random.RandomState(0).rand(size).astype(np.float32)
            assert hm.bias_correct(values).size == size


class TestRollingMedian:
    def test_length_is_preserved(self):
        assert hm.rolling_median(np.arange(50, dtype=np.float32), 7).size == 50

    def test_a_constant_series_is_unchanged(self):
        values = np.full(50, 3.0, dtype=np.float32)
        assert np.allclose(hm.rolling_median(values, 9), 3.0)

    def test_an_outlier_is_ignored(self):
        values = np.full(50, 1.0, dtype=np.float32)
        values[25] = 100.0
        assert hm.rolling_median(values, 9)[25] == pytest.approx(1.0)

    def test_a_window_larger_than_the_series_is_clamped(self):
        assert hm.rolling_median(np.arange(5, dtype=np.float32), 999).size == 5

    def test_edges_are_reflected_not_zero_padded(self):
        """Zero padding would depress the baseline exactly where intro
        inflation lives, which is the opposite of the intended correction."""
        values = np.full(100, 5.0, dtype=np.float32)
        assert hm.rolling_median(values, 21)[0] == pytest.approx(5.0)


class TestHeatmapScoring:
    def _info(self, heatmap, duration: float = 600.0) -> SourceInfo:
        return SourceInfo(
            source_id="s",
            media=MediaInfo(path="x.mp4", duration=duration, width=1920, height=1080,
                            fps=30.0, has_audio=True),
            heatmap=heatmap,
        )

    def test_no_heatmap_returns_none(self):
        """The signal must degrade to unavailable, not to zero."""
        assert hm.score_candidates([candidate("t")], self._info(None)) is None

    def test_a_replayed_window_outscores_an_ignored_one(self):
        segments = [{"start_time": float(i), "end_time": float(i + 1),
                     "value": 1.0 if 300 <= i < 340 else 0.2}
                    for i in range(600)]
        replayed = candidate("hot", start=300, duration=30)
        ignored = candidate("cold", start=100, duration=30)
        ignored.candidate_id = "cold"
        replayed.candidate_id = "hot"

        result = hm.score_candidates([replayed, ignored], self._info(segments))
        assert result is not None
        raws, _ = result
        assert raws["hot"] > raws["cold"]

    def test_features_are_recorded_for_explain(self):
        segments = [{"start_time": 0.0, "end_time": 600.0, "value": 0.5}]
        result = hm.score_candidates([candidate("t", start=100)], self._info(segments))
        _, details = result
        assert set(details["c"]) == {"mean", "peak", "raw_mean"}

    def test_a_window_past_the_end_does_not_crash(self):
        segments = [{"start_time": 0.0, "end_time": 100.0, "value": 0.5}]
        result = hm.score_candidates(
            [candidate("t", start=5000)], self._info(segments, duration=100.0)
        )
        raws, _ = result
        assert raws["c"] == 0.0
