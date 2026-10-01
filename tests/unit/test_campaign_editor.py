"""Creating and editing campaigns from the Control Center."""

from __future__ import annotations

import json

import pytest
import yaml

from clipper.campaign import editor
from clipper.campaign.editor import BriefFields, CampaignError, CampaignForm
from clipper.config import CampaignConfig


def test_slug_and_pretty_title():
    assert editor.slugify("Chad Powers S2 (Hulu)!") == "chad-powers-s2-hulu"
    assert editor.pretty("chad-powers-s2") == "Chad Powers S2"


class TestSave:
    def test_a_new_campaign_is_written_and_loads(self, tmp_path):
        form = CampaignForm(title="Chad Powers S2", marketplace="Content Rewards",
                            reward_per_1k_usd=2.5, required_hashtags=["chadpowers", "#hulu #tv"],
                            fallback_captions=["one", " ", "two"], notes="line one\nline two")
        campaign = editor.create(tmp_path, form)
        assert campaign.name == "chad-powers-s2" and campaign.title == "Chad Powers S2"
        loaded = CampaignConfig.load(tmp_path / "chad-powers-s2.yaml")
        assert loaded.required_hashtags == ("#chadpowers", "#hulu", "#tv")
        assert loaded.fallback_captions == ("one", "two")
        assert "Content Rewards" in loaded.source_authorization  # filled in for them
        assert loaded.notes == "line one\nline two"
        assert loaded.scripted is True

    def test_a_taken_name_is_refused(self, tmp_path):
        editor.create(tmp_path, CampaignForm(title="Same"))
        with pytest.raises(CampaignError, match="already"):
            editor.create(tmp_path, CampaignForm(title="same!"))

    def test_bad_input_says_what_to_fix(self, tmp_path):
        with pytest.raises(CampaignError, match="name"):
            editor.create(tmp_path, CampaignForm(title="  "))
        with pytest.raises(CampaignError, match="platform"):
            editor.create(tmp_path, CampaignForm(title="x", platform_targets=[]))
        with pytest.raises(CampaignError, match="source_authorization"):
            editor.create(tmp_path, CampaignForm(title="x", source_authorization="tbd"))

    def test_editing_renames_without_moving_and_keeps_other_keys(self, tmp_path, valid_campaign_dict):
        path = tmp_path / "test-campaign.yaml"
        path.write_text(yaml.safe_dump({**valid_campaign_dict, "forbidden_terms": ["spoiler"]}),
                        encoding="utf-8")
        form = editor.to_form(CampaignConfig.load(path))
        assert form.title == "Test Campaign"
        editor.update(tmp_path, "test-campaign", form.model_copy(update={"title": "My Show"}))
        loaded = CampaignConfig.load(path)
        assert loaded.name == "test-campaign" and loaded.title == "My Show"
        assert loaded.forbidden_terms == ("spoiler",)
        assert len(list((tmp_path / ".history").glob("test-campaign.*.yaml"))) == 1


def test_a_brief_fills_the_form_without_private_links():
    found = BriefFields(title="Chad Powers S2", reward_per_1k_usd=2.5, platforms=["tiktok", "x", "snapchat"],
                        campaign_url="https://drive.google.com/drive/folders/abc",
                        required_hashtags=["chadpowers"], approved_captions=["a caption"],
                        min_seconds=60, max_seconds=7, deadline="next friday",
                        other_rules=["Tier 1 audience", "- keep posts up 30 days"])
    form = editor.form_from_brief(found)
    assert form.platform_targets == ["tiktok", "x"] and form.campaign_url == ""
    assert form.required_hashtags == ["#chadpowers"] and form.fixed_captions
    assert (form.min_seconds, form.max_seconds) == (7, 60) and form.deadline == ""
    assert form.notes == "- Tier 1 audience\n- keep posts up 30 days"


def test_read_brief_uses_the_model(monkeypatch):
    from clipper.llm.base import LLMResponse

    class Fake:
        def complete(self, request):
            assert "Content Rewards" in request.system
            return LLMResponse(text=json.dumps({"title": "Show", "reward_per_1k_usd": 1.5}),
                               model="fake")

    form = editor.read_brief("x" * 50, Fake())
    assert form.title == "Show" and form.reward_per_1k_usd == 1.5
