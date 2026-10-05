"""Campaigns that don't pay per view, don't take links, or aren't in English (D147)."""

from __future__ import annotations

from clipper import runner
from clipper.campaign import description
from clipper.config import CampaignConfig, Config
from clipper.llm.prompts import PROMPT_A, with_language
from clipper.studio import stats


def campaign(**kw) -> CampaignConfig:
    return CampaignConfig(name="c", source_authorization="authorized", **kw)


def test_each_pay_model_estimates_its_own_way():
    per_view = campaign(reward_per_1k_usd=2.0, min_payout_usd=1.0)
    assert stats.post_earnings(1000, per_view) == 2.0 and stats.post_earnings(100, per_view) == 0.0
    flat = campaign(pay_model="per_clip", flat_fee_usd=5.0)
    assert stats.post_earnings(0, flat) == 5.0 and stats.post_earnings(None, flat) == 5.0
    assert stats.post_earnings(9999, campaign(pay_model="none", reward_per_1k_usd=2.0)) is None
    assert stats.post_earnings(9999, campaign(own_channel=True, reward_per_1k_usd=2.0)) is None   # your own channel
    assert stats.post_earnings(100, None) is None


def test_which_campaigns_take_links():
    assert campaign().submits and not campaign(submit_links=False).submits and not campaign(own_channel=True).submits


def test_english_is_left_as_it_was_and_another_language_is_asked_for():
    assert with_language(PROMPT_A, "en") is PROMPT_A and with_language(PROMPT_A, "auto") is PROMPT_A
    es = with_language(PROMPT_A, "es")
    assert "Spanish" in es.system and es.key != PROMPT_A.key
    assert description._language_note(campaign()) == ""
    assert "German" in description._language_note(campaign(language="de"))


def test_a_spanish_campaign_transcribes_as_spanish_and_skips_english_caption_fixes():
    base = Config()
    spanish = runner.campaign_config(base, campaign(language="es"))
    assert spanish.transcription.language == "es" and spanish.llm.language == "es" and not spanish.llm.correct_captions
    english = runner.campaign_config(base, campaign())
    assert english.transcription.language == base.transcription.language and english.llm.correct_captions


def test_platforms_come_from_one_registry_and_the_page_has_the_same_list():
    from pathlib import Path

    from clipper import platforms
    from clipper.config import PLATFORMS
    from clipper.studio import posts

    assert PLATFORMS == platforms.TARGETS and "facebook_reels" in PLATFORMS
    assert posts.platform_of("https://fb.watch/abc") == "facebook" and posts.platform_of("https://youtu.be/x") == "youtube"
    generated = Path(__file__).parents[2] / "src" / "clipper" / "studio" / "web" / "src" / "api" / "platforms.gen.ts"
    assert generated.read_text(encoding="utf-8") == platforms.typescript()   # run `npm run gen:api` after changing it
