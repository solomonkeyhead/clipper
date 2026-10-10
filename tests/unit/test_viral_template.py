"""D156: the second research report in the build and the writer (taps, punch-ins, bits, shapes, the character)."""

from __future__ import annotations

import itertools

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
    assert got and 0 not in got and 4 not in got    # not the hook, not the punchline
    assert len(got) <= build.PUNCHES
    times = sorted(v[0] for v in got.values())
    assert all(b - a >= build.PUNCH_GAP for a, b in itertools.pairwise(times))


def test_a_running_bit_comes_at_most_every_third_script_and_counts_its_uses(data_root):
    bits = [{"name": "coffee", "how": "Use the cup."}, {"name": "grudge", "how": "One grudge."}]
    ch = channel.make("physics", "Prof").model_copy(update={"bits": bits})   # the pack has none since D190
    channel.save(ch)
    with channel.use(ch.slug):
        name, how = script.next_bit(ch)
        assert name == "coffee" and how
        store.add_video(None, Script(title="a", beats=[Beat(text="x")], bit="coffee").model_dump())
        assert script.next_bit(ch) == ("", "")      # never two running
        store.add_video(None, Script(title="b", beats=[Beat(text="x")]).model_dump())
        assert script.next_bit(ch) == ("", "")      # at most one in three
        store.add_video(None, Script(title="c", beats=[Beat(text="x")]).model_dump())
        name, _ = script.next_bit(ch)
        assert name == "grudge"                      # the least used next


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

    from clipper.create import compose

    Image.new("RGB", (400, 400), (90, 140, 200)).save(tmp_path / "pfp.png")
    badge = compose.load_sprite(tmp_path / "pfp.png", build.CHARACTER_HEIGHT)
    w, h = badge.size
    assert h == build.CHARACTER_HEIGHT and badge.alpha[0, 0] == 0      # round: the corner is clear
    assert badge.alpha[h // 2, w // 2] == 1


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


def test_transition_modes():
    """D165: plain cuts have none, whip or zoom is used at every change of kind, auto alternates."""
    from clipper.create import compose

    def run(mode):
        shots = [compose.Shot(path=None, start=i * 30, frames=30, kind="footage" if i % 2 == 0 else "drawing")
                 for i in range(7)]
        compose.transitions(shots, 0, mode)
        return {s.enter for s in shots[1:]}

    assert run("cut") == {"cut"}
    assert run("whip") == {"whip", "cut"} and run("zoom") == {"zoom", "cut"}
    assert {"whip", "zoom"} <= run("auto") | {"whip", "zoom"}
