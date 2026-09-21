"""Sentence and paragraph segmentation.

Every candidate window must start and end on a sentence boundary, so a bad
boundary here caps the quality of everything downstream.
"""

from __future__ import annotations

from itertools import pairwise

import pytest

from clipper.models import Transcript, Word
from clipper.transcribe.segment import SegmentConfig, ends_sentence, segment


def transcript(*specs: tuple[float, float, str], source_id: str = "s") -> Transcript:
    return Transcript(
        source_id=source_id,
        language="en",
        words=[Word(start=s, end=e, text=t) for s, e, t in specs],
    )


def contiguous(words: list[str], *, start: float = 0.0, dur: float = 0.3,
               gaps: dict[int, float] | None = None) -> Transcript:
    """Build a transcript with a given gap after selected word indices."""
    gaps = gaps or {}
    specs: list[tuple[float, float, str]] = []
    t = start
    for i, text in enumerate(words):
        specs.append((t, t + dur, text))
        t += dur + gaps.get(i, 0.0)
    return transcript(*specs)


class TestEndsSentence:
    @pytest.mark.parametrize("text", ["done.", "really?", "wow!", "fine…", 'said."', "end.)"])
    def test_terminal_punctuation(self, text):
        assert ends_sentence(text)

    @pytest.mark.parametrize("text", ["word", "mid,", "and", "half-"])
    def test_non_terminal(self, text):
        assert not ends_sentence(text)

    @pytest.mark.parametrize("text", ["Mr.", "Dr.", "etc.", "e.g.", "vs.", "Inc."])
    def test_abbreviations_do_not_end_a_sentence(self, text):
        assert not ends_sentence(text)

    def test_decimal_fragment_does_not_end_a_sentence(self):
        """Whisper splits '3.5' into '3.' and '5'."""
        assert not ends_sentence("3.")

    def test_initial_does_not_end_a_sentence(self):
        assert not ends_sentence("J.")

    def test_a_real_number_ending_a_sentence_still_counts(self):
        assert ends_sentence("1999.")


class TestSentenceSplitting:
    def test_splits_on_terminal_punctuation(self):
        t = contiguous(["Hello", "there.", "How", "are", "you?"])
        result = segment(t)
        assert len(result.sentences) == 2
        assert result.sentences[0].text == "Hello there."

    def test_splits_on_a_long_pause_without_punctuation(self):
        """Whisper drops terminal marks on trailing-off speech."""
        t = contiguous(["this", "is", "one", "thing", "and", "another"], gaps={3: 1.2})
        result = segment(t, SegmentConfig(pause_boundary=0.6))
        assert len(result.sentences) == 2
        assert result.sentences[0].text.endswith("thing")

    def test_short_pause_does_not_split(self):
        t = contiguous(["this", "is", "one", "thing", "and", "another"], gaps={3: 0.2})
        assert len(segment(t, SegmentConfig(pause_boundary=0.6)).sentences) == 1

    def test_caps_a_sentence_that_is_never_punctuated(self):
        """An unbounded 'sentence' makes every window from it far too long."""
        t = contiguous([f"w{i}" for i in range(200)], dur=0.3)
        result = segment(t, SegmentConfig(max_sentence_seconds=10.0))
        assert all(s.duration <= 11.0 for s in result.sentences)
        assert len(result.sentences) > 1

    def test_records_whether_punctuation_ended_it(self):
        t = contiguous(["one", "two.", "three", "four"], gaps={3: 2.0})
        sentences = segment(t).sentences
        assert sentences[0].ends_with_terminal_punctuation
        assert not sentences[1].ends_with_terminal_punctuation

    def test_empty_transcript(self):
        assert segment(transcript()).sentences == []

    def test_single_word(self):
        result = segment(transcript((0.0, 0.5, "Hi.")))
        assert len(result.sentences) == 1
        assert result.sentences[0].text == "Hi."


class TestInvariants:
    """Properties every downstream stage relies on."""

    def _fixture(self) -> Transcript:
        return contiguous(
            ["Most", "people", "quit.", "They", "run", "out", "of", "time.",
             "That", "is", "the", "real", "problem.", "Here", "is", "the", "fix."],
            gaps={2: 0.8, 7: 1.9, 12: 0.7},
        )

    def test_sentences_are_in_order_and_do_not_overlap(self):
        sentences = segment(self._fixture()).sentences
        for a, b in pairwise(sentences):
            assert a.end <= b.start + 1e-9
            assert a.index < b.index

    def test_indices_are_contiguous_from_zero(self):
        sentences = segment(self._fixture()).sentences
        assert [s.index for s in sentences] == list(range(len(sentences)))

    def test_word_indices_partition_the_transcript(self):
        """Every word belongs to exactly one sentence, in order."""
        t = self._fixture()
        sentences = segment(t).sentences
        covered: list[int] = []
        for s in sentences:
            lo, hi = s.word_indices
            covered.extend(range(lo, hi))
        assert covered == list(range(len(t.words)))

    def test_text_matches_the_word_span(self):
        t = self._fixture()
        for s in segment(t).sentences:
            lo, hi = s.word_indices
            assert s.text == " ".join(w.text for w in t.words[lo:hi])

    def test_bounds_match_the_word_span(self):
        t = self._fixture()
        for s in segment(t).sentences:
            lo, hi = s.word_indices
            assert s.start == t.words[lo].start
            assert s.end == t.words[hi - 1].end


class TestFragmentMerging:
    def test_a_one_word_fragment_merges_forward(self):
        """'Right.' alone is a useless clip boundary."""
        t = contiguous(["Right.", "Now", "here", "is", "the", "real", "point."], gaps={0: 0.9})
        sentences = segment(t, SegmentConfig(min_sentence_words=2)).sentences
        assert len(sentences) == 1
        assert sentences[0].text.startswith("Right.")

    def test_a_trailing_fragment_merges_backward(self):
        """The last fragment has no successor, so it joins the previous one."""
        t = contiguous(["This", "is", "a", "full", "thought.", "Yeah."], gaps={4: 0.9})
        sentences = segment(t, SegmentConfig(min_sentence_words=2)).sentences
        assert len(sentences) == 1
        assert sentences[0].text.endswith("Yeah.")

    def test_two_fragments_merging_can_satisfy_the_minimum(self):
        """'Yeah.' + 'Right.' is two words, so the pair stands alone at min=2."""
        t = contiguous(["Yeah.", "Right.", "So", "here", "is", "the", "thing."],
                       gaps={0: 0.9, 1: 0.9})
        sentences = segment(t, SegmentConfig(min_sentence_words=2)).sentences
        assert len(sentences) == 2
        assert sentences[0].text == "Yeah. Right."

    def test_fragments_keep_merging_until_the_minimum_is_met(self):
        t = contiguous(["Yeah.", "Right.", "So", "here", "is", "the", "thing."],
                       gaps={0: 0.9, 1: 0.9})
        sentences = segment(t, SegmentConfig(min_sentence_words=3)).sentences
        assert len(sentences) == 1
        assert sentences[0].text.startswith("Yeah. Right.")

    def test_merging_preserves_the_word_partition(self):
        t = contiguous(["Yeah.", "Right.", "So", "here", "we", "go."], gaps={0: 0.9, 1: 0.9})
        sentences = segment(t, SegmentConfig(min_sentence_words=2)).sentences
        covered: list[int] = []
        for s in sentences:
            lo, hi = s.word_indices
            covered.extend(range(lo, hi))
        assert covered == list(range(len(t.words)))

    def test_merged_sentence_takes_the_later_punctuation_flag(self):
        t = contiguous(["Yeah", "so", "here", "we", "go."], gaps={0: 0.9})
        sentences = segment(t, SegmentConfig(min_sentence_words=3)).sentences
        assert sentences[-1].ends_with_terminal_punctuation


class TestParagraphs:
    def test_a_long_pause_starts_a_new_paragraph(self):
        t = contiguous(["First", "topic", "here.", "Second", "topic", "now."], gaps={2: 2.5})
        sentences = segment(t, SegmentConfig(paragraph_pause=1.6)).sentences
        assert sentences[0].paragraph == 0
        assert sentences[-1].paragraph == 1

    def test_a_short_pause_keeps_the_paragraph(self):
        t = contiguous(["First", "topic", "here.", "Second", "topic", "now."], gaps={2: 0.8})
        sentences = segment(t, SegmentConfig(paragraph_pause=1.6)).sentences
        assert {s.paragraph for s in sentences} == {0}

    def test_paragraph_numbers_never_decrease(self):
        t = contiguous([f"w{i}." for i in range(20)], gaps={3: 2.0, 9: 2.0, 14: 2.0})
        paragraphs = [s.paragraph for s in segment(t).sentences]
        assert paragraphs == sorted(paragraphs)
