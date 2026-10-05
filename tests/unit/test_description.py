"""Searchable descriptions under the caption line (campaigns with long_description)."""

from __future__ import annotations

import json
from pathlib import Path

from clipper.campaign.compliance import full_caption
from clipper.campaign.description import clean, describe
from clipper.config import CampaignConfig
from clipper.llm.base import create
from clipper.models import ClipPlan
from clipper.studio.library import split_caption, with_description

CHAD = CampaignConfig.load(Path(__file__).parents[1] / "fixtures" / "chad-powers-s2.yaml")
GOOD = ("On Chad Powers season 2, Ricky confronts Russ on the bench before the last game, and "
        "he admits he stayed for her. A quiet Ricky and Russ moment from Hulu's football comedy.")


def test_the_caption_line_stays_first_with_the_disclosure():
    plan = ClipPlan(clip_id="1", candidate_id="c", rank=1, start=0, end=30, text="",
                    suggested_caption="the chemistry??? hello??? @chadpowershulu #ad",
                    description=GOOD, hashtags=["#chadpowers", "#hulu"])
    line, desc, tags = full_caption(plan).split("\n\n")
    assert line.endswith("#ad") and desc == GOOD and tags == "#chadpowers #hulu"


def test_without_a_description_the_caption_is_unchanged():
    plan = ClipPlan(clip_id="1", candidate_id="c", rank=1, start=0, end=30, text="",
                    suggested_caption="line #ad", hashtags=["#x"])
    assert full_caption(plan) == "line #ad  #x"


class TestClean:
    def test_tags_and_mentions_are_stripped(self):
        assert "#" not in clean(GOOD + " #fyp @hulu") and "@" not in clean(GOOD + " @hulu")

    def test_too_short_or_too_long_is_refused(self):
        assert clean("Ricky and Russ.") is None
        assert clean(GOOD * 4) is None


def test_describe_uses_the_model_and_falls_back_to_nothing():
    good = create("mock", responses=[json.dumps({"description": GOOD})])
    assert describe("You can't be incriminated.", CHAD, "line #ad", [good]) == GOOD
    bad = create("mock", responses=[json.dumps({"description": "Too short."})])
    assert describe("You can't be incriminated.", CHAD, "line #ad", [bad]) == ""


def test_stored_captions_are_split_and_rebuilt():
    stored = "oh they knew EXACTLY #chadpowers @chadpowershulu #ad  #hulu #tvedits"
    assert split_caption(stored) == ("oh they knew EXACTLY #chadpowers @chadpowershulu #ad",
                                     "#hulu #tvedits")
    rebuilt = with_description(stored, GOOD)
    assert rebuilt.split("\n\n") == ["oh they knew EXACTLY #chadpowers @chadpowershulu #ad",
                                     GOOD, "#hulu #tvedits"]
    assert with_description(rebuilt, "New text that is long enough to count.") .split("\n\n")[1] \
        == "New text that is long enough to count."
