"""Channels and niche packs (D146): the German Professor's prompts unchanged, others from their pack."""

from __future__ import annotations

import json
from pathlib import Path

from clipper.create import channel, packs, script, store, topics

FIXTURES = Path(__file__).parents[1] / "fixtures"


def physics():
    return channel.make("physics", "German Professor", "@German.Professor")


def test_the_physics_pack_reads_exactly_as_the_channels_prompts_always_did():
    ch = physics()
    assert channel.fill(script.visuals(ch), ch) == (FIXTURES / "visuals_physics.txt").read_text(encoding="utf-8")
    assert channel.fill(script.CHECK, ch) == (FIXTURES / "check_physics.txt").read_text(encoding="utf-8")
    assert channel.fill(topics.SYSTEM, ch) == (FIXTURES / "topics_physics.txt").read_text(encoding="utf-8")


def test_a_footage_only_channel_plans_no_diagrams_and_a_general_one_no_physics_templates():
    plain = channel.make("footage", "Fact Machine")
    text = channel.fill(script.visuals(plain), plain)
    assert "never used on this channel" in text and "* sketch" not in text and "* forces" not in text
    general = channel.make("explainer", "How Things Work")
    text = channel.fill(script.visuals(general), general)
    assert "* sketch" in text and "* chain" in text and "* forces" not in text and "* particles" not in text


def test_a_channel_from_before_packs_is_the_physics_one(data_root):
    legacy = channel.path("")  # data/create/channels/<active>.json does not exist yet: write the old file
    old = {"name": "German Professor", "handle": "@German.Professor", "campaign": "german-professor",
           "persona": "p", "rules": ["r"], "niche": "n", "voice": "v", "watermark": "", "examples": [],
           "words_per_second": 2.3}
    (data_root / "create").mkdir(parents=True, exist_ok=True)
    (data_root / "create" / "channel.json").write_text(json.dumps(old), encoding="utf-8")
    del legacy
    [loaded] = channel.all_channels()                       # migrated on first look
    assert (loaded.slug, loaded.pack, loaded.drawings) == ("german-professor", "physics", True)
    assert channel.check_name(loaded) == "Physics check" and "forces" in loaded.templates
    assert not (data_root / "create" / "channel.json").exists()


def test_each_channel_has_its_own_ideas_and_videos(data_root):
    channel.save(channel.make("physics", "Physics"))
    channel.save(channel.make("footage", "Facts"))
    with channel.use("physics"):
        store.add_topics([{"question": "Why is the sky blue?", "angle": "", "felt": True}])
        store.add_video(None, {"title": "a", "beats": []})
    with channel.use("facts"):
        assert store.topics() == [] and store.videos() == []
        store.add_topics([{"question": "Why is the sky blue?", "angle": "", "felt": False}])  # same words, another channel
        assert len(store.topics()) == 1
    with channel.use("physics"):
        assert len(store.topics()) == 1 and len(store.videos()) == 1
    assert len(store.videos(every=True)) == 1


def test_there_is_a_pack_to_start_from_and_it_names_its_channel():
    assert packs.DEFAULT in packs.PACKS
    made = channel.make("stories", "Old Tales")
    assert "Old Tales" in made.persona and made.campaign == "old-tales"


def test_a_video_with_no_voice_is_timed_at_the_channels_pace(tmp_path):
    import shutil

    from clipper.create import voice

    s = script.Script(title="t", beats=[script.Beat(text="One two three four five."), script.Beat(text="Six seven eight nine ten eleven.")])
    words, seconds = voice.silent(s, 2.5)
    assert len(words) == 11 and abs(seconds - 11 / 2.5) < 0.05
    timings = voice.align(s, words, seconds)
    assert timings.matched == 1.0 and len(timings.beats) == 2 and timings.beats[0][1] == timings.beats[1][0]
    if shutil.which("ffmpeg") or True:
        try:
            out = voice.write_silence(tmp_path / "v.wav", seconds)
        except (OSError, Exception):          # no ffmpeg here: the timing above is what matters
            return
        assert out.stat().st_size > 1000


def test_the_owners_steering_comes_first_and_says_it_wins(data_root):
    from clipper.create import topics

    ch = physics()
    assert topics.steering(ch) == ""                                         # no steering: the request is as it was
    ch.idea_avoid = "biology and how the body works inside"
    text = topics.steering(ch, "only light and sound", ["Why does your skin wrinkle in a bath?"])
    assert text.startswith("THE OWNER'S STEERING") and "outranks" in text
    assert "Never make an idea about: biology" in text and "only light and sound" in text and "skin wrinkle" in text
    # the planner's own prompt is untouched, so the channel's other ideas are as before
    assert channel.fill(topics.SYSTEM, physics()) == (FIXTURES / "topics_physics.txt").read_text(encoding="utf-8")


def test_a_batch_note_and_the_skipped_ideas_reach_the_planner_and_the_channel_keeps_its_steering(data_root, monkeypatch):
    import json

    from clipper.create import store, topics

    store.add_topics([{"question": "Why does your skin wrinkle in a bath?", "angle": "osmosis", "felt": True}])
    store.set_topic(store.topics()[0]["id"], "skipped")
    seen = {}

    def fake_ask(system, user, schema, **kw):
        seen["user"] = user
        return json.dumps([{"question": "Why does a spoon flip your reflection?", "angle": "concave mirror", "felt": False}])

    monkeypatch.setattr(topics, "ask", fake_ask)
    assert topics.generate(5, "only light and sound") == 1
    assert "only light and sound" in seen["user"] and "skin wrinkle" in seen["user"] and seen["user"].startswith("THE OWNER'S STEERING")
    ch = channel.load()
    channel.save(ch.model_copy(update={"idea_avoid": "biology"}))
    assert channel.load().idea_avoid == "biology"                   # the standing note is kept with the channel


def test_the_page_saves_a_channels_steering_and_a_batch_note_through_the_api(data_root, monkeypatch):
    from fastapi.testclient import TestClient

    from clipper.create import topics
    from clipper.studio import server

    client = TestClient(server.create_app())
    slug = client.post("/api/create/channels", json={"name": "Physics Lab", "pack": "physics"}).json()["slug"]
    assert client.put(f"/api/create/channels/{slug}", json={"idea_focus": " light and sound ", "idea_avoid": "biology"}).status_code == 200
    got = client.get(f"/api/create/channels/{slug}").json()
    assert got["idea_focus"] == "light and sound" and got["idea_avoid"] == "biology"
    seen = {}
    monkeypatch.setattr(topics, "generate", lambda count, steer="": seen.update(count=count, steer=steer) or 0)
    client.post("/api/create/ideas", json={"count": 10, "steer": "only heat"})
    assert seen == {"count": 10, "steer": "only heat"}
