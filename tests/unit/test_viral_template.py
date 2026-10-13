"""D156: the second research report in the build and the writer (taps, punch-ins, bits, shapes, the character)."""

from __future__ import annotations

import itertools

from clipper.create import build, channel, script, sound, store
from clipper.create.script import Beat, Script, Visual
from clipper.create.voice import TimedWord, Timings


def test_chalk_taps_are_few_and_spaced():
    assert sound.spaced([12.0, 1.0, 3.0, 7.0, 20.0, 26.0, 31.0, 37.0, 44.0], 5.0, 6) == [1.0, 7.0, 12.0, 20.0, 26.0, 31.0]


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


def test_shapes_never_repeat_the_last_scripts_and_the_ending_is_always_the_loop(data_root):
    ch = channel.make("physics", "Prof")
    channel.save(ch)
    with channel.use(ch.slug):
        store.add_video(None, Script(title="a", beats=[Beat(text="x")], shape=ch.shapes[0], ending="loop").model_dump())
        store.add_video(None, Script(title="b", beats=[Beat(text="x")], shape=ch.shapes[1], ending="send").model_dump())
        for _ in range(20):
            shape, ending = script.turn(ch)
            assert shape not in ch.shapes[:2] and ending == "loop"   # D195


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
    # the opening may react (D202); not an unknown one, not on the last sentence
    assert got == ["shocked", "aha", "aha", "shocked", "", ""]
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
    # the opening talks from OPEN_POP in (D202), its first frame still the channel's picture
    assert plan == {f["base"]: [(0.0, 0.6)], f["talk1"]: [(0.6, 3), (12, 15.0)], f["shocked"]: [(3, 6)],
                    f["point"]: [(6, 9)], f["talk2"]: [(9, 12)], f["react"]: [(15.0, 18.0)]}
    beats[0] = Beat(text="a", pose="shocked")   # the writer's pose for the opening
    plan = dict(build.character_plan(Script(title="t", beats=beats), timing, ch, 15.0, seed=0))
    assert plan[f["base"]] == [(0.0, 0.6)] and plan[f["shocked"]] == [(0.6, 6)]


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


def test_a_magenta_picture_is_cut_out_and_its_mouth_version_lines_up(tmp_path):
    """D196: Gemini's flat magenta comes out by itself, and a -open version is cut exactly as its picture."""
    from PIL import Image, ImageDraw

    from clipper.create import compose

    def pose(path, mouth):
        img = Image.new("RGB", (400, 400), (255, 0, 255))
        d = ImageDraw.Draw(img)
        d.ellipse((100, 60, 300, 380), fill=(240, 200, 170), outline=(255, 255, 255), width=8)
        d.rectangle((170, 280, 230, 280 + mouth), fill=(60, 20, 20))
        img.save(path)

    pose(tmp_path / "p.png", 6)
    pose(tmp_path / "p-open.png", 40)
    base = compose.load_sprite(tmp_path / "p.png", 300)
    assert base.alpha[0, 0] == 0 and base.alpha[150, base.size[0] // 2] == 1   # magenta gone, the face kept
    assert base.rgb[..., 1].max() > 100                                        # no magenta tint left on the face
    opened = compose.load_variant(tmp_path / "p-open.png", base, 300)
    assert opened is not None and opened.size == base.size
    shows = [(tmp_path / "p.png", 0.0, 2.0)]
    talk = [k % 6 < 3 for k in range(60)]
    p = compose.Presenter(shows, 60, talk=talk, seed=1)
    assert p.sprite_at(0) is p.versions[(tmp_path / "p.png", "open")] and p.sprite_at(4) is p.sprites[tmp_path / "p.png"]
    assert not p.changes   # the mouth moving is no pose change: no pop


def test_the_presenter_can_go_away_and_come_back(tmp_path):
    """D203: "opening and punchline" takes him off screen between them, sliding out and back in."""
    import numpy as np
    from PIL import Image, ImageDraw

    from clipper.create import compose

    img = Image.new("RGB", (400, 400), (255, 0, 255))
    ImageDraw.Draw(img).ellipse((100, 60, 300, 380), fill=(240, 200, 170))
    img.save(tmp_path / "p.png")
    shows = [(tmp_path / "p.png", 0.0, 1.0), (tmp_path / "p.png", 2.0, 3.0)]
    p = compose.Presenter(shows, 90, gaps=True)
    assert p.runs == [(0, 30), (60, 90)] and p.sprite_at(45) is None and not p.changes

    def shown(k):
        frame = np.zeros((compose.H, compose.W, 3), np.uint8)
        p.draw(frame, k)
        return int((frame.sum(axis=2) > 0).sum())

    assert shown(15) > shown(25) > shown(28) and shown(45) == 0   # in full, sliding down, gone
    assert shown(61) < shown(75)                                  # rising back in for the punchline
    assert compose.Presenter(shows, 90).runs == [(0, 90)]         # without gaps he stays all video


def test_the_mouth_opens_on_words_and_long_sentences_change_picture(tmp_path):
    from clipper.create import channel

    words = [TimedWord(text="w", start=s, end=s + 0.3) for s in (0.2, 0.6, 1.0)]
    timing = Timings(words=words, beats=[(0, 1.5)], duration=1.5, matched=1.0)
    mouth = build.talking_frames(tmp_path / "missing.wav", timing, 45)    # no voice: the words' steady pace
    assert not any(mouth[:4]) and any(mouth[6:15]) and not all(mouth[6:15])
    names = ["base", *(f"talking-{i}" for i in range(1, 4))]
    for n in names:
        (tmp_path / f"{n}.png").write_bytes(b"x")
    f = {n: tmp_path / f"{n}.png" for n in names}
    ch = channel.make("physics", "Prof").model_copy(update={
        "character": str(f["base"]), "poses": {n: str(f[n]) for n in names[1:]}})
    long_words = [TimedWord(text="w", start=3 + 0.5 * i, end=3.4 + 0.5 * i) for i in range(16)]
    timing = Timings(words=long_words, beats=[(0.3, 3), (3, 11)], duration=12.0, matched=1.0)
    build.picture_times[:] = []
    plan = build.character_plan(Script(title="t", beats=[Beat(text="a"), Beat(text="b")]), timing, ch, 11.0, seed=0)
    pictures_in_long_sentence = [p for p, times in plan for a, b in times if 3 <= a < 11]
    assert len(pictures_in_long_sentence) >= 3   # an 8 s sentence isn't one still picture
