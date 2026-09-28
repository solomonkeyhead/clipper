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


def test_focus_is_not_a_reason_to_call_a_clip_an_ad():
    variant = with_focus(PROMPT_A, "Clips should sell the romance in the show.")
    assert "never a reason to mark a clip is_sponsor_or_ad" in " ".join(variant.system.split())
