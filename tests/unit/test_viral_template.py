"""D156: the second research report in the build and the writer (taps, punch-ins, bits, shapes, the character)."""

from __future__ import annotations

from clipper.create import build, channel, script, sound, store
from clipper.create.script import Beat, Script, Visual
from clipper.create.voice import TimedWord, Timings


def test_chalk_taps_are_few_and_spaced():
    assert sound.taps([12.0, 1.0, 3.0, 7.0, 20.0, 26.0, 31.0, 37.0, 44.0]) == [1.0, 7.0, 12.0, 20.0, 26.0, 31.0]


def test_punch_ins_land_on_the_highlighted_word_away_from_the_hook_and_punchline():
    texts = ["Why do you feel heavier?", "The floor pushes harder on you.", "Gravity stays exactly the same.",
             "So the floor wins.", "Briefly, you are cargo."]
    beats = [Beat(text=t, emphasis=e, visual=Visual(kind="stock")) for t, e in
             zip(texts, ["heavier", "harder", "same", "wins", "cargo"], strict=True)]
    words, at = [], 0.0
    for t in texts:
        for w in t.split():
            words.append(TimedWord(text=w, start=at, end=at + 0.8))
            at += 1.0
    timing = Timings(words=words, beats=[(0, 1)] * 5, duration=at, matched=1.0)
    got = build.choose_punches(Script(title="t", beats=beats), timing)
    assert list(got) == [1, 3]                       # not the hook, not the punchline, 8 s apart at most twice
    times = [v[0] for v in got.values()]
    assert times[1] - times[0] >= build.PUNCH_GAP


def test_a_running_bit_comes_at_most_every_third_script_and_counts_its_uses(data_root):
    ch = channel.make("physics", "Prof")
    channel.save(ch)
    with channel.use(ch.slug):
        name, how = script.next_bit(ch)
        assert name == "law" and "No. 1:" in how
        store.add_video(None, Script(title="a", beats=[Beat(text="x")], bit="law").model_dump())
        assert script.next_bit(ch) == ("", "")      # never two running
        store.add_video(None, Script(title="b", beats=[Beat(text="x")]).model_dump())
        assert script.next_bit(ch) == ("", "")      # at most one in three
        store.add_video(None, Script(title="c", beats=[Beat(text="x")]).model_dump())
        name, _ = script.next_bit(ch)
        assert name == "coffee"                      # the least used next


def test_shapes_and_endings_never_repeat_the_last_scripts(data_root):
    ch = channel.make("physics", "Prof")
    channel.save(ch)
    with channel.use(ch.slug):
        store.add_video(None, Script(title="a", beats=[Beat(text="x")], shape=ch.shapes[0], ending="loop").model_dump())
        store.add_video(None, Script(title="b", beats=[Beat(text="x")], shape=ch.shapes[1], ending="send").model_dump())
        for _ in range(20):
            shape, ending = script.turn(ch)
            assert shape not in ch.shapes[:2] and ending != "send"


def test_a_square_picture_becomes_a_round_badge(tmp_path):
    from PIL import Image

    Image.new("RGB", (400, 400), (90, 140, 200)).save(tmp_path / "pfp.png")
    out = build._character(tmp_path / "pfp.png", tmp_path / "badge.png")
    badge = Image.open(out)
    assert badge.height == build.CHARACTER_HEIGHT and badge.getpixel((0, 0))[3] == 0     # round: the corner is clear
    assert badge.getpixel((badge.width // 2, badge.height // 2))[3] == 255


def test_poses_are_kept_only_where_they_fit(data_root):
    ch = channel.make("physics", "Prof")
    ch.poses = {"shocked": "a.png", "aha": "b.png"}
    texts = ["Why do you feel heavier in a lift?", "The floor pushes harder on you.", "Gravity stays the same.",
             "So the floor wins.", "Briefly, you are cargo.", "Class."]
    poses = ["shocked", "aha", "aha", "SHOCKED", "wave", "aha"]
    beats = [Beat(text=t, pose=p) for t, p in zip(texts, poses, strict=True)]
    got = [b.pose for b in script._poses(Script(title="t", beats=beats), ch)]
    # not on the opening sentence (too few words said), not an unknown one, not on the last sentence
    assert got == ["", "aha", "aha", "shocked", "", ""]
    assert "shocked (a surprising fact)" in script.pose_note(ch) and script.pose_note(channel.make("physics", "P")) == ""


def test_the_character_stays_on_screen_the_whole_video(tmp_path):
    names = ["base", "shocked", "point", "talk1", "talk2", "react"]
    for n in names:
        (tmp_path / f"{n}.png").write_bytes(b"x")
    f = {n: tmp_path / f"{n}.png" for n in names}
    ch = channel.make("physics", "Prof").model_copy(update={
        "character": str(f["base"]), "presenter": str(f["point"]), "reactions": [str(f["react"])],
        "poses": {"shocked": str(f["shocked"]), "talking-1": str(f["talk1"]), "talking-2": str(f["talk2"])}})
    beats = [Beat(text="a"), Beat(text="b", pose="shocked"), Beat(text="c"), Beat(text="d"), Beat(text="e"), Beat(text="f")]
    timing = Timings(words=[], beats=[(0.3, 3), (3, 6), (6, 9), (9, 12), (12, 15), (15, 17)], duration=17.0, matched=1.0)
    build.picture_times[:] = [(7.0, "drawing")]
    plan = dict(build.character_plan(Script(title="t", beats=beats), timing, ch, 15.0, seed=0))
    assert plan == {f["base"]: [(0.0, 3)], f["shocked"]: [(3, 6)], f["point"]: [(6, 9)], f["talk1"]: [(9, 12)],
                    f["talk2"]: [(12, 15.0)], f["react"]: [(15.0, 18.0)]}
