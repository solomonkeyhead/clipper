"""`clipper cut`: hand-picked ranges, for moments a brief names that selection can't see."""

from __future__ import annotations

from pathlib import Path

import pytest

from clipper.config import CampaignConfig, Config
from clipper.llm.prompts import PROMPT_A, with_focus
from clipper.models import Word
from clipper.runner import campaign_config, manual_plan, parse_range

CHAD_POWERS = Path(__file__).parents[2] / "campaigns" / "chad-powers-s2.yaml"


class TestParseRange:
    @pytest.mark.parametrize("text, expected", [
        ("24:45-26:05", (1485.0, 1565.0)),
        ("1:02:03-1:02:40", (3723.0, 3760.0)),
        ("1485-1560.5", (1485.0, 1560.5)),
        (" 0:30 - 0:45 ", (30.0, 45.0)),
    ])
    def test_formats(self, text, expected):
        assert parse_range(text) == expected

    @pytest.mark.parametrize("text", ["24:45", "26:05-24:45", "a-b", "1-2-3"])
    def test_rejects(self, text):
        with pytest.raises(ValueError):
            parse_range(text)


class TestManualPlan:
    def test_cut_as_given_with_the_campaigns_hooks_and_captions(self):
        campaign = CampaignConfig.load(CHAD_POWERS)
        config = campaign_config(Config(), campaign)
        # A long wordless stretch after the last line: the Ep 4 field scene.
        words = [Word(start=10.3, end=10.8, text="Tell"), Word(start=10.8, end=11.0, text="me.")]
        first = manual_plan(10.0, 50.0, words, config=config, campaign=campaign,
                            rank=1, attempt=1)
        second = manual_plan(60.0, 80.0, words, config=config, campaign=campaign,
                             rank=2, attempt=2)

        assert (first.start, first.end) == (10.0, 50.0)  # no silence trim
        assert first.text == "Tell me."
        assert first.lead_in == 0.3
        assert first.hook_text == campaign.hook_texts[0]
        assert second.hook_text == campaign.hook_texts[1]
        assert first.suggested_caption == campaign.fallback_captions[0] + " #ad"
        assert second.text == "" and second.lead_in is None


class TestRangeTranscript:
    """Ep 6's field scene: the episode transcript had 7 of about 40 words; the
    same range transcribed alone had them all."""

    def w(self, t, text="x"):
        return Word(start=t, end=t + 0.2, text=text)

    def test_a_fuller_range_transcript_replaces_the_episodes(self):
        from clipper.runner import with_range_transcript

        episode = [self.w(1), self.w(12, "score."), self.w(15, "out."), self.w(30)]
        heard = [self.w(10 + i * 0.5, f"w{i}") for i in range(10)]
        merged = with_range_transcript(episode, 10, 20, heard)
        assert [w.text for w in merged] == ["x", *[f"w{i}" for i in range(10)], "x"]

    def test_a_thinner_one_is_not_trusted(self):
        from clipper.runner import with_range_transcript

        episode = [self.w(11), self.w(12), self.w(13)]
        assert with_range_transcript(episode, 10, 20, [self.w(11)]) is episode
        assert with_range_transcript(episode, 10, 20, None) is episode


class TestCampaignLinks:
    def test_only_this_campaigns_posted_clips_oldest_first(self):
        from clipper.learn.log import campaign_links

        rows = [
            {"campaign": "chad-powers-s2", "caption": "b",
             "url": "https://t/2?utm_campaign=tt4d_open_api&utm_source=abc",
             "posted_at": "2026-09-29 10:00"},
            {"campaign": "chad-powers-s2", "caption": "a", "url": "https://t/1",
             "posted_at": "2026-09-28 18:00"},
            {"campaign": "chad-powers-s2", "caption": "unposted", "url": ""},
            {"campaign": "fx-adults-s2", "caption": "c", "url": "https://t/3",
             "posted_at": "2026-09-22 12:00"},
        ]
        assert [u for _, _, u in campaign_links(rows, "chad-powers-s2")] == [
            "https://t/1", "https://t/2"]
        assert [u for _, _, u in campaign_links(rows, "chad-powers-s2",
                                                since="2026-09-29")] == ["https://t/2"]


class TestCaptionHold:
    """Captions left the screen the instant their last word ended: too short to
    read in slow scripted dialogue."""

    def chunk(self, start, end):
        from clipper.render.captions import Chunk

        return Chunk(words=[Word(start=start, end=(start + end) / 2, text="I"),
                            Word(start=(start + end) / 2, end=end, text="promise")])

    def test_held_for_twice_as_long_as_it_took_to_say(self):
        from clipper.render.captions import caption_hold

        assert caption_hold(self.chunk(10.0, 11.0), None) == pytest.approx(12.0)

    def test_a_quick_caption_stays_up_at_least_1_2_seconds(self):
        from clipper.render.captions import caption_hold

        assert caption_hold(self.chunk(10.0, 10.3), None) == pytest.approx(11.2)

    def test_never_over_the_next_caption_or_past_the_end(self):
        from clipper.render.captions import caption_hold

        assert caption_hold(self.chunk(10.0, 11.0), 11.4) == pytest.approx(11.4)
        assert caption_hold(self.chunk(10.0, 11.0), 10.5) == pytest.approx(11.0)

    def test_the_ass_holds_the_last_word_into_the_pause(self):
        from clipper.config import SafeArea
        from clipper.render.captions import build_ass, get_style

        words = [Word(start=1.0, end=1.3, text="I"), Word(start=1.3, end=1.6, text="promise."),
                 Word(start=5.0, end=5.4, text="Okay.")]
        ass = build_ass(words, style=get_style("bold_pop"), width=1080, height=1920,
                        safe_area=SafeArea(), duration=8.0)
        lines = [line for line in ass.splitlines() if line.startswith("Dialogue")]
        assert "0:00:02.20" in lines[1]  # 1.0 + max(2 x 0.6, 1.2)


def test_focus_is_not_a_reason_to_call_a_clip_an_ad():
    variant = with_focus(PROMPT_A, "Clips should sell the romance in the show.")
    assert "never a reason to mark a clip is_sponsor_or_ad" in " ".join(variant.system.split())
