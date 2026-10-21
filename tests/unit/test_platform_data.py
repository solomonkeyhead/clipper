"""Stored platform stats follow the platforms' rules: deleted on disconnect, on revocation, when stale (D231)."""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient

from clipper.learn import log as perf
from clipper.studio import db, platform_data, stats
from clipper.youtube import api as yt


def stamp(days_ago: float = 0) -> str:
    return (datetime.now() - timedelta(days=days_ago)).strftime("%Y-%m-%d %H:%M")


def token(data_root, platform, name, **extra):
    folder = data_root / platform / "accounts"
    folder.mkdir(parents=True, exist_ok=True)
    body = {"youtube": {"handle": name, "title": name, "channel_id": name, "access_token": "a",
                        "refresh_token": "r", "obtained_at": time.time()},
            "instagram": {"username": name}, "tiktok": {"handle": name, "open_id": name}}[platform]
    (folder / f"{name}.json").write_text(json.dumps({**body, **extra}), encoding="utf-8")


def post(platform, account, n, *, synced=0, **extra):
    return {"campaign": "c", "source_id": "s", "clip_id": f"clip{n}", "caption": f"caption {n}",
            "platform": platform, "account": account, "video_id": f"v{n}",
            "url": f"https://example.com/{platform}/{n}", "posted_at": "2026-10-01 10:00", "views_latest": "500",
            "views_7d": "300", "likes": "20", "shares": "3", "saves": "2", "avg_watch_s": "9", "avg_view_pct": "40",
            "posted_caption": "caption", "synced_at": stamp(synced), **extra}


def snapshots(*urls):
    with db.connect() as con:
        for u in urls:
            con.execute("INSERT INTO snapshots (url, at, views) VALUES (?, '2026-10-02 10:00', 5)", (u,))


def snapshot_urls():
    with db.connect() as con:
        return {r[0] for r in con.execute("SELECT url FROM snapshots")}


@pytest.fixture(autouse=True)
def on_the_hosted_site(monkeypatch):
    monkeypatch.setattr(platform_data, "hosted", lambda: True)


def test_on_the_owners_computer_nothing_is_cleared(data_root, monkeypatch):
    monkeypatch.setattr(platform_data, "hosted", lambda: False)
    rows = [post("youtube", "gone", 1, synced=40)]
    before = [dict(r) for r in rows]
    assert platform_data.enforce(rows, gone={"youtube": {"gone"}}, fresh=True) == 0
    assert rows == before


@pytest.fixture
def client(data_root):
    from clipper.studio.server import create_app

    return TestClient(create_app())


class TestDisconnect:
    def test_youtube_account_loses_its_stats_and_snapshots_at_once(self, data_root, client):
        token(data_root, "youtube", "chan")
        token(data_root, "youtube", "other")
        perf.write([post("youtube", "chan", 1), post("youtube", "other", 2, ), post("tiktok", "tt", 3)])
        snapshots("https://example.com/youtube/1", "https://example.com/youtube/2")
        assert client.delete("/api/accounts/youtube/chan").status_code == 200
        rows = perf.read()
        assert [r["account"] for r in rows] == ["other", "tt"]              # chan's derived row is gone
        assert rows[0]["views_latest"] == "500"                                # the other channel is untouched
        assert snapshot_urls() == {"https://example.com/youtube/2"}
        assert not (data_root / "youtube" / "accounts" / "chan.json").exists()

    def test_what_the_user_typed_stays(self, data_root, client):
        token(data_root, "youtube", "chan")
        perf.write([post("youtube", "chan", 1, payout_usd="12", notes="paid")])
        client.delete("/api/accounts/youtube/chan")
        (row,) = perf.read()
        assert row["payout_usd"] == "12" and row["notes"] == "paid"
        assert not any(row.get(c) for c in ("views_latest", "views_7d", "likes", "video_id", "url", "synced_at",
                                            "avg_watch_s", "avg_view_pct", "posted_caption"))

    def test_an_adopted_short_is_deleted_with_its_clip(self, data_root, client):
        token(data_root, "youtube", "chan")
        with db.connect() as con:
            db.upsert_clip(con, {"campaign": "own", "source_id": "channel", "clip_id": "yt-v9", "title": "T",
                                 "file": "", "caption": "c"})
        perf.write([post("youtube", "chan", 9, campaign="own", source_id="channel", clip_id="yt-v9")])
        client.delete("/api/accounts/youtube/chan")
        assert perf.read() == []
        with db.connect() as con:
            assert db.clips(con) == []

    def test_tiktok_clip_rows_stay_but_lose_the_api_numbers(self, data_root, client):
        token(data_root, "tiktok", "tt")
        perf.write([post("", "", 1, url="https://www.tiktok.com/@tt/video/1", saves="4", avg_watch_s="7",
                         new_followers="2")])
        snapshots("https://www.tiktok.com/@tt/video/1")
        client.delete("/api/accounts/tiktok/tt")
        (row,) = perf.read()
        assert row["clip_id"] == "clip1" and row["caption"] == "caption 1"
        assert not row["views_latest"] and not row["likes"] and not row["url"] and not row["video_id"]
        assert row["saves"] == "4" and row["avg_watch_s"] == "7" and row["new_followers"] == "2"   # collected by hand
        assert snapshot_urls() == set()

    def test_another_tiktok_account_keeps_its_stats(self, data_root, client):
        token(data_root, "tiktok", "tt")
        token(data_root, "tiktok", "tt2")
        perf.write([post("", "", 1, url="https://www.tiktok.com/@tt/video/1"),
                    post("", "", 2, url="https://www.tiktok.com/@tt2/video/2")])
        client.delete("/api/accounts/tiktok/tt")
        first, second = perf.read()
        assert not first["views_latest"] and second["views_latest"] == "500"

    def test_instagram_rows_are_dropped(self, data_root, client):
        token(data_root, "instagram", "ig")
        perf.write([post("instagram", "ig", 1)])
        client.delete("/api/accounts/instagram/ig")
        assert perf.read() == []

    def test_a_link_pasted_by_hand_is_not_api_data(self, data_root, client):
        token(data_root, "tiktok", "tt")
        perf.write([post("", "", 1, url="https://www.tiktok.com/@tt/video/1", synced_at="", views_latest="")])
        client.delete("/api/accounts/tiktok/tt")
        assert perf.read()[0]["url"].endswith("/video/1")

    def test_unknown_account_is_a_404(self, client):
        assert client.delete("/api/accounts/youtube/nope").status_code == 404


class TestSweep:
    def test_finds_what_a_gone_account_left(self, data_root, client):
        token(data_root, "youtube", "chan")
        perf.write([post("youtube", "chan", 1), post("youtube", "gone", 2)])
        snapshots("https://example.com/youtube/2")
        assert platform_data.sweep() == 1
        assert [r["account"] for r in perf.read()] == ["chan"]
        assert snapshot_urls() == set()

    def test_leaves_everything_when_the_accounts_are_connected(self, data_root, client):
        token(data_root, "youtube", "Chan")
        token(data_root, "instagram", "ig")
        perf.write([post("youtube", "@chan", 1), post("instagram", "ig", 2)])
        assert platform_data.sweep() == 0 and len(perf.read()) == 2

    def test_a_locked_log_is_retried_next_time(self, data_root, client, monkeypatch):
        perf.write([post("youtube", "gone", 1)])
        real = perf.write

        def locked(*a, **k):
            raise PermissionError

        monkeypatch.setattr(perf, "write", locked)
        assert platform_data.sweep() == -1
        monkeypatch.setattr(perf, "write", real)
        assert platform_data.sweep() == 1 and perf.read() == []

    def test_runs_when_the_studio_starts(self, data_root, monkeypatch):
        from clipper.studio import server

        perf.write([post("youtube", "gone", 1)])
        monkeypatch.setattr(server, "Snapshot", server.Snapshot)
        with TestClient(server.create_app(auto_sync=True)):
            pass
        assert perf.read() == []


class TestRevoked:
    def test_invalid_grant_marks_the_channel_and_a_sync_deletes_its_data(self, data_root, monkeypatch):
        token(data_root, "youtube", "chan", obtained_at=0)
        perf.write([post("youtube", "chan", 1)])
        snapshots("https://example.com/youtube/1")

        def refused(url, data=None, timeout=None, **k):
            return httpx.Response(400, json={"error": "invalid_grant", "error_description": "Token has been revoked"},
                                  request=httpx.Request("POST", url))

        monkeypatch.setattr(yt.httpx, "post", refused)
        monkeypatch.setenv(yt.ENV_ID, "id")
        monkeypatch.setenv(yt.ENV_SECRET, "secret")
        problems = stats.run_sync()["problems"]
        assert any("ended" in p or "expired" in p for p in problems)
        (path,) = yt.token_files()
        assert yt.is_revoked(path)
        saved = yt.read_token(path)
        assert "refresh_token" not in saved and "access_token" not in saved and saved["handle"] == "chan"
        assert perf.read() == [] and snapshot_urls() == set()

    def test_a_revoked_channel_shows_as_disconnected(self, data_root, client):
        token(data_root, "youtube", "chan", revoked_at=time.time())
        (account,) = client.get("/api/accounts").json()
        assert account["connected"] is False and account["health"] == "error"
        with pytest.raises(yt.AccessRevoked):
            yt.access_token(yt.token_files()[0])

    def test_a_broker_refusal_counts_too(self, data_root, monkeypatch):
        from clipper import connect

        token(data_root, "youtube", "chan", obtained_at=0, via="service")
        monkeypatch.setattr(connect, "enabled", lambda: True)

        def refuse(platform, refresh):
            raise connect.ConnectError("Token has been expired or revoked.")

        monkeypatch.setattr(connect, "refresh", refuse)
        with pytest.raises(yt.AccessRevoked):
            yt.access_token(yt.token_files()[0])
        assert yt.is_revoked(yt.token_files()[0])


class TestFreshness:
    def test_youtube_data_not_refreshed_for_30_days_is_deleted(self, data_root):
        token(data_root, "youtube", "chan")
        rows = [post("youtube", "chan", 1, synced=31), post("youtube", "chan", 2, synced=29)]
        assert platform_data.enforce(rows, fresh=True) == 1
        assert [r["clip_id"] for r in rows] == ["clip2"]

    def test_a_successful_sync_refreshes_so_nothing_goes(self, data_root, monkeypatch):
        token(data_root, "youtube", "chan")
        old = post("youtube", "chan", 1, synced=40)
        short = yt.Short(id="v1", caption="caption 1", created=time.time() - 40 * 86400, url=old["url"], views=900)
        monkeypatch.setattr(yt, "list_shorts", lambda token, **k: [short])
        monkeypatch.setattr(yt, "access_token", lambda path: "t")
        perf.write([old])
        stats.run_sync()
        (row,) = perf.read()
        assert row["views_latest"] == "900" and row["synced_at"][:10] == datetime.now().strftime("%Y-%m-%d")

    def test_stale_tiktok_and_instagram_are_not_touched(self, data_root):
        token(data_root, "instagram", "ig")
        rows = [post("instagram", "ig", 1, synced=90)]
        assert platform_data.enforce(rows, fresh=True) == 0 and rows[0]["views_latest"] == "500"

    def test_a_sync_that_cannot_reach_youtube_keeps_fresh_data_for_now(self, data_root, monkeypatch):
        token(data_root, "youtube", "chan")
        perf.write([post("youtube", "chan", 1, synced=5)])

        def down(token, **k):
            raise yt.YouTubeError("couldn't reach YouTube")

        monkeypatch.setattr(yt, "list_shorts", down)
        monkeypatch.setattr(yt, "access_token", lambda path: "t")
        assert any("couldn't reach" in p for p in stats.run_sync()["problems"])
        assert perf.read()[0]["views_latest"] == "500"


class TestNotSentToAI:
    def test_hosted_learning_does_not_use_post_views(self, data_root, monkeypatch):
        from clipper import runner

        monkeypatch.setattr(stats, "clip_performance", lambda rows, log: {1: 3.0})
        assert runner._post_performance([]) == {1: 3.0}
        monkeypatch.setenv("CLIPPER_HOSTED", "1")
        assert runner._post_performance([]) == {}

    def test_hosted_few_shot_examples_are_off(self, data_root, monkeypatch):
        from clipper import pipeline
        from clipper.config import Config

        base = Config.load()
        config = base.model_copy(update={"llm": base.llm.model_copy(update={"use_few_shot_examples": True})})
        folder = pipeline.examples_dir()
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "top_clips.jsonl").write_text(json.dumps({"hook": "h", "text": "t", "views": 5}) + "\n", encoding="utf-8")
        assert pipeline._few_shot_examples(config)
        monkeypatch.setenv("CLIPPER_HOSTED", "1")
        assert pipeline._few_shot_examples(config) is None
