"""Candidate window generation and its hard filters.

BUILD_BRIEF.md section 8 says this stage sets the ceiling for everything else,
so the invariants (sentence-aligned bounds, duration in range, no near-duplicates)
are tested as properties rather than examples wherever that is cheap.
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from clipper.candidates.windows import (
    dedupe,
    generate,
    is_sponsor_read,
    pre_score,
    silence_ratio,
)
from clipper.config import CandidatesConfig
from clipper.models import Candidate, Sentences, Transcript, Word
from clipper.transcribe.segment import segment


def build(sentence_texts: list[str], *, words_each: int = 8, word_dur: float = 0.35,
          gap: float = 0.5) -> tuple[Transcript, Sentences]:
    """A transcript of N sentences with uniform, realistic cadence."""
    specs: list[tuple[float, float, str]] = []
    t = 0.0
    for text in sentence_texts:
        tokens = text.split()
        for i, token in enumerate(tokens):
            specs.append((t, t + word_dur, token))
            t += word_dur
            if i == len(tokens) - 1:
                t += gap
    transcript = Transcript(
        source_id="s", language="en", duration=t,
        words=[Word(start=s, end=e, text=x) for s, e, x in specs],
    )
    return transcript, segment(transcript)


def sentences_of(count: int, words_each: int = 8) -> list[str]:
    return [" ".join(f"w{i}x{j}" for j in range(words_each - 1)) + f" w{i}end."
            for i in range(count)]


CFG = CandidatesConfig(
    min_seconds=10.0, max_seconds=30.0, target_seconds=(12.0, 25.0),
    max_candidates=60, edge_trim_min_source_seconds=1e9,  # disable edge trim
)


class TestBoundaryInvariants:
    def test_every_window_starts_and_ends_on_a_sentence(self):
        transcript, sentences = build(sentences_of(12))
        result = generate(transcript, sentences, CFG, source_duration=transcript.duration)
        starts = {s.start for s in sentences.sentences}
        ends = {s.end for s in sentences.sentences}
        assert result.candidates
        for c in result.candidates:
            assert c.start in starts, c.candidate_id
            assert c.end in ends, c.candidate_id

    def test_every_window_is_inside_the_duration_bounds(self):
        transcript, sentences = build(sentences_of(14))
        result = generate(transcript, sentences, CFG, source_duration=transcript.duration)
        for c in result.candidates:
            assert CFG.min_seconds - 1e-6 <= c.duration <= CFG.max_seconds + 1e-6

    def test_sentence_indices_agree_with_the_times(self):
        transcript, sentences = build(sentences_of(12))
        result = generate(transcript, sentences, CFG, source_duration=transcript.duration)
        items = sentences.sentences
        for c in result.candidates:
            lo, hi = c.sentence_indices
            assert items[lo].start == c.start
            assert items[hi - 1].end == c.end

    def test_candidate_ids_are_unique_and_ordered(self):
        transcript, sentences = build(sentences_of(14))
        result = generate(transcript, sentences, CFG, source_duration=transcript.duration)
        ids = [c.candidate_id for c in result.candidates]
        assert len(set(ids)) == len(ids)
        assert [c.start for c in result.candidates] == sorted(c.start for c in result.candidates)

    def test_text_matches_the_sentence_span(self):
        transcript, sentences = build(sentences_of(10))
        result = generate(transcript, sentences, CFG, source_duration=transcript.duration)
        for c in result.candidates:
            lo, hi = c.sentence_indices
            assert c.text == " ".join(s.text for s in sentences.sentences[lo:hi])

    @settings(max_examples=25, deadline=None)
    @given(st.integers(min_value=0, max_value=25))
    def test_never_exceeds_the_candidate_cap(self, count):
        transcript, sentences = build(sentences_of(count)) if count else build([])
        cfg = CFG.model_copy(update={"max_candidates": 5})
        result = generate(transcript, sentences, cfg, source_duration=transcript.duration)
        assert len(result.candidates) <= 5


class TestHardFilters:
    def test_no_sentences_yields_no_candidates(self):
        transcript = Transcript(source_id="s", language="en", words=[])
        assert generate(transcript, Sentences(source_id="s"), CFG,
                        source_duration=0.0).candidates == []

    def test_source_too_short_for_the_minimum_yields_nothing(self):
        transcript, sentences = build(sentences_of(1, words_each=4))
        result = generate(transcript, sentences, CFG, source_duration=transcript.duration)
        assert result.candidates == []

    def test_edges_are_trimmed_on_a_long_source(self):
        transcript, sentences = build(sentences_of(40))
        cfg = CFG.model_copy(update={
            "edge_trim_seconds": 20.0, "edge_trim_min_source_seconds": 60.0,
        })
        result = generate(transcript, sentences, cfg, source_duration=transcript.duration)
        assert result.candidates
        for c in result.candidates:
            assert c.start >= 20.0
            assert c.end <= transcript.duration - 20.0

    def test_edges_are_kept_on_a_short_source(self):
        """A five-minute source has no throwaway intro to skip."""
        transcript, sentences = build(sentences_of(12))
        cfg = CFG.model_copy(update={
            "edge_trim_seconds": 20.0, "edge_trim_min_source_seconds": 1e9,
        })
        result = generate(transcript, sentences, cfg, source_duration=transcript.duration)
        assert min(c.start for c in result.candidates) < 20.0

    def test_silent_windows_are_dropped(self):
        transcript, sentences = build(sentences_of(12), gap=6.0)
        cfg = CFG.model_copy(update={"max_silence_ratio": 0.25})
        result = generate(transcript, sentences, cfg, source_duration=transcript.duration)
        assert result.candidates == []

    def test_reported_silence_ratio_is_under_the_limit(self):
        transcript, sentences = build(sentences_of(14))
        result = generate(transcript, sentences, CFG, source_duration=transcript.duration)
        for c in result.candidates:
            assert c.silence_ratio <= CFG.max_silence_ratio + 1e-9


class TestSilenceRatio:
    def _candidate(self, start: float, end: float) -> Candidate:
        return Candidate(candidate_id="c", start=start, end=end,
                         sentence_indices=(0, 1), text="")

    def test_fully_voiced_is_zero(self):
        words = [Word(start=0.0, end=10.0, text="x")]
        assert silence_ratio(self._candidate(0.0, 10.0), words) == pytest.approx(0.0)

    def test_half_voiced_is_half(self):
        words = [Word(start=0.0, end=5.0, text="x")]
        assert silence_ratio(self._candidate(0.0, 10.0), words) == pytest.approx(0.5)

    def test_no_words_is_fully_silent(self):
        assert silence_ratio(self._candidate(0.0, 10.0), []) == pytest.approx(1.0)

    def test_words_outside_the_window_are_clipped_not_counted(self):
        words = [Word(start=-5.0, end=5.0, text="x")]
        assert silence_ratio(self._candidate(0.0, 10.0), words) == pytest.approx(0.5)

    def test_result_is_always_a_ratio(self):
        words = [Word(start=0.0, end=100.0, text="x")]
        assert 0.0 <= silence_ratio(self._candidate(0.0, 10.0), words) <= 1.0


class TestSponsorDetection:
    def test_a_full_ad_read_is_caught(self):
        text = (
            "This episode is sponsored by Acme. Use the promo code SHOW for 20% off "
            "and a free trial. The link is in the description."
        )
        assert is_sponsor_read(text)

    def test_one_passing_phrase_is_not_an_ad(self):
        """'Link in the description' said once must not delete a good moment."""
        assert not is_sponsor_read(
            "I wrote about this at length, the link is in the description if you want it."
        )

    def test_ordinary_speech_is_not_an_ad(self):
        assert not is_sponsor_read(
            "We spent four months rebuilding the whole pipeline from scratch."
        )

    def test_matching_is_case_insensitive(self):
        assert is_sponsor_read("SPONSORED BY Acme. USE THE PROMO CODE now. 20% OFF.")

    def test_empty_text(self):
        assert not is_sponsor_read("")


class TestPreScore:
    def _pair(self, text: str, duration: float = 30.0):
        words = text.split()
        step = duration / max(1, len(words))
        transcript = Transcript(
            source_id="s", language="en",
            words=[Word(start=i * step, end=i * step + step * 0.9, text=w)
                   for i, w in enumerate(words)],
        )
        candidate = Candidate(candidate_id="c", start=0.0, end=duration,
                              sentence_indices=(0, 1), text=text)
        return candidate, transcript

    def test_a_hook_opener_scores_above_a_flat_opener(self):
        hooky, t1 = self._pair("Why does nobody talk about the thing that actually matters here")
        flat, t2 = self._pair("And then we also did some other stuff after that as well too")
        assert pre_score(hooky, t1) > pre_score(flat, t2)

    def test_filler_heavy_opening_scores_lower(self):
        clean, t1 = self._pair("I lost eleven months to this exact mistake and here is how")
        filler, t2 = self._pair("So um like you know I mean basically actually it was kind of")
        assert pre_score(clean, t1) > pre_score(filler, t2)

    def test_no_words_scores_zero(self):
        candidate = Candidate(candidate_id="c", start=0.0, end=10.0,
                              sentence_indices=(0, 1), text="")
        assert pre_score(candidate, Transcript(source_id="s", language="en", words=[])) == 0.0

    def test_score_is_finite_and_non_negative(self):
        candidate, transcript = self._pair("A short thing.")
        assert 0.0 <= pre_score(candidate, transcript) < 100.0


class TestDedupe:
    def _c(self, start: float, end: float, score: float, cid: str = "c") -> Candidate:
        return Candidate(candidate_id=cid, start=start, end=end,
                         sentence_indices=(0, 1), text="", pre_score=score)

    def test_near_identical_windows_collapse(self):
        kept = dedupe([self._c(0, 30, 1.0, "a"), self._c(0.1, 30.1, 0.5, "b")], 0.8)
        assert len(kept) == 1

    def test_the_higher_scoring_one_survives(self):
        kept = dedupe([self._c(0, 30, 0.5, "low"), self._c(0.1, 30.1, 0.9, "high")], 0.8)
        assert kept[0].candidate_id == "high"

    def test_distinct_windows_are_both_kept(self):
        assert len(dedupe([self._c(0, 30, 1.0, "a"), self._c(100, 130, 1.0, "b")], 0.8)) == 2

    def test_partial_overlap_below_the_threshold_is_kept(self):
        # IoU of [0,30] and [20,50] is 10/40 = 0.25
        assert len(dedupe([self._c(0, 30, 1.0, "a"), self._c(20, 50, 1.0, "b")], 0.8)) == 2

    def test_output_is_sorted_by_start(self):
        kept = dedupe([self._c(100, 130, 1.0, "b"), self._c(0, 30, 0.5, "a")], 0.8)
        assert [c.candidate_id for c in kept] == ["a", "b"]

    def test_empty_input(self):
        assert dedupe([], 0.8) == []

    @settings(max_examples=40, deadline=None)
    @given(st.lists(
        st.tuples(st.floats(0, 500), st.floats(10, 60), st.floats(0, 3)),
        min_size=0, max_size=25,
    ))
    def test_no_surviving_pair_exceeds_the_threshold(self, specs):
        candidates = [
            self._c(start, start + length, score, f"c{i}")
            for i, (start, length, score) in enumerate(specs)
        ]
        kept = dedupe(candidates, 0.8)
        for i, a in enumerate(kept):
            for b in kept[i + 1:]:
                assert a.iou(b) <= 0.8 + 1e-9


class TestIou:
    def test_identical_windows(self):
        a = Candidate(candidate_id="a", start=0, end=30, sentence_indices=(0, 1), text="")
        assert a.iou(a) == pytest.approx(1.0)

    def test_disjoint_windows(self):
        a = Candidate(candidate_id="a", start=0, end=30, sentence_indices=(0, 1), text="")
        b = Candidate(candidate_id="b", start=100, end=130, sentence_indices=(0, 1), text="")
        assert a.iou(b) == 0.0

    def test_is_symmetric(self):
        a = Candidate(candidate_id="a", start=0, end=30, sentence_indices=(0, 1), text="")
        b = Candidate(candidate_id="b", start=15, end=45, sentence_indices=(0, 1), text="")
        assert a.iou(b) == pytest.approx(b.iou(a))

    def test_overlaps_agrees_with_a_positive_iou(self):
        a = Candidate(candidate_id="a", start=0, end=30, sentence_indices=(0, 1), text="")
        b = Candidate(candidate_id="b", start=15, end=45, sentence_indices=(0, 1), text="")
        assert a.overlaps(b) and a.iou(b) > 0


class TestSpreadCap:
    """The LLM budget must reach the whole source, not one strong stretch.

    Measured on a 13-minute TV episode: 43 of 48 candidates were variations of
    one two-minute scene, and a request for four clips produced one.
    """

    @staticmethod
    def _cand(start: float, end: float, score: float):
        from clipper.models import Candidate

        c = Candidate(candidate_id="x", start=start, end=end, sentence_indices=(0, 1), text="x")
        c.pre_score = score
        return c

    def _hot_scene_and_the_rest(self):
        # Twenty strong variations of one scene at 200-320s, then weaker
        # windows spread over the rest of a 780s episode.
        hot = [self._cand(200 + i, 260 + i * 3, 0.9 - i * 0.001) for i in range(20)]
        rest = [self._cand(t, t + 50, 0.3) for t in range(0, 780, 60) if not 150 <= t <= 330]
        return hot, rest

    def test_the_budget_reaches_beyond_the_strongest_scene(self):
        from clipper.candidates.windows import spread_cap

        hot, rest = self._hot_scene_and_the_rest()
        chosen = spread_cap(hot + rest, 10, 3)
        outside = [c for c in chosen if not 150 <= c.start <= 330]
        assert len(outside) >= 5, "most of the budget went to one scene"

    def test_the_strongest_scene_still_gets_several_cuts(self):
        """The LLM needs a few boundary variants to pick the best cut."""
        from clipper.candidates.windows import spread_cap

        hot, rest = self._hot_scene_and_the_rest()
        chosen = spread_cap(hot + rest, 10, 3)
        assert sum(1 for c in chosen if 150 <= c.start <= 330) >= 2

    def test_no_instant_is_over_covered_while_there_is_budget_elsewhere(self):
        from clipper.candidates.windows import _peak_coverage, spread_cap

        hot, rest = self._hot_scene_and_the_rest()
        chosen = spread_cap(hot + rest, 10, 3)
        for c in chosen:
            others = [o for o in chosen if o is not c]
            assert _peak_coverage(c, others) < 3

    def test_unused_budget_is_filled(self):
        from clipper.candidates.windows import spread_cap

        hot, _ = self._hot_scene_and_the_rest()
        assert len(spread_cap(hot, 10, 3)) == 10

    def test_the_limit_is_respected(self):
        from clipper.candidates.windows import spread_cap

        hot, rest = self._hot_scene_and_the_rest()
        assert len(spread_cap(hot + rest, 7, 3)) == 7


class TestScriptedCampaign:
    """Sitcom windows run a median 38-41% without dialogue (podcasts 13-18%);
    at the default 25% cut-off only 4-10% of an episode survived."""

    def test_scripted_relaxes_the_dialogue_gap_filter(self):
        from clipper.config import CampaignConfig, Config
        from clipper.runner import SCRIPTED_MAX_SILENCE, campaign_config

        base = dict(name="t", source_authorization="Authorized for this test campaign.")
        tv = campaign_config(Config(), CampaignConfig(**base, scripted=True))
        pod = campaign_config(Config(), CampaignConfig(**base))
        assert tv.candidates.max_silence_ratio == SCRIPTED_MAX_SILENCE
        assert pod.candidates.max_silence_ratio == Config().candidates.max_silence_ratio

    def test_scripted_keeps_scenes_that_need_context(self):
        from clipper.config import CampaignConfig, Config
        from clipper.models import RubricScores
        from clipper.runner import campaign_config
        from clipper.signals.llm import _hard_drop_reason

        base = dict(name="t", source_authorization="Authorized for this test campaign.")
        cfg = campaign_config(Config(), CampaignConfig(**base, scripted=True))
        needs = RubricScores(hook_strength=6, standalone_clarity=4, payoff=6,
                             emotional_intensity=6, quotability=6, ending_completeness=6,
                             needs_prior_context=True)
        assert not cfg.llm.drop_needs_prior_context
        assert _hard_drop_reason(needs, needs, single_opinion=False,
                                 drop_context=cfg.llm.drop_needs_prior_context) == ""
        assert _hard_drop_reason(needs, needs, single_opinion=False) != ""

    def test_scripted_still_drops_ads_and_high_risk(self):
        from clipper.models import RubricScores
        from clipper.signals.llm import _hard_drop_reason

        risky = RubricScores(hook_strength=6, standalone_clarity=6, payoff=6,
                             emotional_intensity=6, quotability=6, ending_completeness=6,
                             policy_risk="high")
        assert _hard_drop_reason(risky, risky, single_opinion=False, drop_context=False)
