"""Create: original Shorts for the user's own channel (create/, D108)."""

from __future__ import annotations

from typing import ClassVar

import pytest

from clipper.create import stock, store
from clipper.create.ai import CreateError
from clipper.create.script import Beat, Script, Visual, tidy
from clipper.create.voice import TimedWord, align


def script(*beats: tuple[str, Visual]) -> Script:
    return Script(title="Why?", beats=[Beat(text=t, visual=v) for t, v in beats])


class TestTidy:
    def test_diagrams_never_first_or_three_running_and_templates_are_known(self):
        d = Visual(kind="diagram", template="chain", labels=["a", "b"])
        s = tidy(script(("Why do you get dizzy?", d), ("Fluid moves.", d), ("It keeps moving.", d),
                        ("And moving.", d), ("Your brain is confused.", Visual(kind="diagram", template="tornado"))))
        assert [b.visual.kind for b in s.beats] == ["stock", "diagram", "diagram", "stock", "stock"]
        assert s.beats[0].visual.queries and s.beats[0].visual.card  # searches and a chalk card from the sentence

    def test_emphasis_must_be_in_the_beat_and_at_most_five_hashtags(self):
        s = Script(title="t", beats=[Beat(text="Physics wins.", emphasis="wins"), Beat(text="Dignity does not.", emphasis="pop")],
                   hashtags=["physics", "#science", "a b", "#4", "#5", "#6"])
        s = tidy(s)
        assert [b.emphasis for b in s.beats] == ["wins", ""] and s.hashtags == ["#physics", "#science", "#ab", "#4", "#5"]


class TestVoiceTiming:
    def test_script_spelling_with_the_voices_times_and_beats_cut_between_sentences(self):
        s = script(("Why do ears pop?", Visual()), ("Pressure, Newton says.", Visual()))
        heard = [TimedWord(text=w, start=i * 0.5, end=i * 0.5 + 0.4)
                 for i, w in enumerate(["why", "do", "ears", "pop", "pressure", "nuton", "says"])]
        t = align(s, heard, audio_seconds=4.0)
        assert [w.text for w in t.words][-2:] == ["Newton", "says."]   # the script's spelling
        assert t.words[5].start > t.words[4].end                       # a missed word sits between its neighbours
        assert t.beats[0] == (0.0, 2.0) and t.beats[1][0] == 2.0       # beat 2 starts on "Pressure"
        assert t.duration == pytest.approx(3.4 + 0.6)  # a breath after the punchline

    def test_a_voice_for_another_script_is_refused(self):
        s = script(("Why do ears pop on planes today?", Visual()))
        heard = [TimedWord(text=w, start=0, end=0.3) for w in ["completely", "different", "words"]]
        with pytest.raises(CreateError, match="doesn't match"):
            align(s, heard, audio_seconds=3)


HITS = [{"id": i, "duration": dur, "tags": "", "url": "", "width": w, "height": h, "thumb": "x"}
        for i, dur, w, h in [(1, 3, 1920, 1080), (2, 12, 1920, 1080), (3, 12, 1080, 1920), (4, 30, 1920, 1080)]]


class TestStock:
    def test_the_model_judges_all_searches_at_once_long_enough_and_vertical_first(self, monkeypatch):
        seen = []
        results = {"ear close up": HITS[:2], "headphones": HITS[2:]}
        monkeypatch.setattr(stock, "search", lambda q: results.get(q, []))
        monkeypatch.setattr(stock, "_judge", lambda sentence, q, hits: seen.append([h["id"] for h in hits]) or hits[1])
        assert stock.choose(["ear close up", "headphones"], 6.0, used={4}, sentence="Your middle ear")["id"] == 2
        assert seen[0] == [3, 2, 1]  # long enough and vertical, long enough, too short; 4 already used

    def test_nothing_fitting_or_no_judge_means_a_chalk_card(self, monkeypatch):
        monkeypatch.setattr(stock, "search", lambda q: HITS)
        monkeypatch.setattr(stock, "_judge", lambda sentence, q, hits: None)
        assert stock.choose(["man yawning airplane"], 2.0, used=set(), sentence="s") is None  # a chalk card instead

        def busy(sentence, q, hits):
            raise stock._NoAnswer
        monkeypatch.setattr(stock, "_judge", busy)
        assert stock.choose(["man yawning airplane"], 2.0, used=set(), sentence="s") is None  # no judge: a card too

    def test_green_screen_footage_is_left_out(self, monkeypatch, tmp_path):
        import httpx

        class Reply:
            def raise_for_status(self):
                pass

            def json(self):
                video = {"tiny": {"url": "u", "width": 1080, "height": 1920, "thumbnail": "t"}}
                return {"hits": [{"id": 1, "tags": "skeleton, green screen", "videos": video},
                                 {"id": 2, "tags": "skeleton, halloween", "videos": video}]}
        monkeypatch.setattr(stock, "_dir", lambda: tmp_path)
        monkeypatch.setattr(stock, "_key", lambda: "k")
        monkeypatch.setattr(httpx, "get", lambda *a, **k: Reply())
        assert [h["id"] for h in stock.search("dancing skeleton")] == [2]


def test_topics_and_videos_in_the_database(data_root):
    assert store.add_topics([{"question": "Why do ears pop?", "angle": "pressure", "felt": True},
                             {"question": "why do ears  pop?", "angle": "dup", "felt": True},
                             {"question": "Why is the sky blue?", "angle": "scattering", "felt": False}]) == 2
    assert [t["question"] for t in store.topics()] == ["Why do ears pop?", "Why is the sky blue?"]  # felt first
    vid = store.add_video(1, {"title": "t", "beats": []}, "ok")
    store.update_video(vid, status="voiced", timings={"beats": []})
    v = store.video(vid)
    assert v["status"] == "voiced" and v["timings"] == {"beats": []} and v["script"]["title"] == "t"
    with pytest.raises(ValueError):
        store.update_video(vid, status="nope")


@pytest.mark.parametrize("v", [
    Visual(kind="diagram", template="forces", title="Elevator", labels=["gravity", "floor"], directions=["down", "up"], values=[1, 2]),
    Visual(kind="diagram", template="circle", labels=["you", "door"]),
    Visual(kind="diagram", template="equation", equation="F = Δp ÷ Δt", labels=["F: force"]),
    Visual(kind="diagram", template="compare", labels=["metal", "wood"], values=[9, 2]),
    Visual(kind="diagram", template="chain", labels=["cold", "slow ions", "less current"]),
    Visual(kind="diagram", template="graph", labels=["altitude", "boiling point"], shape="falling"),
    Visual(kind="diagram", template="card", title="bone conduction"),
    Visual(kind="diagram", template="forces", subject="you", labels=["gravity"], directions=["down"]),
    Visual(kind="diagram", template="wave", title="Pitch", labels=["low note", "high note"], values=[1, 3]),
    Visual(kind="diagram", template="particles", labels=["cold air", "hot air"], values=[1, 2], amounts=[2, 1]),
    Visual(kind="diagram", template="ray", title="Straw in water", labels=["air", "water"], values=[1.0, 1.33]),
    Visual(kind="diagram", template="ray", labels=["water", "air"], values=[1.33, 1.0]),  # total internal reflection
    Visual(kind="diagram", template="number", title="343 m/s", labels=["speed of sound"]),
    Visual(kind="diagram", template="number", title="1,000x", labels=["more"]),
])
def test_every_diagram_draws_through_its_whole_sentence(v):
    from clipper.create.diagrams import BOTTOM, H, W, frame

    for t in (0.0, 1.0, 2.5, 3.9):
        img = frame(v, t, 4.0)
        assert img.size == (W, H)
    # Nothing drawn in the caption band.
    band = img.crop((0, BOTTOM + 60, W, BOTTOM + 160)).convert("L")
    assert max(band.getdata()) < 120


def test_the_physics_check_sees_the_diagrams():
    from clipper.create.script import _diagrams

    s = Script(title="t", beats=[Beat(text="Bone absorbs high frequencies.", visual=Visual(
        kind="diagram", template="graph", labels=["frequency", "loudness"], shape="rising"))])
    assert "shape rising" in _diagrams(s) and "Bone absorbs high frequencies." in _diagrams(s)


def test_no_made_up_equations_or_bars_of_nothing():
    fake = Visual(kind="diagram", template="equation", equation="sound means air plus bone")
    untitled = Visual(kind="diagram", template="compare", labels=["bone", "tissue"], values=[90, 40])
    real = Visual(kind="diagram", template="equation", equation="F = m x a")
    s = tidy(script(("Why?", Visual()), ("Fake.", fake), ("Gap.", Visual()), ("Bars.", untitled),
                    ("Gap.", Visual()), ("Real.", real)))
    assert [b.visual.kind for b in s.beats] == ["stock", "stock", "stock", "stock", "stock", "diagram"]
    vague = Visual(kind="diagram", template="number", title="a lot", labels=["of energy"])
    assert tidy(script(("Why?", Visual()), ("Vague.", vague))).beats[1].visual.kind == "stock"  # a number needs one


def test_the_ray_bends_by_snells_law():
    """Air to water bends toward the normal; water to air at 42 degrees reflects back."""
    from clipper.create.diagrams import frame

    def lit(v, box):
        return sum(1 for px in frame(v, 3.9, 4.0).crop(box).getdata() if px[0] > 200 and px[2] < 120)

    into_water = Visual(kind="diagram", template="ray", labels=["air", "water"], values=[1.0, 1.33])
    out_of_water = Visual(kind="diagram", template="ray", labels=["water", "air"], values=[1.33, 1.0])
    below_right, above_right = (620, 760, 1000, 1040), (620, 400, 1000, 700)
    assert lit(into_water, below_right) > 500 and lit(into_water, above_right) < 50
    assert lit(out_of_water, above_right) > 500 and lit(out_of_water, below_right) < 50


class TestPexels:
    def test_pexels_clips_are_read_kept_apart_from_pixabays_and_both_libraries_alternate(self, monkeypatch, tmp_path):
        import httpx

        class Reply:
            def __init__(self, body):
                self.body = body

            def raise_for_status(self):
                pass

            def json(self):
                return self.body

        pexels = {"videos": [
            {"id": 7, "duration": 9, "url": "https://www.pexels.com/video/woman-wearing-headphones-7/",
             "image": "t", "video_files": [
                 {"file_type": "video/mp4", "width": 720, "height": 1280, "link": "small"},
                 {"file_type": "video/mp4", "width": 1080, "height": 1920, "link": "hd"},
                 {"file_type": "video/mp4", "width": 2160, "height": 3840, "link": "4k"}]},
            {"id": 8, "duration": 9, "url": "https://www.pexels.com/video/skeleton-green-screen-8/",
             "image": "t", "video_files": [{"file_type": "video/mp4", "width": 1080, "height": 1920, "link": "x"}]}]}
        video = {"tiny": {"url": "u", "width": 1080, "height": 1920, "thumbnail": "t"}}
        pixabay = {"hits": [{"id": 7, "tags": "headphones", "videos": video}, {"id": 9, "tags": "music", "videos": video}]}
        monkeypatch.setattr(stock, "_dir", lambda: tmp_path)
        monkeypatch.setenv("PEXELS_API_KEY", "p")
        monkeypatch.setenv("PIXABAY_API_KEY", "k")
        monkeypatch.setattr(httpx, "get", lambda url, **k: Reply(pexels if "pexels" in url else pixabay))
        hits = stock.search("headphones")
        assert [h["id"] for h in hits] == ["pexels-7", 7, 9]  # Pexels first, green screen dropped
        assert hits[0]["url"] == "hd" and hits[0]["tags"] == "woman wearing headphones"

    def test_one_library_is_enough_and_none_says_which_keys(self, monkeypatch):
        monkeypatch.delenv("PEXELS_API_KEY", raising=False)
        monkeypatch.delenv("PIXABAY_API_KEY", raising=False)
        with pytest.raises(CreateError, match="PEXELS_API_KEY"):
            stock.search("ear")
        monkeypatch.setenv("PEXELS_API_KEY", "p")
        monkeypatch.setattr(stock, "pexels", lambda q: [{"id": "pexels-1"}])
        assert stock.search("ear") == [{"id": "pexels-1"}]


def test_claude_goes_first_only_with_a_key(monkeypatch):
    from clipper.config import Config
    from clipper.create import ai

    monkeypatch.setenv("GEMINI_API_KEY", "g")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    config = Config.load()
    assert all(b.name != "anthropic" for b in ai.backends(config))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    first = ai.backends(config)[0]
    assert first.name == "anthropic" and first.model == config.llm.create_model
    free = config.model_copy(update={"llm": config.llm.model_copy(update={"create_model": None})})
    assert all(b.name != "anthropic" for b in ai.backends(free))


def test_a_loose_match_is_not_good_enough(monkeypatch):
    monkeypatch.setattr(stock, "_thumb", lambda h: b"jpg")
    for score, expect in ((6, None), (7, 2)):
        monkeypatch.setattr(stock, "ask", lambda *a, score=score, **k: f'{{"pick": 2, "score": {score}}}')
        hit = stock._judge("Your voice sounds deeper inside your head.", "voice", HITS[:3])
        assert (hit and hit["id"]) == expect
    assert hit["center"] == 0.5  # where the subject is, for the crop


def test_diagram_pieces_arrive_as_they_are_said():
    from clipper.create.diagrams import cues

    v = Visual(kind="diagram", template="chain", labels=["bone vibrates", "skull carries bass", "deeper voice"])
    said = [(0.0, "Your"), (0.3, "bones"), (0.8, "vibrate,"), (1.5, "and"), (1.7, "your"), (2.0, "skull"),
            (2.6, "carries"), (3.4, "the"), (3.6, "bass.")]
    assert cues(v, said) == [0.3, 2.0, None]  # "deeper" is never said: it keeps the default schedule
    # A later label said earlier than the one before it still waits its turn.
    assert cues(Visual(labels=["skull", "bones"]), said) == [2.0, None]


def test_wide_footage_is_cropped_tall_around_its_subject(tmp_path):
    import shutil
    import subprocess

    from PIL import Image

    from clipper.create.build import H, W, _stock_shot
    from clipper.ingest.probe import probe

    if not shutil.which("ffmpeg"):
        pytest.skip("no ffmpeg")
    # A wide black clip with a white block near its right edge: the subject.
    src = tmp_path / "wide.mp4"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "color=black:size=1920x1080:rate=30",
                    "-vf", "drawbox=x=1500:y=400:w=200:h=280:color=white:t=fill", "-t", "2", str(src)], check=True)
    out = _stock_shot(src, 1.5, tmp_path / "out.mp4", center=1600 / 1920)
    info = probe(out)
    assert (info.width, info.height) == (W, H) and info.duration == pytest.approx(1.5, abs=0.1)
    still = tmp_path / "still.png"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-ss", "0.7", "-i", str(out), "-frames:v", "1", str(still)],
                   check=True)
    img = Image.open(still).convert("L")
    xs = [x for x in range(0, W, 8) if img.getpixel((x, H // 2)) > 200]
    assert xs and abs((xs[0] + xs[-1]) / 2 - W / 2) < 120  # the block sits in the middle of the tall frame


class TestSketch:
    MARKS: ClassVar[list[dict]] = [
        {"kind": "loop", "xy": [380, 110, 560, 110, 640, 250, 600, 500, 430, 500, 330, 340]},
        {"kind": "circle", "xy": [420, 300, 26], "color": "dim"},
        {"kind": "curve", "xy": [640, 400, 800, 330, 560, 250, 450, 300], "color": "blue", "dashed": True, "cue": "air"},
        {"kind": "mover", "xy": [560, 440, 445, 320], "color": "yellow", "cue": "skull"},
        {"kind": "arrow", "xy": [100, 100, 200, 100]}, {"kind": "line", "xy": [100, 150, 200, 150], "dashed": True},
        {"kind": "dot", "xy": [50, 50]}, {"kind": "box", "xy": [700, 50, 900, 150]},
        {"kind": "wave", "xy": [100, 300, 300, 300], "cycles": 4, "size": 3},
        {"kind": "text", "xy": [220, 450], "text": "through bone", "color": "yellow", "cue": "skull"},
    ]

    def test_every_mark_draws_and_stays_out_of_the_captions(self):
        from clipper.create.diagrams import BOTTOM, W, frame
        from clipper.create.sketch import Sketch

        v = Visual(kind="diagram", template="sketch", title="Two ways", sketch=Sketch.model_validate({"marks": self.MARKS}))
        for t in (0.0, 1.0, 2.5, 3.9):
            img = frame(v, t, 4.0)
        band = img.crop((0, BOTTOM + 60, W, BOTTOM + 160)).convert("L")
        assert max(band.getdata()) < 120

    def test_marks_arrive_when_their_word_is_said(self):
        from clipper.create.diagrams import cues
        from clipper.create.sketch import Sketch

        v = Visual(kind="diagram", template="sketch", sketch=Sketch.model_validate({"marks": self.MARKS}))
        got = cues(v, [(0.4, "Through"), (0.9, "air"), (1.6, "and"), (2.1, "your"), (2.4, "skull.")])
        assert got[2] == 0.9 and got[3] == 2.4 and got[0] is None

    def test_the_sketcher_looks_at_its_drawing_and_fixes_it(self, monkeypatch):
        import json

        from clipper.create import sketch

        first = {"marks": self.MARKS[:2]}
        fixed = {"marks": self.MARKS}
        seen = []

        def ask(system, user, schema, *, temperature, media=None):
            seen.append(bool(media))
            if schema is sketch.Sketch:
                return json.dumps(first)
            return json.dumps({"ok": len(seen) > 2, "problems": ["the head is unrecognisable"], "sketch": fixed})

        monkeypatch.setattr(sketch, "ask", ask)
        drawn = sketch.draw("Sound travels through your skull.", "a head, two sound paths")
        assert len(drawn.marks) == len(self.MARKS) and seen == [False, True, True]  # drew, looked, looked again

    def test_a_sketch_beat_needs_an_idea_and_a_failed_sketch_becomes_footage(self, monkeypatch):
        from clipper.create import sketch

        empty = Visual(kind="diagram", template="sketch")
        assert tidy(script(("Why?", Visual()), ("How.", empty))).beats[1].visual.kind == "stock"

        def broken(*a, **k):
            raise CreateError("busy")
        monkeypatch.setattr(sketch, "draw", broken)
        planned = script(("Why?", Visual()), ("How.", Visual(kind="diagram", template="sketch", idea="a head")))
        drawn, notes = sketch.draw_all(planned)
        assert drawn.beats[1].visual.kind == "stock" and notes
