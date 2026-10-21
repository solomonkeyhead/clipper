"""What a Create Short costs in AI (D233): cheaper tiers for small jobs, fewer drawings made by themselves, New
pictures without a rebuild, a cancel that stops drawings, and the seams a hosted plan meters by."""

from __future__ import annotations

import io
import json
import threading
from types import SimpleNamespace

import pytest
from PIL import Image

from clipper.create import build, meterhooks, sketch, stock, store
from clipper.create import script as scripts
from clipper.create.script import Beat, Review, Script, Visual

SVG = "<svg xmlns='http://www.w3.org/2000/svg' width='10' height='10'><rect width='10' height='10'/></svg>"


def _sketched(*ideas: str, manual: bool = False) -> Script:
    return Script(title="t", beats=[Beat(text=f"Sentence about {k}.", visual=Visual(kind="diagram", template="sketch",
                                                                                     idea=k, manual=manual))
                                    for k in ideas])


# ---------- 2. stand-ins: an SVG one is used, and only so many are made ----------

def test_an_svg_stand_in_is_used_not_thrown_away_for_a_card(monkeypatch):
    monkeypatch.setattr(sketch, "draw", lambda *a, **k: sketch.Sketch(svg=SVG, grid=900))
    beat = Beat(text="Ice floats.", visual=Visual(queries=["ice"]))
    got = build._fallback(beat, Script(title="t", beats=[beat]))
    assert got.template == "sketch" and got.sketch.svg == SVG


def test_a_build_draws_stand_ins_only_up_to_its_limit_planned_sketches_first(monkeypatch):
    drawn = []
    monkeypatch.setattr(build, "_fallback", lambda *a, **k: drawn.append(1) or Visual(kind="diagram", template="sketch"))
    planned = _sketched("a", "b", "c")
    assert build._auto_drawings(planned, 4) == 1                       # 4 less the 3 planned
    assert build._auto_drawings(_sketched("a", "b", manual=True), 4) == 4   # the user's own aren't counted
    s = Script(title="t", beats=[Beat(text=f"Sentence {i} is here.", visual=Visual(queries=[f"q{i}"])) for i in range(3)])
    build.picture_notes.clear()
    build._auto_left[0] = 1
    try:
        made = [build._drawn(i, s.beats[i], s, "") for i in range(3)]
    finally:
        build._auto_left[0] = None
    assert len(drawn) == 1 and [v.template for v in made] == ["sketch", "card", "card"]
    assert "New drawing" in build.picture_notes[0]
    # A drawing the user asked for (a planned diagram) is never stopped by the limit.
    build._auto_left[0] = 0
    try:
        build._drawn(0, _sketched("x").beats[0], _sketched("x"), "")
    finally:
        build._auto_left[0] = None
    assert len(drawn) == 2


# ---------- 3. the footage judge and the sound picks on the cheap tier ----------

def _jpeg(w: int, h: int) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (w, h), (90, 120, 200)).save(out, "JPEG")
    return out.getvalue()


def test_the_judge_sees_at_most_8_small_thumbnails_on_the_cheap_tier(monkeypatch):
    seen = {}

    def ask(system, user, schema, **kw):
        seen.update(kw, user=user)
        return json.dumps({"scores": [8] * len(kw["media"])})

    monkeypatch.setattr(stock, "ask", ask)
    monkeypatch.setattr(stock, "_thumb", lambda h: _jpeg(360, 203))
    hits = [{"id": f"v{k}", "width": 1920, "height": 1080, "duration": 9, "tags": "ice"} for k in range(12)]
    out, _ = stock._scored("Ice floats.", hits)
    assert seen["tier"] == "cheap" and seen["job"] == "footage" and len(seen["media"]) == 8
    assert all(max(Image.open(io.BytesIO(m)).size) <= 256 for m, _ in seen["media"])
    assert "Thumbnails 1 to 8" in seen["user"]
    assert len(out) == 12 and [h["score"] for h in out[8:]] == [None] * 4   # the rest offered in the picker, unjudged

    monkeypatch.setattr(stock, "ask", lambda *a, **kw: seen.update(kw) or json.dumps({"searches": ["ice cube"]}))
    assert stock.plan_searches("Ice floats.") == ["ice cube"] and seen["tier"] == "cheap"


def test_sound_hits_are_picked_on_the_cheap_tier(monkeypatch):
    from clipper.create import sfx

    seen = {}
    monkeypatch.setattr(sfx, "ask", lambda *a, **kw: seen.update(kw) or json.dumps({"spots": []}))
    sfx._asked(_sketched("a"), 2, set())
    assert seen["tier"] == "cheap" and seen["job"] == "sfx"


# ---------- 4. the editor's read off; a rules-only rewrite on the review tier ----------

def _write_checked(monkeypatch, facts: list[str]) -> list[str]:
    tiers, critic = [], []
    s = Script(title="t", beats=[Beat(text="Why?")])
    monkeypatch.setattr(scripts, "write", lambda *a, tier="", **k: tiers.append(tier) or s)
    monkeypatch.setattr(scripts, "check", lambda *a, **k: Review(ok=not facts, problems=facts))
    monkeypatch.setattr(scripts, "lint", lambda *a, **k: ["too long"] if not tiers[1:] else [])
    monkeypatch.setattr(scripts, "critique", lambda *a, **k: critic.append(1) or [])
    monkeypatch.setattr(scripts, "_planned_or_note", lambda s, words=False: (s, "planned", Review(ok=True)))
    scripts.write_checked("Why?")
    assert not critic   # create.critic is off by default
    return tiers


def test_the_editor_is_off_and_a_rules_only_rewrite_goes_to_the_review_tier(data_root, monkeypatch):
    assert _write_checked(monkeypatch, []) == ["", "review"]
    assert _write_checked(monkeypatch, ["the floor doesn't push harder"]) == ["", ""]


# ---------- 5. topic ideas ----------

def test_ideas_see_the_last_50_past_ideas_on_the_review_tier(data_root, monkeypatch):
    from clipper.create import topics

    store.add_topics([{"question": f"Why does thing number {k} happen?", "angle": "a", "felt": False} for k in range(60)])
    seen = {}

    def ask(system, user, schema, **kw):
        seen.update(kw, user=user)
        return json.dumps([{"question": "Why does thing number 3 happen again?", "angle": "a",
                            "scores": {"fit": 3, "felt": 3, "surprise": 3, "clear": 3}}])

    monkeypatch.setattr(topics, "ask", ask)
    monkeypatch.setattr(topics, "viewer_comments", lambda ch: ["COMMENT-MARK"])
    monkeypatch.setattr(topics, "track_record", lambda: ["RECORD-MARK: 900 views"])
    topics.generate(5)
    assert seen["tier"] == "review" and "number 59 " in seen["user"] and "number 9 " not in seen["user"]
    assert "COMMENT-MARK" in seen["user"] and "RECORD-MARK" in seen["user"]       # here, unchanged
    assert len(store.topics(status=None)) == 60                                  # a repeat is still caught


def test_hosted_the_track_record_sends_no_views_or_watch_share(monkeypatch):
    from clipper.create import topics

    monkeypatch.setattr(store, "videos", lambda: [{"id": k, "script": {"title": f"Q{k}"}} for k in (1, 2, 3)])
    rows = [{"clip_id": f"create-{k}", "views_latest": "100", "avg_view_pct": "50", "posted_at": "2026-01-01 10:00"}
            for k in (1, 2, 3)]
    assert len(topics.track_record(rows)) == 3       # here, as before
    monkeypatch.setenv("CLIPPER_HOSTED", "1")
    assert topics.track_record(rows) == []


def test_hosted_no_youtube_comments_are_read_for_ideas(monkeypatch):
    from clipper.create import topics
    from clipper.youtube import api as yt

    monkeypatch.setenv("CLIPPER_HOSTED", "1")
    monkeypatch.setattr(yt, "token_files", lambda: pytest.fail("YouTube was asked on the hosted site"))
    assert topics.viewer_comments(SimpleNamespace(handle="@lab")) == []


# ---------- 7. cancel stops the drawings not started; usage tags reach the pool ----------

def test_a_cancel_stops_drawings_not_started_and_the_video_tag_reaches_each(monkeypatch):
    from clipper.llm import usage

    started, tags, go = [], [], threading.Event()

    def draw(sentence, idea, *a, **k):
        started.append(idea)
        tags.append(usage._ref.get())
        if idea != "a":
            go.wait(5)
        return sketch.Sketch(svg=SVG, grid=900)

    class Stop(Exception):
        pass

    def progress(stage, pct):
        raise Stop

    monkeypatch.setattr(sketch, "draw", draw)
    with usage.tag("video:7"), pytest.raises(Stop):
        sketch.draw_all(_sketched("a", "b", "c", "d", "e", "f"), progress)
    go.set()
    assert "f" not in started and tags and all(t == "video:7" for t in tags)


# ---------- 7. New pictures plans and waits for Build ----------

def test_new_pictures_replans_without_building(data_root, monkeypatch):
    from fastapi.testclient import TestClient

    from clipper.studio import create_api, server

    client = TestClient(server.create_app())
    client.post("/api/create/channels", json={"name": "Physics Lab", "pack": "physics"})
    video = store.add_video(None, _sketched("a").model_dump(), "")
    store.update_video(video, status="built", voice="voice.wav", clip_id=1)
    builds = []
    monkeypatch.setattr(create_api, "_start", lambda *a: builds.append(a))
    monkeypatch.setattr(scripts, "replan", lambda s: (_sketched("b"), "planned"))
    assert client.post(f"/api/create/videos/{video}/pictures").status_code == 200
    row = store.video(video)
    assert not builds and row["status"] == "built" and row["script"]["beats"][0]["visual"]["idea"] == "b"


# ---------- 8. the meter's seams ----------

def test_the_first_build_and_each_later_redraw_are_told_to_the_meter(data_root, monkeypatch):
    from clipper.create import channel
    from clipper.create.voice import Timings

    channel.save(channel.make("physics", "Physics Lab"))
    calls = []
    monkeypatch.setattr(meterhooks, "on_first_build", lambda v: calls.append(("first", v)))
    monkeypatch.setattr(meterhooks, "on_redraw", lambda v, n=1: calls.append(("redraw", v, n)))

    class Stop(Exception):
        pass

    monkeypatch.setattr(build, "draw_all", lambda *a, **k: (_ for _ in ()).throw(Stop()))
    timings = Timings(words=[], beats=[(0.0, 2.0), (2.0, 4.0)], duration=4.0, matched=1.0).model_dump()
    s = _sketched("a", "b")
    s.beats[0].visual.sketch = sketch.Sketch(svg=SVG, grid=900)   # drawn already; "b" still to draw
    video = store.add_video(None, s.model_dump(), "")
    store.update_video(video, voice="voice.wav", timings=timings)
    with pytest.raises(Stop):
        build.build(video)
    assert calls == [("first", video)]                # its own drawings aren't redraws
    calls.clear()
    store.update_video(video, clip_id=1)
    with pytest.raises(Stop):
        build.build(video)
    assert calls == [("redraw", video, 1)]
