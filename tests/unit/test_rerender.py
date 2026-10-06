"""A clip made again with a new on-screen hook; choosing lines is paid (D90)."""

from __future__ import annotations

import pytest
import yaml
from fastapi.testclient import TestClient

from clipper.studio import db, rerender

AUTH = "Official footage supplied by the campaign on Content Rewards"
HOOKS = ["first line", "second line", "third line"]


@pytest.fixture
def client(data_root, tmp_path, monkeypatch):
    folder = tmp_path / "campaigns"
    folder.mkdir()
    (folder / "plm.yaml").write_text(yaml.safe_dump({"name": "plm", "source_authorization": AUTH,
                                                     "hook_texts": HOOKS, "fallback_captions": ["cap a", "cap b"]}),
                                     encoding="utf-8")
    from clipper.studio import server

    monkeypatch.setattr(server, "campaigns_dir", lambda: folder)
    asked = []
    monkeypatch.setattr(rerender.Rerenders, "submit", lambda self, clip_id, hook: asked.append((clip_id, hook)))
    app = server.create_app()
    app.state.asked = asked
    return TestClient(app)


def add(data_root, clip_id, title, hook, **extra):
    path = data_root / "library" / "plm" / f"{clip_id}.mp4"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x00" * 1024)
    with db.connect() as con:
        return db.upsert_clip(con, {"campaign": "plm", "source_id": "s", "clip_id": clip_id, "title": title,
                                    "hook": hook, "file": f"plm/{clip_id}.mp4", "caption": "cap a  #x", **extra})


def test_new_hook_takes_the_least_used_other_line(client, data_root):
    cid = add(data_root, "c1", "first line", "first line")
    assert client.post(f"/api/clips/{cid}/rerender", json={}).json()["hook"] == "second line"
    assert client.app.state.asked == [(cid, "second line")]


def test_showing_its_title_is_free_but_choosing_is_paid(client, data_root):
    cid = add(data_root, "c1", "second line", "first line")
    with db.connect() as con:
        db.set_setting(con, "plan", "free")
    kept = client.post(f"/api/clips/{cid}/rerender", json={"hook": "second line"})
    assert kept.status_code == 200 and kept.json()["hook"] == "second line"  # the hook asked for, not the next line
    assert client.post(f"/api/clips/{cid}/rerender", json={"hook": "first line"}).json()["hook"] == "first line"
    assert client.post(f"/api/clips/{cid}/rerender", json={"hook": "my own"}).status_code == 402
    assert client.put(f"/api/clips/{cid}/caption", json={"caption": "cap b  #x"}).status_code == 402


def test_posted_clips_keep_their_video(client, data_root):
    cid = add(data_root, "c1", "first line", "first line", status="posted")
    assert client.post(f"/api/clips/{cid}/rerender", json={}).status_code == 400
