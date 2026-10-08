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
