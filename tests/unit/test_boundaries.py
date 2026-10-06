"""Clip boundary refinement (docs/BUILD_BRIEF.md section 10).

The invariant that matters most: padding and trimming must never cut into a
word. A clipped syllable is instantly audible.
"""

from __future__ import annotations

import pytest

from clipper.candidates.boundaries import refine
from clipper.config import RefineConfig
from clipper.models import Transcript, Word
from clipper.transcribe.segment import segment

CFG = RefineConfig()


def build(spec: list[tuple[str, float]], *, start: float = 0.0):
    """Build (transcript, sentences) from (text, gap_after_sentence) pairs."""
    specs: list[tuple[float, float, str]] = []
    t = start
    for text, gap in spec:
        for token in text.split():
            specs.append((t, t + 0.3, token))
            t += 0.3
        t += gap
    transcript = Transcript(
        source_id="s", language="en", duration=t,
        words=[Word(start=s, end=e, text=x) for s, e, x in specs],
    )
    return transcript, segment(transcript).sentences


def do_refine(transcript, sentences, start, end, **kwargs):
    defaults = dict(
        transcript=transcript, sentences=sentences, cfg=CFG,
        min_duration=1.0, max_duration=600.0, source_duration=transcript.duration,
    )
    defaults.update(kwargs)
    return refine(start, end, **defaults)


SCRIPT = [
    ("This is the opening sentence here.", 0.9),
    ("So um the real point starts here now.", 0.9),
    ("And it carries on for a while longer.", 0.9),
    ("Then it finally reaches a conclusion.", 0.9),
    ("A trailing sentence follows it.", 0.9),
]


class TestSnapping:
    def test_start_snaps_to_a_sentence_start(self):
        """Uses a filler-free sentence so only snapping can move the start."""
        transcript, sentences = build(SCRIPT)
        target = sentences[2]  # "And it carries on..." -- no leading filler run
        result = do_refine(transcript, sentences, target.start + 0.7, sentences[4].end,
                           cfg=RefineConfig(filler_window=0.0, pre_roll=0.0, post_roll=0.0))
        assert result.start == pytest.approx(target.start)

    def test_end_snaps_to_a_sentence_end(self):
        transcript, sentences = build(SCRIPT)
        result = do_refine(transcript, sentences, sentences[1].start, sentences[3].end - 0.6)
        assert any(abs(result.end - s.end) < CFG.post_roll + 1e-6 for s in sentences)

    def test_a_punctuated_end_is_preferred_nearby(self):
        transcript, sentences = build([
            ("First thought complete here.", 0.9),
            ("Second thought trails off", 1.5),
            ("Third thought is complete.", 0.9),
        ])
        result = do_refine(transcript, sentences, sentences[0].start, sentences[1].end)
        punctuated = [s.end for s in sentences if s.ends_with_terminal_punctuation]
        assert any(abs(result.end - e) < CFG.post_roll + 1e-6 for e in punctuated)

    def test_end_never_snaps_backwards_past_a_whole_sentence(self):
        """Regression: a nearby earlier full stop must not shrink the clip.

        With a long unpunctuated tail, the closest punctuated end can be
        *behind* the requested end. Preferring it dropped an entire sentence and
        cut a 2.7s window down to 0.95s.
        """
        transcript, sentences = build([
            ("Opening here.", 0.9),
            ("This trails off without", 0.9),
            ("x " * 60 + "finally done.", 0.9),
        ])
        result = do_refine(transcript, sentences, sentences[0].start, sentences[1].end)
        assert result.end >= sentences[1].end - 1e-6, (
            "end snapped backwards and discarded a sentence"
        )

    def test_notes_record_what_moved(self):
        transcript, sentences = build(SCRIPT)
        result = do_refine(transcript, sentences, sentences[1].start + 0.9, sentences[3].end)
        assert result.notes
        assert any("snap" in n for n in result.notes)

    def test_no_sentences_means_no_snapping(self):
        """Other rules still apply -- only the snap step is skipped."""
        transcript, _ = build(SCRIPT)
        result = do_refine(transcript, [], 5.0, 20.0,
                           cfg=RefineConfig(filler_window=0.0, pre_roll=0.0, post_roll=0.0))
        assert result.start == pytest.approx(5.0)
        assert result.end == pytest.approx(20.0)
        assert not any("snap" in n for n in result.notes)


class TestFillerTrimming:
    def test_leading_filler_is_trimmed(self):
        """'So, um,' at the front wastes the seconds that decide the watch."""
        transcript, sentences = build(SCRIPT)
        target = sentences[1]  # "So um the real point starts here now."
        result = do_refine(transcript, sentences, target.start, sentences[3].end)
        assert result.start > target.start
        assert any("filler" in n for n in result.notes)

    def test_a_clean_opening_is_untouched(self):
        transcript, sentences = build(SCRIPT)
        target = sentences[0]  # "This is the opening sentence here."
        result = do_refine(transcript, sentences, target.start, sentences[2].end)
        assert not any("filler" in n for n in result.notes)

    def test_trimming_lands_on_a_word_start(self):
        transcript, sentences = build(SCRIPT)
        target = sentences[1]
        result = do_refine(transcript, sentences, target.start, sentences[3].end)
        starts = [w.start for w in transcript.words]
        assert any(abs(result.start - s) <= CFG.pre_roll + 1e-6 for s in starts)

    def test_an_all_filler_sentence_is_not_trimmed_to_nothing(self):
        transcript, sentences = build([("So um like you know.", 0.9),
                                       ("A real sentence here.", 0.9)])
        result = do_refine(transcript, sentences, sentences[0].start, sentences[1].end)
        assert result.start < result.end

    def test_trimming_is_disabled_by_a_zero_window(self):
        transcript, sentences = build(SCRIPT)
        target = sentences[1]
        cfg = RefineConfig(filler_window=0.0, pre_roll=0.0, post_roll=0.0)
        result = do_refine(transcript, sentences, target.start, sentences[3].end, cfg=cfg)
        assert result.start == pytest.approx(target.start)


class TestPadding:
    def test_padding_never_cuts_into_a_preceding_word(self):
        transcript, sentences = build(SCRIPT)
        for i in range(1, len(sentences)):
            result = do_refine(transcript, sentences, sentences[i].start, sentences[-1].end)
            before = [w for w in transcript.words if w.end <= sentences[i].start + 1e-9]
            if before:
                assert result.start >= before[-1].end - 1e-6

    def test_padding_never_cuts_into_a_following_word(self):
        transcript, sentences = build(SCRIPT)
        result = do_refine(transcript, sentences, sentences[0].start, sentences[1].end)
        after = [w for w in transcript.words if w.start >= sentences[1].end - 1e-9]
        if after:
            assert result.end <= after[0].start + 1e-6

    def test_padding_is_capped_by_the_config(self):
        transcript, sentences = build(SCRIPT, start=30.0)
        result = do_refine(transcript, sentences, sentences[1].start, sentences[3].end)
        assert result.start >= sentences[1].start - CFG.pre_roll - 1e-6

    def test_start_never_goes_negative(self):
        transcript, sentences = build(SCRIPT, start=0.0)
        result = do_refine(transcript, sentences, sentences[0].start, sentences[2].end)
        assert result.start >= 0.0

    def test_end_never_passes_the_source(self):
        transcript, sentences = build(SCRIPT)
        result = do_refine(transcript, sentences, sentences[0].start, sentences[-1].end,
                           source_duration=transcript.duration)
        assert result.end <= transcript.duration + 1e-6


class TestExtendToFinishThought:
    def test_extends_past_an_unfinished_ending(self):
        transcript, sentences = build([
            ("Opening sentence goes here.", 0.9),
            ("This one trails off without", 0.9),
            ("and then it concludes properly.", 0.9),
        ])
        assert len(sentences) == 3, "fixture must produce an unpunctuated middle sentence"
        assert not sentences[1].ends_with_terminal_punctuation
        result = do_refine(transcript, sentences, sentences[0].start, sentences[1].end)
        assert result.end >= sentences[2].end - 1e-6
        # Either snapping forward or explicit extension may achieve this; both
        # are correct, so assert the outcome rather than the mechanism.
        assert any(("finish" in n) or ("punctuation" in n) for n in result.notes)

    def test_does_not_extend_when_already_complete(self):
        transcript, sentences = build(SCRIPT)
        result = do_refine(transcript, sentences, sentences[0].start, sentences[1].end)
        assert not any("extended" in n for n in result.notes)

    def test_respects_the_extension_limit(self):
        transcript, sentences = build([
            ("Opening here.", 0.9),
            ("This trails off without", 0.9),
            ("x " * 60 + "finally done.", 0.9),
        ])
        cfg = RefineConfig(max_extend_seconds=2.0)
        result = do_refine(transcript, sentences, sentences[0].start, sentences[1].end, cfg=cfg)
        assert any("over the" in n for n in result.notes)

    def test_respects_the_campaign_maximum(self):
        transcript, sentences = build([
            ("Opening here.", 0.9),
            ("This trails off without", 0.9),
            ("and then it concludes properly.", 0.9),
        ])
        result = do_refine(transcript, sentences, sentences[0].start, sentences[1].end,
                           max_duration=4.0)
        assert any("campaign maximum" in n for n in result.notes) or result.dropped


class TestDurationEnforcement:
    def test_too_short_is_dropped(self):
        transcript, sentences = build(SCRIPT)
        result = do_refine(transcript, sentences, sentences[0].start, sentences[0].end,
                           min_duration=60.0)
        assert result.dropped
        assert "under the campaign minimum" in result.drop_reason

    def test_too_long_is_dropped(self):
        transcript, sentences = build(SCRIPT)
        result = do_refine(transcript, sentences, sentences[0].start, sentences[-1].end,
                           max_duration=2.0)
        assert result.dropped
        assert "over the campaign maximum" in result.drop_reason

    def test_in_range_is_kept(self):
        transcript, sentences = build(SCRIPT)
        result = do_refine(transcript, sentences, sentences[0].start, sentences[-1].end,
                           min_duration=1.0, max_duration=600.0)
        assert not result.dropped


class TestOrdering:
    def test_bounds_stay_ordered(self):
        transcript, sentences = build(SCRIPT)
        for i in range(len(sentences)):
            for j in range(i + 1, len(sentences)):
                result = do_refine(transcript, sentences, sentences[i].start, sentences[j].end)
                assert result.start < result.end, (i, j)

    def test_filler_is_trimmed_before_padding_is_measured(self):
        """Padding from the filler's start would be added and then thrown away."""
        transcript, sentences = build(SCRIPT)
        target = sentences[1]
        result = do_refine(transcript, sentences, target.start, sentences[3].end)
        order = [n.split()[0] for n in result.notes]
        if "trimmed" in order and "padded" in order:
            assert order.index("trimmed") < order.index("padded")
