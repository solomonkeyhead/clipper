"""Control Center: delete with undo, downloads, uploads and clipping jobs."""

from __future__ import annotations

import time
from types import SimpleNamespace
from typing import ClassVar

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
                                          "mode": "top", "top": 2}).json()
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
    FORM: ClassVar[dict] = {"title": "My New Show", "reward_per_1k_usd": 2, "required_hashtags": ["#myshow"]}

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


class TestSetup:
    def test_keys_are_saved_to_env_and_never_shown(self, client, tmp_path, monkeypatch):
        from clipper.studio import setup

        env = tmp_path / ".env"
        monkeypatch.setattr(setup, "env_path", lambda: env)
        monkeypatch.delenv("TIKTOK_CLIENT_KEY", raising=False)
        res = client.put("/api/setup/keys", json={"TIKTOK_CLIENT_KEY": "abc123"})
        assert res.status_code == 200 and res.json()["keys"]["TIKTOK_CLIENT_KEY"] is True
        assert "abc123" not in res.text
        assert "TIKTOK_CLIENT_KEY=abc123" in env.read_text()
        assert client.put("/api/setup/keys", json={"PATH": "x"}).status_code == 400
        monkeypatch.delenv("TIKTOK_CLIENT_KEY", raising=False)

    def test_accounts_are_listed_and_disconnected(self, client, data_root):
        import json
        import time as _time

        folder = data_root / "instagram" / "accounts"
        folder.mkdir(parents=True)
        for name in ("one", "two"):
            (folder / f"{name}.json").write_text(json.dumps(
                {"access_token": "t", "obtained_at": _time.time(), "username": name}))
        handles = [a["handle"] for a in client.get("/api/accounts").json()]
        assert handles == ["one", "two"]
        assert client.delete("/api/accounts/instagram/one").status_code == 200
        assert [a["handle"] for a in client.get("/api/accounts").json()] == ["two"]
        assert client.delete("/api/accounts/instagram/nope").status_code == 404


def test_tiktok_connect_hands_the_page_the_consent_link(client, monkeypatch):
    from clipper.tiktok import api

    def fake_login(*, open_browser, **kwargs):
        open_browser("https://www.tiktok.com/v2/auth/authorize/?x=1")
        time.sleep(0.2)
        return {"display_name": "Second Account"}

    monkeypatch.setattr(api, "login", fake_login)
    started = client.post("/api/accounts/tiktok/connect").json()
    assert started["state"] == "waiting" and started["url"].startswith("https://www.tiktok.com/")
    for _ in range(50):
        state = client.get("/api/accounts/tiktok/connect").json()
        if state["state"] != "waiting":
            break
        time.sleep(0.05)
    assert state == {"state": "done", "message": "Second Account", "url": started["url"]}


class TestModes:
    def run_job(self, client, body):
        for _ in range(100):
            (latest, *_rest) = client.get("/api/jobs").json()
            if latest["status"] in ("done", "failed"):
                return latest
            time.sleep(0.05)
        return latest

    def test_auto_lets_the_quality_bar_decide_up_to_the_campaign_max(self, client, tmp_path, monkeypatch):
        from clipper import runner

        seen = {}

        def fake_run(source, **kwargs):
            seen.update(kwargs)
            return SimpleNamespace(accepted=[1, 2, 3], rejected=[],
                                   selection_note="stopped with 3: 20 below the absolute quality bar")

        monkeypatch.setattr(runner, "run", fake_run)
        video = tmp_path / "home_downloads" / "ep.mp4"
        video.write_bytes(b"x")
        job = client.post("/api/jobs", json={"campaign": "test-campaign", "source": str(video)}).json()
        assert job["mode"] == "auto" and job["top"] is None
        done = self.run_job(client, job)
        assert seen["top"] == 8  # the campaign's max_clips_per_source default
        assert done["clips"] == 3 and "quality bar" in done["message"]

    def test_manual_cuts_the_given_ranges(self, client, tmp_path, monkeypatch):
        from clipper import runner

        seen = {}

        def fake_cut(source, ranges, **kwargs):
            seen["ranges"] = ranges
            return SimpleNamespace(accepted=[1, 2], rejected=[], selection_note="ranges chosen by hand")

        monkeypatch.setattr(runner, "cut", fake_cut)
        video = tmp_path / "home_downloads" / "ep.mp4"
        video.write_bytes(b"x")
        bad = client.post("/api/jobs", json={"campaign": "test-campaign", "source": str(video),
                                             "mode": "manual", "ranges": [["1:00", "0:50"]]})
        assert bad.status_code == 400
        job = client.post("/api/jobs", json={"campaign": "test-campaign", "source": str(video),
                                             "mode": "manual",
                                             "ranges": [["24:45", "26:05"], ["90", "120.5"]]}).json()
        assert self.run_job(client, job)["clips"] == 2
        assert seen["ranges"] == [(1485.0, 1565.0), (90.0, 120.5)]


class TestRatings:
    def test_a_rating_is_stored_and_shown(self, client, data_root):
        clip = add_clip(data_root)
        res = client.put(f"/api/clips/{clip}/rating", json={"rating": 2, "reasons": ["weak_hook"]})
        assert res.status_code == 200
        got = client.get(f"/api/clips/{clip}").json()
        assert got["rating"] == 2 and got["reasons"] == ["weak_hook"]
        assert client.put(f"/api/clips/{clip}/rating", json={"rating": 9}).status_code == 400
        assert client.put(f"/api/clips/{clip}/rating", json={"rating": 3, "reasons": ["x"]}).status_code == 400
        report = client.get("/api/learning").json()
        assert report["rated"] == 1 and report["reasons"][0]["key"] == "weak_hook"
