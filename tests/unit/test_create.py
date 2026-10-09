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
        s = tidy(script(("Why do you get so dizzy after spinning around?", d), ("Fluid moves.", d), ("It keeps moving.", d),
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
        monkeypatch.setattr(stock, "_judge", lambda sentence, q, hits, ctx="", good=7: (seen.append([h["id"] for h in hits]) or [hits[1]], []))
        assert stock.choose(["ear close up", "headphones"], 6.0, used={4}, sentence="Your middle ear")[0]["id"] == 2
        assert seen[0] == [3, 2, 1]  # long enough and vertical, long enough, too short; 4 already used

    def test_nothing_fitting_or_no_judge_means_a_chalk_card(self, monkeypatch):
        monkeypatch.setattr(stock, "search", lambda q: HITS)
        monkeypatch.setattr(stock, "_judge", lambda sentence, q, hits, ctx="", good=7: ([], []))
        assert stock.choose(["man yawning airplane"], 2.0, used=set(), sentence="s") == []  # a chalk card instead

        def busy(sentence, q, hits, ctx="", good=7):
            raise stock._NoAnswer
        monkeypatch.setattr(stock, "_judge", busy)
        assert stock.choose(["man yawning airplane"], 2.0, used=set(), sentence="s") == []  # no judge: a card too

    def test_one_judgement_serves_every_part_of_a_long_sentence(self, monkeypatch):
        """D181: a sentence long enough for two clips was judged once a part."""
        monkeypatch.setattr(stock, "_thumb", lambda h: b"jpg")
        monkeypatch.setattr(stock, "search", lambda q: HITS)
        calls = []
        # Shown in the pool's order (long enough and vertical first): 3, 2, 4, then 1 (too short).
        monkeypatch.setattr(stock, "ask", lambda *a, **k: calls.append(1) or
                            '{"scores": [9, 4, 8, 7], "centers": [0.5, 0.5, 0.3, 0.5]}')
        got = stock.choose(["ear"], 6.0, used=set(), sentence="Your ear.", count=2)
        assert [h["id"] for h in got] == [3, 4] and got[1]["center"] == 0.3 and calls == [1]

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
    # Nothing drawn at the panel's bottom edge, where the captions begin.
    band = img.crop((0, BOTTOM + 5, W, H)).convert("L")
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
    s = tidy(script(("Why do you get so dizzy after spinning around?", Visual()), ("Fake.", fake), ("Gap.", Visual()), ("Bars.", untitled),
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
    below_right, above_right = (620, 580, 1000, 900), (620, 260, 1000, 520)   # the surface is at y ~540 (D162)
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

    def test_one_library_is_enough_and_nasa_needs_no_key(self, monkeypatch):
        for name in ("PEXELS_API_KEY", "PIXABAY_API_KEY", "COVERR_API_KEY"):
            monkeypatch.delenv(name, raising=False)
        monkeypatch.setattr(stock, "nasa", lambda q: (_ for _ in ()).throw(CreateError("NASA's library didn't answer")))
        with pytest.raises(CreateError, match="NASA"):          # nothing answered: says which
            stock.search("rocket launch")
        monkeypatch.setattr(stock, "nasa", lambda q: [])
        monkeypatch.setenv("PEXELS_API_KEY", "p")
        monkeypatch.setattr(stock, "pexels", lambda q: [{"id": "pexels-1"}])
        assert stock.search("rocket launch") == [{"id": "pexels-1"}]


def test_claude_goes_first_only_with_a_key(monkeypatch):
    from clipper.config import Config
    from clipper.create import ai

    monkeypatch.setenv("GEMINI_API_KEY", "g")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    config = Config.load()
    assert all(b.name != "anthropic" for b in ai.backends(config))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    # D142: the paid key does the drawing and nothing else, unless the user lists more jobs.
    first = ai.backends(config, job="sketch")[0]
    assert first.name == "anthropic" and first.model == config.llm.create_model
    for job in ("script", "topics", "footage", ""):
        assert all(b.name != "anthropic" for b in ai.backends(config, job=job)), job
    more = config.model_copy(update={"llm": config.llm.model_copy(update={"paid_api_jobs": ["sketch", "script"]})})
    assert ai.backends(more, job="script")[0].name == "anthropic"
    free = config.model_copy(update={"llm": config.llm.model_copy(update={"create_model": None})})
    assert all(b.name != "anthropic" for b in ai.backends(free))


def test_a_loose_match_is_not_good_enough(monkeypatch):
    monkeypatch.setattr(stock, "_thumb", lambda h: b"jpg")
    for score, expect in ((6, []), (7, [2])):
        monkeypatch.setattr(stock, "ask", lambda *a, score=score, **k:
                            f'{{"scores": [5, {score}, 3], "centers": [0.1, 0.5, 0.9]}}')
        good, _ = stock._judge("Your voice sounds deeper inside your head.", "voice", HITS[:3])
        assert [h["id"] for h in good] == expect
    assert good[0]["center"] == 0.5  # where the subject is, for the crop


def test_the_judge_sees_the_script_and_searches_again_with_its_own_words(monkeypatch):
    """D124: nothing good enough, so its better searches get one more look; Gemini first."""
    monkeypatch.setattr(stock, "_thumb", lambda h: b"jpg")
    results = {"wall music": HITS[:2], "subwoofer speaker": HITS[2:4]}
    monkeypatch.setattr(stock, "search", lambda q: results.get(q, []))
    prompts, answers = [], iter(['{"scores": [3, 2], "better": ["subwoofer speaker", "wall music"]}',
                                 '{"scores": [8, 4]}'])
    seen_kw = []

    def fake(system, user, schema, **kw):
        prompts.append(user)
        seen_kw.append(kw)
        return next(answers)

    monkeypatch.setattr(stock, "ask", fake)
    hit = stock.choose(["wall music"], 2.0, set(), sentence="Like music through a wall.", context="A video about sound.")
    assert hit and hit[0]["id"] in (3, 4) and len(prompts) == 2
    assert "A video about sound." in prompts[0] and all(k.get("job") == "footage" for k in seen_kw)


def test_footage_goes_to_gemini_first_and_isnt_stopped_by_claude_only(monkeypatch, data_root):
    from clipper.create import ai

    class B:
        def __init__(self, name):
            self.name, self.calls = name, 0

        def describe(self):
            return self.name

        def complete(self, request):
            self.calls += 1
            return type("R", (), {"text": self.name})()

    claude, gemini = B("claude_code"), B("gemini")
    monkeypatch.setattr(ai, "backends", lambda config, model=None, job="": [claude, gemini])
    assert ai.ask("s", "u", None, temperature=0.0, quick=True, job="footage") == "gemini"
    assert ai.ask("s", "u", None, temperature=0.0) == "claude_code"   # everything else: Claude first


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

    from clipper.create.build import W, _stock_shot
    from clipper.create.compose import PANEL_H as H
    from clipper.ingest.probe import probe

    if not shutil.which("ffmpeg"):
        pytest.skip("no ffmpeg")
    # A wide black clip with a white block right of its middle: the subject.
    src = tmp_path / "wide.mp4"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "color=black:size=1920x1080:rate=30",
                    "-vf", "drawbox=x=1100:y=400:w=200:h=280:color=white:t=fill", "-t", "2", str(src)], check=True)
    out = _stock_shot(src, 1.5, tmp_path / "out.mp4", center=1200 / 1920)
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
        band = img.crop((0, BOTTOM + 5, W, img.height)).convert("L")   # the panel's bottom edge stays clear
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

        first = {"marks": self.MARKS[:3]}
        fixed = {"marks": self.MARKS}
        seen = []

        def ask(system, user, schema, *, temperature, media=None, **_):
            seen.append(bool(media))
            if schema is sketch._Drawn:
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


def test_a_tiny_or_off_scale_sketch_is_fitted_to_the_board_and_an_empty_one_refused():
    from clipper.create.sketch import FIT_X, FIT_Y, Mark, Sketch, fit

    tiny = Sketch(marks=[Mark(kind="circle", xy=[0.2, 0.3, 0.05]), Mark(kind="arrow", xy=[0.3, 0.3, 0.45, 0.3]),
                         Mark(kind="dot", xy=[0.5, 0.32]),
                         Mark(kind="line", xy=[0.1, 0.1, 0.2])])  # normalised numbers, and one mark short of a point
    fitted = fit(tiny)
    assert [m.kind for m in fitted.marks] == ["circle", "arrow", "dot"]  # the broken line is dropped
    xs = [v for m in fitted.marks for v in (m.xy[:1] + m.xy[2:3] if m.kind == "arrow" else m.xy[:1])]
    assert min(xs) >= FIT_X[0] - 1 and max(xs) <= FIT_X[1] + 1 and max(xs) - min(xs) > 500  # fills the width
    assert all(FIT_Y[0] - 1 <= m.xy[1] <= FIT_Y[1] + 1 for m in fitted.marks)
    with pytest.raises(CreateError):
        fit(Sketch(marks=[Mark(kind="text", xy=[1, 1], text="air"), Mark(kind="text", xy=[2, 2], text="bone")]))


def test_a_small_icon_is_drawn_bigger_but_never_into_its_neighbour():
    """D170: models gave icons 120-170 wide at either end of a long arrow; on a phone they were thumbnails."""
    from clipper.create.sketch import Mark, Sketch, fit

    far = fit(Sketch(marks=[Mark(kind="icon", text="magnet", xy=[100, 450, 60]), Mark(kind="icon", text="paperclip", xy=[900, 450, 50]),
                            Mark(kind="arrow", xy=[200, 450, 800, 450])]))
    assert all(m.xy[2] >= 200 for m in far.marks if m.kind == "icon")
    near = fit(Sketch(marks=[Mark(kind="icon", text="sun", xy=[100, 100, 30]), Mark(kind="icon", text="cloud", xy=[140, 100, 30]),
                             Mark(kind="arrow", xy=[100, 400, 900, 400])]))
    a, b = [m for m in near.marks if m.kind == "icon"]
    assert a.xy[2] / 2 + b.xy[2] / 2 < abs(a.xy[0] - b.xy[0])     # grown, but they don't touch


def test_a_sketch_is_kept_out_of_the_button_corner_and_its_labels_off_its_lines():
    """D182: half the reviewer's fixes were these, each one another call."""
    from clipper.create import sketch
    from clipper.create.sketch import CORNER, Mark, Sketch, fit

    got = fit(Sketch(marks=[Mark(kind="icon", text="speaker", xy=[100, 100, 200]), Mark(kind="icon", text="ear", xy=[900, 800, 200]),
                            Mark(kind="arrow", xy=[200, 100, 900, 700]), Mark(kind="text", xy=[550, 400], text="air"),
                            Mark(kind="box", xy=[150, 650, 350, 750]), Mark(kind="text", xy=[250, 700], text="list")]))
    discs, boxes = sketch._covered(got.marks)
    assert not any(x + r > CORNER[0] and y + r > CORNER[1] for x, y, r in discs)
    assert not any(b[2] > CORNER[0] and b[3] > CORNER[1] for b in boxes)
    air, listed = [m for m in got.marks if m.kind == "text"]
    x0, y0, x1, y1 = sketch._letters(air)
    assert not sketch._hits((air.xy[0] + x0, air.xy[1] + y0, air.xy[0] + x1, air.xy[1] + y1), discs, boxes)   # off the arrow
    frame = next(m for m in got.marks if m.kind == "box").xy
    assert frame[0] < listed.xy[0] < frame[2] and frame[1] < listed.xy[1] < frame[3]   # words in a box stay in it
    assert sketch._Review.model_validate({"ok": True}).sketch.marks == []   # an ok review sends no sketch back


def test_a_black_shot_is_caught(tmp_path):
    import shutil
    import subprocess

    from clipper.create.build import _too_dark

    if not shutil.which("ffmpeg"):
        pytest.skip("no ffmpeg")
    for name, source, dark in (("black", "color=black:size=270x480", True), ("bars", "testsrc2=size=270x480", False)):
        out = tmp_path / f"{name}.mp4"
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", f"{source}:rate=30", "-t", "1",
                        "-pix_fmt", "yuv420p", str(out)], check=True)
        assert _too_dark(out) is dark


def test_claude_through_claude_code_on_the_users_plan(monkeypatch):
    """No API key, Claude Code installed: Create asks Claude through it (D113); images are
    handed over as files it may only read, the schema as --json-schema."""
    import json
    import subprocess

    from clipper.config import Config
    from clipper.create import ai
    from clipper.llm import claude_code
    from clipper.llm.base import LLMRequest

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "g")
    monkeypatch.setattr(claude_code, "cli", lambda: "/usr/bin/claude")
    first = ai.backends(Config.load())[0]
    assert first.name == "claude_code" and first.model == "claude-opus-5-5"

    seen = {}

    def run(args, input, cwd, **k):
        from pathlib import Path

        seen.update(args=args, prompt=input, files=sorted(p.name for p in Path(cwd).iterdir()))
        return subprocess.CompletedProcess(args, 0, json.dumps(
            {"type": "result", "is_error": False, "result": "", "structured_output": {"scores": [2, 8]}}), "")
    monkeypatch.setattr(claude_code.subprocess, "run", run)
    reply = first.complete(LLMRequest(system="judge", user="Sentence: x", response_schema=stock._Ranks,
                                      media=[(b"jpg", "image/jpeg"), (b"jpg", "image/jpeg")]))
    assert json.loads(reply.text) == {"scores": [2, 8]}
    assert seen["files"] == ["1.jpg", "2.jpg", "instructions.txt"] and "1.jpg, 2.jpg" in seen["prompt"]
    args = seen["args"]
    assert args[args.index("--tools") + 1] == "Read" and "--json-schema" in args and "--no-session-persistence" in args

    def limited(args, input, cwd, **k):
        return subprocess.CompletedProcess(args, 1, json.dumps({"is_error": True, "result": "Usage limit reached"}), "")
    monkeypatch.setattr(claude_code.subprocess, "run", limited)
    from clipper.llm.base import LLMError
    with pytest.raises(LLMError):
        first.complete(LLMRequest(system="s", user="u"))


def test_a_label_with_a_line_break_still_draws():
    from clipper.create.diagrams import frame
    from clipper.create.sketch import Sketch

    marks = [{"kind": "circle", "xy": [500, 300, 100]}, {"kind": "dot", "xy": [500, 300]},
             {"kind": "text", "xy": [500, 480], "text": "air\nconducted"}]
    frame(Visual(kind="diagram", template="sketch", sketch=Sketch.model_validate({"marks": marks})), 3.9, 4.0)


def test_on_windows_claude_cmd_gets_no_json_or_line_breaks_on_its_command_line(monkeypatch):
    import json
    import subprocess

    from clipper.llm import claude_code
    from clipper.llm.base import LLMRequest

    monkeypatch.setattr(claude_code, "cli", lambda: r"C:\\Users\\m\\AppData\\Roaming\\npm\\claude.cmd")
    seen = {}

    def run(args, input, cwd, **k):
        seen.update(args=args, prompt=input)
        return subprocess.CompletedProcess(args, 0, json.dumps(
            {"is_error": False, "result": 'Here you go:\n```json\n{"scores": [3, 9]}\n```'}), "")
    monkeypatch.setattr(claude_code.subprocess, "run", run)
    reply = claude_code.ClaudeCodeBackend().complete(
        LLMRequest(system="line one\nline two", user="u", response_schema=stock._Ranks))
    assert json.loads(reply.text) == {"scores": [3, 9]}
    assert "--json-schema" not in seen["args"] and "--system-prompt-file" in seen["args"]
    assert not any("\n" in a for a in seen["args"]) and "JSON Schema" in seen["prompt"]


def test_when_claude_is_out_of_usage_create_stops_instead_of_using_gemini(monkeypatch):
    from clipper.create import ai
    from clipper.llm.base import RateLimited

    class Claude:
        name = "claude_code"

        def describe(self):
            return "claude_code:claude-opus-5-5"

        def complete(self, request):
            raise RateLimited("Claude plan limit: You've hit your weekly limit")

    class Gemini:
        name = "gemini"

        def describe(self):
            return "gemini:flash"

        def complete(self, request):
            raise AssertionError("Gemini must not be asked")

    monkeypatch.setattr(ai, "backends", lambda config, model=None, job="": [Claude(), Gemini()])
    with pytest.raises(CreateError, match="weekly limit"):
        ai.ask("s", "u", None, temperature=0)


def test_an_opening_drawing_that_stood_in_for_footage_goes_back_to_footage():
    """D161: a build that found no footage for the opening kept a drawing; the next build looks again."""
    from clipper.create.build import retry_opening_footage
    from clipper.create.sketch import Sketch

    drawn = Visual(kind="diagram", template="sketch", queries=["ice cube in water"], sketch=Sketch(marks=[]))
    planned = Visual(kind="diagram", template="sketch", idea="an ice cube", queries=["ice"])
    picked = drawn.model_copy(update={"manual": True})
    s = Script(title="t", beats=[Beat(text="Why does ice float?", visual=drawn), Beat(text="Ice is lighter than the water around it.", visual=planned),
                                 Beat(text="Water shrinks as it cools down, all the way to four degrees.", visual=drawn)])
    out = retry_opening_footage(s).beats
    assert out[0].visual.kind == "stock" and out[0].visual.sketch is None
    assert out[1].visual.kind == "diagram"            # a drawing the writer planned stays
    assert out[2].visual.kind == "diagram"            # past the opening, the kept drawing stays
    assert retry_opening_footage(Script(title="t", beats=[Beat(text="a", visual=picked)])).beats[0].visual.kind == "diagram"


def test_icons_shading_and_sparks_draw_and_an_unknown_icon_is_written_instead():
    """D162: things are drawn from the icon set; a name it doesn't have is written as words, not lost."""
    from clipper.create import diagrams, icons
    from clipper.create.sketch import Sketch, fit

    assert all(icons.strokes(n) for n in icons.OFFERED) and icons.name_for("lightning") == "zap"
    sk = fit(Sketch(marks=[{"kind": "icon", "text": "speaker", "xy": [200, 450, 300]},
                           {"kind": "icon", "text": "flux-capacitor", "xy": [800, 450]},
                           {"kind": "zigzag", "xy": [350, 450, 650, 450], "color": "yellow"},
                           {"kind": "hatch", "xy": [300, 600, 700, 600, 700, 800, 300, 800], "color": "blue"}]))
    assert [m.kind for m in sk.marks] == ["icon", "text", "zigzag", "hatch"] and sk.marks[1].text == "flux capacitor"
    img = diagrams.frame(Visual(kind="diagram", template="sketch", sketch=sk), 9.0, 4.0)
    assert img.size == (diagrams.W, diagrams.H)
    square = [(100, 100), (300, 100), (300, 300), (100, 300)]
    lines = diagrams._hatch(square)
    assert len(lines) > 5 and all(95 <= x <= 305 and 95 <= y <= 305 for seg in lines for x, y in seg)


def test_a_template_drawing_is_scaled_to_fill_the_board():
    """D162: the templates were laid out for the old 640 px band and sat small at the top of the panel."""
    from clipper.create.diagrams import FILL_MAX, FILL_X, FILL_Y, _placement

    v = Visual(kind="diagram", template="forces", subject="you", labels=["gravity"], directions=["down"])
    (x0, _, x1, _), (w, h), (ax, ay) = _placement(v.model_dump_json(), 4.0)
    assert FILL_X[0] <= ax and ax + w <= FILL_X[1] and FILL_Y[0] <= ay and ay + h <= FILL_Y[1]
    assert h == FILL_Y[1] - FILL_Y[0] or w == FILL_X[1] - FILL_X[0] or w > (x1 - x0) / 2 * (FILL_MAX - 0.01)


def test_create_asks_the_best_free_gemini_first_and_skips_the_lite_ones(monkeypatch):
    from clipper.config import Config
    from clipper.create import ai

    monkeypatch.setenv("GEMINI_API_KEY", "test")
    config = Config.load()
    config = config.model_copy(update={"llm": config.llm.model_copy(update={"backend": "gemini"})})
    got = [b.model for b in ai.free_backends(config)]
    assert got == config.llm.create_gemini_models and got[0] == "gemini-3.8-flash"
    assert not any("lite" in m for m in got)


def test_a_few_photos_come_after_the_videos_and_download_as_pictures():
    """D162: photos are offered with the footage, after the videos that are long enough."""
    from clipper.create import stock

    vid = lambda i, d: {"id": f"pexels-{i}", "duration": d, "width": 1920, "height": 1080}  # noqa: E731
    photo = lambda i: {"id": f"photo-pixabay-{i}", "duration": 0, "width": 1280, "height": 853}  # noqa: E731
    pool = [vid(1, 2), photo(1), vid(2, 9), photo(2), photo(3), photo(4), *[vid(k, 9) for k in range(3, 20)]]
    got = stock._shortlist(pool, 5.0, 12)
    assert len(got) == 12 and [stock.is_photo(h) for h in got] == [False] * 9 + [True] * 3
    assert got[0]["id"] == "pexels-2" and got[-3]["id"] == "photo-pixabay-1"     # long enough first; photos in order
    assert stock.is_photo({"id": "photo-pexels-5"}) and not stock.is_photo({"id": 5})


def test_the_writer_writes_words_and_the_director_plans_the_pictures_after(data_root, monkeypatch):
    """D164: the writer's prompt has no picture rules; the director plans on the final words, once."""
    import json

    from clipper.create import script as scripts

    asked = []

    def ask(system, user, schema, **kw):
        asked.append((schema.__name__, system, user))
        if schema.__name__ == "WordsScript":
            return json.dumps({"lines": ["Why does the lift make you heavier?", "The floor pushes up harder.",
                                         "Gravity stays the same, so the floor wins today."],
                               "title": "Why lifts make you heavier", "hook": "Heavier in a lift", "hashtags": ["#a"]})
        if schema.__name__ == "Plan":
            return json.dumps({"beats": [
                {"text": "x", "emphasis": "heavier", "visual": {"kind": "stock", "queries": ["elevator doors"]}},
                {"text": "x", "emphasis": "harder", "visual": {"kind": "stock", "queries": ["feet on floor"]}},
                {"text": "x", "emphasis": "floor", "visual": {"kind": "diagram", "template": "forces", "subject": "you",
                                                             "labels": ["gravity", "floor"], "directions": ["down", "up"],
                                                             "values": [1, 2]}}]})
        return json.dumps({"ok": True, "problems": []})

    monkeypatch.setattr(scripts, "ask", ask)
    out, note = scripts.write_checked("Why does the lift make you heavier?")
    kinds = [a[0] for a in asked]
    # A rewrite (the short test script breaks the word count) is words again; the pictures are planned once, after.
    assert kinds.count("Plan") == 1 and kinds.index("Plan") > len(kinds) - 1 - kinds[::-1].index("WordsScript")
    writer_system = next(a[1] for a in asked if a[0] == "WordsScript")
    director_system = next(a[1] for a in asked if a[0] == "Plan")
    assert 'kind "stock"' not in writer_system and 'kind "stock"' in director_system and "director" in director_system
    assert [b.text for b in out.beats][1] == "The floor pushes up harder."           # the words are the writer's
    assert out.beats[2].visual.template == "forces" and out.beats[0].emphasis == "heavier"
    assert "Pictures:" in note


def test_an_unreadable_answer_goes_on_to_the_next_model(monkeypatch):
    """D167: a weak model's answer that doesn't fit the schema isn't the end; the next model is asked."""
    from clipper.create import ai
    from clipper.llm.base import LLMResponse

    class Fake:
        def __init__(self, name, text):
            self.name, self.text, self.timeout = name, text, 30

        def describe(self):
            return self.name

        def complete(self, req):
            return LLMResponse(text=self.text, model=self.name)

    bad, good = Fake("a", "not json"), Fake("b", '{"n": 1}')
    monkeypatch.setattr(ai, "_ordered", lambda *a, **k: ([bad, good], [], False, False))
    schema = __import__("pydantic").create_model("S", n=(int, ...))
    assert ai.ask("s", "u", schema, temperature=0) == '{"n": 1}'
    monkeypatch.setattr(ai, "_ordered", lambda *a, **k: ([bad], [], False, False))
    assert ai.ask("s", "u", schema, temperature=0) == "not json"     # none fit: the caller reports it


def test_json_inside_prose_is_cut_out_and_an_unreadable_answer_does_not_rest_its_model(monkeypatch):
    """D167: prose around the JSON is taken off; an unreadable answer isn't a failure that rests the model."""
    from clipper.create import ai
    from clipper.llm.base import LLMResponse

    class Fake:
        name, timeout = "a", 30

        def __init__(self, text):
            self.text = text

        def describe(self):
            return "a:m"

        def complete(self, req):
            return LLMResponse(text=self.text, model="m")

    schema = __import__("pydantic").create_model("S2", n=(int, ...))
    monkeypatch.setattr(ai, "_ordered", lambda *a, **k: ([Fake('Sure! Here it is: {"n": 2} Hope it helps.')], [], False, False))
    assert ai.ask("s", "u", schema, temperature=0) == '{"n": 2}'
    monkeypatch.setattr(ai, "_ordered", lambda *a, **k: ([Fake("nope")], [], False, False))
    ai.missed_at.pop("a:m", None)
    assert ai.ask("s", "u", schema, temperature=0) == "nope" and "a:m" not in ai.missed_at
