"""The user's own script in Create (D120): splitting, tidying, planning pictures, the endpoints."""

from __future__ import annotations

import json

import pytest

from clipper.create import script as scripts
from clipper.create.ai import CreateError
from clipper.create.script import Beat, Script, Visual


def test_sentences_are_split_the_way_a_speaker_would():
    text = ("Why does your voice sound odd? It travels through bone, e.g. your skull. Dr. Lee measured 3.5 times "
            "faster speed in bone! So it sounds deeper to you.")
    beats = scripts.split_beats(text)
    assert beats[0] == "Why does your voice sound odd?"
    assert any(b.startswith("It travels") and "e.g. your skull." in b for b in beats)   # "e.g." doesn't end it
    assert any("Dr. Lee measured 3.5 times" in b for b in beats)                      # nor "Dr." nor "3.5"
    assert beats[-1].endswith("deeper to you.")


def test_line_breaks_are_the_users_own_cuts_and_list_marks_go():
    assert scripts.split_beats("First line here ok\n- second line goes here\n\n3. third line goes here\r\n4) fourth") == [
        "First line here ok", "second line goes here", "third line goes here", "fourth"]


def test_fragments_join_and_long_sentences_split_at_a_comma():
    assert scripts.split_beats("Yes. It really does work like that, you know.") == ["Yes. It really does work like that, you know."]
    long = ("Sound is a pressure wave that moves through air, water and solid bone at very different speeds, "
            "which is why a recording of your own voice always sounds strange to you")
    parts = scripts.split_beats(long)
    assert len(parts) == 2 and parts[0].endswith(",") and all(len(p.split()) >= 6 for p in parts)
    assert scripts.split_beats("One short.") == ["One short."]
    assert scripts.split_beats("   \n\n  ") == []


def test_from_text_keeps_the_words_and_refuses_the_impossible():
    s = scripts.from_text("", "Why is the sky blue? Because air scatters short waves more than long ones.", "d", ["#sky", " "])
    assert s.title == "Why is the sky blue" and s.beats[0].text == "Why is the sky blue?"
    assert all(b.visual.kind == "stock" and b.visual.query for b in s.beats) and s.hashtags == ["#sky"]
    with pytest.raises(CreateError, match="write the script"):
        scripts.from_text("t", "   ")
    with pytest.raises(CreateError, match="too long"):
        scripts.from_text("t", "\n".join(["word " * 12] * 40))


def test_a_manual_pick_survives_tidy_and_replanning(monkeypatch):
    card = Visual(kind="diagram", template="card", title="Bone", manual=True)
    drawing = Visual(kind="diagram", template="sketch", idea="a skull with arrows", manual=True)
    s = Script(title="t", beats=[Beat(text="First sentence goes here today.", visual=card),
                                 Beat(text="Second sentence goes here today.", visual=drawing),
                                 Beat(text="Third sentence goes here today.", visual=Visual(kind="stock", query="sound"))])
    tidy = scripts.tidy(s)
    assert tidy.beats[0].visual.kind == "diagram" and tidy.beats[0].visual.template == "card"   # a chalk card may open
    assert tidy.beats[1].visual.idea == "a skull with arrows"
    # an unchosen diagram first is still turned into footage, as before
    auto = scripts.tidy(Script(title="t", beats=[Beat(text="First sentence here today ok.", visual=Visual(kind="diagram", template="forces"))]))
    assert auto.beats[0].visual.kind == "stock"

    plan = Script(title="t", beats=[Beat(text=b.text, visual=Visual(kind="stock", query=f"new {i}", queries=[f"new {i}"]))
                                    for i, b in enumerate(s.beats)])
    monkeypatch.setattr(scripts, "ask", lambda *a, **k: plan.model_dump_json())
    monkeypatch.setattr(scripts, "check", lambda sc: scripts.Review(ok=True, problems=[]))
    monkeypatch.setattr(scripts, "_sketched", lambda sc, note: (sc, note))
    fresh, _ = scripts.replan(s)
    assert fresh.beats[0].visual.template == "card" and fresh.beats[1].visual.manual
    assert fresh.beats[2].visual.query == "new 2"        # the unchosen one is planned


@pytest.fixture
def client(data_root):
    from fastapi.testclient import TestClient

    from clipper.studio.server import create_app

    return TestClient(create_app())


def test_endpoints_for_a_script_of_your_own(client, monkeypatch):
    from clipper.create import store

    made = client.post("/api/create/videos", json={"title": "Bone", "text": "Sound goes through bone. It sounds deeper to you."})
    assert made.status_code == 200
    row = store.video(made.json()["id"])
    assert row["topic_id"] is None and row["status"] == "draft" and row["check_notes"] == "Written by you."
    assert [b["text"] for b in row["script"]["beats"]] == ["Sound goes through bone.", "It sounds deeper to you."] or len(row["script"]["beats"]) == 1
    assert client.post("/api/create/videos", json={"text": ""}).status_code == 400

    # a failed plan never loses the writing
    def down(*a, **k):
        raise CreateError("Claude isn't available right now")

    monkeypatch.setattr(scripts, "replan", down)
    kept = client.post("/api/create/videos", json={"text": "Sound goes through bone. It sounds deeper to you.", "plan": True})
    assert kept.status_code == 200
    assert "weren't planned" in store.video(kept.json()["id"])["check_notes"]
    assert client.post(f"/api/create/videos/{kept.json()['id']}/plan").status_code == 400

    # another take would overwrite their words: refused
    assert client.post(f"/api/create/videos/{row['id']}/rewrite").status_code == 400

    # the check on its own
    monkeypatch.setattr(scripts, "check", lambda sc: scripts.Review(ok=False, problems=["Bone is not faster."]))
    res = client.post(f"/api/create/videos/{row['id']}/check")
    assert res.status_code == 200 and "Bone is not faster." in store.video(row["id"])["check_notes"]

    ok = Script(title="Bone", beats=[Beat(text="Sound goes through bone.", visual=Visual(kind="stock", query="bone")),
                                     Beat(text="It sounds deeper to you.", visual=Visual(kind="stock", query="ear"))])
    monkeypatch.setattr(scripts, "replan", lambda sc: (ok, "Pictures planned again."))
    assert client.post(f"/api/create/videos/{row['id']}/plan").status_code == 200
    assert store.video(row["id"])["check_notes"] == "Pictures planned again."
    store.update_video(row["id"], status="building")
    assert client.post(f"/api/create/videos/{row['id']}/plan").status_code == 409
    assert json.loads(json.dumps(store.video(row["id"])["script"]))["title"] == "Bone"


def test_the_ready_made_script_is_sound(client):
    """The bundled script reads right, its drawings are on the board, and it can be started."""
    from clipper.create import store
    from clipper.create.diagrams import frame

    listed = client.get("/api/create/ready").json()
    assert [r["name"] for r in listed] == ["voice-on-a-recording"]
    assert listed[0]["title"] == "Why does your voice sound so different on a recording?" and 80 <= listed[0]["words"] <= 125
    made = client.post("/api/create/ready/voice-on-a-recording")
    assert made.status_code == 200
    row = store.video(made.json()["id"])
    script = Script.model_validate(row["script"])
    assert row["topic_id"] is None and len(script.beats) == 9 and len(script.hashtags) == 3
    assert all(len(b.text.split()) <= 16 for b in script.beats) and len(script.beats[0].text.split()) <= 14
    for b in script.beats:
        assert b.emphasis, b.text                         # a highlight word that is really in the sentence
        if b.visual.kind == "stock":
            assert b.visual.queries and b.visual.card
        elif b.visual.template == "sketch":
            marks = b.visual.sketch.marks
            assert len(marks) >= 4 and all(30 <= v <= 970 for m in marks if m.kind != "text"
                                           for v in (m.xy[:2] if m.kind == "circle" else m.xy))
            frame(b.visual, 4.0, 4.0)                      # draws without error
    assert client.post("/api/create/ready/nope").status_code == 404
