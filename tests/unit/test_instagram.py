"""Instagram Reels into the performance log, via Instagram's official API."""

from __future__ import annotations

import json
import time

import pytest

from clipper.instagram import api, sync
from clipper.instagram.api import Reel

DAY = 86400.0
NOW = 1_790_000_000.0


def clip_row(caption: str, clip_id: str, **extra) -> dict[str, str]:
    return {"caption": caption, "clip_id": clip_id, "campaign": "chad-powers-s2",
            "source_id": "a394", "duration_s": "36", **extra}


def reel(id_: str, caption: str, **extra) -> Reel:
    return Reel(id=id_, caption=caption, created=NOW - 2 * DAY,
                url=f"https://www.instagram.com/reel/{id_}/", **extra)


class TestSync:
    def test_a_reel_gets_its_own_row_copied_from_its_clip(self):
        rows = [clip_row("the chemistry??? hello??? @chadpowershulu #ad", "005",
                         url="https://www.tiktok.com/@s/video/1", video_id="1")]
        result = sync.apply([reel("R1", "the chemistry??? hello??? @chadpowershulu #ad",
                                  views=900, likes=40, saves=12, avg_watch_s=11.5,
                                  skip_rate_pct=38.0)],
                            rows, account="solomonkeyclips", now=NOW)
        assert len(result.matched) == 1 and len(rows) == 2
        tiktok, insta = rows
        assert tiktok["url"].startswith("https://www.tiktok.com")  # untouched
        assert insta["platform"] == "instagram" and insta["account"] == "solomonkeyclips"
        assert insta["clip_id"] == "005" and insta["campaign"] == "chad-powers-s2"
        assert insta["url"] == "https://www.instagram.com/reel/R1/"
        assert insta["views_24h"] == "900" and insta["views_latest"] == "900"
        assert (insta["saves"], insta["avg_watch_s"], insta["skip_rate_pct"]) == ("12", "11.5", "38")

    def test_the_next_sync_finds_the_row_by_id_not_a_new_copy(self):
        rows = [clip_row("caption one", "001")]
        sync.apply([reel("R1", "caption one", views=10)], rows, now=NOW)
        sync.apply([reel("R1", "caption one", views=50, likes=3)], rows, now=NOW + 3600)
        assert len(rows) == 2
        assert rows[1]["views_latest"] == "50" and rows[1]["likes"] == "3"

    def test_counts_only_go_up(self):
        rows = [clip_row("caption one", "001")]
        sync.apply([reel("R1", "caption one", likes=30)], rows, now=NOW)
        sync.apply([reel("R1", "caption one", likes=25)], rows, now=NOW + 60)
        assert rows[1]["likes"] == "30"

    def test_a_caption_on_two_clips_is_skipped(self):
        rows = [clip_row("same caption", "001"), clip_row("same caption", "002")]
        result = sync.apply([reel("R1", "same caption")], rows, now=NOW)
        assert result.ambiguous and len(rows) == 2

    def test_a_reel_not_from_clipper_is_left_alone(self):
        rows = [clip_row("caption one", "001")]
        result = sync.apply([reel("R9", "my own post")], rows, now=NOW)
        assert result.unmatched and len(rows) == 1

    def test_tiktok_sync_never_claims_an_instagram_row(self):
        from clipper.tiktok.api import Video
        from clipper.tiktok.sync import apply as tiktok_apply

        rows = [clip_row("caption one", "001")]
        sync.apply([reel("R1", "caption one")], rows, now=NOW)
        tiktok_apply([Video(id="T1", caption="caption one", created=NOW, url="https://t/1",
                            duration=30, views=5, likes=0, comments=0, shares=0)],
                     rows, now=NOW)
        assert rows[0]["video_id"] == "T1" and rows[1]["video_id"] == "R1"


class TestInsights:
    def test_values_are_read_and_watch_time_is_converted_from_ms(self, monkeypatch):
        def fake_get(url, params):
            return {"data": [{"name": "views", "values": [{"value": 1200}]},
                             {"name": "saved", "values": [{"value": 9}]},
                             {"name": "shares", "values": [{"value": 4}]},
                             {"name": "ig_reels_avg_watch_time", "values": [{"value": 8450}]},
                             {"name": "reels_skip_rate", "values": [{"value": 41.26}]}]}

        monkeypatch.setattr(api, "_get", fake_get)
        r = reel("R1", "x")
        api._add_insights(r, "token")
        assert (r.views, r.saves, r.shares, r.avg_watch_s, r.skip_rate_pct) == (
            1200, 9, 4, 8.4, 41.3)

    def test_a_metric_instagram_refuses_is_dropped_not_fatal(self, monkeypatch):
        asked = []

        def fake_get(url, params):
            asked.append(params["metric"])
            if "reels_skip_rate" in params["metric"]:
                raise api.InstagramError("(#100) metric[5] must be one of ... reels_skip_rate")
            return {"data": [{"name": "views", "values": [{"value": 7}]}]}

        monkeypatch.setattr(api, "_get", fake_get)
        r = reel("R1", "x")
        api._add_insights(r, "token")
        assert r.views == 7 and r.skip_rate_pct is None and len(asked) == 2

    def test_only_reels_are_listed(self, monkeypatch):
        page = {"data": [
            {"id": "1", "media_product_type": "REELS", "caption": "a",
             "permalink": "https://www.instagram.com/reel/1/", "timestamp": "2026-09-28T18:00:00+0000"},
            {"id": "2", "media_product_type": "FEED", "caption": "photo"}]}
        monkeypatch.setattr(api, "_get", lambda url, params: page)
        reels = api.list_reels("token", insights=False)
        assert [r.id for r in reels] == ["1"] and reels[0].created > 0


class TestToken:
    @pytest.fixture
    def stored(self, tmp_path, monkeypatch):
        path = tmp_path / "token.json"
        monkeypatch.setattr(api, "token_path", lambda account=None: path)
        return path

    def test_a_young_token_is_used_as_is(self, stored, monkeypatch):
        stored.write_text(json.dumps({"access_token": "A", "obtained_at": time.time() - DAY}))
        monkeypatch.setattr(api, "_refresh", lambda t: pytest.fail("refreshed too early"))
        assert api.access_token() == "A"

    def test_a_month_old_token_is_extended(self, stored, monkeypatch):
        stored.write_text(json.dumps({"access_token": "A",
                                      "obtained_at": time.time() - 31 * DAY}))
        monkeypatch.setattr(api, "_refresh", lambda t: "B")
        assert api.access_token() == "B"
        assert json.loads(stored.read_text())["access_token"] == "B"

    def test_an_expired_token_asks_for_a_new_login(self, stored):
        stored.write_text(json.dumps({"access_token": "A",
                                      "obtained_at": time.time() - 61 * DAY}))
        with pytest.raises(api.InstagramError, match="reconnect"):
            api.access_token()

    def test_errors_never_echo_the_token(self, monkeypatch):
        import urllib.error

        def boom(*a, **k):
            raise urllib.error.URLError("refused")

        monkeypatch.setattr(api.urllib.request, "urlopen", boom)
        with pytest.raises(api.InstagramError) as info:
            api._get("https://graph.instagram.com/v25.0/me", {"access_token": "SECRET123"})
        assert "SECRET123" not in str(info.value)
