"""Several accounts per platform: groups, the viewing switcher, the plan's limit (D89)."""

from __future__ import annotations

import pytest
import yaml
from fastapi.testclient import TestClient

from clipper.learn import log as perf
from clipper.studio import accounts, db

AUTH = "Official footage supplied by the campaign on Content Rewards"


@pytest.fixture
def campaigns(tmp_path, monkeypatch):
    folder = tmp_path / "campaigns"
    folder.mkdir()
    for name, targets in (("movies", ["tiktok", "instagram_reels"]), ("podcast", ["youtube_shorts"])):
        (folder / f"{name}.yaml").write_text(yaml.safe_dump(
            {"name": name, "source_authorization": AUTH, "platform_targets": targets}), encoding="utf-8")
    from clipper.studio import server

    monkeypatch.setattr(server, "campaigns_dir", lambda: folder)
    return folder


def clip(data_root, campaign, clip_id, **extra):
    path = data_root / "library" / campaign / f"{clip_id}.mp4"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x00" * 1024)
    with db.connect() as con:
        return db.upsert_clip(con, {"campaign": campaign, "source_id": "s", "clip_id": clip_id, "title": clip_id,
                                    "file": f"{campaign}/{clip_id}.mp4", "caption": f"caption {clip_id}", **extra})


@pytest.fixture
def client(data_root, campaigns):
    from clipper.studio.server import create_app

    clip(data_root, "movies", "a")                       # ready
    clip(data_root, "podcast", "b")                      # ready
    clip(data_root, "movies", "c", status="posted")      # posted on two accounts
    perf.write([
        {"campaign": "movies", "source_id": "s", "clip_id": "c", "platform": "tiktok", "account": "main",
         "url": "https://www.tiktok.com/@main/video/1", "views_latest": "100"},
        {"campaign": "movies", "source_id": "s", "clip_id": "c", "platform": "tiktok", "account": "second",
         "url": "https://www.tiktok.com/@second/video/2", "views_latest": "50"}])
    return TestClient(create_app())


def ids(res):
    return sorted(c["title"] for c in res.json())


class TestScope:
    def test_all_shows_everything(self, client):
        assert ids(client.get("/api/clips")) == ["a", "b", "c"]

    def test_one_account_shows_its_posts_and_the_campaigns_on_its_platform(self, client):
        res = client.get("/api/clips", params={"scope": "account:tiktok:second"})
        assert ids(res) == ["a", "c"]  # podcast posts only on YouTube
        posted = next(c for c in res.json() if c["title"] == "c")
        assert [p["account"] for p in posted["posts"]] == ["second"]
        assert client.get("/api/home", params={"scope": "account:tiktok:second"}).json()["metrics"]["views"] == 50

    def test_a_group_with_campaigns_shows_only_them(self, client):
        gid = client.post("/api/account-groups", json={"name": "Movies", "members": ["tiktok:main"],
                                                       "campaigns": ["movies"]}).json()["id"]
        res = client.get("/api/clips", params={"scope": f"group:{gid}"})
        assert ids(res) == ["a", "c"]
        assert [p["account"] for p in client.get("/api/posts", params={"scope": f"group:{gid}"}).json()] == ["main"]

    def test_an_unknown_scope_shows_everything(self, client):
        assert ids(client.get("/api/clips", params={"scope": "group:999"})) == ["a", "b", "c"]


class TestGroups:
    def test_make_rename_and_delete(self, client):
        made = client.post("/api/account-groups", json={"name": "Movies", "members": ["TikTok:Main"]}).json()
        assert client.get("/api/account-groups").json()[0]["members"] == ["tiktok:main"]  # keys in lower case
        assert client.post("/api/account-groups", json={"name": "movies", "members": ["x:a"]}).status_code == 400
        assert client.post("/api/account-groups", json={"id": made["id"], "name": "Film", "members": ["x:a"]}).status_code == 200
        assert [g["name"] for g in client.get("/api/account-groups").json()] == ["Film"]
        assert client.delete(f"/api/account-groups/{made['id']}").status_code == 200
        assert client.get("/api/account-groups").json() == []

    def test_a_group_needs_a_name_and_an_account(self, data_root):
        with pytest.raises(ValueError):
            accounts.save_group(" ", ["x:a"], [])
        with pytest.raises(ValueError):
            accounts.save_group("Movies", [], [])

    def test_groups_are_part_of_pro(self, client):
        with db.connect() as con:
            db.set_setting(con, "plan", "free")
        assert client.post("/api/account-groups", json={"name": "Movies", "members": ["x:a"]}).status_code == 402


class TestPlanLimit:
    def test_a_second_account_on_the_free_plan_is_undone(self, data_root, monkeypatch):
        from clipper.x import api as x_api

        folder = x_api.accounts_dir()
        folder.mkdir(parents=True)
        (folder / "first.json").write_text("{}", encoding="utf-8")
        before = set(x_api.account_files())
        (folder / "second.json").write_text("{}", encoding="utf-8")
        with db.connect() as con:
            db.set_setting(con, "plan", "free")
        assert "one X account" in accounts.undo_if_over("x", before)
        assert [p.stem for p in x_api.account_files()] == ["first"]

    def test_the_first_account_and_pro_are_never_undone(self, data_root):
        from clipper.x import api as x_api

        folder = x_api.accounts_dir()
        folder.mkdir(parents=True)
        (folder / "first.json").write_text("{}", encoding="utf-8")
        with db.connect() as con:
            db.set_setting(con, "plan", "free")
        assert accounts.undo_if_over("x", set()) is None
        with db.connect() as con:
            db.set_setting(con, "plan", "pro")
        (folder / "second.json").write_text("{}", encoding="utf-8")
        assert accounts.undo_if_over("x", {folder / "first.json"}) is None
