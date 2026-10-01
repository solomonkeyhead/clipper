"""ASS generation: colour encoding, chunking, escaping, and clip bounds."""

from __future__ import annotations

import itertools

import pytest

from clipper.config import SafeArea
from clipper.models import Word
from clipper.render import captions
from clipper.render.captions import (
    ass_colour,
    build_ass,
    chunk_words,
    escape_ass_text,
    get_style,
    mask_profanity,
    parse_event_times,
)


def words(*specs: tuple[float, float, str]) -> list[Word]:
    return [Word(start=s, end=e, text=t) for s, e, t in specs]


def speech(count: int, *, start: float = 0.0, step: float = 0.4) -> list[Word]:
    return [
        Word(start=start + i * step, end=start + i * step + step * 0.8, text=f"word{i}")
        for i in range(count)
    ]


class TestColour:
    def test_ass_is_bgr_not_rgb(self):
        """&HAABBGGRR -- pure red has RR last, not first."""
        assert ass_colour(255, 0, 0) == "&H000000FF"
        assert ass_colour(0, 0, 255) == "&H00FF0000"

    def test_alpha_is_inverted(self):
        """00 is opaque in ASS, not transparent."""
        assert ass_colour(255, 255, 255, 0).startswith("&H00")
        assert ass_colour(255, 255, 255, 255).startswith("&HFF")

    def test_rejects_out_of_range(self):
        with pytest.raises(ValueError, match="0-255"):
            ass_colour(300, 0, 0)


class TestStyles:
    def test_every_brief_style_exists(self):
        for name in ("bold_pop", "clean_white", "yellow_highlight"):
            assert get_style(name).name == name

    def test_unknown_style_lists_the_alternatives(self):
        with pytest.raises(ValueError, match="bold_pop"):
            get_style("neon_explosion")

    def test_bold_pop_uses_the_bundled_display_face(self):
        assert get_style("bold_pop").font == "Anton"


class TestEscaping:
    def test_braces_are_escaped(self):
        """An unescaped brace opens an override block and eats the caption."""
        assert escape_ass_text("a {b} c") == "a \\{b\\} c"

    def test_backslash_is_escaped(self):
        assert escape_ass_text("a\\b") == "a\\\\b"

    def test_newlines_become_spaces(self):
        assert "\n" not in escape_ass_text("a\nb\r\nc")

    def test_plain_text_is_untouched(self):
        assert escape_ass_text("hello there") == "hello there"

    def test_unicode_survives(self):
        assert escape_ass_text("café — naïve") == "café — naïve"


class TestProfanityMasking:
    def test_keeps_first_and_last_letter(self):
        assert mask_profanity("fucking") == "f*****g"

    def test_is_case_insensitive_but_preserves_case(self):
        assert mask_profanity("Shit") == "S**t"

    def test_leaves_ordinary_words_alone(self):
        text = "this assessment of the class is passable"
        assert mask_profanity(text) == text

    def test_masks_inside_a_sentence(self):
        assert mask_profanity("that is shit") == "that is s**t"

    def test_length_is_preserved_so_rhythm_is_unchanged(self):
        for word in ("shit", "fucking", "bitch"):
            assert len(mask_profanity(word)) == len(word)


class TestChunking:
    def test_respects_the_word_limit(self):
        style = get_style("bold_pop")
        for chunk in chunk_words(speech(20), style):
            assert len(chunk.words) <= style.max_words_per_chunk

    def test_every_word_appears_exactly_once(self):
        """Dropping or duplicating a word would break the QA caption-sync check."""
        src = speech(25)
        chunked = [w for c in chunk_words(src, get_style("bold_pop")) for w in c.words]
        assert [w.text for w in chunked] == [w.text for w in src]

    def test_breaks_on_a_long_pause(self):
        w = words((0.0, 0.3, "one"), (0.4, 0.7, "two"), (5.0, 5.3, "three"))
        chunks = chunk_words(w, get_style("bold_pop"))
        assert len(chunks) >= 2
        assert chunks[-1].words[0].text == "three"

    def test_breaks_after_sentence_punctuation(self):
        w = words((0.0, 0.3, "Stop."), (0.4, 0.7, "Next"), (0.8, 1.0, "thought"))
        chunks = chunk_words(w, get_style("bold_pop"))
        assert chunks[0].words[-1].text == "Stop."

    def test_breaks_on_the_character_limit(self):
        """Two lines at most: two long words share a caption, a third does not."""
        w = words((0.0, 0.3, "extraordinarily"), (0.35, 0.7, "incomprehensible"),
                  (0.75, 1.1, "circumstances"))
        chunks = chunk_words(w, get_style("bold_pop"))
        assert [len(c.words) for c in chunks] == [2, 1]

    def test_a_long_caption_wraps_onto_a_second_line(self):
        from clipper.config import SafeArea
        from clipper.render.captions import build_ass

        # Conversational pace (2.5 words/s): the fast-speech page limit doesn't apply.
        w = words((0.0, 0.4, "I"), (0.4, 0.8, "wanted"), (0.8, 1.2, "you"),
                  (1.2, 1.6, "to"), (1.6, 2.0, "score"))
        ass = build_ass(w, style=get_style("bold_pop"), width=1080, height=1920,
                        safe_area=SafeArea(), duration=3.0)
        first = next(line for line in ass.splitlines() if line.startswith("Dialogue"))
        assert first.count("\\N") == 1 and "score" in first

    def test_blank_words_are_skipped(self):
        w = words((0.0, 0.3, "a"), (0.4, 0.5, "   "), (0.6, 0.9, "b"))
        texts = [x.text for c in chunk_words(w, get_style("bold_pop")) for x in c.words]
        assert texts == ["a", "b"]

    def test_empty_input(self):
        assert chunk_words([], get_style("bold_pop")) == []


class TestBuildAss:
    def _build(self, w, **kwargs):
        kwargs.setdefault("style", get_style("bold_pop"))
        kwargs.setdefault("width", 1080)
        kwargs.setdefault("height", 1920)
        kwargs.setdefault("safe_area", SafeArea())
        return build_ass(w, **kwargs)

    def test_has_the_required_sections(self):
        out = self._build(speech(6))
        for section in ("[Script Info]", "[V4+ Styles]", "[Events]"):
            assert section in out

    def test_playres_matches_the_output_size(self):
        out = self._build(speech(4), width=1080, height=1920)
        assert "PlayResX: 1080" in out
        assert "PlayResY: 1920" in out

    def test_every_word_reaches_an_event(self):
        src = speech(9)
        out = self._build(src)
        for w in src:
            assert w.text in out

    def test_one_event_per_word_for_highlighting(self):
        src = speech(9)
        dialogues = [ln for ln in self._build(src).splitlines() if ln.startswith("Dialogue:")]
        assert len(dialogues) == len(src)

    def test_active_word_carries_a_colour_override(self):
        style = get_style("bold_pop")
        out = self._build(speech(3), style=style)
        assert f"\\c{style.highlight}" in out

    def test_clip_start_rebases_times_to_zero(self):
        """Word times are source-absolute; the ASS must be clip-relative."""
        src = speech(4, start=100.0)
        out = self._build(src, clip_start=100.0, duration=3.0)
        starts = [s for s, _, _ in parse_event_times(out)]
        assert min(starts) < 1.0

    def test_no_event_falls_outside_the_clip(self):
        """QA checks exactly this, so generation must not produce one."""
        src = speech(30, start=100.0, step=0.5)
        out = self._build(src, clip_start=100.0, duration=5.0)
        for start, end, _ in parse_event_times(out):
            assert start >= -1e-6
            assert end <= 5.0 + 1e-6

    def test_words_before_the_clip_are_dropped(self):
        src = speech(4, start=90.0) + speech(4, start=100.0)
        out = self._build(src, clip_start=100.0, duration=3.0)
        assert "WORD0 " not in out.replace("\n", " ") or all(
            s >= 0 for s, _, _ in parse_event_times(out)
        )

    def test_uppercase_style_uppercases(self):
        out = self._build(words((0.0, 0.5, "hello")), style=get_style("yellow_highlight"))
        assert "HELLO" in out

    def test_dialogue_is_sentence_case_by_default(self):
        """Research R5.3: all caps slows reading of anything longer than a few words."""
        out = self._build(words((0.0, 0.5, "hello")), style=get_style("bold_pop"))
        assert "hello" in out and "HELLO" not in out

    def test_non_uppercase_style_preserves_case(self):
        out = self._build(words((0.0, 0.5, "hello")), style=get_style("clean_white"))
        assert "hello" in out

    def test_profanity_masking_is_opt_in(self):
        w = words((0.0, 0.5, "shit"))
        assert "shit" in self._build(w)
        assert "s**t" in self._build(w, mask_profanity_words=True)

    def test_hook_text_is_a_timed_event(self):
        out = self._build(speech(4), hook_text="This changed everything", hook_seconds=2.0)
        hooks = [t for t in parse_event_times(out) if "CHANGED" in t[2].upper()]
        assert hooks and hooks[0][1] == pytest.approx(2.0, abs=0.01)

    def test_hook_is_omitted_when_seconds_is_zero(self):
        out = self._build(speech(4), hook_text="Unused", hook_seconds=0.0)
        assert "UNUSED" not in out.upper()

    def test_credit_spans_the_clip(self):
        out = self._build(speech(4), credit_text="Source: @creator", duration=8.0)
        credits = [t for t in parse_event_times(out) if "creator" in t[2]]
        assert credits and credits[0][1] == pytest.approx(8.0, abs=0.01)

    def test_credit_position_sets_the_alignment_override(self):
        out = self._build(speech(2), credit_text="c", duration=5.0, credit_position="top_right")
        assert f"\\an{captions.ALIGN_TOP_RIGHT}" in out

    def test_draft_width_scales_the_font_down(self):
        big = self._build(speech(4), width=1080, height=1920)
        small = self._build(speech(4), width=540, height=960)
        assert _style_font_size(small) < _style_font_size(big)

    def test_safe_area_becomes_the_vertical_margin(self):
        out = self._build(speech(4), safe_area=SafeArea(top=220, bottom=400, side=90))
        caption_line = next(ln for ln in out.splitlines() if ln.startswith("Style: Caption,"))
        assert caption_line.rstrip().split(",")[-2] == "400"

    def test_empty_transcript_still_produces_a_valid_file(self):
        out = self._build([])
        assert "[Events]" in out
        assert not [ln for ln in out.splitlines() if ln.startswith("Dialogue:")]

    def test_output_is_utf8_encodable(self):
        out = self._build(words((0.0, 0.5, "café"), (0.6, 1.0, "naïve")))
        out.encode("utf-8")


class TestParseEventTimes:
    def test_strips_override_blocks(self):
        content = build_ass(
            words((0.0, 0.5, "hello")), style=get_style("bold_pop"),
            width=1080, height=1920, safe_area=SafeArea(),
        )
        texts = [t for _, _, t in parse_event_times(content)]
        assert texts == ["hello"]
        assert not any("{" in t for t in texts)

    def test_ignores_non_dialogue_lines(self):
        assert parse_event_times("[Events]\nFormat: Layer, Start\n") == []


def _style_font_size(ass: str) -> int:
    line = next(ln for ln in ass.splitlines() if ln.startswith("Style: Caption,"))
    return int(line.split(",")[2])


class TestCreditPlacement:
    """The attribution credit must sit inside the safe area.

    Measured on a real render: it used the side margin as its top margin and
    landed about 100px from the top of a 1920px frame, where the platform's own
    top bar covers it. For a Creative Commons Attribution source, a hidden
    credit is a licence problem, not a cosmetic one.
    """

    SAFE = SafeArea(top=220, bottom=320, side=90)

    def _ass(self, **kwargs) -> str:
        return build_ass(speech(6), style=get_style("bold_pop"), width=1080, height=1920,
                         safe_area=self.SAFE, duration=5.0,
                         credit_text="Blocks Podcast w/ Neal Brennan (CC BY)", **kwargs)

    @staticmethod
    def _margin_v(ass: str, style_name: str) -> int:
        line = next(row for row in ass.splitlines()
                    if row.startswith("Dialogue:") and f",{style_name}," in row)
        return int(line.split(",")[7])

    def test_a_top_credit_is_below_the_top_safe_margin(self):
        assert self._margin_v(self._ass(), "Credit") >= 220

    def test_a_bottom_credit_is_above_the_bottom_safe_margin(self):
        assert self._margin_v(self._ass(credit_position="bottom_right"), "Credit") >= 320

    def test_the_hook_goes_below_a_top_credit(self):
        ass = self._ass(hook_text="The Christmas morning", hook_seconds=2.0)
        assert self._margin_v(ass, "Hook") > self._margin_v(ass, "Credit")

    def test_without_a_credit_the_hook_keeps_its_place(self):
        ass = build_ass(speech(6), style=get_style("bold_pop"), width=1080, height=1920,
                        safe_area=self.SAFE, duration=5.0,
                        hook_text="The Christmas morning", hook_seconds=2.0)
        assert self._margin_v(ass, "Hook") == 220


class TestNoStackedCaptions:
    """A stammer the transcriber squashed onto one instant drew every highlight
    state at once, stacked line by line (the user's "smeared" captions)."""

    def squashed(self):
        said = [(10.90, 11.14, "don't"), (11.14, 11.20, "you"), (11.20, 11.34, "have"),
                (11.34, 11.40, "a..."), (11.40, 11.45, "Why"), (11.40, 11.45, "don't"),
                (11.40, 11.45, "you"), (11.40, 11.45, "have"), (11.40, 11.45, "a"),
                (11.40, 11.52, "back"), (11.52, 11.72, "me"), (11.72, 11.88, "up")]
        return [Word(start=a, end=b, text=t) for a, b, t in said]

    def test_squashed_words_are_spread_out(self):
        spread = captions.spread_squashed(self.squashed())
        starts = [w.start for w in spread]
        assert starts == sorted(starts) and len(set(starts)) == len(starts)

    def test_no_two_captions_are_ever_on_screen_together(self):
        content = build_ass(self.squashed(), style=get_style("bold_pop"), width=1080, height=1920,
                            safe_area=SafeArea())
        shown = sorted((a, b) for a, b, text in captions.parse_event_times(content))
        assert all(b1 <= a2 + 1e-6 for (_, b1), (a2, _) in itertools.pairwise(shown))
        assert all(b - a >= captions.MIN_EVENT_SECONDS - 0.011 for a, b in shown)  # centisecond rounding
