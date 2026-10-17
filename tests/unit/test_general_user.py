"""D221: what a general user sees. A Short made in Create is neither footage to clip nor a moment to learn from."""

from __future__ import annotations


def _create_clip(title: str) -> None:
    from clipper.studio import db

    with db.connect() as con:
        db.upsert_clip(con, {"campaign": "my-channel", "source_id": "create", "clip_id": "create-1",
                             "title": title, "file": "my-channel/1.mp4", "caption": "c"})


def test_a_saved_short_is_not_listed_as_footage(data_root, tmp_path, monkeypatch):
    from clipper.studio import server

    downloads = tmp_path / "Downloads"
    downloads.mkdir()
    for name in ("Why does your wet shirt feel cold_ (1).mp4", "episode 204.mov"):
        (downloads / name).write_bytes(b"x")
    monkeypatch.setattr(server, "source_folders", lambda: [downloads])
    _create_clip("Why does your wet shirt feel cold?")
    assert [s["name"] for s in server.list_sources()] == ["episode 204.mov"]


def test_learning_leaves_out_shorts_made_in_create(data_root):
    from fastapi.testclient import TestClient

    from clipper.studio import db
    from clipper.studio.server import create_app

    _create_clip("A Short")
    with db.connect() as con:
        db.update_clip(con, 1, status="posted")
    report = TestClient(create_app()).get("/api/learning").json()
    assert report["rated"] == 0 and report["unrated"] == 0
