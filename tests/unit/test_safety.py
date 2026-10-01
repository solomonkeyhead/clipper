"""Masking the words that get posts flagged, and nothing more (D82)."""

from __future__ import annotations

from clipper.campaign import compliance, rules, safety
from clipper.config import CampaignConfig
from clipper.models import ClipPlan

AUTH = "Official footage supplied by the campaign on Content Rewards"


def campaign(**extra) -> CampaignConfig:
    return CampaignConfig.model_validate({"name": "please-like-me", "source_authorization": AUTH, **extra})


def test_flagged_words_lose_a_letter_and_swearing_stays():
    text = safety.clean("What the fuck, he said penis. SEX! Porn? that's some shit")
    assert text == "What the fuck, he said p*nis. S*X! P*rn? that's some shit"


def test_slurs_keep_only_their_first_letter_and_phrases_are_caught():
    assert safety.clean("that retard") == "that r*****"
    assert safety.clean("I'll kill myself") == "I'll k*ll myself"


def test_whole_words_only_and_mentions_links_untouched():
    text = "cocktails on Essex road, analysis by @sexpanther at https://x.com/porn"
    assert safety.clean(text) == text


def test_a_flagged_hashtag_is_dropped_without_breaking_the_layout():
    assert safety.clean("so true  #sex #pleaselikeme") == "so true  #pleaselikeme"
    assert safety.clean("#pleaselikeme #porn") == "#pleaselikeme"


def test_the_campaigns_own_words_stay():
    show = campaign(title="Sex Education", description_keywords=["Sex Education"])
    assert safety.clean("Sex Education is back, sex jokes and all", show) == "Sex Education is back, sex jokes and all"


def test_every_platforms_text_and_the_youtube_title_are_masked():
    posts = rules.post_texts("Suck my penis", "a penis joke  #pleaselikeme", "", campaign(
        platform_targets=["tiktok", "youtube_shorts"]))
    assert {p.platform: (p.title, p.caption) for p in posts} == {
        "tiktok": ("", "a p*nis joke  #pleaselikeme"),
        "youtube_shorts": ("Suck my p*nis", "a p*nis joke  #pleaselikeme")}
    assert all(p.passed for p in posts)


def test_it_can_be_turned_off_and_then_the_check_is_gone():
    off = campaign(censor_flagged_words=False, platform_targets=["tiktok"])
    (post,) = rules.post_texts("x", "a penis joke", "", off)
    assert post.caption == "a penis joke" and "No words that get posts flagged" not in [c.name for c in post.checks]


def test_new_clips_get_a_clean_caption_hook_and_hashtags():
    plan = ClipPlan(clip_id="001", candidate_id="c", rank=1, start=0, end=30, text="w",
                    hook_text="penis or not penis", suggested_caption="the porn joke", hashtags=["#porn", "#funny"])
    out = compliance.apply_campaign_caption(plan, campaign())
    assert (out.hook_text, out.suggested_caption, out.hashtags) == ("p*nis or not p*nis", "the p*rn joke", ["#funny"])
