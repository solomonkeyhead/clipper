"""Campaign alerts from the Whop feed: reading the feeds, judging, keeping.

Whop's API is replaced (patched calls), and the judge answers through the mock backend.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from clipper.llm.base import MockBackend
from clipper.studio import alerts, finder
from clipper.watch.judge import Verdict

CAMPAIGN = ("NEW CAMPAIGN: Adults Season 2 clipping. $2 per 1K views on TikTok, "
            "budget $20,000. Join: https://whop.com/adults-s2")


def answer(**kw) -> str:
    base = dict(is_new_campaign=True, source="whop", name="Adults S2", owner="FX", rate="$2/1K",
                rate_per_1k_usd=2.0, platforms=["tiktok"], fit="yes", why="TV comedy",
                link="https://whop.com/adults-s2")
    return json.dumps(Verdict(**{**base, **kw}).model_dump())


FEED = {"id": "exp_1", "company": "Content Rewards", "name": "New Campaigns"}


def wpost(i: int, text: str = CAMPAIGN, *, days_ago: float = 0) -> dict:
    sent = (datetime.now(UTC) - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    title, _, rest = text.partition(". ")
    return {"id": f"post_{i}", "title": title, "content": rest, "created_at": sent, "parent_id": None,
            "user": {"username": "contentrewardsbot"}}


@pytest.fixture
def whop_setup(data_root, monkeypatch):
    from clipper.watch import whop

    monkeypatch.setattr(whop, "signed_in", lambda: True)
    alerts.whop_watch(["exp_1"], [FEED])
    alerts._attempts.clear()
    return whop


def whop_serve(monkeypatch, whop, posts):
    calls = []

    def new_posts(exp, *, after=None, first=25):
        calls.append((after, first))
        got = [p for p in posts if not after or p["created_at"] > after]
        return sorted(got, key=lambda p: p["created_at"])[-first:]
    monkeypatch.setattr(whop, "new_posts", new_posts)
    return calls


class TestWhop:
    def test_a_campaign_post_is_kept_with_its_link(self, whop_setup, monkeypatch):
        whop_serve(monkeypatch, whop_setup, [wpost(1)])
        result = alerts.check(MockBackend(responses=[answer(link="")]))
        assert result["new"] == ["Adults S2"]
        (row,) = finder.found()
        assert row["via"] == "whop" and row["link"] == "https://whop.com/adults-s2"

    def test_a_campaign_is_kept_once_however_many_posts_mention_it(self, whop_setup, monkeypatch):
        whop_serve(monkeypatch, whop_setup, [wpost(1), wpost(2, CAMPAIGN + " Reminder!")])
        result = alerts.check(MockBackend(responses=[answer(), answer()]))
        assert result["campaigns"] == 2 and result["new"] == ["Adults S2"] and len(finder.found()) == 1

    def test_later_checks_read_after_the_newest_post_and_catch_up(self, whop_setup, monkeypatch):
        posts = [wpost(1, days_ago=1)]
        calls = whop_serve(monkeypatch, whop_setup, posts)
        alerts.check(MockBackend(responses=[answer()]))
        posts.append(wpost(2, "NEW CAMPAIGN: Chad Powers S2 clipping. $3 per 1K views, TikTok."))
        alerts.check(MockBackend(responses=[answer(name="Chad Powers S2")]))
        assert calls[0] == (None, alerts.FIRST_LOOK) and calls[1] == (posts[0]["created_at"], 100)
        assert {r["name"] for r in finder.found()} == {"Adults S2", "Chad Powers S2"}

    def test_old_posts_arent_judged_the_first_time(self, whop_setup, monkeypatch):
        whop_serve(monkeypatch, whop_setup, [wpost(1, days_ago=30)])
        assert alerts.check(MockBackend(responses=[]))["read"] == 0
        assert alerts.whop_watched()[0]["last_seen"] is not None

    def test_chatter_isnt_judged(self, whop_setup, monkeypatch):
        whop_serve(monkeypatch, whop_setup, [wpost(1, "gm")])
        assert alerts.check(MockBackend(responses=[]))["read"] == 0

    def test_a_poor_fit_is_still_kept(self, whop_setup, monkeypatch):
        whop_serve(monkeypatch, whop_setup, [wpost(1)])
        alerts.check(MockBackend(responses=[answer(fit="no", why="crypto")]))
        assert finder.found()[0]["fit"] == "no"

    def test_an_unanswered_post_is_read_again(self, whop_setup, monkeypatch):
        calls = whop_serve(monkeypatch, whop_setup, [wpost(1)])
        first = alerts.check(MockBackend(responses=["not json"]))
        assert first["errors"] and finder.found() == []
        alerts.check(MockBackend(responses=[answer()]))
        assert calls[1][0] is None and len(finder.found()) == 1

    def test_a_refused_feed_is_reported(self, whop_setup, monkeypatch):
        def refused(*a, **k):
            raise whop_setup.WhopError("You do not have access to read these posts")
        monkeypatch.setattr(whop_setup, "new_posts", refused)
        assert alerts.check(MockBackend(responses=[]))["errors"] == [
            "New Campaigns: You do not have access to read these posts"]
        assert alerts.status()["error"] == "New Campaigns: You do not have access to read these posts"

    def test_signed_out_means_nothing_is_read(self, whop_setup, monkeypatch):
        monkeypatch.setattr(whop_setup, "signed_in", lambda: False)
        calls = whop_serve(monkeypatch, whop_setup, [wpost(1)])
        assert alerts.check(MockBackend(responses=[]))["read"] == 0 and calls == []

    def test_feeds_skip_lapsed_memberships_and_name_twins(self, monkeypatch):
        from clipper.watch import whop

        def get(path, params=None):
            if path == "/memberships":
                return {"data": [{"status": "canceled", "company": {"id": "biz_old", "title": "Old"}},
                                 {"status": "completed", "company": {"id": "biz_cr", "title": "Content Rewards"}}]}
            assert params["account_id"] == "biz_cr"
            forum = {"name": "Forums"}
            return {"data": [{"id": "e1", "name": "New Campaigns", "app": forum},
                             {"id": "e2", "name": "New Campaigns", "app": forum},
                             {"id": "e3", "name": "Chat", "app": {"name": "Chat"}}]}
        monkeypatch.setattr(whop, "get", get)
        assert [f["name"] for f in whop.feeds()] == ["New Campaigns", "New Campaigns (2)"]

    def test_the_campaign_link_is_found_in_the_post(self):
        from clipper.watch import whop

        assert whop.link_in("Campaign link: https://contentrewards.com/discover/abc.") == \
            "https://contentrewards.com/discover/abc"
        assert whop.link_in("see https://evil.example/x") == ""


class TestSettings:
    def test_feeds_no_longer_in_your_communities_are_refused(self, data_root):
        with pytest.raises(ValueError):
            alerts.whop_watch(["exp_9"], [FEED])

    def test_prefs_default_to_the_config_and_can_be_changed(self, data_root):
        assert alerts.prefs()["profile"]
        assert alerts.set_prefs("Gaming streams on YouTube Shorts") == {"profile": "Gaming streams on YouTube Shorts"}

    def test_the_users_profile_reaches_the_judge(self, whop_setup, monkeypatch):
        alerts.set_prefs("Gaming streams only")
        whop_serve(monkeypatch, whop_setup, [wpost(1)])
        backend = MockBackend(responses=[answer()])
        alerts.check(backend)
        assert "Gaming streams only" in backend.calls[0].user
