"""D197: zooms only where the picture shows the word and never crop a drawing's title; a loop's end runs into its
start; sound effects on the edit, spaced and kept off the punchline."""

from __future__ import annotations

import numpy as np

from clipper.create import build, compose, sound
from clipper.create.script import Beat, Script
from clipper.create.voice import TimedWord, Timings


def test_a_punch_in_needs_the_word_in_the_clips_own_words():
    assert build._shows("vacuum", {"tags": "vacuum cleaner, carpet, cleaning"})
    assert not build._shows("thirty-seven", {"tags": "fireplace, fire, cozy"})
    assert not build._shows("outsourced", {"tags": "ceiling fan, room"})


def test_a_drawing_push_keeps_the_title_in_view():
    title = (300.0, 150.0, 780.0, 265.0)
    part = compose.Focus(a=1.0, b=2.0, box=(258.0, 524.0, 822.0, 746.0), keep=title)
    u, v, z = compose.aim(part)
    hw, hh = compose.W / (2 * z), compose.PANEL_H / (2 * z)
    assert 1.06 <= z <= compose.FOCUS_MAX
    assert v * compose.PANEL_H - hh <= title[1] + 1e-6 and u * compose.W - hw <= title[0] + 1e-6   # the title isn't cropped
    wide = compose.Focus(a=1.0, b=2.0, box=(400.0, 500.0, 600.0, 600.0), keep=(0.0, 150.0, 1080.0, 1000.0))
    assert compose.aim(wide) == (0.5, 0.5, 1.0)   # keeping all that in view leaves no push worth making


def test_auto_never_zooms_into_a_drawing():
    shots = [compose.Shot(path=None, start=i * 30, frames=30, kind="drawing" if i % 2 else "footage") for i in range(9)]
    compose.transitions(shots, 1, "auto")
    assert all(s.enter != "zoom" for s in shots if s.kind == "drawing")


def _loop_script(n: int = 5) -> tuple[Script, Timings]:
    beats = [Beat(text=f"Line {i} here.") for i in range(n)]
    words = [TimedWord(text="w", start=0.1 + i, end=0.6 + i) for i in range(n)]
    timing = Timings(words=words, beats=[(i, i + 1) for i in range(n)], duration=n + 0.2, matched=1.0)
    return Script(title="t", beats=beats, ending="loop"), timing


def test_a_loop_ends_a_breath_after_its_last_word():
    script, timing = _loop_script()
    assert build.loops(script, timing) and not build.loops(script.model_copy(update={"loop": False}), timing)
    cut = build.loop_timing(timing)
    gap = cut.duration - cut.words[-1].end + cut.words[0].start
    assert abs(gap - build.LOOP_GAP) < 0.01 and cut.beats[-1][1] == cut.duration


def test_a_cut_in_gives_way_to_a_punch_in_and_a_loop_ends_its_last_one_as_the_last_line_starts():
    script, timing = _loop_script(8)
    script.beats[3].pose = "smug"
    timing = timing.model_copy(update={"beats": [(i * 3, i * 3 + 3) for i in range(8)], "duration": 24.0,
                                       "words": [TimedWord(text="w", start=t, end=t + 0.4) for t in range(24)]})
    build.punches.clear()
    got = build.cutaways(script, timing, punchline=18.0, total=720, loop_at=21.0)
    assert any(c.z == 1.28 for c in got) and got[-1].b == 21 * build.FPS
    build.punches.update({2: [11.5]})
    assert all(c.z != 1.28 for c in build.cutaways(script, timing, punchline=18.0, total=720, loop_at=21.0))
    build.punches.clear()


def test_sound_effects_are_spaced_ranked_and_kept_off_the_punchline():
    events = [(0.03, "pop"), (2.6, "swish"), (4.0, "thump"), (4.3, "swish"), (10.0, "swish"), (13.5, "swish"),
              (21.0, "swish"), (30.0, "whoosh"), (37.4, "zoom"), (38.0, "ding"), (37.39, "riser")]
    events += [(10.1, "chalk"), (10.4, "chalk"), (11.0, "chalk"), (38.5, "chalk")]
    got = sound.pick(events, 42.0, quiet=(37.39, 41.5))
    assert (4.3, "swish") not in got and (4.0, "thump") in got        # the thump outranks a swish beside it
    assert sum(k == "swish" for _, k in got) <= sound.FX_MOST["swish"]
    assert (37.4, "zoom") not in got and (38.0, "ding") not in got     # the punchline is said in silence
    assert (10.4, "chalk") not in got and (38.5, "chalk") not in got
    for kind in sound.FX_DB:
        s, lead = sound.effect(kind, 3)
        assert np.isfinite(s).all() and 0.99 < np.abs(s).max() <= 1.0 and 0 <= lead <= len(s) / sound.RATE
    track = sound.bed(5.0, words=[(0.5, 1.0)], quiet_from=5.0, effects=[(1.0, "thump"), (2.0, "whoosh")], logo_at=None,
                      with_music=False, seed=1)
    assert 0 < np.abs(track).max() < 0.5
