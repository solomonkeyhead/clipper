"""YouTube Shorts stats: reading the channel, and filling the performance log.

YouTube's APIs are replaced by canned answers; nothing touches the network.
"""

from __future__ import annotations

import time

import pytest

from clipper.instagram import sync as post_sync
from clipper.studio import stats
from clipper.youtube import api


def answers(monkeypatch, *, analytics_fails=False, long_ids=()):
    def get(url, token, params):
        if url.endswith("/channels"):
            return {"items": [{"id": "UC1", "snippet": {"title": "Solomon Clips", "customUrl": "@solomonkeyclips"},
                               "contentDetails": {"relatedPlaylists": {"uploads": "UU1"}}}]}
        if url.endswith("/playlistItems"):
            return {"items": [{"contentDetails": {"videoId": v}} for v in ("s1", "s2", "long")]}
        if url.endswith("/videos"):
            return {"items": [
                {"id": v, "snippet": {"title": f"Never invite these friends to dinner {v}", "description": "#adults",
                                      "publishedAt": "2026-09-28T12:00:00Z"},
                 "statistics": {"viewCount": "1500", "likeCount": "90", "commentCount": "4"},
                 "contentDetails": {"duration": "PT12M" if v == "long" else "PT34S"}}
                for v in params["id"].split(",")]}
        if analytics_fails:
            raise api.YouTubeError("YouTube Analytics API has not been used in project")
        return {"rows": [["s1", 14.5, 7]]}
    monkeypatch.setattr(api, "_get", get)


class TestReading:
    def test_durations(self):
        assert api.seconds("PT34S") == 34 and api.seconds("PT1M5S") == 65
        assert api.seconds("PT2H") == 7200 and api.seconds("P1DT1S") == 86401 and api.seconds("x") == 0

    def test_shorts_only_with_stats_and_analytics(self, monkeypatch):
        answers(monkeypatch)
        shorts = api.list_shorts("tok")
        assert [s.id for s in shorts] == ["s1", "s2"]  # the 12-minute upload isn't a Short
        s1, s2 = shorts
        assert s1.url == "https://www.youtube.com/shorts/s1" and s1.views == 1500 and s1.likes == 90
        assert s1.caption.startswith("Never invite these friends to dinner s1")
        assert (s1.avg_watch_s, s1.shares) == (14.5, 7)
        assert s2.avg_watch_s is None  # too new for Analytics

    def test_without_analytics_the_counts_still_come(self, monkeypatch):
        answers(monkeypatch, analytics_fails=True)
        assert [s.views for s in api.list_shorts("tok")] == [1500, 1500]

    def test_a_disabled_api_is_explained(self, monkeypatch):
        import httpx

        monkeypatch.setattr(httpx, "get", lambda *a, **k: httpx.Response(
            403, json={"error": {"message": "YouTube Data API v3 has not been used in project 1 before"}}))
        with pytest.raises(api.YouTubeError, match="turn on the YouTube Data API"):
            api._get("https://x/channels", "tok", {})

    def test_an_expired_sign_in_says_how_to_fix_it(self, monkeypatch):
        import httpx

        monkeypatch.setattr(httpx, "post", lambda *a, **k: httpx.Response(400, json={"error": "invalid_grant"}))
        with pytest.raises(api.YouTubeError, match="In production"):
            api._token({"grant_type": "refresh_token"})


class TestLog:
    ROW = {"caption": "Never invite these friends to dinner s1 #adults", "clip_id": "c1", "campaign": "fx",
           "platform": "", "url": "https://www.tiktok.com/@s/video/1", "video_id": "1"}

    def test_a_short_gets_its_own_row_from_the_clips(self, monkeypatch):
        answers(monkeypatch)
        rows = [dict(self.ROW)]
        post_sync.apply(api.list_shorts("tok"), rows, account="solomonkeyclips", platform="youtube",
                        now=time.time())
        yt = [r for r in rows if r.get("platform") == "youtube"]
        assert len(yt) == 1 and yt[0]["clip_id"] == "c1" and yt[0]["campaign"] == "fx"
        assert yt[0]["url"] == "https://www.youtube.com/shorts/s1" and yt[0]["views_latest"] == "1500"
        assert yt[0]["avg_watch_s"] == "14.5" and yt[0]["shares"] == "7"
        assert rows[0]["url"].startswith("https://www.tiktok.com/")  # the TikTok row is untouched

    def test_sync_all_reads_connected_channels(self, monkeypatch, data_root):
        answers(monkeypatch)
        path = api.accounts_dir() / "UC1.json"
        path.parent.mkdir(parents=True)
        path.write_text('{"handle": "solomonkeyclips", "access_token": "t", "obtained_at": 9e12}', encoding="utf-8")
        rows = [dict(self.ROW)]
        assert stats.sync_all(rows) == []
        assert any(r.get("platform") == "youtube" for r in rows)
        assert api.remove("UC1") and api.token_files() == []
