"""Campaign alerts from Discord: reading the user's server, judging, keeping, pushing.

Discord's API is replaced (a mock transport or patched calls), and the judge
answers through the mock backend.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from clipper.llm.base import MockBackend
from clipper.studio import alerts, finder
from clipper.watch import discord, notify
from clipper.watch.judge import Verdict

CAMPAIGN = ("NEW CAMPAIGN: Adults Season 2 clipping. $2 per 1K views on TikTok, "
            "budget $20,000. Join: https://whop.com/adults-s2")


def post(i: int, text: str = CAMPAIGN, *, days_ago: float = 0, embed: dict | None = None) -> dict:
    sent = datetime.now(UTC) - timedelta(days=days_ago)
    return {"id": str(1000 + i), "content": text, "timestamp": sent.isoformat(),
            "author": {"username": "Vyro #campaigns", "bot": True},
            "embeds": [embed] if embed else []}


def answer(**kw) -> str:
    base = dict(is_new_campaign=True, source="whop", name="Adults S2", owner="FX", rate="$2/1K",
                rate_per_1k_usd=2.0, platforms=["tiktok"], fit="yes", why="TV comedy",
                link="https://whop.com/adults-s2")
    return json.dumps(Verdict(**{**base, **kw}).model_dump())


CHANNEL = {"id": "55", "name": "campaigns", "guild_id": "9", "guild": "My alerts", "kind": "text"}


@pytest.fixture
def setup_alerts(data_root, monkeypatch):
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "bot-token")
    monkeypatch.delenv("NTFY_TOPIC", raising=False)
    alerts.watch(["55"], [CHANNEL])
    alerts._attempts.clear()
    return data_root


def serve(monkeypatch, posts: list[dict]):
    """Discord returns `posts` (those after `after`); records each call."""
    calls = []

    def messages(token, channel_id, *, after=None, latest=25):
        calls.append(after)
        got = [p for p in posts if after is None or int(p["id"]) > int(after)]
        return got[-latest:] if after is None else got
    monkeypatch.setattr(discord, "messages", messages)
    return calls


def pushes(monkeypatch):
    sent = []
    monkeypatch.setattr(notify, "push", lambda server, body, timeout=15.0: sent.append(body))
    return sent


class TestDiscordApi:
    def client(self, handler):
        return httpx.Client(transport=httpx.MockTransport(handler))

    def test_the_bot_token_is_sent_as_a_bot(self):
        seen = {}

        def handler(request):
            seen["auth"] = request.headers["authorization"]
            return httpx.Response(200, json={"ok": 1})
        assert discord._get("tok", "/x", client=self.client(handler)) == {"ok": 1}
        assert seen["auth"] == "Bot tok"

    @pytest.mark.parametrize(("status", "words"), [(401, "refused the bot token"),
                                                  (403, "isn't allowed"), (404, "gone")])
    def test_refusals_are_explained(self, status, words):
        with pytest.raises(discord.DiscordError, match=words):
            discord._get("tok", "/x", client=self.client(lambda r: httpx.Response(status, json={})))

    def test_rate_limits_are_waited_out(self, monkeypatch):
        monkeypatch.setattr(discord.time, "sleep", lambda s: None)
        answers = iter([httpx.Response(429, json={"retry_after": 0.1}), httpx.Response(200, json=[])])
        assert discord._get("tok", "/x", client=self.client(lambda r: next(answers))) == []

    def test_embeds_are_read_like_text(self):
        m = post(1, "New drop!", embed={"title": "Adults S2", "url": "https://whop.com/a",
                                          "description": "$2 per 1K",
                                          "fields": [{"name": "Platforms", "value": "TikTok"}]})
        text = discord.text_of(m)
        assert "Adults S2" in text and "Platforms: TikTok" in text and "https://whop.com/a" in text

    def test_invite_asks_only_to_read(self):
        url = discord.invite_url("123")
        assert "client_id=123" in url and f"permissions={1024 + 65536}" in url

    def test_a_now_id_is_later_than_old_posts(self):
        assert int(discord.snowflake_now()) > int("1000")


class TestCheck:
    def test_a_campaign_is_kept_and_pushed_once(self, setup_alerts, monkeypatch):
        monkeypatch.setenv("NTFY_TOPIC", "topic")
        serve(monkeypatch, [post(1), post(2, CAMPAIGN + " Reminder!")])
        sent = pushes(monkeypatch)
        result = alerts.check(MockBackend(responses=[answer(), answer()]))
        assert result["campaigns"] == 2 and result["new"] == ["Adults S2"] and result["pushed"] == 1
        assert len(sent) == 1 and "Adults S2" in sent[0]["title"]
        (row,) = finder.found()
        assert row["via"] == "discord" and row["link"] == "https://whop.com/adults-s2"

    def test_only_new_posts_are_read_next_time(self, setup_alerts, monkeypatch):
        calls = serve(monkeypatch, [post(1)])
        alerts.check(MockBackend(responses=[answer()]))
        alerts.check(MockBackend(responses=[]))
        assert calls == [None, "1001"]

    def test_chatter_and_old_posts_arent_judged(self, setup_alerts, monkeypatch):
        serve(monkeypatch, [post(1, "gm"), post(2, days_ago=30), post(3, "Payouts are out today! " * 3)])
        result = alerts.check(MockBackend(responses=[answer(is_new_campaign=False)]))
        assert result["read"] == 1 and result["campaigns"] == 0 and finder.found() == []

    def test_without_a_campaign_link_it_points_at_the_post(self, setup_alerts, monkeypatch):
        serve(monkeypatch, [post(1)])
        alerts.check(MockBackend(responses=[answer(link="https://evil.example/x")]))
        assert finder.found()[0]["link"] == "https://discord.com/channels/9/55/1001"

    def test_a_poor_fit_is_kept_but_not_pushed(self, setup_alerts, monkeypatch):
        monkeypatch.setenv("NTFY_TOPIC", "topic")
        serve(monkeypatch, [post(1)])
        sent = pushes(monkeypatch)
        alerts.check(MockBackend(responses=[answer(fit="no", why="crypto")]))
        assert sent == [] and finder.found()[0]["fit"] == "no"

    def test_an_unanswered_post_is_read_again(self, setup_alerts, monkeypatch):
        calls = serve(monkeypatch, [post(1)])
        first = alerts.check(MockBackend(responses=["not json"]))
        assert first["errors"] and finder.found() == []
        alerts.check(MockBackend(responses=[answer()]))
        assert calls == [None, None] and len(finder.found()) == 1

    def test_a_discord_error_is_reported_not_raised(self, setup_alerts, monkeypatch):
        def broken(*a, **k):
            raise discord.DiscordError("That channel is gone")
        monkeypatch.setattr(discord, "messages", broken)
        result = alerts.check(MockBackend(responses=[]))
        assert result["errors"] == ["#campaigns: That channel is gone"]
        assert alerts.status()["error"] == "#campaigns: That channel is gone"

    def test_an_empty_channel_starts_from_now(self, setup_alerts, monkeypatch):
        calls = serve(monkeypatch, [])
        alerts.check(MockBackend(responses=[]))
        alerts.check(MockBackend(responses=[]))
        assert calls[0] is None and calls[1] is not None and int(calls[1]) > 1000

    def test_nothing_happens_without_a_token(self, setup_alerts, monkeypatch):
        monkeypatch.delenv("DISCORD_BOT_TOKEN")
        calls = serve(monkeypatch, [post(1)])
        assert alerts.check(MockBackend(responses=[]))["read"] == 0 and calls == []


class TestSettings:
    def test_channels_the_bot_cant_see_are_refused(self, setup_alerts):
        with pytest.raises(ValueError):
            alerts.watch(["77"], [CHANNEL])

    def test_rewatching_keeps_the_place(self, setup_alerts, monkeypatch):
        serve(monkeypatch, [post(1)])
        alerts.check(MockBackend(responses=[answer()]))
        alerts.watch(["55"], [CHANNEL])
        assert alerts.watched()[0]["last_id"] == "1001"
        alerts.watch([], [])
        assert alerts.watched() == []

    def test_prefs_default_to_the_config_and_can_be_changed(self, setup_alerts):
        assert alerts.prefs()["profile"]
        p = alerts.set_prefs("Gaming streams on YouTube Shorts", 1.5)
        assert p == {"profile": "Gaming streams on YouTube Shorts", "min_rate": 1.5}

    def test_the_users_profile_reaches_the_judge(self, setup_alerts, monkeypatch):
        alerts.set_prefs("Gaming streams only", 0)
        serve(monkeypatch, [post(1)])
        backend = MockBackend(responses=[answer()])
        alerts.check(backend)
        assert "Gaming streams only" in backend.calls[0].user


# ---- Whop feeds (D69) ----

FEED = {"id": "exp_1", "company": "Content Rewards", "name": "New Campaigns"}


def wpost(i: int, text: str = CAMPAIGN, *, days_ago: float = 0) -> dict:
    sent = (datetime.now(UTC) - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    title, _, rest = text.partition(". ")
    return {"id": f"post_{i}", "title": title, "content": rest, "created_at": sent, "parent_id": None,
            "user": {"username": "contentrewardsbot"}}


@pytest.fixture
def whop_setup(data_root, monkeypatch):
    from clipper.watch import whop

    monkeypatch.delenv("DISCORD_BOT_TOKEN", raising=False)
    monkeypatch.delenv("NTFY_TOPIC", raising=False)
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

    def test_a_refused_feed_is_reported(self, whop_setup, monkeypatch):
        def refused(*a, **k):
            raise whop_setup.WhopError("You do not have access to read these posts")
        monkeypatch.setattr(whop_setup, "new_posts", refused)
        assert alerts.check(MockBackend(responses=[]))["errors"] == [
            "New Campaigns: You do not have access to read these posts"]

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
