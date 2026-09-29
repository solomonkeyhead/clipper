"""Control Center: delete with undo, downloads, uploads and clipping jobs."""

from __future__ import annotations

import time
from types import SimpleNamespace

import pytest
import yaml

from clipper.studio import db


@pytest.fixture
def client(data_root, tmp_path, monkeypatch, valid_campaign_dict):
    from fastapi.testclient import TestClient

    from clipper.studio import server

    campaigns = tmp_path / "campaigns"
    campaigns.mkdir()
    (campaigns / "test-campaign.yaml").write_text(yaml.safe_dump(valid_campaign_dict), encoding="utf-8")
    monkeypatch.setattr(server, "campaigns_dir", lambda: campaigns)
    downloads = tmp_path / "home_downloads"
    downloads.mkdir()
    monkeypatch.setattr(server, "source_folders",
                        lambda: [data_root / "downloads", downloads])
    return TestClient(server.create_app())


def add_clip(data_root, title="the chemistry is insane") -> int:
    path = data_root / "library" / "test-campaign" / "x.mp4"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x00" * 4096)
    with db.connect() as con:
        return db.upsert_clip(con, {"campaign": "test-campaign", "source_id": "s", "clip_id": "001",
                                    "title": title, "file": "test-campaign/x.mp4", "caption": "c"})


class TestDelete:
    def test_delete_hides_the_clip_and_undo_brings_it_back(self, client, data_root):
        clip = add_clip(data_root)
        assert client.delete(f"/api/clips/{clip}").status_code == 200
        assert client.get("/api/clips").json() == []
        assert (data_root / "library" / "test-campaign" / "x.mp4").exists()  # kept 30 days
        client.post(f"/api/clips/{clip}/restore")
        assert [c["id"] for c in client.get("/api/clips").json()] == [clip]

    def test_after_30_days_the_file_goes_to_the_recycle_bin(self, data_root, monkeypatch):
        from clipper.studio import server
        from clipper.utils import recycle

        clip = add_clip(data_root)
        with db.connect() as con:
            con.execute("UPDATE clips SET deleted_at='2020-01-01 00:00' WHERE id=?", (clip,))
        recycled = []
        monkeypatch.setattr(recycle, "recycle", lambda p: recycled.append(p) or True)
        assert server.purge_trash() == 1
        assert recycled and recycled[0].name == "x.mp4"
        with db.connect() as con:
            assert db.clip(con, clip) is None


class TestDownload:
    def test_the_download_is_named_after_the_clip(self, client, data_root):
        clip = add_clip(data_root, title='the chemistry: "insane"?')
        res = client.get(f"/media/{clip}?download=true")
        assert res.status_code == 200
        from urllib.parse import unquote

        disposition = unquote(res.headers["content-disposition"])
        assert "attachment" in disposition and "the chemistry insane - test-campaign.mp4" in disposition


class TestSources:
    def test_videos_in_the_source_folders_are_listed(self, client, tmp_path):
        (tmp_path / "home_downloads" / "ChadPowers_205.mov").write_bytes(b"x" * 10)
        (tmp_path / "home_downloads" / "notes.txt").write_text("no")
        names = [s["name"] for s in client.get("/api/sources").json()]
        assert names == ["ChadPowers_205.mov"]

    def test_an_upload_lands_in_clippers_downloads(self, client, data_root):
        res = client.put("/api/uploads/My%20Episode%204.mp4", content=b"\x00" * 2048)
        assert res.status_code == 200 and res.json()["name"] == "My Episode 4.mp4"
        assert (data_root / "downloads" / "My Episode 4.mp4").stat().st_size == 2048
        assert client.put("/api/uploads/evil.exe", content=b"x").status_code == 400

    def test_a_job_only_takes_a_listed_video(self, client, tmp_path):
        outside = tmp_path / "elsewhere.mp4"
        outside.write_bytes(b"x")
        res = client.post("/api/jobs", json={"campaign": "test-campaign", "source": str(outside)})
        assert res.status_code == 400
        res = client.post("/api/jobs", json={"campaign": "nope", "source": str(outside)})
        assert res.status_code == 400


def test_a_job_runs_the_pipeline_and_reports_progress(client, tmp_path, monkeypatch):
    import logging

    from clipper import runner

    video = tmp_path / "home_downloads" / "ep.mp4"
    video.write_bytes(b"x")

    def fake_run(source, **kwargs):
        log = logging.getLogger("clipper.runner")
        log.info("ingested abc: ep (60s)")
        log.info("selected 2 clip(s) of 2 requested")
        log.info("001_0m10s accepted: pass")
        log.info("002_0m40s accepted: pass")
        return SimpleNamespace(accepted=[1, 2], rejected=[], selection_note="")

    monkeypatch.setattr(runner, "run", fake_run)
    job = client.post("/api/jobs", json={"campaign": "test-campaign", "source": str(video),
                                          "top": 2}).json()
    for _ in range(100):
        (latest,) = client.get("/api/jobs").json()
        if latest["status"] in ("done", "failed"):
            break
        time.sleep(0.05)
    assert latest["id"] == job["id"] and latest["status"] == "done", latest
    assert latest["clips"] == 2 and latest["pct"] == 100 and latest["stage"] == "Made 2 clips"


class TestSubmitted:
    def test_a_clip_is_marked_submitted_and_back(self, client, data_root):
        clip = add_clip(data_root)
        assert client.put(f"/api/clips/{clip}/submitted", json={"submitted": True}).status_code == 200
        assert client.get(f"/api/clips/{clip}").json()["status"] == "submitted"
        client.put(f"/api/clips/{clip}/submitted", json={"submitted": False})
        assert client.get(f"/api/clips/{clip}").json()["status"] == "ready"


def test_other_sites_cannot_change_anything(client, data_root):
    clip = add_clip(data_root)
    res = client.delete(f"/api/clips/{clip}", headers={"Origin": "https://evil.example"})
    assert res.status_code == 403
    assert client.delete(f"/api/clips/{clip}", headers={"Origin": "http://testserver"}).status_code == 200


class TestCampaigns:
    FORM = {"title": "My New Show", "reward_per_1k_usd": 2, "required_hashtags": ["#myshow"]}

    def test_create_edit_and_delete(self, client, tmp_path):
        res = client.post("/api/campaigns", json=self.FORM)
        assert res.status_code == 200 and res.json()["name"] == "my-new-show"
        names = {c["name"]: c["title"] for c in client.get("/api/campaigns").json()}
        assert names["my-new-show"] == "My New Show"
        form = client.get("/api/campaigns/my-new-show/form").json()
        assert client.put("/api/campaigns/my-new-show", json={**form, "title": "Renamed"}).status_code == 200
        assert client.get("/api/campaigns/my-new-show").json()["campaign"]["title"] == "Renamed"
        assert client.post("/api/campaigns", json={"title": "my new show"}).status_code == 400

    def test_a_campaign_with_clips_is_not_deleted(self, client, data_root):
        add_clip(data_root)
        assert client.delete("/api/campaigns/test-campaign").status_code == 400

    def test_reading_a_brief_without_ai_says_so(self, client, monkeypatch):
        from clipper import pipeline

        def no_key(*a, **k):
            raise RuntimeError("GEMINI_API_KEY is not set.")

        monkeypatch.setattr(pipeline, "build_backend", no_key)
        res = client.post("/api/campaigns/read-brief", json={"text": "x" * 100})
        assert res.status_code == 400 and "Settings" in res.json()["detail"]
