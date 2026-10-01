"""X: posts found and their numbers synced (D83), and links filed for every platform.

X's API is replaced by canned answers; nothing touches the network.
"""

from __future__ import annotations

import time
from typing import ClassVar

import pytest

from clipper.campaign import rules
from clipper.config import CampaignConfig
from clipper.learn import log as perf
from clipper.studio import posts
from clipper.x import api

NOW = time.time()


def tweet(id_, text, views=100, days_ago=0.5):
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(NOW - days_ago * 86400))
    return {"id": id_, "text": text, "created_at": stamp,
            "public_metrics": {"impression_count": views, "like_count": 9, "reply_count": 2,
                               "retweet_count": 3, "quote_count": 1, "bookmark_count": 4}}


@pytest.fixture
def fake_x(monkeypatch):
    calls = []

    def get(path, params):
        calls.append((path, dict(params)))
        if path.startswith("/users/by/username/"):
            return {"data": {"id": "42", "username": "SolomonKeyClips", "name": "Solomon"}}
        if path == "/users/42/tweets":
            if params.get("since_id") == "300":
                return {"meta": {"result_count": 0}}
            return {"data": [tweet("300", "Never invite these friends to dinner https://t.co/abc", 5000),
                             tweet("299", "an unrelated thought")]}
        if path == "/tweets":
            return {"data": [tweet(i, "old", 777, 3) for i in params["ids"].split(",")]}
        raise AssertionError(path)

    monkeypatch.setenv(api.ENV_TOKEN, "token")
    monkeypatch.setattr(api, "_get", get)
    return calls


class TestSync:
    ROW: ClassVar[dict] = {"caption": "Never invite these friends to dinner  #adults", "campaign": "fx", "clip_id": "c1",
           "source_id": "s1", "platform": "", "url": "https://www.tiktok.com/@s/video/1", "video_id": "1"}

    def test_a_new_post_is_matched_to_its_clip_with_its_numbers(self, data_root, fake_x):
        path = api.accounts_dir() / "solomonkeyclips.json"
        assert api.connect("@SolomonKeyClips")["user_id"] == "42" and path.exists()
        rows = [dict(self.ROW)]
        assert api.sync(path, rows, now=NOW) == 2
        x = [r for r in rows if r.get("platform") == "x"]
        assert len(x) == 1 and x[0]["clip_id"] == "c1" and x[0]["video_id"] == "300"
        assert x[0]["url"] == "https://x.com/SolomonKeyClips/status/300"
        assert (x[0]["views_latest"], x[0]["likes"], x[0]["comments"], x[0]["shares"], x[0]["saves"]) == \
            ("5000", "9", "2", "4", "4")
        assert fake_x[1][1]["max_results"] == api.FIRST_LOOK  # the first look is short
        account = api.read(path)
        assert account["since_id"] == "300" and account["posts_read"] == 2

    def test_later_syncs_ask_only_for_new_posts_and_refresh_recent_ones(self, data_root, fake_x):
        path = api.accounts_dir() / "solomonkeyclips.json"
        api.connect("SolomonKeyClips")
        rows = [dict(self.ROW)]
        api.sync(path, rows, now=NOW)
        rows.append({**self.ROW, "platform": "x", "video_id": "10", "url": "https://x.com/a/status/10",
                     "posted_at": time.strftime("%Y-%m-%d %H:%M", time.localtime(NOW - 40 * 86400))})
        assert api.sync(path, rows, now=NOW + 60) == 0  # not due yet
        fake_x.clear()
        api.sync(path, rows, now=NOW + 2 * 3600)
        assert fake_x[0][1]["since_id"] == "300"
        assert fake_x[1] == ("/tweets", {"tweet.fields": "created_at,public_metrics", "ids": "300"})  # not the 40-day-old one

    def test_errors_say_what_to_do(self, monkeypatch):
        import httpx

        monkeypatch.setenv(api.ENV_TOKEN, "t")
        monkeypatch.setattr(httpx, "get", lambda *a, **k: httpx.Response(401, json={}))
        with pytest.raises(api.XError, match="Bearer Token"):
            api._get("/x", {})
        monkeypatch.delenv(api.ENV_TOKEN)
        with pytest.raises(api.XError, match="Accounts page"):
            api.token()
        with pytest.raises(api.XError, match="username"):
            api.connect("not a name!")


class TestLinks:
    @pytest.mark.parametrize(("url", "platform"), [
        ("https://www.tiktok.com/@a/video/1", "tiktok"), ("https://instagram.com/reel/x", "instagram"),
        ("https://youtu.be/x", "youtube"), ("https://twitter.com/a/status/1", "x"),
        ("https://mobile.x.com/a/status/1", "x"), ("https://www.facebook.com/reel/1", "facebook"),
        ("https://fb.watch/abc", "facebook"), ("https://www.snapchat.com/spotlight/x", "snapchat"),
        ("https://www.threads.net/@a/post/x", "threads")])
    def test_every_platform_is_recognised(self, url, platform):
        assert posts.platform_of(url) == platform

    def test_an_x_link_is_filed_by_its_id_so_the_sync_fills_it(self, data_root):
        clip = {"campaign": "fx", "source_id": "s1", "clip_id": "c1", "caption": "hi"}
        assert posts.add_link(clip, "https://twitter.com/me/status/123?s=20") == "x"
        row = next(r for r in perf.read() if r.get("platform") == "x")
        assert (row["url"], row["video_id"]) == ("https://x.com/me/status/123", "123")
        with pytest.raises(posts.PostLinkError, match="status"):
            posts.add_link(clip, "https://x.com/me")
        assert posts.add_link(clip, "https://www.facebook.com/reel/99") == "facebook"
        with pytest.raises(posts.PostLinkError):
            posts.add_link(clip, "https://example.com/v/1")


def test_an_x_post_fits_280_characters():
    campaign = CampaignConfig.model_validate({
        "name": "c", "source_authorization": "Official footage supplied by the campaign",
        "platform_targets": ["tiktok", "x"], "required_hashtags": ["#must"]})
    tags = " ".join(f"#optional{i}" for i in range(40))
    caption = f"the line\n\n{'a long description. ' * 10}\n\n#must {tags}"
    by = {p.platform: p for p in rules.post_texts("t", caption, "", campaign)}
    x = by["x"].caption
    assert len(x) <= 280 and x.startswith("the line  #must #optional0") and "description" not in x
    assert by["x"].passed and "description" in by["tiktok"].caption
