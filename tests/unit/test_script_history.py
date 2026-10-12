"""D193: a script's earlier versions are kept for Undo, and a new take sees the one it replaces."""

from __future__ import annotations

from clipper.create import channel, script, store
from clipper.create.script import Beat, Script


def _client():
    from fastapi.testclient import TestClient

    from clipper.studio.server import create_app

    return TestClient(create_app())


def test_an_edit_can_be_undone_twice_over(data_root):
    channel.save(channel.make("physics", "Prof"))
    with channel.use("prof"):
        vid = store.add_video(None, Script(title="t", beats=[Beat(text="One.")]).model_dump())
        store.update_video(vid, status="draft")
        c = _client()
        for words in ("Two.", "Three."):
            c.put(f"/api/create/videos/{vid}/script", json={"script": {"beats": [{"text": words}]}})
        c.put(f"/api/create/videos/{vid}/script", json={"script": {}})   # no change: nothing to undo
        assert c.get("/api/create").json()["videos"][0]["undo"] == "your edit"
        assert c.post(f"/api/create/videos/{vid}/undo").json() == {"undid": "your edit"}
        assert [b["text"] for b in store.video(vid)["script"]["beats"]] == ["Two."]
        c.post(f"/api/create/videos/{vid}/undo")
        assert [b["text"] for b in store.video(vid)["script"]["beats"]] == ["One."]
        assert c.post(f"/api/create/videos/{vid}/undo").status_code == 400
        # Redo (D196): forward again, twice, and a new edit clears what's left to redo
        assert c.get("/api/create").json()["videos"][0]["redo"] is True
        c.post(f"/api/create/videos/{vid}/undo?forward=true")
        assert [b["text"] for b in store.video(vid)["script"]["beats"]] == ["Two."]
        c.put(f"/api/create/videos/{vid}/script", json={"script": {"beats": [{"text": "Four."}]}})
        assert c.post(f"/api/create/videos/{vid}/undo?forward=true").status_code == 400
        c.post(f"/api/create/videos/{vid}/undo")
        assert [b["text"] for b in store.video(vid)["script"]["beats"]] == ["Two."]
        c.post(f"/api/create/videos/{vid}/undo?forward=true")
        assert [b["text"] for b in store.video(vid)["script"]["beats"]] == ["Four."]


def test_a_note_revises_the_last_take_and_no_note_avoids_it():
    last = Script(title="Lips", beats=[Beat(text="Pursed lips make a jet."), Beat(text="Send this to a friend.")])
    revise = script._previous_block(last, "change only the ending")
    assert "Pursed lips make a jet." in revise and "Keep every other line exactly" in revise
    fresh = script._previous_block(last, "")
    assert "reusing none of its lines" in fresh
    assert script._previous_block(None, "x") == ""


def test_any_earlier_take_can_be_compared_and_brought_back(data_root):
    """D199: the takes are listed oldest first, and using one keeps the script it replaces."""
    channel.save(channel.make("physics", "Prof"))
    with channel.use("prof"):
        vid = store.add_video(None, Script(title="t", beats=[Beat(text="One.")]).model_dump())
        store.update_video(vid, status="draft")
        c = _client()
        for words in ("Two.", "Three."):
            c.put(f"/api/create/videos/{vid}/script", json={"script": {"beats": [{"text": words}]}})
        assert c.get("/api/create").json()["videos"][0]["takes"] == 2
        assert [t["lines"] for t in c.get(f"/api/create/videos/{vid}/takes").json()] == [["One."], ["Two."]]
        assert c.post(f"/api/create/videos/{vid}/takes/0/use").json() == {"ok": True}
        assert [b["text"] for b in store.video(vid)["script"]["beats"]] == ["One."]
        assert [t["lines"] for t in c.get(f"/api/create/videos/{vid}/takes").json()][-1] == ["Three."]
        assert c.post(f"/api/create/videos/{vid}/takes/9/use").status_code == 404
