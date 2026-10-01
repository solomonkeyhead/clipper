"""Brief rules followed to a T (D81): each platform's text, enforced and checked,
the AI's second reading against the brief, and keeping unposted clips current."""

from __future__ import annotations

import json

import pytest
import yaml
from fastapi.testclient import TestClient

from clipper.campaign import audit, compliance, editor, rules
from clipper.config import CampaignConfig, CaptionRule
from clipper.llm.base import LLMResponse, MockBackend
from clipper.models import ClipPlan
from clipper.studio import db, evidence, rulecheck

AUTH = "Official footage supplied by the campaign Please Like Me on Content Rewards"
TAG_RULE = {"text": "@JoshThomasChannel", "place": "title", "platforms": ["youtube_shorts"],
            "quote": "On YouTube, tag @JoshThomasChannel in the title."}
HOOK = "the most underrated gay show of the 2010s is free on youtube and nobody told you"
CAPTION = ("full series is free on youtube (Josh Thomas channel)\n\nIn this scene from Please Like Me, "
           "Josh and Tom talk.\n\n#pleaselikeme #underratedshows #lgbtq #netflix")


def plm(**extra) -> CampaignConfig:
    return CampaignConfig.model_validate({
        "name": "please-like-me", "source_authorization": AUTH,
        "platform_targets": ["tiktok", "instagram_reels", "youtube_shorts"],
        "required_hashtags": ["#pleaselikeme", "#underratedshows", "#lgbtq"],
        "caption_rules": [TAG_RULE], **extra})


def by_platform(posts):
    return {p.platform: p for p in posts}


class TestEachPlatformsText:
    def test_the_youtube_title_gets_the_tag_and_the_others_dont(self):
        posts = by_platform(rules.post_texts(HOOK, CAPTION, HOOK, plm()))
        yt = posts["youtube_shorts"]
        assert yt.title == f"{HOOK} @JoshThomasChannel" and len(yt.title) <= rules.TITLE_MAX
        assert yt.caption == CAPTION  # the description is the caption as stored
        assert "@JoshThomasChannel" not in posts["tiktok"].caption + posts["instagram_reels"].caption
        assert all(p.passed for p in posts.values())
        names = [c.name for c in yt.checks]
        assert "Title includes “@JoshThomasChannel”" in names
        assert "Title includes “@JoshThomasChannel”" not in [c.name for c in posts["tiktok"].checks]

    def test_a_long_name_is_cut_at_a_word_so_the_tag_fits(self):
        title = rules.youtube_title("word " * 40, "", plm())
        assert len(title) <= rules.TITLE_MAX and title.endswith("… @JoshThomasChannel")

    def test_a_title_rule_on_a_platform_without_titles_goes_in_the_caption(self):
        campaign = plm(caption_rules=[{**TAG_RULE, "platforms": []}])
        posts = by_platform(rules.post_texts(HOOK, CAPTION, HOOK, campaign))
        assert "@JoshThomasChannel" in posts["tiktok"].caption
        assert "@JoshThomasChannel" in posts["youtube_shorts"].title
        assert all(p.passed for p in posts.values())

    def test_missing_requirements_are_added(self):
        campaign = plm(required_caption_text="#ad", caption_rules=[
            {"text": "@adultsfx", "platforms": ["tiktok"]}])
        text = rules.enforce("Never invite these friends to dinner  #funny", campaign, "tiktok")
        assert text == ("Never invite these friends to dinner. #ad @adultsfx  "
                        "#pleaselikeme #underratedshows #lgbtq #funny")
        assert rules.enforce(text, campaign, "tiktok") == text  # already there: unchanged

    def test_only_the_briefs_hashtags_survive_including_its_own_captions(self):
        campaign = plm(only_required_hashtags=True, required_caption_text="#ad",
                       fallback_captions=["#RICKYRUSS hive rise"])
        text = rules.enforce("#RICKYRUSS hive rise #comedy #ad  #pleaselikeme #fyp", campaign)
        assert "#comedy" not in text and "#fyp" not in text and "#RICKYRUSS" in text and "#ad" in text
        post = rules.PostText("tiktok", text)
        assert all(c.passed for c in rules.check(post, campaign))

    def test_a_banned_word_fails_and_is_named(self):
        campaign = plm(forbidden_terms=["suicide"], caption_rules=[
            {"text": "Netflix", "must": "avoid", "quote": "Don't mention Netflix."}])
        post = rules.PostText("tiktok", CAPTION)
        failed = {c.name: c.detail for c in rules.check(post, campaign, hook="a suicide joke") if not c.passed}
        assert failed == {"Doesn't say “Netflix”": "Don't mention Netflix.",
                          "None of the brief's banned words": "suicide"}

    def test_platform_limits(self):
        many = " ".join(f"#t{i}" for i in range(31))
        failed = [c for c in rules.check(rules.PostText("instagram_reels", many), plm(required_hashtags=[]))
                  if not c.passed]
        assert [c.name for c in failed] == ["Fits Instagram's limits"] and "30 hashtags" in failed[0].detail
        assert "<" not in rules.youtube_title("a <b> c", "", plm())
        assert "<" not in rules.enforce("x < y", plm(), "youtube_shorts")

    def test_the_summary_names_the_platform_when_only_one_fails(self):
        campaign = plm(caption_rules=[{"text": "Hulu", "must": "avoid", "platforms": ["tiktok"]}])
        posts = rules.post_texts(HOOK, "Watch on Hulu", HOOK, campaign)
        assert rules.summary(posts) == ["Doesn't say “Hulu” (TikTok)"]

    def test_both_caption_layouts_split_and_rejoin(self):
        for text in (CAPTION, "line  #a #b", "just a line"):
            assert rules.join(*rules.parts(text)) == text


class TestMakingTheCaption:
    def plan(self, caption="So good", hashtags=("#fun", "#netflixshows")):
        return ClipPlan(clip_id="001", candidate_id="c", rank=1, start=0, end=30, text="words",
                        suggested_caption=caption, hashtags=list(hashtags))

    def test_every_platform_rules_go_in_when_the_clip_is_made(self):
        campaign = plm(caption_rules=[TAG_RULE, {"text": "Free on YouTube"}])
        out = compliance.apply_campaign_caption(self.plan(), campaign)
        assert out.suggested_caption == "So good. Free on YouTube"  # the title rule waits for YouTube

    def test_a_caption_saying_what_the_brief_bans_is_swapped_for_the_briefs_own(self):
        campaign = plm(caption_rules=[{"text": "netflix", "must": "avoid"}], fallback_captions=["it's all on youtube"])
        out = compliance.apply_campaign_caption(self.plan("Better than anything on Netflix"), campaign)
        assert out.suggested_caption == "it's all on youtube"
        assert "#netflixshows" in out.hashtags  # a whole word only: "netflixshows" isn't "netflix"
        out = compliance.apply_campaign_caption(self.plan(hashtags=("#netflix",)), campaign)
        assert "#netflix" not in out.hashtags

    def test_the_clips_checks_cover_each_platform(self):
        report = compliance.check_clip(self.plan(), plm(), duration=30)
        names = [r.name for r in report.rules]
        assert {"post_text:tiktok", "post_text:instagram_reels", "post_text:youtube_shorts"} <= set(names)


class TestProofPack:
    def test_a_posted_short_is_checked_against_its_platforms_rules(self):
        saved = {"caption_rules": [TAG_RULE], "required_hashtags": ["#pleaselikeme"]}
        ok = evidence.caption_checks(f"{HOOK} @JoshThomasChannel\n\n#pleaselikeme", saved, "youtube")
        assert all(c["passed"] for c in ok) and len(ok) == 2
        missing = evidence.caption_checks(f"{HOOK}\n\n#pleaselikeme", saved, "youtube")
        assert [c["name"] for c in missing if not c["passed"]] == ["Includes “@JoshThomasChannel”"]
        assert len(evidence.caption_checks("#pleaselikeme", saved, "tiktok")) == 1  # YouTube's rule only

    def test_the_briefs_own_caption_tags_are_not_extra(self):
        saved = {"only_required_hashtags": True, "required_hashtags": ["#chadpowers"],
                 "fallback_captions": ["#RICKYRUSS hive rise"]}
        assert all(c["passed"] for c in evidence.caption_checks("#RICKYRUSS hive rise #chadpowers", saved))


def answer(problems=(), breaks=True):
    """A mock model: the audit's answer, then a verdict for each problem."""
    found = json.dumps({"problems": list(problems)})
    return [found] + [json.dumps({"breaks": breaks, "why": "x"})] * len(problems)


class TestTheAICheck:
    def texts(self, campaign=None):
        return rules.post_texts(HOOK, CAPTION, HOOK, campaign or plm(caption_rules=[]))

    def test_it_reads_the_brief_and_reports_a_missing_tag_with_its_fix(self):
        posts = self.texts()
        user = audit.build_user("On YouTube, tag @JoshThomasChannel in the title.", posts, HOOK, [])
        problem = {"rule": "On YouTube, tag @JoshThomasChannel in the title.", "platform": "youtube_shorts",
                   "where": "title", "problem": "No tag", "add": "@JoshThomasChannel"}
        backend = MockBackend(responses=answer([problem]))
        found = audit.audit(user, posts, [backend])
        assert [p.add for p in found] == ["@JoshThomasChannel"]
        assert "BRIEF:\nOn YouTube" in backend.calls[0].user and "TITLE: " in backend.calls[0].user

    def test_a_problem_not_confirmed_on_a_second_look_is_dropped(self):
        problem = {"rule": "No hashtags other than the required ones.", "platform": "tiktok",
                   "problem": "@chadpowershulu is an extra hashtag"}
        assert audit.audit("u", self.texts(), [MockBackend(responses=answer([problem], breaks=False))]) == []

    def test_a_missing_text_that_is_there_is_dropped(self):
        problem = {"rule": "Use #lgbtq", "platform": "all", "add": "#lgbtq"}
        assert audit.audit("u", self.texts(), [MockBackend(responses=answer([problem]))]) == []

    def test_no_usable_answer_is_unchecked_not_passed(self):
        assert audit.audit("u", self.texts(), [MockBackend(responses=["not json"])]) is None

    def test_the_rules_code_checks_are_listed_for_the_model_to_skip(self):
        checked = audit.code_checked(plm())
        assert any("@JoshThomasChannel" in c and "YouTube" in c for c in checked)
        assert any(c.startswith("Required hashtags: #pleaselikeme") for c in checked)
        brief = audit.brief_text(plm(notes="Keep likes on."), None)
        assert "Keep likes on." in brief and "#pleaselikeme" not in brief
        assert audit.brief_text(plm(), "  the pasted brief ") == "the pasted brief"


class TestTheBriefReader:
    def test_caption_rules_and_posting_rules_come_from_the_brief(self):
        found = editor.BriefFields(title="Please Like Me", caption_rules=[
            editor.BriefCaptionRule(**TAG_RULE),
            editor.BriefCaptionRule(text="  ", quote="nothing"),
            editor.BriefCaptionRule(text="@x", platforms=["snapchat"], place="footer")],
            posting_rules=["Keep likes and comments ON."])
        form = editor.form_from_brief(found)
        assert [r.model_dump() for r in form.caption_rules] == [
            {**TAG_RULE, "must": "include", "platforms": ("youtube_shorts",)},
            {"text": "@x", "must": "include", "place": "caption", "platforms": (), "quote": ""}]
        assert form.posting_rules == ["Keep likes and comments ON."]
        assert "caption_rules" in editor.BRIEF_SYSTEM and "Drop none" in editor.BRIEF_SYSTEM

    def test_a_rule_is_added_once(self, tmp_path):
        (tmp_path / "please-like-me.yaml").write_text(
            yaml.safe_dump({"name": "please-like-me", "source_authorization": AUTH}), encoding="utf-8")
        editor.add_caption_rule(tmp_path, "please-like-me", TAG_RULE)
        campaign = editor.add_caption_rule(tmp_path, "please-like-me", {**TAG_RULE, "quote": "again"})
        assert campaign.caption_rules == (CaptionRule(**TAG_RULE),)
        assert list((tmp_path / ".history").glob("*.yaml"))  # the old file is kept

    def test_unknown_platforms_are_refused(self):
        with pytest.raises(ValueError, match="unknown platform"):
            CaptionRule(text="@x", platforms=("snapchat",))


@pytest.fixture
def campaigns(tmp_path, monkeypatch):
    folder = tmp_path / "campaigns"
    folder.mkdir()
    (folder / "please-like-me.yaml").write_text(yaml.safe_dump(plm().model_dump(mode="json")), encoding="utf-8")
    from clipper.studio import server

    monkeypatch.setattr(server, "campaigns_dir", lambda: folder)
    return folder


def add_clip(data_root, caption="old caption  #pleaselikeme", **extra) -> int:
    path = data_root / "library" / "please-like-me" / "a.mp4"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x00" * 2048)
    with db.connect() as con:
        return db.upsert_clip(con, {"campaign": "please-like-me", "source_id": "s1", "clip_id": "001",
                                    "title": HOOK, "hook": HOOK, "file": "please-like-me/a.mp4",
                                    "caption": caption, "duration_s": 30.0, **extra})


class TestKeepingClipsCurrent:
    def test_a_new_rule_reaches_stored_captions_and_the_log(self, data_root, campaigns):
        from clipper.learn import log as perf

        clip_id = add_clip(data_root)
        perf.write([{"campaign": "please-like-me", "source_id": "s1", "clip_id": "001",
                     "caption": "old caption  #pleaselikeme"}])
        backend = MockBackend(responses=answer())
        rulecheck.recheck("please-like-me", backends=[backend])
        with db.connect() as con:
            clip = db.clip(con, clip_id)
        assert clip["caption"] == "old caption  #pleaselikeme #underratedshows #lgbtq"
        assert perf.read()[0]["caption"] == clip["caption"]
        stored = rulecheck.stored(clip)
        assert stored["problems"] == [] and stored["key"]
        rulecheck.recheck("please-like-me", backends=[backend])
        assert len(backend.calls) == 1  # the same texts and brief aren't read twice

    def test_posted_clips_are_left_alone(self, data_root, campaigns):
        clip_id = add_clip(data_root, status="posted")
        rulecheck.recheck("please-like-me", backends=[MockBackend(responses=answer())])
        with db.connect() as con:
            assert db.clip(con, clip_id)["caption"] == "old caption  #pleaselikeme"


class TestTheAPI:
    @pytest.fixture
    def client(self, data_root, campaigns):
        from clipper.studio.server import create_app

        return TestClient(create_app())

    def test_a_ready_clip_carries_each_platforms_text_and_its_checks(self, client, data_root):
        clip_id = add_clip(data_root)
        clip = client.get(f"/api/clips/{clip_id}").json()
        copy = {c["platform"]: c for c in clip["post_copy"]}
        assert copy["youtube_shorts"]["title"].endswith("@JoshThomasChannel")
        assert all(ch["passed"] for c in copy.values() for ch in c["checks"])
        assert clip["rules"] == {"failed": [], "checked": False, "checking": False, "brief": []}

    def test_the_ai_checks_findings_show_until_the_texts_change(self, client, data_root):
        clip_id = add_clip(data_root)
        with db.connect() as con:
            clip = db.clip(con, clip_id)
            _, key = rulecheck.audit_key(clip, plm(), None)
            db.set_audit(con, clip_id, {"key": key, "problems": [{"rule": "r", "problem": "p"}]})
        assert client.get(f"/api/clips/{clip_id}").json()["rules"]["brief"][0]["problem"] == "p"
        res = client.put(f"/api/clips/{clip_id}/caption", json={"caption": "a new line"})
        assert res.json()["caption"] == "a new line  #pleaselikeme #underratedshows #lgbtq"
        assert client.get(f"/api/clips/{clip_id}").json()["rules"]["brief"] == []  # stale: read again

    def test_a_posted_clips_caption_cant_change(self, client, data_root):
        clip_id = add_clip(data_root, status="posted")
        assert client.put(f"/api/clips/{clip_id}/caption", json={"caption": "x"}).status_code == 400

    def test_a_finding_becomes_a_rule_for_every_clip(self, client, campaigns, _no_background_rule_checks):
        res = client.post("/api/campaigns/please-like-me/caption-rules",
                          json={"text": "#ad", "quote": "Include #ad."})
        assert res.status_code == 200
        saved = yaml.safe_load((campaigns / "please-like-me.yaml").read_text(encoding="utf-8"))
        assert [r["text"] for r in saved["caption_rules"]] == ["@JoshThomasChannel", "#ad"]
        assert _no_background_rule_checks == ["please-like-me"]


def test_the_mock_answers_in_order():
    """The helpers above rely on MockBackend's scripted replies."""
    backend = MockBackend(responses=["a", "b"])
    from clipper.llm.base import LLMRequest

    assert [backend.complete(LLMRequest(system="", user="")).text for _ in range(2)] == ["a", "b"]
    assert isinstance(backend.complete(LLMRequest(system="", user="")), LLMResponse)
