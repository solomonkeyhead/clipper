"""Fixing misheard caption words from context.

Reported on real output: "I heard the *picture* from Los Angeles Dodgers" for a
baseball pitcher. The LLM that proposes fixes is not trusted to rewrite: every
edit must name the word it changes, must sound like it, must be confirmed by a
second question, and a clip can only change a few words. These tests pin each
of those guards, using the real proposals seen on the real clips.
"""

from __future__ import annotations

import json

import pytest

from clipper.llm.base import MockBackend
from clipper.models import Word
from clipper.transcribe.correct import (
    _Edit,
    apply_edits,
    correct_words,
    sounds_alike,
)


def words(text: str) -> list[Word]:
    return [Word(start=i * 0.3, end=i * 0.3 + 0.25, text=f" {t}")
            for i, t in enumerate(text.split())]


CLIP = words("I heard the picture from Los Angeles Dodgers. Monkey never clap "
             "because a monkey every day bananas.")


def edit(index: int, original: str, replacement: str) -> _Edit:
    return _Edit(index=index, original=original, replacement=replacement, reason="test")


class TestSoundsAlike:
    """The measured pairs: 20 real homophones and near-homophones, 15 swaps that
    change what was said. All of the first must pass, none of the second."""

    HOMOPHONES = (
        ("picture", "pitcher"), ("clap", "cramp"), ("there", "their"), ("to", "too"),
        ("weather", "whether"), ("knight", "night"), ("right", "write"), ("sea", "see"),
        ("eight", "ate"), ("flower", "flour"), ("lose", "loose"), ("then", "than"),
        ("accept", "except"), ("Rosalie", "Rosolie"), ("its", "it's"),
        ("your", "you're"), ("pier", "peer"), ("break", "brake"),
        ("course", "coarse"), ("allowed", "aloud"),
    )
    SWAPS = (
        ("bananas", "apples"), ("monkey", "gorilla"), ("clap", "cheer"),
        ("never", "always"), ("jungle", "forest"), ("dog", "cat"),
        ("pitcher", "catcher"), ("died", "survived"), ("big", "huge"),
        ("not", "definitely"), ("Dodgers", "Yankees"), ("eat", "like"),
        ("fly", "swim"), ("I", "we"), ("bananas", "bread"),
    )

    @pytest.mark.parametrize(("heard", "meant"), HOMOPHONES)
    def test_a_misheard_word_sounds_like_the_real_one(self, heard, meant):
        assert sounds_alike(heard, meant)

    @pytest.mark.parametrize(("heard", "meant"), SWAPS)
    def test_a_different_word_does_not(self, heard, meant):
        assert not sounds_alike(heard, meant)

    def test_real_proposals_that_had_to_be_blocked(self):
        """Seen on the real clips: the model tried both of these."""
        assert not sounds_alike("river", "jungle")
        assert not sounds_alike("pickies", "chiggers")

    def test_rhymes_are_not_homophones(self):
        """High on similarity, different words; the opening sound tells them apart."""
        assert not sounds_alike("pitcher", "catcher")


class TestApplyEdits:
    def test_a_valid_fix_is_applied_in_place(self):
        out, fixes = apply_edits(CLIP, [edit(3, "picture", "pitcher")])
        assert out[3].text == " pitcher"
        assert [(f.original, f.replacement) for f in fixes] == [("picture", "pitcher")]

    def test_timings_are_untouched(self):
        out, _ = apply_edits(CLIP, [edit(3, "picture", "pitcher")])
        assert [(w.start, w.end) for w in out] == [(w.start, w.end) for w in CLIP]

    def test_punctuation_and_capitals_are_carried_over(self):
        text = words("Clap.")
        out, _ = apply_edits(text, [edit(0, "Clap.", "cramp")])
        assert out[0].text == " Cramp."

    def test_the_named_word_must_be_the_word_at_that_position(self):
        """A model that miscounts must not change the wrong word."""
        out, fixes = apply_edits(CLIP, [edit(4, "picture", "pitcher")])
        assert fixes == []
        assert out == CLIP

    def test_an_out_of_range_index_is_ignored(self):
        assert apply_edits(CLIP, [edit(99, "picture", "pitcher")])[1] == []

    def test_a_meaning_change_is_blocked(self):
        _, fixes = apply_edits(CLIP, [edit(18, "bananas.", "apples")])
        assert fixes == []

    def test_a_long_replacement_is_blocked(self):
        _, fixes = apply_edits(CLIP, [edit(3, "picture", "pitch her the ball now")])
        assert fixes == []

    def test_too_many_edits_is_rewriting_and_nothing_is_applied(self):
        many = [edit(i, w.text.strip(), w.text.strip() + "s")
                for i, w in enumerate(CLIP) if len(w.text.strip()) > 3]
        out, fixes = apply_edits(CLIP, many)
        assert fixes == []
        assert out == CLIP

    def test_a_no_op_edit_is_not_reported(self):
        assert apply_edits(CLIP, [edit(3, "picture", "Picture")])[1] == []


def mock(*replies) -> MockBackend:
    return MockBackend(responses=[json.dumps(r) for r in replies])


PROPOSAL = [{"index": 3, "original": "picture", "replacement": "pitcher",
             "reason": "baseball"}]


class TestCorrectWords:
    def test_a_confirmed_fix_is_applied(self):
        backend = mock(PROPOSAL, [{"item": 0, "choice": "B"}])
        out, fixes = correct_words(CLIP, before=[], after=[], backend=backend)
        assert out[3].text == " pitcher"
        assert len(fixes) == 1

    @pytest.mark.parametrize("choice", ["A", "unsure", "b-ish"])
    def test_anything_but_a_clear_yes_keeps_the_original(self, choice):
        """The real case: 'lay bot flies on there' was changed to 'their' by the
        first pass. The second question is what stops it."""
        backend = mock(PROPOSAL, [{"item": 0, "choice": choice}])
        out, fixes = correct_words(CLIP, before=[], after=[], backend=backend)
        assert fixes == []
        assert out[3].text == " picture"

    def test_the_verification_sees_both_versions(self):
        backend = mock(PROPOSAL, [{"item": 0, "choice": "B"}])
        correct_words(CLIP, before=[], after=[], backend=backend)
        verify = backend.calls[1].user
        assert "A: I heard the picture" in verify
        assert "B: I heard the pitcher" in verify

    def test_no_proposals_means_no_second_call(self):
        backend = mock([])
        out, fixes = correct_words(CLIP, before=[], after=[], backend=backend)
        assert (out, fixes) == (CLIP, [])
        assert len(backend.calls) == 1

    def test_blocked_proposals_are_not_sent_for_verification(self):
        backend = mock([{"index": 18, "original": "bananas.", "replacement": "apples",
                         "reason": "x"}])
        correct_words(CLIP, before=[], after=[], backend=backend)
        assert len(backend.calls) == 1

    def test_a_failing_backend_leaves_the_words_alone(self):
        backend = MockBackend(fail_times=99, max_retries=0)
        out, fixes = correct_words(CLIP, before=[], after=[], backend=backend)
        assert (out, fixes) == (CLIP, [])

    def test_the_fallback_answers_when_the_preferred_model_fails(self):
        """Measured: the stronger free models return 503 and 429 often."""
        preferred = MockBackend(fail_times=99, max_retries=0)
        fallback = mock(PROPOSAL, [{"item": 0, "choice": "B"}])
        out, _ = correct_words(CLIP, before=[], after=[],
                               backend=[preferred, fallback])
        assert out[3].text == " pitcher"
        assert len(fallback.calls) == 2

    def test_the_preferred_model_is_asked_first(self):
        preferred = mock(PROPOSAL, [{"item": 0, "choice": "B"}])
        fallback = mock([])
        correct_words(CLIP, before=[], after=[], backend=[preferred, fallback])
        assert len(preferred.calls) == 2
        assert fallback.calls == []

    def test_an_unparseable_reply_leaves_the_words_alone(self):
        backend = MockBackend(responses=["this is not json"])
        out, fixes = correct_words(CLIP, before=[], after=[], backend=backend)
        assert (out, fixes) == (CLIP, [])

    def test_context_is_shown_but_marked_as_not_editable(self):
        backend = mock([])
        correct_words(CLIP, before=words("we were talking about baseball"),
                      after=words("and then"), backend=backend)
        prompt = backend.calls[0].user
        assert "CONTEXT BEFORE (do not flag):\nwe were talking about baseball" in prompt
        assert "[3]picture" in prompt
