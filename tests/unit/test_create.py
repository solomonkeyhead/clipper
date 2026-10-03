"""Create: original Shorts for the user's own channel (create/, D108)."""

from __future__ import annotations

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

    def test_nothing_fitting_means_no_footage_but_no_answer_trusts_the_search(self, monkeypatch):
        monkeypatch.setattr(stock, "search", lambda q: HITS)
        monkeypatch.setattr(stock, "_judge", lambda sentence, q, hits: None)
        assert stock.choose(["man yawning airplane"], 2.0, used=set(), sentence="s") is None  # a chalk card instead

        def busy(sentence, q, hits):
            raise stock._NoAnswer
        monkeypatch.setattr(stock, "_judge", busy)
        assert stock.choose(["man yawning airplane"], 2.0, used=set(), sentence="s")["id"] == 1  # the search's own best

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
