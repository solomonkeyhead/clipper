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
    names_in,
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


class Ears:
    """A stand-in for the audio recheck: says yes to the replacements given."""

    def __init__(self, *heard: str):
        self.heard = set(heard)
        self.asked: list[tuple[str, str]] = []

    def said(self, word, replacement, context):
        self.asked.append((word.text.strip(), replacement))
        return replacement in self.heard


PROPOSAL = [{"index": 3, "original": "picture", "replacement": "pitcher",
             "reason": "baseball"}]


def run(backend, ears, **kwargs):
    return correct_words(CLIP, before=[], after=[], backend=backend, recheck=ears, **kwargs)


class TestCorrectWords:
    def test_a_fix_the_audio_confirms_is_applied(self):
        out, fixes = run(mock(PROPOSAL), Ears("pitcher"))
        assert out[3].text == " pitcher"
        assert len(fixes) == 1

    def test_a_fix_the_audio_does_not_confirm_is_not_applied(self):
        """The real case: "from the bot" was changed to "bite" -- true, but not
        said. Every text-only check let it through in some runs; Whisper,
        nudged toward "bite", still heard "bot"."""
        out, fixes = run(mock(PROPOSAL), Ears())
        assert fixes == []
        assert out[3].text == " picture"

    def test_without_audio_nothing_is_applied(self):
        backend = mock(PROPOSAL)
        out, fixes = correct_words(CLIP, before=[], after=[], backend=backend, recheck=None)
        assert (out, fixes) == (CLIP, [])
        assert backend.calls == [], "no point asking when nothing can be verified"

    def test_a_name_is_never_replaced(self):
        """"Issa" (a character) was "fixed" to "It's a"; the audio check passed
        it because the two sound the same."""
        episode = words("Thank you, Issa. Wait, Issa, did you read this? Issa. Issa.")
        names = names_in(episode)
        assert "issa" in names
        clip = words("Issa. Issa. It's so simple.")
        proposal = [{"index": 0, "original": "Issa.", "replacement": "It's a.", "reason": "x"}]
        ears = Ears("It's a.")
        _, fixes = correct_words(clip, before=[], after=[], backend=mock(proposal),
                                 recheck=ears, names=names)
        assert fixes == [] and ears.asked == []

    def test_words_capitalised_only_at_sentence_starts_are_not_names(self):
        assert "cup" not in names_in(words("Cup? Cup? No. Cup. What cup?"))

    def test_a_rejected_fix_is_never_applied_or_checked(self):
        ears = Ears("pitcher")
        _, fixes = run(mock(PROPOSAL), ears,
                       rejected=frozenset({("picture", "pitcher")}))
        assert fixes == []
        assert ears.asked == []

    def test_rejection_ignores_case_and_punctuation(self):
        text = words("They get it from the Bot.")
        proposal = [{"index": 5, "original": "Bot.", "replacement": "bite.", "reason": "x"}]
        _, fixes = correct_words(text, before=[], after=[], backend=mock(proposal),
                                 recheck=Ears("bite."),
                                 rejected=frozenset({("bot", "bite")}))
        assert fixes == []

    def test_only_proposals_passing_the_code_checks_reach_the_audio(self):
        ears = Ears("apples", "pitcher")
        proposals = [*PROPOSAL, {"index": 18, "original": "bananas.",
                                 "replacement": "apples", "reason": "x"}]
        _, fixes = run(mock(proposals), ears)
        assert ears.asked == [("picture", "pitcher")]
        assert [f.replacement for f in fixes] == ["pitcher"]

    def test_no_proposals_means_no_audio_check(self):
        ears = Ears()
        out, fixes = run(mock([]), ears)
        assert (out, fixes) == (CLIP, [])
        assert ears.asked == []

    def test_a_failing_backend_leaves_the_words_alone(self):
        out, fixes = run(MockBackend(fail_times=99, max_retries=0), Ears("pitcher"))
        assert (out, fixes) == (CLIP, [])

    def test_the_fallback_proposes_when_the_preferred_model_fails(self):
        """Measured: the stronger free model returned 503, 504 and 429."""
        preferred = MockBackend(fail_times=99, max_retries=0)
        out, _ = run([preferred, mock(PROPOSAL)], Ears("pitcher"))
        assert out[3].text == " pitcher"

    def test_the_preferred_model_is_asked_first(self):
        preferred, fallback = mock(PROPOSAL), mock([])
        run([preferred, fallback], Ears("pitcher"))
        assert len(preferred.calls) == 1
        assert fallback.calls == []

    def test_an_unparseable_reply_leaves_the_words_alone(self):
        out, fixes = run(MockBackend(responses=["this is not json"]), Ears("pitcher"))
        assert (out, fixes) == (CLIP, [])

    def test_context_is_shown_but_marked_as_not_editable(self):
        backend = mock([])
        correct_words(CLIP, before=words("we were talking about baseball"),
                      after=words("and then"), backend=backend, recheck=Ears())
        prompt = backend.calls[0].user
        assert "CONTEXT BEFORE (do not flag):\nwe were talking about baseball" in prompt
        assert "[3]picture" in prompt


class TestRejectedConfig:
    def test_entries_are_parsed_and_normalised(self):
        from clipper.config import LLMConfig

        cfg = LLMConfig(rejected_caption_fixes=["Bot. -> bite", " pickies ->piques "])
        assert cfg.rejected_fix_pairs == {("bot", "bite"), ("pickies", "piques")}

    def test_a_malformed_entry_is_refused(self):
        from pydantic import ValidationError

        from clipper.config import LLMConfig

        with pytest.raises(ValidationError, match="heard -> replacement"):
            LLMConfig(rejected_caption_fixes=["bot bite"])

    def test_the_default_config_rejects_the_reported_fixes(self):
        from clipper.config import Config

        pairs = Config.load().llm.rejected_fix_pairs
        assert ("bot", "bite") in pairs
        assert ("pickies", "piques") in pairs


class TestNoOpProposals:
    def test_a_same_word_proposal_never_reaches_the_audio(self):
        """Seen on a real clip: "smokes" -> "smokes" cost a Whisper re-listen."""
        ears = Ears("picture")
        proposal = [{"index": 3, "original": "picture", "replacement": "Picture",
                     "reason": "x"}]
        _, fixes = run(mock(proposal), ears)
        assert fixes == []
        assert ears.asked == []
