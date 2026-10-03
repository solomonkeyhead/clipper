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


# ---------- cancelling a build, footage with no model to judge it (D122) ----------

def test_a_build_can_be_cancelled(client):
    from clipper.create import store
    from clipper.studio import create_api

    vid = store.add_video(None, Script(title="t", beats=[Beat(text="One two three four.")]).model_dump())
    assert client.post(f"/api/create/videos/{vid}/cancel").status_code == 400          # not building
    # stuck after Clipper was closed mid-build: put back at once
    store.update_video(vid, status="building")
    assert client.post(f"/api/create/videos/{vid}/cancel").json() == {"stopping": False}
    assert store.video(vid)["status"] == "failed" and "cancelled" in store.video(vid)["error"]
    # a video built before goes back to its finished version
    store.update_video(vid, status="voiced", clip_id=42)
    client.post(f"/api/create/videos/{vid}/cancel")
    assert store.video(vid)["status"] == "built"
    # a running build is asked to stop, and stops at its next step; it can't be deleted meanwhile
    store.update_video(vid, status="voiced")
    create_api._running.add(vid)
    try:
        assert client.post(f"/api/create/videos/{vid}/cancel").json() == {"stopping": True}
        assert next(v for v in client.get("/api/create").json()["videos"] if v["id"] == vid)["cancelling"] is True
    finally:
        create_api._running.discard(vid)
    create_api._work(vid, lambda *a: None)          # it sees the request before doing anything
    assert store.video(vid)["status"] == "built" and vid not in create_api.cancelled


def test_a_video_can_always_be_deleted_and_never_stays_stuck(client, monkeypatch):
    from clipper.create import store
    from clipper.studio import create_api
    from clipper.studio.server import create_app

    vid = store.add_video(None, Script(title="t", beats=[Beat(text="One two three four.")]).model_dump())
    for status in ("draft", "approved", "failed", "built"):
        other = store.add_video(None, Script(title="t", beats=[Beat(text="One two three four.")]).model_dump())
        store.update_video(other, status=status)
        assert client.delete(f"/api/create/videos/{other}").status_code == 200 and store.video(other) is None
    # deleted while its build runs: hidden at once, gone when the build lets go
    store.update_video(vid, status="building")
    create_api._running.add(vid)
    try:
        assert client.delete(f"/api/create/videos/{vid}").json() == {"ok": True, "after_stop": True}
        assert all(v["id"] != vid for v in client.get("/api/create").json()["videos"])
    finally:
        create_api._running.discard(vid)
    create_api._work(vid, lambda *a: None)
    assert store.video(vid) is None and vid not in create_api.doomed
    # left "building" when Clipper closed: put back when it starts again
    stuck = store.add_video(None, Script(title="t", beats=[Beat(text="One two three four.")]).model_dump())
    store.update_video(stuck, status="building")
    create_app()
    assert store.video(stuck)["status"] == "failed" and "closed during the build" in store.video(stuck)["error"]


def test_footage_by_search_words_when_no_model_can_look(monkeypatch):
    from clipper.create import stock

    hits = [{"id": 1, "tags": "lipstick, makeup", "duration": 9, "width": 1920, "height": 1080},
            {"id": 2, "tags": "studio, microphone, music, recording", "duration": 9, "width": 1920, "height": 1080},
            {"id": 3, "tags": "podcast", "duration": 9, "width": 1080, "height": 1920}]
    assert stock.by_words(["microphone recording studio"], hits)["id"] == 2
    assert stock.by_words(["human ear close up"], hits) is None                        # never a lipstick for an ear
    assert stock.by_words(["podcast"], hits)["id"] == 3
    assert stock.by_words(["the of"], hits) is None

    def no_one(*a, **k):
        raise stock._NoAnswer

    monkeypatch.setattr(stock, "search", lambda q: hits)
    monkeypatch.setattr(stock, "_judge", no_one)
    stock.unjudged.clear()
    got = stock.choose(["microphone recording studio"], 3.0, set(), sentence="That is the only route a microphone gets.")
    assert got["id"] == 2 and got["center"] is None and stock.unjudged == ["That is the only route a microphone gets."]
    # the judge saying "nothing good enough" is still respected: no fallback then
    monkeypatch.setattr(stock, "_judge", lambda *a, **k: (None, []))
    assert stock.choose(["microphone recording studio"], 3.0, set(), sentence="x") is None


# ---------- a drawing held over several sentences (D124) ----------

def _held_script():
    d = Visual(kind="diagram", template="sketch", idea="a face", manual=True)
    return Script(title="t", beats=[
        Beat(text="Why does it sound odd to you?", visual=Visual(kind="stock", query="voice")),
        Beat(text="Sound reaches your ears two ways.", visual=d),
        Beat(text="Route one is air, round the cheek.", visual=Visual(kind="stock", query="air", hold=True)),
        Beat(text="Route two is bone, through the jaw.", visual=Visual(kind="stock", query="bone", hold=True)),
        Beat(text="So you hear more bass than us.", visual=Visual(kind="stock", query="bass"))])


def test_held_sentences_join_the_drawing_before_them():
    s = scripts.tidy(_held_script())
    assert scripts.spans(s) == [[0], [1, 2, 3], [4]]
    # a hold after footage, or on the first sentence, is dropped; long chains stop at MAX_HOLD
    first = scripts.tidy(Script(title="t", beats=[Beat(text="One two three four.", visual=Visual(kind="stock", query="x", hold=True))]))
    assert not first.beats[0].visual.hold
    after_stock = scripts.tidy(_held_script().model_copy(update={"beats": [
        *_held_script().beats[:1], _held_script().beats[2]]}))
    assert not after_stock.beats[1].visual.hold
    long = _held_script().model_copy(update={"beats": [*_held_script().beats[:2],
                                                       *[_held_script().beats[2]] * (scripts.MAX_HOLD + 2)]})
    held = [b.visual.hold for b in scripts.tidy(long).beats]
    assert held.count(True) == scripts.MAX_HOLD
    # a held picture counts once towards "never three diagrams in a row"
    three = scripts.tidy(Script(title="t", beats=[
        Beat(text="Opening line goes here now.", visual=Visual(kind="stock", query="x")),
        Beat(text="A diagram goes here now.", visual=Visual(kind="diagram", template="forces", labels=["a"])),
        Beat(text="Held on goes here now.", visual=Visual(kind="stock", query="x", hold=True)),
        Beat(text="Another diagram goes here.", visual=Visual(kind="diagram", template="forces", labels=["b"]))]))
    assert three.beats[3].visual.kind == "diagram"


def test_a_held_drawing_is_one_shot_with_every_word(tmp_path, monkeypatch):
    from clipper.create import build
    from clipper.create.voice import TimedWord, Timings

    s = scripts.tidy(_held_script())
    shots = []

    def fake(i, beat, visual, seconds, said, script_, work, used, tag=""):
        shots.append((i, round(seconds, 2), [w for _, w in said]))
        return [tmp_path / f"{i}.mp4"]

    monkeypatch.setattr(build, "_planned", fake)
    spans = [(0.0, 2.0), (2.0, 4.0), (4.0, 7.0), (7.0, 10.0), (10.0, 12.0)]
    words = [TimedWord(text=w, start=a + 0.1 * k, end=a + 0.1 * k + 0.05)
             for b, (a, _) in zip(s.beats, spans, strict=True) for k, w in enumerate(b.text.split())]
    build.shots(s, Timings(words=words, beats=spans, duration=12.0, matched=1.0), tmp_path)
    assert [x[:2] for x in shots] == [(0, 2.0), (1, 8.0), (4, 2.0)]
    assert "bone," in shots[1][2] and "air," in shots[1][2]       # the drawing hears all three sentences


def test_a_part_on_a_late_word_arrives_late_but_is_seen():
    from clipper.create.diagrams import CUES, stage

    CUES.set((None, 9.0, 11.9))
    assert stage(8.0, 12.0, 1, 3, label=1) == 0.0 and stage(9.6, 12.0, 1, 3, label=1) == 1.0   # on its word
    assert stage(11.5, 12.0, 2, 3, label=2) == 1.0          # said at the very end: still up for the last 1.2 s
