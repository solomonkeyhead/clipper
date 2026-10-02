"""The Control Center: clip library, database and web API."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from clipper.studio import db, library


@pytest.fixture
def campaigns(tmp_path, monkeypatch, valid_campaign_dict):
    folder = tmp_path / "campaigns"
    folder.mkdir()
    brief = {**valid_campaign_dict, "name": "chad-powers-s2", "required_caption_text": "#ad",
             "required_hashtags": ["#chadpowers"], "platform_targets": ["tiktok", "instagram_reels"]}
    (folder / "chad-powers-s2.yaml").write_text(yaml.safe_dump(brief), encoding="utf-8")
    from clipper.studio import server

    monkeypatch.setattr(server, "campaigns_dir", lambda: folder)
    return folder


def add_clip(data_root: Path, **extra) -> int:
    rel = extra.pop("file", "chad-powers-s2/001_a.mp4")
    path = data_root / "library" / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x00" * 2048)
    with db.connect() as con:
        return db.upsert_clip(con, {"campaign": "chad-powers-s2", "source_id": "a394",
                                    "clip_id": "001_16m12s", "title": "the looks", "file": rel,
                                    "caption": "I started Chad Powers for football #ad",
                                    "duration_s": 57.7, **extra})


class TestDatabase:
    def test_a_rerender_keeps_the_users_status_and_notes(self, data_root):
        clip_id = add_clip(data_root)
        with db.connect() as con:
            db.update_clip(con, clip_id, status="submitted", notes="paid")
        again = add_clip(data_root, title="new title")
        with db.connect() as con:
            row = db.clip(con, clip_id)
        assert again == clip_id
        assert (row["status"], row["notes"], row["title"]) == ("submitted", "paid", "new title")

    def test_status_must_be_known(self, data_root):
        clip_id = add_clip(data_root)
        with db.connect() as con, pytest.raises(ValueError):
            db.update_clip(con, clip_id, status="viral")

    def test_auto_post_is_off_until_turned_on(self, data_root):
        with db.connect() as con:
            assert db.settings(con)["auto_post"] == "0"
            db.set_setting(con, "auto_post", "1")
            assert db.settings(con)["auto_post"] == "1"
            with pytest.raises(ValueError):
                db.set_setting(con, "not_a_setting", "1")


class TestImport:
    def test_posting_notes_and_the_posted_prefix(self, data_root, tmp_path):
        folder = tmp_path / "fx-adults-s2-ready-v2"
        folder.mkdir()
        (folder / "POSTED02_ep201_6m37s.mp4").write_bytes(b"x")
        (folder / "03_ep201_12m11s.mp4").write_bytes(b"x")
        (folder / "POSTING.md").write_text(
            "# FX\n\n## 02_ep201_6m37s.mp4  (33s)\n\n```\nThe confidence made it worse. #fx\n```\n\n"
            "## 03_ep201_12m11s.mp4  (43s)\nHook on screen: Amazing jobs?\n\n```\nWait, jobs? #fx\n```\n",
            encoding="utf-8")
        rows = [{"campaign": "fx-adults-s2", "file": "POSTED02_ep201_6m37s.mp4",
                 "source_id": "ca2b", "clip_id": "003_6m37s", "caption": "The confidence made it worse. #fx",
                 "url": "https://www.tiktok.com/@s/video/1"}]
        library.import_folder(folder, "fx-adults-s2", rows)
        with db.connect() as con:
            clips = {c["clip_id"]: c for c in db.clips(con, "fx-adults-s2")}
        assert clips["003_6m37s"]["status"] == "posted" and clips["003_6m37s"]["source_id"] == "ca2b"
        other = clips["03_ep201_12m11s"]
        assert (other["status"], other["hook"], other["caption"], other["duration_s"]) == (
            "ready", "Amazing jobs?", "Wait, jobs? #fx", 43.0)
        assert (data_root / "library" / "fx-adults-s2" / "02_ep201_6m37s.mp4").exists()

    def test_captions_txt(self, tmp_path):
        folder = tmp_path / "ready"
        folder.mkdir()
        (folder / "captions.txt").write_text(
            "All from Episode 6.\n\n2 bench - incriminated  (62s)\n  On screen: POV: you came\n"
            "  Caption:   oh they knew #ad\n", encoding="utf-8")
        notes = library.read_notes(folder)
        assert notes["2 bench - incriminated"].caption == "oh they knew #ad"
        assert notes["2 bench - incriminated"].hook == "POV: you came"


class TestStats:
    def test_earnings_wait_for_the_minimum_and_stop_at_the_maximum(self):
        from clipper.studio.stats import estimate_earnings

        assert estimate_earnings(2000, 2.5, None, None) == 5.0
        assert estimate_earnings(1000, 3.0, 6.0, None) == 0.0     # below the $6 minimum
        assert estimate_earnings(10_000_000, 2.5, None, 1000) == 1000
        assert estimate_earnings(500, None, None, None) is None

    def test_medians_need_three_posts(self):
        from clipper.studio.stats import medians

        posts = [{"platform": "tiktok", "campaign": "a", "views_latest": v} for v in (10, 20, 900)]
        assert medians(posts) == {("tiktok", "a"): 20}
        assert medians(posts[:2]) == {}

    def test_the_handle_comes_from_a_tiktok_link(self):
        from clipper.studio.stats import handle_from_url

        assert handle_from_url("https://www.tiktok.com/@solomonkeyclips/video/1") == "solomonkeyclips"
        assert handle_from_url("https://www.instagram.com/reel/A/") == ""


def test_live_updates_reach_every_open_page():
    import asyncio

    from clipper.studio.events import Broker

    async def scenario():
        broker = Broker()
        broker.bind(asyncio.get_running_loop())
        a, b = broker.subscribe(), broker.subscribe()
        broker.publish("stats.synced", {"at": "now"})
        return (await a.get())[1:], (await b.get())[1:]

    assert asyncio.run(scenario()) == (("stats.synced", '{"at": "now"}'),) * 2


@pytest.mark.parametrize("raw, shown", [
    ("ChadPowers_206_pix_rec709_220_2398fps_hd_20_20260807", "ChadPowers 206"),
    ("Adults ep201", "Adults ep201"),
    ("😂😂😂 STEP DADDIES w/ TRAP DICKEY", "😂😂😂 STEP DADDIES w/ TRAP DICKEY"),
])
def test_source_names_lose_the_delivery_suffix(raw, shown):
    from clipper.studio.server import display_source

    assert display_source(raw) == shown


@pytest.mark.parametrize("name, own", [
    ("the most underrated gay show - please-like-me.mp4", True),
    ("the most underrated gay show - please-like-me (1).mp4", True),
    ("PLM_s01_ep1-CarRide.mp4", False),
    ("Interview - someone-else.mp4", False),  # not a campaign of ours
])
def test_downloaded_clips_are_not_footage(name, own):
    from pathlib import Path

    from clipper.studio.server import own_clip

    assert own_clip(Path(name), {"please-like-me"}) is own


class TestApi:
    @pytest.fixture
    def client(self, data_root, campaigns):
        from fastapi.testclient import TestClient

        from clipper.studio.server import create_app

        return TestClient(create_app())

    def post(self, **extra):
        from clipper.learn import log as perf

        perf.write([{"caption": "I started Chad Powers for football #ad", "campaign": "chad-powers-s2",
                     "source_id": "a394", "clip_id": "001_16m12s",
                     "url": "https://www.tiktok.com/@s/video/9?utm_source=key",
                     "views_latest": "1200", "posted_at": "2026-09-20 12:00", **extra}])

    def test_the_app_and_its_routes_load(self, client, data_root):
        add_clip(data_root)
        for path in ("/", "/campaigns/chad-powers-s2", "/clips?status=ready"):
            page = client.get(path)
            assert page.status_code == 200 and '<div id="root">' in page.text
        assert client.get("/api/nope").status_code == 404
        (campaign,) = client.get("/api/campaigns").json()
        assert campaign["name"] == "chad-powers-s2" and campaign["counts"]["ready"] == 1
        assert client.get("/api/settings").json()["auto_post"] == "0"

    def test_the_rating_reasons_come_grouped_from_one_list(self, client):
        from clipper.studio import db

        groups = client.get("/api/reasons").json()
        assert [g["tone"] for g in groups] == ["good", "bad", "edit"]
        offered = {r["key"]: r["label"] for g in groups for r in g["reasons"]}
        assert offered == db.REASONS and offered["weak_hook"] == "Weak opening"

    def test_a_synced_post_makes_a_clip_posted_with_its_earnings(self, client, data_root, campaigns):
        brief = yaml.safe_load((campaigns / "chad-powers-s2.yaml").read_text(encoding="utf-8"))
        brief["reward_per_1k_usd"] = 2.5
        (campaigns / "chad-powers-s2.yaml").write_text(yaml.safe_dump(brief), encoding="utf-8")
        add_clip(data_root)
        self.post(avg_watch_s="6.6")
        data = client.get("/api/campaigns/chad-powers-s2").json()
        (clip,) = data["clips"]
        assert clip["status"] == "posted" and clip["marked"] == "ready"
        (post,) = clip["posts"]
        assert post["url"] == "https://www.tiktok.com/@s/video/9"  # no tracking query
        assert (post["views"], post["avg_watch_s"], post["est_earnings"]) == (1200, 6.6, 3.0)
        assert data["brief"]["required_text"] == "#ad"
        assert data["campaign"]["est_earnings"] == 3.0 and data["campaign"]["to_submit"] == 1

    def test_marking_a_clip_submitted_marks_its_links(self, client, data_root):
        add_clip(data_root)
        self.post()
        (clip,) = client.get("/api/clips").json()
        url = f"/api/clips/{clip['id']}/submitted"
        assert client.put(url, json={"submitted": True}).status_code == 200
        (post,) = client.get("/api/posts").json()
        assert post["submitted_at"]
        assert client.get("/api/clips").json()[0]["status"] == "submitted"
        client.put(url, json={"submitted": False})
        assert client.get("/api/clips").json()[0]["status"] == "posted"

    def test_a_fresh_reels_zero_watch_time_is_not_yet_reported(self, client, data_root):
        from datetime import datetime

        from clipper.learn import log as perf

        add_clip(data_root)
        perf.write([{"caption": "x", "campaign": "chad-powers-s2", "source_id": "a394",
                     "clip_id": "001_16m12s", "platform": "instagram",
                     "url": "https://www.instagram.com/reel/A/", "views_latest": "69",
                     "avg_watch_s": "0", "skip_rate_pct": "0",
                     "posted_at": datetime.now().strftime("%Y-%m-%d %H:%M")}])
        (post,) = client.get("/api/posts").json()
        assert post["settling"] and post["avg_watch_s"] is None and post["skip_rate_pct"] is None

    def test_status_archive_and_settings_changes(self, client, data_root):
        clip_id = add_clip(data_root)
        assert client.patch(f"/api/clips/{clip_id}", json={"status": "skipped"}).status_code == 200
        assert client.patch(f"/api/clips/{clip_id}", json={"status": "nope"}).status_code == 400
        # Captions come from runs; the page cannot rewrite them.
        client.patch(f"/api/clips/{clip_id}", json={"caption": "hacked"})
        assert client.get(f"/api/clips/{clip_id}").json()["caption"].startswith("I started")
        client.patch("/api/campaigns/chad-powers-s2", json={"archived": True, "auto_post": True})
        (campaign,) = client.get("/api/campaigns").json()
        assert campaign["archived"] and campaign["auto_post"] is True
        assert campaign["counts"]["skipped"] == 1
        assert client.put("/api/settings", json={"auto_post": "1"}).json()["auto_post"] == "1"
        assert client.put("/api/settings", json={"nope": "1"}).status_code == 400

    def test_since_you_were_last_here_needs_a_break(self, client, data_root):
        from clipper.studio import db

        add_clip(data_root)
        client.post("/api/visit")
        client.post("/api/visit")
        assert client.get("/api/home").json()["since"] is None  # one session so far
        with db.connect() as con:  # pretend the last activity was an hour ago
            db.set_setting(con, "seen_at", "2026-09-28 09:00:00")
        client.post("/api/visit")
        home = client.get("/api/home").json()
        assert home["since"]["since"] == "2026-09-28 09:00:00"

    def test_videos_stream_with_ranges(self, client, data_root):
        clip_id = add_clip(data_root)
        whole = client.get(f"/media/{clip_id}")
        assert whole.status_code == 200 and whole.headers["content-type"] == "video/mp4"
        part = client.get(f"/media/{clip_id}", headers={"Range": "bytes=0-99"})
        assert part.status_code == 206 and len(part.content) == 100
        assert client.get("/media/999").status_code == 404
