"""Fewer AI calls in Create and the post descriptions, and Gemini for the jobs the user gave it (D136)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from clipper.create import ai, build, stock
from clipper.create import script as scripts
from clipper.create.script import Beat, Script, Visual


class Fake:
    """A backend that answers with `answer` (or raises), and counts the calls."""

    def __init__(self, name, answer='{"ok": true}', fail=False):
        self.name, self.answer, self.fail, self.calls, self.timeout = name, answer, fail, 0, 30.0

    def describe(self):
        return f"{self.name}:model"

    def complete(self, request):
        self.calls += 1
        if self.fail:
            raise RuntimeError("busy")
        return SimpleNamespace(text=self.answer)


class Ok(BaseModel):
    ok: bool


@pytest.fixture
def models(data_root, monkeypatch):
    claude, gemini = Fake("claude_code"), Fake("gemini")
    monkeypatch.setattr(ai, "backends", lambda config, model=None, job="": [claude, gemini])
    return claude, gemini


def test_a_kept_answer_is_not_asked_for_twice_and_a_bad_one_is_never_kept(models):
    claude, _ = models
    for _ in range(3):
        assert ai.ask("s", "u", Ok, temperature=0.0, keep=True) == '{"ok": true}'
    assert claude.calls == 1 and ai.last_used == "claude_code:model"
    ai.ask("s", "other", Ok, temperature=0.0, keep=True)           # a different question is asked
    assert claude.calls == 2
    ai.ask("s", "u", Ok, temperature=0.0)                          # not asked to keep: always asked
    assert claude.calls == 3
    claude.answer = "not json"
    for _ in range(2):
        ai.ask("s", "bad", Ok, temperature=0.0, keep=True)
    assert claude.calls == 5                                       # a bad answer came back for no one


def test_the_users_gemini_jobs_go_to_gemini_first_patiently_and_claude_answers_if_it_cant(models):
    claude, gemini = models
    ai.ask("s", "u", None, temperature=0.4, job="script")
    assert (gemini.calls, claude.calls) == (1, 0) and gemini.timeout >= ai.GEMINI_PATIENCE
    gemini.fail = True                                             # create_claude_only doesn't stop these jobs
    ai.ask("s", "u2", None, temperature=0.0, job="check")
    assert claude.calls == 1
    ai.ask("s", "u3", None, temperature=0.4, job="sketch")         # drawing: Claude, as before
    assert claude.calls == 2


def test_writing_a_script_draws_no_sketches_that_waste_if_it_is_dropped(data_root, monkeypatch):
    from clipper.create import sketch

    sketchy = Visual(kind="diagram", template="sketch", idea="a head with two sound paths")
    written = Script(title="Why?", beats=[Beat(text="Why does your voice sound odd?"), Beat(text="Two routes, air and bone.", visual=sketchy)])
    monkeypatch.setattr(scripts, "write", lambda *a, **k: written)
    monkeypatch.setattr(scripts, "check", lambda s: scripts.Review(ok=True))
    monkeypatch.setattr(sketch, "draw", lambda *a, **k: pytest.fail("drew while writing"))
    out, note = scripts.write_checked("Why?")
    assert out.beats[1].visual.template == "sketch" and not out.beats[1].visual.sketch and "Written by" in note


def beat_and_script(wish=""):
    s = Script(title="t", beats=[Beat(text="Like music through a wall.", visual=Visual(queries=["wall music"], wish=wish))])
    return s.beats[0], s


def test_the_scripts_own_searches_are_tried_first_and_the_footage_model_only_when_they_fail(monkeypatch):
    beat, s = beat_and_script()
    written = []
    monkeypatch.setattr(stock, "plan_searches", lambda *a, **k: written.append(1) or ["subwoofer speaker"])
    asked = []

    def choose(queries, seconds, taken, **kw):
        asked.append(list(queries))
        return {"id": "a"} if "subwoofer speaker" in queries else None

    monkeypatch.setattr(stock, "choose", choose)
    got = build._pick_footage(0, beat, beat.visual, 2.0, s, set(), False)
    assert written == [1] and asked == [["wall music"], ["subwoofer speaker", "wall music"]] and got.hits == [{"id": "a"}]
    written.clear()
    asked.clear()
    monkeypatch.setattr(stock, "choose", lambda q, *a, **k: asked.append(q) or {"id": "b"})
    build._pick_footage(0, beat, beat.visual, 2.0, s, set(), False)
    assert written == [] and asked == [["wall music"]]              # the first searches were enough: no extra call


def test_a_wish_or_new_footage_goes_straight_to_searches_written_for_it(monkeypatch):
    beat, s = beat_and_script(wish="a subwoofer")
    written = []
    monkeypatch.setattr(stock, "plan_searches", lambda *a, **k: written.append(k["wish"]) or ["subwoofer speaker"])
    monkeypatch.setattr(stock, "choose", lambda q, *a, **k: {"id": "a", "q": list(q)})
    got = build._pick_footage(0, beat, beat.visual, 2.0, s, set(), False)
    assert written == ["a subwoofer"] and got.hits[0]["q"][0] == "subwoofer speaker"


def test_footage_chosen_ahead_is_used_unless_an_earlier_sentence_took_that_clip(data_root, tmp_path, monkeypatch):
    beat, s = beat_and_script()
    ahead = build._Footage([3.0], [{"id": "a", "url": "u", "width": 1, "height": 1}], ["wall music"])
    live = []
    monkeypatch.setattr(build, "_pick_footage", lambda *a, **k: live.append(1) or build._Footage([3.0], [{"id": "z"}], ["q"]))
    monkeypatch.setattr(build, "_stock_shot", lambda src, seconds, out, *a, **k: out)
    monkeypatch.setattr(build, "_too_dark", lambda clip: False)
    monkeypatch.setattr(build.stock, "fetch", lambda hit: tmp_path / "clip.mp4")
    monkeypatch.setattr(build, "shot_cache", None)
    build._planned(0, beat, beat.visual, 2.0, [], s, tmp_path, set(), pre=ahead)
    assert live == []                                               # used as chosen
    build._planned(0, beat, beat.visual, 2.0, [], s, tmp_path, {"a"}, pre=ahead)
    assert live == [1]                                              # clashed: chosen again, in turn


def test_every_stock_sentence_is_chosen_ahead_together(monkeypatch):
    from clipper.create.voice import Timings

    s = Script(title="t", beats=[Beat(text=f"Sentence {i} is here today.", visual=Visual(queries=[f"q{i}"])) for i in range(3)])
    s.beats[1] = s.beats[1].model_copy(update={"visual": Visual(kind="diagram", template="card")})
    timings = Timings(words=[], beats=[(0, 2), (2, 4), (4, 6)], duration=6, matched=1.0)
    seen = []
    monkeypatch.setattr(build, "_pick_footage", lambda i, *a, **k: seen.append(i) or build._Footage([2.0], [{"id": i}], []))
    got = build._prefetch(s, timings, [[0], [1], [2]])
    assert sorted(got) == [0, 2] and sorted(seen) == [0, 2]


def test_post_descriptions_are_remembered_by_what_went_into_them(data_root, monkeypatch):
    from clipper import runner
    from clipper.campaign import description

    seen = {}
    monkeypatch.setattr(description, "describe", lambda *a, **k: seen.update(k) or "text")
    plan = SimpleNamespace(start=0.0, end=2.0, suggested_caption="cap", hook_text="", hook_shown=False)
    words = [SimpleNamespace(start=0.1, end=0.5, text="hello")]
    runner._description(plan, words, SimpleNamespace(name="c"), [object()], None)
    assert seen["cache"] is not None

