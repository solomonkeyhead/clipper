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
    def test_diagrams_never_first_or_twice_running_and_templates_are_known(self):
        d = Visual(kind="diagram", template="chain", labels=["a", "b"])
        s = tidy(script(("Why do you get dizzy?", d), ("Fluid moves.", d), ("It keeps moving.", d),
                        ("Your brain is confused.", Visual(kind="diagram", template="tornado"))))
        assert [b.visual.kind for b in s.beats] == ["stock", "diagram", "stock", "stock"]
        assert s.beats[0].visual.query  # a fallback query from the sentence

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

    def test_the_model_picks_from_long_enough_vertical_first_candidates(self, monkeypatch):
        seen = []
        monkeypatch.setattr(stock, "search", lambda q: HITS)
        monkeypatch.setattr(stock, "_judge", lambda sentence, q, hits: seen.append([h["id"] for h in hits]) or hits[1])
        assert stock.choose("ear close up", 6.0, used={4}, sentence="Your middle ear")["id"] == 2
        assert seen[0] == [3, 2, 1]  # long enough and vertical, long enough, too short; 4 already used

    def test_none_fitting_tries_fewer_words_but_no_answer_trusts_the_search(self, monkeypatch):
        asked = []
        monkeypatch.setattr(stock, "search", lambda q: asked.append(q) or HITS)
        monkeypatch.setattr(stock, "_judge", lambda sentence, q, hits: None)
        assert stock.choose("man yawning airplane", 2.0, used=set(), sentence="s")["id"] == 3
        assert asked == ["man yawning airplane", "yawning airplane", stock.FALLBACK]

        def busy(sentence, q, hits):
            raise stock._NoAnswer
        monkeypatch.setattr(stock, "_judge", busy)
        assert stock.choose("man yawning airplane", 2.0, used=set(), sentence="s")["id"] == 3


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
])
def test_every_diagram_draws_through_its_whole_sentence(v):
    from clipper.create.diagrams import BOTTOM, H, W, frame

    for t in (0.0, 1.0, 2.5, 3.9):
        img = frame(v, t, 4.0)
        assert img.size == (W, H)
    # Nothing drawn in the caption band.
    band = img.crop((0, BOTTOM + 60, W, BOTTOM + 160)).convert("L")
    assert max(band.getdata()) < 120
