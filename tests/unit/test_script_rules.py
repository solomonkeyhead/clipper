"""D155: the research report's rules, held in code (script lint, idea scores, first drawing, pauses)."""

from __future__ import annotations

import json

from clipper.create import channel, packs, script, topics, voice
from clipper.create.ai import _family
from clipper.create.script import Beat, Script, Visual


def _script(*sentences: str, hook: str = "") -> Script:
    return Script(title="t", hook=hook, beats=[Beat(text=s) for s in sentences])


def test_lint_holds_a_script_to_the_channels_countable_rules():
    ch = channel.make("physics", "German Professor")
    clean = _script("Why does the lift make you heavier?", *["The floor pushes up on your feet a little harder."] * 9,
                    hook="Why lifts make you heavier")
    assert 85 <= clean.words <= 110 and script.lint(clean, ch) == []
    bad = _script("Why do elevators accelerate upward so strangely sometimes at all?",
                  "It's not gravity, it's the floor — and here's the thing about floors and their very strong push.",
                  hook="Why do elevators make everyone feel heavy?")
    problems = " ".join(script.lint(bad, ch))
    for said in ("words; it must be 85 to 110", "Sentences over 12", "opening question is 10 words",
                 '"you" or "your"', "on-screen hook is 7", "em dash", "it's not X", "here's the thing"):
        assert said in problems, said


def test_short_takes_alternate_and_are_held_to_their_own_length(monkeypatch):
    """D199: every other script a short take, when the channel tests them."""
    from clipper.create import store

    ch = channel.make("physics", "German Professor")
    newest: list[dict] = []
    monkeypatch.setattr(store, "videos", lambda: newest)
    assert script.next_length(ch) == ""                                    # off by default
    ch = ch.model_copy(update={"short_takes": True})
    assert script.next_length(ch) == "short"
    newest.append({"script": {"length": "short"}})
    assert script.next_length(ch) == ""
    usual = _script("Why does the lift make you heavier?", *["The floor pushes up on your feet a little harder."] * 9)
    assert script.lint(usual, ch) == []
    assert "it must be 70 to 90" in " ".join(script.lint(usual.model_copy(update={"length": "short"}), ch))


def test_ideas_under_15_of_21_or_off_the_channel_are_dropped():
    good = topics._Idea(question="Why does your ear pop?", angle="pressure", series=" In  the Car ",
                        scores=topics._Scores(felt=3, common=3, surprise=2, mechanism=3, showable=2, searched=2, fit=3))
    low = good.model_copy(update={"scores": topics._Scores(felt=2, common=2, surprise=2, mechanism=2, showable=2, searched=2, fit=2)})
    off = good.model_copy(update={"scores": good.scores.model_copy(update={"fit": 1, "searched": 3, "surprise": 3})})
    [kept] = topics._kept([good, low, off])
    assert kept == {"question": "Why does your ear pop?", "angle": "pressure", "series": "In the Car", "felt": True, "score": 18}


def test_no_drawing_in_the_first_four_seconds():
    drawn = Visual(kind="diagram", template="chain", labels=["a", "b"])
    s = script.tidy(Script(title="t", beats=[Beat(text="Why do you feel it?"), Beat(text="Because floors push hard on you.", visual=drawn),
                                             Beat(text="One two three four five six seven.", visual=drawn)]))
    assert [b.visual.kind for b in s.beats] == ["stock", "stock", "diagram"]


def test_long_pauses_are_cut_but_the_beat_before_the_punchline_stays():
    w = voice.TimedWord
    words = [w(text="a", start=0.5, end=0.8), w(text="b", start=2.0, end=2.3), w(text="c", start=2.4, end=2.6),
             w(text="d", start=4.0, end=4.2)]
    gaps = voice.cuts(words, last_from=3)
    left = [b - a for a, b in gaps]
    assert abs(left[0] - 0.4) < 1e-9                      # lead-in down to 0.1 s
    assert abs((1.2 - left[1]) - voice.MAX_PAUSE) < 1e-9  # a 1.2 s pause down to 0.25 s
    assert abs((1.4 - left[2]) - voice.BEAT_PAUSE) < 1e-9  # the pause before the last sentence keeps 0.6 s
    assert len(gaps) == 3                                  # the 0.1 s gap is left alone


def test_a_cut_keeps_a_word_tail_the_recognizer_missed():
    """D190: "generator" sounded 0.2 s past its heard end; the cut is only where the voice is quiet."""
    loud = [-10.0] * 100 + [-20.0] * 20 + [-50.0] * 60 + [-10.0] * 20   # word, its soft tail, silence, next word
    [(a, b)] = voice.quiet_only([(1.05, 1.75)], loud)                   # the cut from the heard end
    assert a >= 1.2 + voice.EDGE - 1e-9 and b == 1.75                   # starts after the tail
    dip = [-10.0] * 100 + [-40.0] * 3 + [-10.0] * 10 + [-50.0] * 60      # a short dip inside the word
    [(a, b)] = voice.quiet_only([(1.0, 1.7)], dip)
    assert a >= 1.13                                                    # past "-ture", not in the dip
    assert voice.quiet_only([(1.0, 1.1)], [-10.0] * 200) == []          # no quiet: no cut


def test_an_untouched_physics_channel_moves_to_the_new_rules_and_an_edited_one_keeps_its_own(data_root):
    old = channel.make("physics", "Prof").model_dump()
    for slug, rules in (("prof", packs.PHYSICS_RULES_BEFORE_D155), ("mine", ["my own rule"])):
        raw = {k: v for k, v in old.items() if k not in channel.LIMITS} | {"slug": slug, "rules": list(rules)}
        (channel.channels_dir() / f"{slug}.json").write_text(json.dumps(raw), encoding="utf-8")
    prof, mine = channel.load("prof"), channel.load("mine")
    assert prof.rules == packs.PHYSICS_RULES and (prof.words, prof.hook_max, len(prof.shapes)) == ([85, 110], 8, 6)
    assert mine.rules == ["my own rule"] and (mine.words, mine.hook_max, mine.shapes) == ([80, 125], 14, [])


def test_the_editor_is_another_ai_family_from_the_writer():
    assert _family("claude_code:opus") == _family("anthropic") == "claude" != _family("gemini:gemini-2.5-pro")


def test_lint_catches_filler_a_finished_loop_line_and_copied_example_lines():
    """D199: free checks in code before anyone reads the script."""
    ch = channel.make("physics", "German Professor")
    lines = ["Why does the lift make you heavier?", *["The floor pushes up on your feet a little harder."] * 8]
    loop = Script(title="t", ending="loop", beats=[Beat(text=t) for t in [*lines, "So next time you step in and wonder"]])
    assert script.lint(loop, ch) == []
    done = loop.model_copy(update={"beats": [*loop.beats[:-1], Beat(text="So next time, wonder why.")]})
    assert any(p.startswith("The last line must stop") for p in script.lint(done, ch))
    joke = next(q for q in ch.jokes if "coffee" in q.lower())
    copied = loop.model_copy(update={"beats": [*loop.beats[:-2], Beat(text="The car brakes. You continue. Your coffee continues."),
                                               loop.beats[-1]]})
    assert any(p.startswith("Lines copied") for p in script.lint(copied, ch)), joke
    filler = loop.model_copy(update={"beats": [Beat(text="Why is it essentially heavier for you?"), *loop.beats[1:]]})
    assert any("filler" in p for p in script.lint(filler, ch))


def test_a_still_clip_is_found_and_its_still_camera_pushes_in(tmp_path):
    """D200: the judge sees one thumbnail, so a clip that barely moves needs measuring."""
    import cv2
    import numpy as np

    from clipper.create import build

    def clip(name, moving):
        path = tmp_path / name
        w = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 10, (160, 90))
        for k in range(30):
            frame = np.full((90, 160, 3), 90, np.uint8)
            if moving:
                cv2.rectangle(frame, (k * 4, 20), (k * 4 + 40, 70), (255, 255, 255), -1)
            w.write(frame)
        w.release()
        return path

    assert build._activity(clip("still.mp4", False), 0.0, 2.0) < build.STILL <= build._activity(clip("moves.mp4", True), 0.0, 2.0)
    assert build._camera("still", None, lively=False) == "push"
    assert build._camera("still", 1.0, lively=False) == "still"      # a punch-in holds still on purpose
    assert build._camera("still", None, lively=True) == "still"


def test_a_drawing_that_goes_on_with_an_earlier_one_keeps_its_marks_and_draws_only_the_new(monkeypatch):
    """D200: footage between two sentences of one mechanism no longer makes the second start from a blank board."""
    import json as _json

    from clipper.create import diagrams, sketch
    from clipper.create.script import Visual

    old = sketch.Sketch(marks=[sketch.Mark(kind="icon", text="ear", xy=[300, 400, 300])], grid=900)
    new = {"marks": [{"kind": "arrow", "xy": [500, 400, 800, 400], "color": "yellow", "cue": "bone"}]}
    monkeypatch.setattr(sketch, "ask", lambda *a, **k: _json.dumps(new))
    both = sketch.draw_on("The sound goes through bone.", "an arrow through bone", old)
    assert [m.kind for m in both.marks] == ["icon", "arrow"] and both.base == 1 and both.marks[0] == old.marks[0]
    v = Visual(kind="diagram", template="sketch", sketch=both, builds=True)
    assert diagrams.cues(v, [(1.0, "bone")]) == [None, 1.0]            # the old mark has no cue in this shot
    fresh = v.model_copy(update={"sketch": both.model_copy(update={"base": 0})})
    assert diagrams.frame(v, 0.05, 4.0).tobytes() != diagrams.frame(fresh, 0.05, 4.0).tobytes()   # the ear is there at once


def test_the_professor_and_the_captions_stay_clear_of_the_apps_buttons_and_titles():
    """D201: YouTube's channel name and title covered his chin and hands, and a long caption ran under the like button."""
    from clipper.create import compose as C
    from clipper.create.voice import TimedWord

    hands = C.H - C.PRESENTER_LIFT - C.PRESENTER_H * (1 - 0.55)   # his face and hands are the top 55 % of him
    assert hands <= C.SAFE_BOTTOM and C.H - C.PRESENTER_LIFT - C.PRESENTER_H > C.CAPTION_Y + 50
    said = ["Both", "breaths", "leave", "your", "lungs", "at", "thirty-seven", "degrees", "Fahrenheit"]
    caps = C.Captions([TimedWord(text=w, start=i * 0.4, end=i * 0.4 + 0.35) for i, w in enumerate(said)])
    assert all(caps.image(p, 0).shape[1] - 2 * 18 <= C.CAPTION_W for p in range(len(caps.pages)))   # less its blank margin
    assert C.W / 2 + C.CAPTION_W / 2 <= C.SAFE_RIGHT + 10
