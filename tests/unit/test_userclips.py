"""The user's own clips in a Create video (D119): storage checks, placement, fitting, building."""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from clipper.create import build, userclips
from clipper.create.ai import CreateError
from clipper.create.script import Beat, Script, Visual
from clipper.create.voice import TimedWord, Timings

needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="no ffmpeg")


def script(n: int = 4, diagrams: tuple[int, ...] = ()) -> Script:
    return Script(title="Why", beats=[
        Beat(text=f"Sentence number {i + 1} says something about sound.",
             visual=Visual(kind="diagram" if i in diagrams else "stock", query="sound", template="card" if i in diagrams else ""))
        for i in range(n)])


def make_clip(path, seconds=3.0, size="1920x1080", audio=False):
    args = ["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", f"testsrc=size={size}:rate=25"]
    if audio:
        args += ["-f", "lavfi", "-i", "sine=frequency=440"]
    subprocess.run([*args, "-t", str(seconds), str(path)], check=True)
    return path


# ---------- layout, fitting, notes (pure) ----------

def test_in_order_layout_covers_what_fits_and_spreads_the_rest():
    seconds = [3.0] * 8
    layout, unused = userclips.order_layout(seconds, list(range(8)), [("c1", 6.5), ("c2", 3.2)])
    assert unused == []
    # c1 fully covers two 3s sentences, c2 one; the hook is a clip; leftovers spread after each
    assert layout[0] == ("c1", 0.0) and layout[1] == ("c1", None)
    runs = [layout.get(i) for i in range(8)]
    assert sum(1 for r in runs if r) == 3
    assert any(r and r[0] == "c2" for r in runs)
    assert [i for i, r in enumerate(runs) if r is None] and runs[-1] is None  # a planned tail remains


def test_in_order_layout_with_more_clips_than_sentences_leaves_the_rest_unused():
    layout, unused = userclips.order_layout([3.0] * 2, [0, 1], [("a", 9), ("b", 9), ("c", 9)])
    assert unused == ["c"] or unused == ["b", "c"] or len(layout) <= 2
    assert set(layout) <= {0, 1}
    assert len(unused) >= 1


def test_in_order_layout_never_uses_a_protected_sentence():
    layout, _ = userclips.order_layout([3.0] * 5, [0, 2, 4], [("a", 3.0), ("b", 3.0), ("c", 3.0)])
    assert set(layout) == {0, 2, 4}


def test_in_order_layout_with_nothing_allowed_is_empty():
    assert userclips.order_layout([3.0], [], [("a", 3.0)]) == ({}, ["a"])


@pytest.mark.parametrize("avail,seconds,fill,expect", [
    (5.0, 4.0, "auto", "cut"),
    (3.97, 4.0, "auto", "cut"),            # within a frame or two: cut
    (3.5, 4.0, "auto", "hold"),            # a small gap: its last picture held
    (2.0, 4.0, "auto", "planned"),         # a big gap: the planned picture takes over
    (2.0, 4.0, "loop", "loop"),
    (2.0, 4.0, "slow", "slow"),
    (2.0, 4.0, "hold", "hold"),
    (2.0, 4.0, "planned", "planned"),
])
def test_fill_plan(avail, seconds, fill, expect):
    assert userclips.fill_plan(avail, seconds, fill)[0] == expect


def test_slowing_stops_at_two_times_and_holds_the_rest():
    mode, factor, rest = userclips.fill_plan(1.0, 4.0, "slow")
    assert (mode, factor, rest) == ("slow", 2.0, 2.0)
    assert userclips.fill_plan(2.0, 3.0, "slow")[2] == 0.0  # 1.5x fills it exactly


def test_the_models_answer_is_made_safe():
    plan = userclips._Plan(placements=[
        userclips._Put(beat=1, clip="c1", start=99),        # start past the end: pulled inside
        userclips._Put(beat=2, clip="c1", start=1),         # the next sentence carries on
        userclips._Put(beat=2, clip="c2", start=0),         # a second clip for one sentence: ignored
        userclips._Put(beat=9, clip="c1"),                  # no such sentence
        userclips._Put(beat=3, clip="zzz"),                 # no such clip
        userclips._Put(beat=0, clip="c1"),
        userclips._Put(beat=4, clip="c2", start=float("nan"))])
    layout = userclips.normalize(plan, 4, {"c1": 6.0, "c2": 2.0})
    assert layout[0] == ("c1", 5.5)
    assert layout[1] == ("c1", None)
    assert 2 not in layout
    assert layout[3] == ("c2", 0.0)


def test_notes_block_is_replaced_not_piled_up():
    base = "Physics check: no problems found.\nWritten and drawn by: x."
    one = userclips.with_notes(base, ["a", "b"])
    two = userclips.with_notes(one, ["c"])
    assert two.count(userclips.MARK) == 1 and "  - c" in two and "  - a" not in two
    assert userclips.with_notes(two, []) == base
    assert userclips.with_notes("", ["x"]).startswith(userclips.MARK)
    assert userclips.with_notes("Physics ok.\nYour clips:\n  - old", ["new"]) == f"Physics ok.\n{userclips.MARK}\n  - new"


def test_estimates_never_below_a_beat_floor():
    assert all(s >= 1.2 for s in userclips.estimate(script(3), 0))


def test_set_beat_checks_everything():
    s, known = script(3), {"c1": 4.0}
    ok = userclips.set_beat(s, 2, "c1", 1.0, "loop", known)
    assert ok.beats[1].visual.clip == "c1" and ok.beats[1].visual.fill == "loop"
    assert userclips.set_beat(ok, 2, "", 1.0, "loop", known).beats[1].visual.clip == ""  # back to planned
    for bad in [(0, "c1", None, "auto"), (4, "c1", None, "auto"), (1, "nope", None, "auto"),
                (1, "c1", -1.0, "auto"), (1, "c1", float("nan"), "auto"), (1, "c1", 3.9, "auto"),
                (1, "c1", None, "weird")]:
        with pytest.raises(CreateError):
            userclips.set_beat(s, *bad[:1], *bad[1:2], bad[2], bad[3], known)


def test_a_written_script_has_no_clips_and_replan_keeps_them():
    from clipper.create import script as scripts

    s = script(2).model_copy(update={"beats": [
        b.model_copy(update={"visual": b.visual.model_copy(update={"clip": "c9", "clip_start": 2.0, "fill": "loop"})})
        for b in script(2).beats]})
    answer = json.dumps({"title": "t", "beats": [{"text": "A b c d e.", "visual": {"clip": "c9", "picked": [{"id": 1}]}}]})
    assert scripts._parse(answer).beats[0].visual.clip == ""   # the writer can't place clips
    assert "clip" not in scripts._WriterVisual.model_fields and "picked" not in scripts._WriterVisual.model_fields
    gone, dropped = userclips.forget(s, {"c1"})
    assert dropped == 2 and all(b.visual.clip == "" and b.visual.fill == "auto" for b in gone.beats)
    kept, dropped = userclips.forget(s, {"c9"})
    assert dropped == 0 and kept.beats[0].visual.clip == "c9"


# ---------- storage ----------

@needs_ffmpeg
def test_adding_checking_and_removing_clips(data_root, tmp_path, monkeypatch):
    from clipper.utils import recycle as bin_

    binned = []
    monkeypatch.setattr(bin_, "recycle", lambda path: binned.append(path.name))
    good = make_clip(tmp_path / "a.mp4", 3.0)
    entry = userclips.add(1, good, "../../My Clip.mp4")
    assert entry["name"] == "My Clip.mp4" and entry["id"] == "c1" and entry["duration"] == pytest.approx(3.0, abs=0.2)
    assert entry["low_res"] is False
    assert userclips.thumb_path(1, "c1") is not None and userclips.file_path(1, "c1").is_file()
    # a duplicate, a non-video, an unreadable file, a sound-only file, a too-short one
    junk = tmp_path / "j.mp4"
    junk.write_bytes(b"not a video at all")
    with pytest.raises(CreateError, match="can't be read"):
        userclips.add(1, junk, "j.mp4")
    assert binned == ["j.mp4"]  # to the Recycle Bin, never a hard delete
    txt = tmp_path / "t.txt"
    txt.write_text("hi")
    with pytest.raises(CreateError, match="isn't a video"):
        userclips.add(1, txt, "t.txt")
    audio = tmp_path / "s.mp4"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=440", "-t", "2", str(audio)], check=True)
    with pytest.raises(CreateError, match="no picture"):
        userclips.add(1, audio, "s.mp4")
    flash = make_clip(tmp_path / "f.mp4", 0.2)
    with pytest.raises(CreateError, match="half a second"):
        userclips.add(1, flash, "f.mp4")
    empty = tmp_path / "e.mp4"
    empty.write_bytes(b"")
    with pytest.raises(CreateError, match="empty"):
        userclips.add(1, empty, "e.mp4")
    # same name and size again: refused; ids are never reused after a removal
    dup = tmp_path / "d.mp4"
    shutil.copy(userclips.file_path(1, "c1"), dup)
    with pytest.raises(CreateError, match="already added"):
        userclips.add(1, dup, "My Clip.mp4")
    assert userclips.remove(1, "c1") is True and userclips.remove(1, "c1") is False
    second = userclips.add(1, make_clip(tmp_path / "b.mp4", 2.0, size="640x360"), "b.mp4")
    assert second["id"] == "c2" and second["low_res"] is True


def test_a_damaged_manifest_reads_as_empty(data_root):
    userclips._manifest(7).write_text("{not json", encoding="utf-8")
    assert userclips.load(7)["clips"] == [] and userclips.files(7) == {}
    userclips._manifest(7).write_text("[1,2]", encoding="utf-8")
    assert userclips.load(7)["clips"] == []


def test_settings_are_checked(data_root):
    assert userclips.settings(3, auto=False, fill="loop")["fill"] == "loop"
    with pytest.raises(CreateError):
        userclips.settings(3, fill="nonsense")
    assert userclips.load(3)["auto"] is False


# ---------- placing ----------

@needs_ffmpeg
def test_placing_with_no_model_uses_the_order_and_never_touches_diagrams(data_root, tmp_path):
    for k in range(2):
        userclips.add(2, make_clip(tmp_path / f"{k}.mp4", 4.0 + k), f"{k}.mp4")
    s = script(6, diagrams=(2,))
    placed, note = userclips.place(2, s, [3.0] * 6, use_ai=False)
    used = [b.visual.clip for b in placed.beats]
    assert used[2] == "" and used[0] == "c1"          # the hook has a clip; the diagram keeps its picture
    assert {"c1", "c2"} <= set(used)
    assert "in the order you added them" in note
    with pytest.raises(CreateError, match="add some clips"):
        userclips.place(99, s, [3.0] * 6, use_ai=False)


@needs_ffmpeg
def test_the_model_places_clips_and_a_failing_model_falls_back(data_root, tmp_path, monkeypatch):
    for k in range(2):
        userclips.add(4, make_clip(tmp_path / f"{k}.mp4", 6.0), f"{k}.mp4")
    s = script(5)
    answer = json.dumps({"placements": [{"beat": 3, "clip": "c2", "start": 1.0}, {"beat": 4, "clip": "c2"},
                                        {"beat": 1, "clip": "c1", "start": 0.5}]})
    monkeypatch.setattr(userclips, "ask", lambda *a, **k: answer)
    placed, note = userclips.place(4, s, [3.0] * 5)
    clips = [b.visual.clip for b in placed.beats]
    assert clips == ["c1", "", "c2", "c2", ""] and "Placed 2 of 2" in note
    assert placed.beats[2].visual.clip_start == 1.0 and placed.beats[3].visual.clip_start is None

    def boom(*a, **k):
        raise CreateError("Claude isn't available right now (weekly limit)")

    monkeypatch.setattr(userclips, "ask", boom)
    placed, note = userclips.place(4, s, [3.0] * 5)
    assert "in the order you added them" in note and "weekly limit" in note
    assert {b.visual.clip for b in placed.beats} >= {"c1", "c2"}
    monkeypatch.setattr(userclips, "ask", lambda *a, **k: "not json")
    assert "Placed 2 of 2" in userclips.place(4, s, [3.0] * 5)[1]


@needs_ffmpeg
def test_strict_leaves_unmatched_clips_and_the_default_uses_every_clip(data_root, tmp_path, monkeypatch):
    for k in range(3):
        userclips.add(5, make_clip(tmp_path / f"{k}.mp4", 3.0 + k * 0.1), f"{k}.mp4")
    answer = json.dumps({"placements": [{"beat": 1, "clip": "c1"}]})
    monkeypatch.setattr(userclips, "ask", lambda *a, **k: answer)
    s = script(6)
    strict, note = userclips.place(5, s, [3.0] * 6, strict=True)
    assert [b.visual.clip for b in strict.beats].count("") == 5 and "Not used" in note
    loose, note = userclips.place(5, s, [3.0] * 6)
    assert {b.visual.clip for b in loose.beats} >= {"c1", "c2", "c3"} and "Not used" not in note


@needs_ffmpeg
def test_prepare_places_on_its_own_only_when_asked_and_nothing_is_placed(data_root, tmp_path, monkeypatch):
    monkeypatch.setattr(userclips, "ask", lambda *a, **k: (_ for _ in ()).throw(CreateError("no AI")))
    userclips.add(6, make_clip(tmp_path / "a.mp4", 4.0), "a.mp4")
    s = script(4)
    placed, note = userclips.prepare(6, s, [3.0] * 4)
    assert placed.beats[0].visual.clip == "c1" and "Placed" in note
    again, note2 = userclips.prepare(6, placed, [3.0] * 4)   # already placed: left alone
    assert again == placed and note2 == ""
    userclips.settings(6, auto=False)
    off, note3 = userclips.prepare(6, s, [3.0] * 4)
    assert all(b.visual.clip == "" for b in off.beats) and note3 == ""
    userclips.remove(6, "c1")
    gone, note4 = userclips.prepare(6, placed, [3.0] * 4)
    assert all(b.visual.clip == "" for b in gone.beats) and "gone" in note4


# ---------- building ----------

def timings(spans):
    return Timings(words=[TimedWord(text="w", start=a, end=a + 0.1) for a, _ in spans], beats=spans,
                   duration=spans[-1][1], matched=1.0)


@needs_ffmpeg
def test_shots_cut_hold_and_fill_with_the_planned_picture(data_root, tmp_path, monkeypatch):
    from clipper.ingest.probe import probe

    planned = []

    def fake_planned(i, beat, visual, seconds, said, script_, work, used, tag="", pre=None, at=None):
        out = work / f"{i:02d}{tag}_planned.mp4"
        planned.append((i, round(seconds, 1), tag))
        return [build._own_shot(src, 0, 1.0, seconds, out, loop=True)]

    src = make_clip(tmp_path / "src.mp4", 3.0)
    monkeypatch.setattr(build, "_planned", fake_planned)
    monkeypatch.setattr(build, "_subject_x", lambda *a, **k: 0.5)
    work = tmp_path / "work"
    work.mkdir()
    s = script(5)
    visuals = [dict(clip="c1", clip_start=0.0),                     # 3s clip on a 2s sentence: cut
               dict(clip="c1", clip_start=2.6, fill="auto"),        # 0.4s left of 2s: hold? 1.6s gap -> planned
               dict(clip="c1", clip_start=1.0, fill="hold"),        # 2s left of 3s: held
               dict(clip="c1", clip_start=1.5, fill="slow"),        # 1.5s stretched
               dict(clip="gone", fill="auto")]                      # clip no longer there: planned, whole
    beats = [b.model_copy(update={"visual": b.visual.model_copy(update=v)}) for b, v in zip(s.beats, visuals, strict=True)]
    s = s.model_copy(update={"beats": beats})
    spans = [(0.0, 2.0), (2.0, 4.0), (4.0, 7.0), (7.0, 9.5), (9.5, 11.5)]
    notes: list[str] = []
    own = {"c1": (src, 3.0, "a.mp4")}
    made = build.shots(s, timings(spans), work, own=own, notes=notes)
    assert [round(probe(p).duration, 1) for p in made[:1]] == [2.0]
    total = sum(probe(p).duration for p in made)
    assert total == pytest.approx(11.5, abs=0.4)
    # sentence 2: the clip's 0.4s gap logic -> start clamped to 2.5, then planned for the remainder
    assert any(i == 1 and tag == "b" for i, _, tag in planned)
    assert any(i == 4 and tag == "" and sec == 2.0 for i, sec, tag in planned)   # the whole sentence
    text = "\n".join(notes)
    assert "clip is gone" in text and "held on its last picture" in text and "slowed to" in text


@needs_ffmpeg
def test_a_clip_that_has_run_out_gives_way_to_the_planned_picture(data_root, tmp_path, monkeypatch):
    seen = []
    src = make_clip(tmp_path / "src.mp4", 2.0)

    def fake_planned(i, beat, visual, seconds, said, script_, work, used, tag="", pre=None, at=None):
        seen.append(i)
        return [build._own_shot(src, 0, 1.0, seconds, work / f"{i}{tag}.mp4", loop=True)]

    monkeypatch.setattr(build, "_planned", fake_planned)
    monkeypatch.setattr(build, "_subject_x", lambda *a, **k: 0.5)
    work = tmp_path / "w"
    work.mkdir()
    s = script(3)
    s = s.model_copy(update={"beats": [b.model_copy(update={"visual": b.visual.model_copy(update={"clip": "c1"})}) for b in s.beats]})
    notes: list[str] = []
    build.shots(s, timings([(0.0, 1.0), (1.0, 2.0), (2.0, 3.0)]), work, own={"c1": (src, 2.0, "a.mp4")}, notes=notes)
    # carried on: 0-1s, 1-2s, then the clip has run out
    assert seen == [2] and any("run out" in n for n in notes)


@needs_ffmpeg
def test_a_clip_ffmpeg_cannot_cut_falls_back_not_fails(data_root, tmp_path, monkeypatch):
    seen = []
    broken = tmp_path / "broken.mp4"
    broken.write_bytes(b"\x00" * 2048)
    src = make_clip(tmp_path / "ok.mp4", 2.0)

    def fake_planned(i, beat, visual, seconds, said, script_, work, used, tag="", pre=None, at=None):
        seen.append(i)
        return [build._own_shot(src, 0, 1.0, seconds, work / f"{i}{tag}.mp4", loop=True)]

    monkeypatch.setattr(build, "_planned", fake_planned)
    s = script(1)
    s = s.model_copy(update={"beats": [s.beats[0].model_copy(update={"visual": s.beats[0].visual.model_copy(update={"clip": "c1"})})]})
    notes: list[str] = []
    work = tmp_path / "w"
    work.mkdir()
    build.shots(s, timings([(0.0, 1.5)]), work, own={"c1": (broken, 2.0, "broken.mp4")}, notes=notes)
    assert seen == [0] and any("couldn't be read" in n for n in notes)


# ---------- the page's endpoints ----------

@needs_ffmpeg
def test_the_endpoints_end_to_end(data_root, tmp_path):
    from fastapi.testclient import TestClient

    from clipper.create import store
    from clipper.studio.server import create_app

    client = TestClient(create_app())
    video = store.add_video(None, script(4).model_dump())
    base = f"/api/create/videos/{video}"
    clip = make_clip(tmp_path / "one.mp4", 4.0).read_bytes()
    up = client.put(f"{base}/clips/one.mp4", content=clip)
    assert up.status_code == 200 and up.json()["id"] == "c1"
    assert client.put(f"{base}/clips/notes.txt", content=b"hi").status_code == 400
    assert client.put(f"{base}/clips/bad.mp4", content=b"junk").status_code == 400
    assert client.put(f"{base}/clips/one.mp4", content=clip).status_code == 400        # already added
    assert client.get(f"{base}/clips/c1/thumb").status_code == 200
    assert client.get(f"{base}/clips/c1/file").status_code == 200
    assert client.get(f"{base}/clips/zz/file").status_code == 404
    assert client.put("/api/create/videos/999/clips/x.mp4", content=clip).status_code == 404

    mine = next(v for v in client.get("/api/create").json()["videos"] if v["id"] == video)["mine"]
    assert mine["auto"] is True and [c["id"] for c in mine["clips"]] == ["c1"] and mine["clips"][0]["used"] == []

    assert client.post(f"{base}/place", json={"how": "nonsense"}).status_code == 400
    placed = client.post(f"{base}/place", json={"how": "order"})
    assert placed.status_code == 200 and "Placed 1 of 1" in placed.json()["note"]
    row = store.video(video)
    assert row["script"]["beats"][0]["visual"]["clip"] == "c1" and userclips.MARK in row["check_notes"]
    assert row["status"] == "draft" and row["script"].get("take", 1) == 1       # nothing else disturbed

    by_hand = client.put(f"{base}/placement", json={"beat": 3, "clip": "c1", "start": 1.5, "fill": "loop"})
    assert by_hand.status_code == 200
    assert store.video(video)["script"]["beats"][2]["visual"]["fill"] == "loop"
    for bad in ({"beat": "x"}, {"beat": 9, "clip": "c1"}, {"beat": 1, "clip": "nope"}, {"beat": 1, "clip": "c1", "start": "soon"},
                {"beat": 1, "clip": "c1", "start": 99}, {"beat": 1, "clip": "c1", "fill": "weird"}):
        assert client.put(f"{base}/placement", json=bad).status_code == 400, bad

    assert client.put(f"{base}/clips-settings", json={"auto": False, "fill": "slow"}).status_code == 200
    assert client.put(f"{base}/clips-settings", json={"fill": "weird"}).status_code == 400
    assert client.put(f"{base}/clips-settings", json={"auto": "yes"}).status_code == 400

    cleared = client.post(f"{base}/place", json={"how": "clear"})
    assert cleared.status_code == 200
    assert all(b["visual"]["clip"] == "" for b in store.video(video)["script"]["beats"])

    # removing a clip frees its sentences
    client.put(f"{base}/placement", json={"beat": 1, "clip": "c1"})
    assert client.delete(f"{base}/clips/c1").status_code == 200
    assert client.delete(f"{base}/clips/c1").status_code == 404
    assert store.video(video)["script"]["beats"][0]["visual"]["clip"] == ""

    # while it is being built, the clips can't change
    store.update_video(video, status="building")
    assert client.put(f"{base}/clips/one.mp4", content=clip).status_code == 409
    assert client.post(f"{base}/place", json={"how": "order"}).status_code == 409
    assert client.put(f"{base}/placement", json={"beat": 1, "clip": ""}).status_code == 409
    assert client.delete(f"{base}/clips/c1").status_code == 409
