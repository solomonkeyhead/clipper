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


@pytest.mark.parametrize("raw, shown", [
    ("ChadPowers_206_pix_rec709_220_2398fps_hd_20_20260807", "ChadPowers 206"),
    ("Adults ep201", "Adults ep201"),
    ("😂😂😂 STEP DADDIES w/ TRAP DICKEY", "😂😂😂 STEP DADDIES w/ TRAP DICKEY"),
])
def test_source_names_lose_the_delivery_suffix(raw, shown):
    from clipper.studio.server import display_source

    assert display_source(raw) == shown


class TestApi:
    @pytest.fixture
    def client(self, data_root, campaigns):
        from fastapi.testclient import TestClient

        from clipper.studio.server import create_app

        return TestClient(create_app())

    def test_the_page_and_overview_load(self, client, data_root):
        add_clip(data_root)
        assert "Control Center" in client.get("/").text
        overview = client.get("/api/overview").json()
        (campaign,) = overview["campaigns"]
        assert campaign["name"] == "chad-powers-s2" and campaign["counts"]["ready"] == 1
        assert overview["settings"]["auto_post"] == "0"

    def test_a_synced_post_makes_a_clip_posted(self, client, data_root):
        from clipper.learn import log as perf

        add_clip(data_root)
        perf.write([{"caption": "I started Chad Powers for football #ad", "campaign": "chad-powers-s2",
                     "source_id": "a394", "clip_id": "001_16m12s",
                     "url": "https://www.tiktok.com/@s/video/9?utm_source=key",
                     "views_latest": "1200", "avg_watch_s": "6.6"}])
        data = client.get("/api/campaigns/chad-powers-s2").json()
        (clip,) = data["clips"]
        assert clip["status"] == "posted" and clip["marked"] == "ready"
        (post,) = clip["posts"]
        assert post["url"] == "https://www.tiktok.com/@s/video/9"  # no tracking query
        assert (post["views_latest"], post["avg_watch_s"]) == (1200, 6.6)
        assert "#ad" in data["campaign"]["required_text"]

    def test_status_archive_and_auto_post_changes(self, client, data_root):
        clip_id = add_clip(data_root)
        assert client.patch(f"/api/clips/{clip_id}", json={"status": "submitted"}).status_code == 200
        assert client.patch(f"/api/clips/{clip_id}", json={"status": "nope"}).status_code == 400
        client.patch("/api/campaigns/chad-powers-s2", json={"archived": True, "auto_post": True})
        (campaign,) = client.get("/api/overview").json()["campaigns"]
        assert campaign["archived"] and campaign["auto_post"] == 1
        assert campaign["counts"]["submitted"] == 1
        assert client.put("/api/settings", json={"auto_post": "1"}).json()["auto_post"] == "1"

    def test_videos_stream_with_ranges(self, client, data_root):
        clip_id = add_clip(data_root)
        whole = client.get(f"/media/{clip_id}")
        assert whole.status_code == 200 and whole.headers["content-type"] == "video/mp4"
        part = client.get(f"/media/{clip_id}", headers={"Range": "bytes=0-99"})
        assert part.status_code == 206 and len(part.content) == 100
        assert client.get("/media/999").status_code == 404
