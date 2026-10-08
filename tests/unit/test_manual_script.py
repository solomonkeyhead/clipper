"""The user's own script in Create (D120): splitting, tidying, planning pictures, the endpoints."""

from __future__ import annotations

import json

import pytest

from clipper.create import script as scripts
from clipper.create.ai import CreateError
from clipper.create.script import Beat, Script, Visual


@pytest.fixture(autouse=True)
def _physics_channel(data_root):
    """These tests are about the physics channel's ready-made script and archive (D146)."""
    from clipper.create import channel

    channel.save(channel.make("physics", "German Professor", "@German.Professor"))


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
    assert s.title == "Why is the sky blue?" and s.beats[0].text == "Why is the sky blue?"
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

    def fake(i, beat, visual, seconds, said, script_, work, used, tag="", pre=None, at=None):
        shots.append((i, round(seconds, 2), [w for _, w in said]))
        return [tmp_path / f"{i}.mp4"]

    monkeypatch.setattr(build, "_planned", fake)
    monkeypatch.setattr(build, "_loop_back", lambda made, *a: made)
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


# ---------- reviewing a built video, picture by picture (D125) ----------

def test_a_rebuild_keeps_the_footage_it_picked_and_turns_down_what_was_refused(tmp_path, monkeypatch):
    from clipper.create import build, stock

    monkeypatch.setattr(build, "HOOK_WORDS", 0)   # not about the quicker opening shots (D156)

    beat = Beat(text="Sound goes through bone here.", visual=Visual(kind="stock", query="bone"))
    s = Script(title="t", beats=[beat])
    monkeypatch.setattr(build, "_stock_shot", lambda src, part, out, *a, **k: out)
    monkeypatch.setattr(build, "_too_dark", lambda clip: False)
    monkeypatch.setattr(stock, "fetch", lambda hit: tmp_path / "x.mp4")
    picks = iter([{"id": "a", "url": "u", "center": 0.3}, {"id": "b", "url": "u", "center": 0.6}])
    asked = []

    def choose(queries, part, used, sentence="", context="", good_enough=7):
        asked.append(set(used))
        return next(picks)

    monkeypatch.setattr(stock, "choose", choose)
    build.chosen.clear()
    build._planned(0, beat, beat.visual, 2.0, [], s, tmp_path, set())
    kept = build.remember(s)
    assert kept.beats[0].visual.picked[0]["id"] == "a"
    # built again: the same clip, no judging
    build.chosen.clear()
    build._planned(0, kept.beats[0], kept.beats[0].visual, 3.0, [], kept, tmp_path, set())
    assert len(asked) == 1 and build.remember(kept).beats[0].visual.picked[0]["id"] == "a"
    # "new footage": the old one is turned down and another is chosen
    v = kept.beats[0].visual.model_copy(update={"picked": [], "avoid": ["a"], "redo": True})
    build.chosen.clear()
    build._planned(0, kept.beats[0], v, 3.0, [], kept, tmp_path, set())
    assert "a" in asked[-1] and build.chosen[0]["picked"][0]["id"] == "b"


def test_asking_for_a_new_picture_and_undoing_it(client):
    from clipper.create import store

    s = Script(title="t", beats=[
        Beat(text="First sentence of it here.", visual=Visual(kind="stock", query="x", picked=[{"id": "p1", "url": "u"}])),
        Beat(text="A drawing for this one.", visual=Visual(kind="diagram", template="sketch", idea="a face")),
        Beat(text="Held on the drawing too.", visual=Visual(kind="stock", query="y", hold=True))])
    vid = store.add_video(None, s.model_dump())
    store.update_video(vid, status="built")
    base = f"/api/create/videos/{vid}/redo"
    assert client.post(base, json={"beat": 1, "want": "footage", "note": "man with headphones"}).status_code == 200
    v = Script.model_validate(store.video(vid)["script"]).beats[0].visual
    assert v.redo and v.avoid == ["p1"] and not v.picked and v.queries[0] == "man with headphones" and v.previous
    assert client.post(base, json={"beat": 1, "want": "undo"}).status_code == 200
    v = Script.model_validate(store.video(vid)["script"]).beats[0].visual
    assert not v.redo and v.picked[0]["id"] == "p1" and v.previous is None
    assert client.post(base, json={"beat": 1, "want": "undo"}).status_code == 400            # nothing to undo
    # a held sentence asking for its own drawing stops holding
    assert client.post(base, json={"beat": 3, "want": "drawing", "note": "an ear"}).status_code == 200
    v = Script.model_validate(store.video(vid)["script"]).beats[2].visual
    assert v.kind == "diagram" and v.template == "sketch" and v.idea == "an ear" and not v.hold and v.sketch is None
    for bad in ({"beat": 9, "want": "footage"}, {"beat": 1, "want": "music"}, {"beat": "x", "want": "footage"}):
        assert client.post(base, json=bad).status_code == 400
    store.update_video(vid, status="building")
    assert client.post(base, json={"beat": 1, "want": "footage"}).status_code == 409


def test_the_page_gets_the_pictures_in_order_and_a_rebuild_reuses_the_timing(client, monkeypatch):
    from clipper.create import build, store, voice
    from clipper.studio import create_api

    s = scripts.tidy(_held_script())
    vid = store.add_video(None, s.model_dump())
    spans_ = [(0.0, 2.0), (2.0, 4.0), (4.0, 7.0), (7.0, 10.0), (10.0, 12.0)]
    store.update_video(vid, status="built", voice="v.mp3",
                       timings={"words": [], "beats": spans_, "duration": 12.0, "matched": 1.0})
    shots = next(v for v in client.get("/api/create").json()["videos"] if v["id"] == vid)["shots"]
    assert shots == [{"beats": [1], "start": 0.0, "end": 2.0}, {"beats": [2, 3, 4], "start": 2.0, "end": 10.0},
                     {"beats": [5], "start": 10.0, "end": 12.0}]
    built = []
    monkeypatch.setattr(voice, "heard", lambda path: (_ for _ in ()).throw(AssertionError("timed again")))
    monkeypatch.setattr(build, "build", lambda video_id, progress=None: built.append(video_id))
    create_api._work(vid, lambda *a: None)
    assert built == [vid]


# ---------- D126: the wrong part changing, and slow rebuilds ----------

def test_shots_made_before_are_reused_not_made_again(tmp_path, monkeypatch):
    from clipper.create import build, diagrams

    made = []

    def render(v, seconds, out, words=None, frames=None):
        made.append(seconds)
        out.write_bytes(b"shot")
        return out

    monkeypatch.setattr(diagrams, "render", render)
    monkeypatch.setattr(build, "shot_cache", tmp_path / "shots")
    v = Visual(kind="diagram", template="card", title="Bone")
    beat = Beat(text="Bone carries the bass.", visual=v)
    s = Script(title="t", beats=[beat])
    first = build._planned(0, beat, v, 3.0, [(0.1, "Bone")], s, tmp_path, set())
    again = build._planned(0, beat, v.model_copy(update={"redo": False, "picked": []}), 3.0, [(0.1, "Bone")], s, tmp_path, set())
    assert first == again and made == [3.0]                 # the same picture: kept, not drawn again
    build._planned(0, beat, v, 3.5, [(0.1, "Bone")], s, tmp_path, set())
    assert made == [3.0, 3.5]                                # a different length: made anew


def test_asked_for_footage_and_none_fits_keeps_what_it_had(tmp_path, monkeypatch):
    from clipper.create import build, diagrams, stock

    drawing = Visual(kind="diagram", template="card", title="Friend")
    asked = drawing.model_copy(update={"kind": "stock", "queries": ["friend"], "query": "friend", "redo": True,
                                       "previous": drawing.model_dump()})
    beat = Beat(text="Everyone else hears the air.", visual=asked)
    s = Script(title="t", beats=[beat])
    levels = []
    monkeypatch.setattr(stock, "choose", lambda *a, good_enough=7, **k: levels.append(good_enough))
    monkeypatch.setattr(build, "_fallback", lambda *a, **k: (_ for _ in ()).throw(AssertionError("drew instead")))
    monkeypatch.setattr(diagrams, "render", lambda v, seconds, out, words=None, frames=None: out)
    build.chosen.clear()
    build.picture_notes.clear()
    build._planned(0, beat, asked, 2.0, [], s, tmp_path, set())
    assert levels == [build.ASKED_GOOD_ENOUGH]               # a looser match is fine when asked for
    kept = build.remember(s).beats[0].visual
    assert kept.kind == "diagram" and kept.title == "Friend" and not kept.redo
    assert "kept what it had" in build.picture_notes[0]


def test_a_long_sentence_gets_a_different_clip_for_each_part(tmp_path, monkeypatch):
    from clipper.create import build, stock

    monkeypatch.setattr(build, "HOOK_WORDS", 0)   # not about the quicker opening shots (D156)

    pool = [{"id": n, "url": "u"} for n in ("a", "b", "c")]
    monkeypatch.setattr(stock, "choose", lambda q, part, used, **k: next(h for h in pool if h["id"] not in used))
    monkeypatch.setattr(build, "_stock_shot", lambda src, part, out, *a, **k: out)
    monkeypatch.setattr(build, "_too_dark", lambda clip: False)
    monkeypatch.setattr(stock, "fetch", lambda hit: tmp_path / "x.mp4")
    v = Visual(kind="stock", query="sound")
    beat = Beat(text="A long held explanation goes here.", visual=v)
    build.chosen.clear()
    shots = build._planned(0, beat, v, 12.0, [], Script(title="t", beats=[beat]), tmp_path, set())
    assert len(shots) == 3 and [h["id"] for h in build.chosen[0]["picked"]] == ["a", "b", "c"]


def test_a_failed_rebuild_keeps_the_video_built_before(client, monkeypatch):
    """D127: a rebuild that fails leaves the finished video and its review in place, with the reason."""
    from clipper.create import build, store
    from clipper.studio import create_api

    vid = store.add_video(None, Script(title="t", beats=[Beat(text="One two three four.")]).model_dump())
    store.update_video(vid, status="voiced", voice="v.mp3", clip_id=7,
                       timings={"words": [], "beats": [(0.0, 2.0)], "duration": 2.0, "matched": 1.0})
    monkeypatch.setattr(build, "build", lambda video_id, progress=None: (_ for _ in ()).throw(RuntimeError("no stock key")))
    create_api._work(vid, lambda *a: None)
    row = store.video(vid)
    assert row["status"] == "built" and row["error"] == "no stock key" and row["clip_id"] == 7
    # never built before: it fails as before
    other = store.add_video(None, Script(title="t", beats=[Beat(text="One two three four.")]).model_dump())
    store.update_video(other, status="voiced", voice="v.mp3",
                       timings={"words": [], "beats": [(0.0, 2.0)], "duration": 2.0, "matched": 1.0})
    create_api._work(other, lambda *a: None)
    assert store.video(other)["status"] == "failed"


def test_new_footage_on_a_part_with_a_number_id_keeps_the_script_readable(client):
    """D128: Pixabay's ids are numbers. Turning one down made the saved script unreadable, so the
    parts list came back empty (the section "closed and wouldn't open") and a rebuild would fail."""
    from clipper.create import store

    s = Script(title="t", beats=[Beat(text="Your skull has been flattering you.", visual=Visual(
        kind="stock", query="mirror", picked=[{"id": 4000008, "url": "u", "center": 0.5}]))])
    vid = store.add_video(None, s.model_dump())
    store.update_video(vid, status="built", clip_id=1, timings={"words": [], "beats": [(0.0, 4.0)], "duration": 4.0, "matched": 1.0})
    assert client.post(f"/api/create/videos/{vid}/redo", json={"beat": 1, "want": "footage"}).status_code == 200
    saved = Script.model_validate(store.video(vid)["script"])             # reads again
    assert saved.beats[0].visual.avoid == [4000008]                       # kept as the number the build compares
    view = next(v for v in client.get("/api/create").json()["videos"] if v["id"] == vid)
    assert len(view["shots"]) == 1 and "problem" not in view


def test_a_list_that_cant_be_made_says_why(client):
    from clipper.create import store

    vid = store.add_video(None, Script(title="t", beats=[Beat(text="One two three four.")]).model_dump())
    store.update_video(vid, status="built", clip_id=1, timings={"words": [], "beats": "broken", "duration": 4.0, "matched": 1.0})
    view = next(v for v in client.get("/api/create").json()["videos"] if v["id"] == vid)
    assert view["shots"] == [] and view["problem"].startswith("The parts can't be listed")


# ---------- D129: choosing footage yourself; better searches ----------

def test_searches_are_written_for_the_sentence_and_never_repeat(monkeypatch):
    from clipper.create import stock

    monkeypatch.setattr(stock, "ask", lambda *a, **k: json.dumps({"searches": [
        "man looking in mirror", "Man Looking In Mirror", "bathroom mirror", "an extremely long search that goes on and on", ""]}))
    got = stock.plan_searches("Your skull flatters you.", "script", wish="mirror", tried=["bathroom mirror"])
    assert got == ["man looking in mirror", "an extremely long search that"]
    monkeypatch.setattr(stock, "ask", lambda *a, **k: (_ for _ in ()).throw(CreateError("no model")))
    assert stock.plan_searches("x") == []


def test_every_candidate_is_scored_and_the_best_comes_first(monkeypatch):
    from clipper.create import stock

    hits = [{"id": n, "tags": f"t{n}", "width": 1080, "height": 1920, "thumb": "x", "duration": 9} for n in (1, 2, 3)]
    monkeypatch.setattr(stock, "_thumb", lambda h: b"jpg")
    monkeypatch.setattr(stock, "ask", lambda *a, **k: json.dumps({"scores": [3, 15, 7], "centers": [0.2, 0.5, 2]}))
    ranked = stock.rank("s", hits)
    assert [h["id"] for h in ranked] == [2, 3, 1] and ranked[0]["score"] == 10 and ranked[1]["center"] == 1.0
    monkeypatch.setattr(stock, "ask", lambda *a, **k: (_ for _ in ()).throw(CreateError("no model")))
    assert [h["score"] for h in stock.rank("s", hits)] == [None, None, None]     # unscored, still offered
    monkeypatch.setattr(stock, "search", lambda q: hits)
    monkeypatch.setattr(stock, "ask", lambda *a, **k: json.dumps({"scores": [5, 5], "centers": []}))
    assert {h["id"] for h in stock.candidates(["q"], 4.0, {2}, "s")} == {1, 3}    # the excluded one isn't offered


def test_picking_footage_yourself_from_the_offer(client, monkeypatch):
    from clipper.create import stock, store

    s = Script(title="t", beats=[
        Beat(text="Your skull flatters you.", visual=Visual(kind="stock", query="mirror", picked=[{"id": 4000008, "url": "u"}])),
        Beat(text="Another part goes here.", visual=Visual(kind="stock", query="x", picked=[{"id": 77, "url": "u"}]))])
    vid = store.add_video(None, s.model_dump())
    store.update_video(vid, status="built", clip_id=1, timings={"words": [], "beats": [(0.0, 9.5), (9.5, 12.0)], "duration": 12.0, "matched": 1.0})
    pool = [{"id": n, "tags": "mirror", "width": 1080, "height": 1920, "thumb": "x", "duration": 12, "url": "u"} for n in (4000008, 77, 5, 6)]
    monkeypatch.setattr(stock, "search", lambda q: pool)
    monkeypatch.setattr(stock, "_thumb", lambda h: b"jpg")
    monkeypatch.setattr(stock, "ask", lambda system, user, schema, **k: json.dumps(
        {"searches": ["man looking in mirror"]} if schema.__name__ == "_Searches" else {"scores": [4, 8], "centers": [0.5, 0.5]}))
    offer = client.post(f"/api/create/videos/{vid}/footage", json={"beat": 1, "wish": "a mirror"}).json()
    assert [c["id"] for c in offer["candidates"]] == ["6", "5"]       # neither the clip in use nor one used elsewhere
    assert offer["clips"] == 3 and offer["searches"][0] == "a mirror" and offer["candidates"][0]["score"] == 8
    assert client.post(f"/api/create/videos/{vid}/footage/use", json={"beat": 1, "ids": ["999"]}).status_code == 400
    assert client.post(f"/api/create/videos/{vid}/footage/use", json={"beat": 1, "ids": []}).status_code == 400
    assert client.post(f"/api/create/videos/{vid}/footage/use", json={"beat": 1, "ids": ["6", "5"]}).status_code == 200
    v = Script.model_validate(store.video(vid)["script"]).beats[0].visual
    assert [h["id"] for h in v.picked] == [6, 5] and v.redo and v.avoid == [4000008] and v.previous


def test_asked_footage_that_doesnt_fit_says_so_on_the_part(tmp_path, monkeypatch):
    from clipper.create import build, diagrams, stock

    before = Visual(kind="stock", query="mirror", picked=[{"id": 1, "url": "u"}])
    asked = before.model_copy(update={"picked": [], "avoid": [1], "redo": True, "previous": before.model_dump()})
    beat = Beat(text="Your skull flatters you.", visual=asked)
    s = Script(title="t", beats=[beat])
    seen = []
    monkeypatch.setattr(stock, "plan_searches", lambda *a, **k: [])
    monkeypatch.setattr(stock, "choose", lambda q, part, used, good_enough=7, **k: seen.append((set(used), good_enough)))
    monkeypatch.setattr(build, "_stock_shot", lambda src, part, out, *a, **k: out)
    monkeypatch.setattr(build, "_too_dark", lambda clip: False)
    monkeypatch.setattr(stock, "fetch", lambda hit: tmp_path / "x.mp4")
    monkeypatch.setattr(diagrams, "render", lambda v, seconds, out, words=None, frames=None: out)
    build.chosen.clear()
    build._planned(0, beat, asked, 2.0, [], s, tmp_path, set())
    assert seen[0] == ({1}, 6)                                        # the old clip is never chosen again; 6 to pass
    kept = build.remember(s).beats[0].visual
    assert kept.picked[0]["id"] == 1 and "kept what it had" in kept.notice and 1 in kept.avoid
    # the next rebuild keeps that clip: nothing is chosen again
    seen.clear()
    build.chosen.clear()
    build._planned(0, beat, kept, 2.0, [], s, tmp_path, set())
    assert seen == [] and build.chosen[0]["picked"][0]["id"] == 1


# ---------- D130: more footage libraries, previews ----------

class _Reply:
    def __init__(self, data):
        self.data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self.data


def test_coverr_and_nasa_are_read_into_footage(data_root, monkeypatch):
    import httpx

    from clipper.create import stock

    def fake_get(url, params=None, headers=None, timeout=None):
        if "coverr" in url:
            assert headers["Authorization"] == "Bearer ck" and params["urls"] == "true"
            return _Reply({"hits": [
                {"id": "abc", "title": "Man with headphones", "tags": ["music", {"name": "studio"}], "duration": 12.5,
                 "max_width": 3840, "max_height": 2160, "thumbnail": "t.jpg",
                 "urls": {"mp4": "https://c/v.mp4", "mp4_preview": "https://c/p.mp4"}},
                {"id": "gs", "title": "green screen dancer", "urls": {"mp4": "https://c/g.mp4"}},     # cheap: left out
                {"id": "nourl", "title": "no file"}]})
        if "images-api" in url:
            return _Reply({"collection": {"items": [{"href": "https://n/manifest.json", "data": [
                {"nasa_id": "rocket 1", "title": "Rocket launch", "keywords": ["rocket", "launch"]}],
                "links": [{"href": "https://n/thumb.jpg", "render": "image"}]}]}})
        if "manifest" in url:
            return _Reply(["http://n/rocket~orig.mp4", "http://n/rocket~mobile.mp4", "http://n/rocket.srt"])
        raise AssertionError(url)

    monkeypatch.setattr(httpx, "get", fake_get)
    monkeypatch.setenv("COVERR_API_KEY", "ck")
    c = stock.coverr("headphones")
    assert [h["id"] for h in c] == ["coverr-abc"] and c[0]["preview"] == "https://c/p.mp4" and "studio" in c[0]["tags"]
    n = stock.nasa("rocket")
    assert n[0]["id"] == "nasa-rocket1" and n[0]["url"] == "https://n/rocket~orig.mp4" and n[0]["preview"].endswith("~mobile.mp4")
    assert n[0]["thumb"] == "https://n/thumb.jpg"


def test_search_takes_turns_across_libraries_and_needs_no_key_for_nasa(monkeypatch):
    from clipper.create import stock

    for name in ("PIXABAY_API_KEY", "PEXELS_API_KEY", "COVERR_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(stock, "nasa", lambda q: [{"id": "nasa-1"}])
    assert stock.search("rocket") == [{"id": "nasa-1"}]                  # works with no keys at all
    monkeypatch.setenv("PIXABAY_API_KEY", "p")
    monkeypatch.setenv("COVERR_API_KEY", "c")
    monkeypatch.setattr(stock, "pixabay", lambda q: [{"id": 1}, {"id": 2}])
    monkeypatch.setattr(stock, "coverr", lambda q: (_ for _ in ()).throw(CreateError("Coverr didn't answer")))
    assert [h["id"] for h in stock.search("rocket x")] == [1, "nasa-1", 2]      # one library down: the others still answer
    assert [h["id"] for h in stock.search("boiling pot")] == [1, 2]     # NASA only for space-sounding searches (D133)


def _finished(store, db, vid: int, status: str = "ready") -> int:
    with db.connect() as con:
        clip_id = db.upsert_clip(con, {"campaign": "german-professor", "source_id": "create", "clip_id": f"create-{vid}",
                                       "title": "t", "file": f"german-professor/{vid}.mp4", "caption": "c"})
        db.update_clip(con, clip_id, status=status)
    store.update_video(vid, status="built", clip_id=clip_id)
    return clip_id


def test_posted_videos_move_to_the_archive_and_can_come_back(client, monkeypatch):
    """D131: a posted video (found by the syncs or marked in Clips) goes to the archive once;
    brought back by hand it stays out; archived by hand works for any finished or unfinished video."""
    from clipper.create import store
    from clipper.learn import log as perf
    from clipper.studio import db

    found = client.post("/api/create/ready/voice-on-a-recording").json()["id"]
    marked = client.post("/api/create/ready/voice-on-a-recording").json()["id"]
    draft = client.post("/api/create/videos", json={"text": "Sound goes through bone. It sounds deeper to you."}).json()["id"]
    _finished(store, db, found)
    _finished(store, db, marked, status="posted")
    monkeypatch.setattr(perf, "read", lambda *a: [{"campaign": "german-professor", "source_id": "create",
                                                   "clip_id": f"create-{found}", "platform": "youtube",
                                                   "url": "https://youtube.com/shorts/abc?x=1",
                                                   "posted_at": "2026-10-03 18:20:00", "views_latest": "1234"}])
    videos = {v["id"]: v for v in client.get("/api/create").json()["videos"]}
    assert videos[found]["archived"] and videos[found]["archived_at"] == "2026-10-03 18:20"
    assert videos[found]["posts"][0]["url"] == "https://youtube.com/shorts/abc" and videos[found]["posts"][0]["views"] == 1234
    assert videos[marked]["archived"] and videos[marked]["posted"] and videos[marked]["posts"] == []
    assert not videos[draft]["archived"] and not videos[draft]["posted"]

    # Brought back: stays out on every later look, though it's still posted.
    assert client.post(f"/api/create/videos/{found}/archive", json={"archived": False}).status_code == 200
    for _ in range(2):
        v = next(v for v in client.get("/api/create").json()["videos"] if v["id"] == found)
        assert not v["archived"] and v["posted"]
    # Archived by hand, a draft too; and back.
    assert client.post(f"/api/create/videos/{draft}/archive", json={"archived": True}).status_code == 200
    assert next(v for v in client.get("/api/create").json()["videos"] if v["id"] == draft)["archived"]
    client.post(f"/api/create/videos/{draft}/archive", json={"archived": False})
    assert not next(v for v in client.get("/api/create").json()["videos"] if v["id"] == draft)["archived"]
    # Not while it builds.
    store.update_video(draft, status="building")
    assert client.post(f"/api/create/videos/{draft}/archive", json={"archived": True}).status_code == 409
    assert client.post("/api/create/videos/9999/archive", json={"archived": True}).status_code == 404

    # The ready-made list says what was made from it and what's archived.
    ready = next(r for r in client.get("/api/create/ready").json() if r["name"] == "voice-on-a-recording")
    assert {m["id"]: m["archived"] for m in ready["made"]} == {found: False, marked: True}


def test_a_build_in_progress_isnt_archived_until_it_finishes(client, monkeypatch):
    from clipper.create import store
    from clipper.studio import db

    vid = client.post("/api/create/ready/voice-on-a-recording").json()["id"]
    _finished(store, db, vid, status="posted")
    store.update_video(vid, status="building")
    assert not next(v for v in client.get("/api/create").json()["videos"] if v["id"] == vid)["archived"]
    store.update_video(vid, status="built")
    assert next(v for v in client.get("/api/create").json()["videos"] if v["id"] == vid)["archived"]


def test_videos_made_before_d131_find_their_ready_made_script(client):
    from clipper.create import store

    made = client.post("/api/create/ready/voice-on-a-recording").json()["id"]
    store.update_video(made, ready="")            # as an older version saved it
    ready = next(r for r in client.get("/api/create/ready").json() if r["name"] == "voice-on-a-recording")
    assert [m["id"] for m in ready["made"]] == [made] and store.video(made)["ready"] == "voice-on-a-recording"


def test_nothing_changes_a_video_under_a_running_build(client, tmp_path):
    """D132: a script edit, another take, approve, new pictures or a new voice mid-build cleared
    the voice and timings under the build, or set a built video back to "approved"."""
    from clipper.create import store

    vid = client.post("/api/create/videos", json={"title": "Bone", "text": "Sound goes through bone. It sounds deeper to you."}).json()["id"]
    store.update_video(vid, status="building", voice=str(tmp_path / "voice.mp3"))
    base = f"/api/create/videos/{vid}"
    assert client.put(f"{base}/script", json={"script": {"title": "Other"}}).status_code == 409
    assert client.post(f"{base}/rewrite").status_code == 409
    assert client.post(f"{base}/pictures").status_code == 409
    assert client.put(f"{base}/voice/take2.mp3", content=b"x").status_code == 409
    row = store.video(vid)
    assert row["status"] == "building" and row["voice"] and row["script"]["title"] == "Bone"

    store.update_video(vid, status="built")
    assert client.post(f"{base}/approve").status_code == 400
    assert store.video(vid)["status"] == "built"
    store.update_video(vid, status="draft")
    assert client.post(f"{base}/approve").status_code == 200


def test_nasa_footage_downloads_the_large_file_not_the_original():
    """D132: NASA's "orig" was 106 MB against "large" at 21 MB for the same launch."""
    from clipper.create import stock

    files = ["a~orig.mp4", "a~large.mp4", "a~medium.mp4", "a~mobile.mp4"]
    assert stock._rendition_named(files, "large", "orig", "medium") == "a~large.mp4"
    assert stock._rendition_named(["a~orig.mp4"], "large", "orig", "medium") == "a~orig.mp4"


def test_a_clip_used_twice_in_a_row_carries_on(tmp_path, monkeypatch):
    """D132: a long sentence with one fitting clip played the same seconds twice."""
    from clipper.create import build

    starts = []
    monkeypatch.setattr(build, "probe", lambda p: type("I", (), {"duration": 20.0, "width": 1920, "height": 1080})())
    monkeypatch.setattr(build, "_subject_x", lambda *a: 0.5)
    monkeypatch.setattr(build.compose, "video_panel", lambda *a, start, **k: starts.append(start))
    build._stock_shot(tmp_path / "c.mp4", 4.0, tmp_path / "a.mp4", 0.5)
    build._stock_shot(tmp_path / "c.mp4", 4.0, tmp_path / "b.mp4", 0.5, skip=4.0)
    assert starts == [3.0, 7.0]
