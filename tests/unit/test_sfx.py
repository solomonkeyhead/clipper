"""D223: meme sound hits, picked after a word of the voice, the user's to change, mixed under the voice."""

from __future__ import annotations

import json
import subprocess
import wave

import numpy as np
import pytest

from clipper.create import channel, sfx, sound, store
from clipper.create.ai import CreateError
from clipper.create.script import Beat, Hit, Script
from clipper.create.voice import TimedWord, Timings

LINES = ["Your ears hear your voice twice.", "But the bone hears it first!", "That is why recordings sound wrong.",
         "Sound in bone is four times faster.", "So the voice you know is not the one we hear."]


def _video(seconds_per_word: float = 0.4) -> tuple[Script, Timings]:
    script = Script(title="t", beats=[Beat(text=t) for t in LINES])
    words, at, beats = [], 0.3, []
    for line in LINES:
        start = at
        for w in line.split():
            words.append(TimedWord(text=w, start=at, end=at + seconds_per_word - 0.05))
            at += seconds_per_word
        beats.append((start, at))
        at += 0.6
    return script, Timings(words=words, beats=beats, duration=at + 0.5, matched=1.0)


def test_the_ai_picks_spots_after_its_word_spaced_and_never_on_the_punchline(monkeypatch):
    script, timings = _video()
    answer = {"spots": [{"sentence": 2, "word": "first", "sound": "boom"},
                        {"sentence": 2, "word": "bone", "sound": "bonk"},      # too close to the first
                        {"sentence": 4, "word": "faster", "sound": "kazoo"},   # not in the pack: a boom
                        {"sentence": 5, "word": "hear", "sound": "ding"}]}      # the punchline: never
    monkeypatch.setattr(sfx, "ask", lambda *a, **k: json.dumps(answer))
    hits = sfx.suggest(script, timings, {4})
    first = len(LINES[0].split())
    assert [(h.word, h.sound) for h in hits] == [(first + 5, "boom"), (sum(len(x.split()) for x in LINES[:3]) + 6, "boom")]
    assert all(h.auto for h in hits)
    assert sfx.at(hits[0], timings) == pytest.approx(timings.words[first + 5].end + sfx.AFTER)


def test_without_an_ai_a_guess_from_the_words_and_never_more_than_a_few(monkeypatch):
    def down(*a, **k):
        raise CreateError("no AI model answered")
    monkeypatch.setattr(sfx, "ask", down)
    script, timings = _video()
    hits = sfx.suggest(script, timings, {4})
    assert hits and len(hits) <= sfx.most(timings.duration)
    assert hits[0].word == len(LINES[0].split()) + len(LINES[1].split()) - 1   # after "first!", the line that shouts
    assert sfx.most(12) == 2 and sfx.most(60) == 4 and sfx.most(300) == 5


def test_the_track_puts_a_hit_after_its_word_and_lower_under_speech():
    _, timings = _video()
    w = timings.words[5]
    track = sfx.track([Hit(word=5, sound="boom")], timings, seed=1)
    assert len(track) == int(sound.RATE * timings.duration)
    i = int((w.end + sfx.AFTER) * sound.RATE)
    assert not track[: i - 10].any() and np.abs(track[i:i + 4800]).max() > 0.05
    alone = sfx.clip("boom", 1)[0]
    assert np.abs(track).max() <= np.abs(alone).max() + 1e-9 <= sound._db(sound.FX_DB["boom"]) + 1e-9
    # a word it can't find (the words changed) or a sound that isn't there: left out, never a failed build
    assert not sfx.track([Hit(word=999), Hit(word=1, sound="freesound:1")], timings, seed=1).any()


def test_the_mix_keeps_the_voice_clear_under_the_limiter(tmp_path):
    """Short generated audio through the same mix as assemble(): voice and hits, amix then alimiter (D223)."""
    from clipper.render.ffmpeg import ffmpeg_path

    try:
        ff = str(ffmpeg_path())
    except Exception:
        pytest.skip("no ffmpeg")
    _, timings = _video()
    n = int(sound.RATE * timings.duration)
    t = np.arange(n) / sound.RATE
    voice = np.zeros(n)
    for w in timings.words:   # a 200 Hz "voice" at about -16 dBFS RMS while each word is said
        on = (t >= w.start) & (t < w.end)
        voice[on] = 0.22 * np.sin(2 * np.pi * 200 * t[on])
    hits = [Hit(word=k, sound=s) for k, s in ((5, "boom"), (11, "scratch"), (20, "riser"))]
    sound.write(voice, tmp_path / "voice.wav")
    sound.write(sfx.track(hits, timings, seed=3), tmp_path / "bed.wav")
    subprocess.run([ff, "-loglevel", "error", "-y", "-i", str(tmp_path / "voice.wav"), "-i", str(tmp_path / "bed.wav"),
                    "-filter_complex", "[0:a][1:a]amix=inputs=2:normalize=0:duration=first,alimiter=limit=0.89:level=0[a]",
                    "-map", "[a]", str(tmp_path / "mix.wav")], check=True, timeout=60)
    with wave.open(str(tmp_path / "mix.wav")) as f:
        mix = np.frombuffer(f.readframes(f.getnframes()), dtype="<i2").astype(float) / 32768
    assert np.abs(mix).max() <= 0.9
    # Where the voice speaks, the mix is mostly the voice: the hits add at most a few dB there.
    said = np.zeros(n, bool)
    for w in timings.words:
        said[int(w.start * sound.RATE):int(w.end * sound.RATE)] = True
    m = len(mix)
    gain = np.sqrt(np.mean(mix[said[:m]] ** 2)) / np.sqrt(np.mean(voice[:m][said[:m]] ** 2))
    assert 0.8 < gain < 1.6
    # The boom is heard: between words the mix is far louder than the voice's silence there.
    gap = slice(int((timings.words[5].end + 0.06) * sound.RATE), int(timings.words[6].start * sound.RATE))
    assert np.abs(mix[gap]).max() > 0.1


def test_the_page_changes_the_hits_and_turns_them_off(data_root):
    from fastapi.testclient import TestClient

    from clipper.studio.server import create_app

    channel.save(channel.make("physics", "Prof"))
    with channel.use("prof"):
        script, timings = _video()
        vid = store.add_video(None, script.model_dump())
        store.update_video(vid, status="built", timings=timings.model_dump(), built=script.model_dump())
        c = TestClient(create_app())
        view = c.get("/api/create").json()["videos"][0]
        assert view["words"][0] == ["Your", pytest.approx(timings.words[0].end, abs=0.01)]
        assert c.put(f"/api/create/videos/{vid}/hits", json={"hits": [{"word": 9, "sound": "scratch", "auto": False},
                                                                      {"word": 2, "sound": "boom"}]}).json() == {"ok": True}
        saved = store.video(vid)["script"]
        assert [(h["word"], h["sound"]) for h in saved["hits"]] == [(2, "boom"), (9, "scratch")]
        assert "sound hits" in c.get("/api/create").json()["videos"][0]["unbuilt"]
        assert c.put(f"/api/create/videos/{vid}/hits", json={"hits": [{"word": 999, "sound": "boom"}]}).status_code == 400
        assert c.put(f"/api/create/videos/{vid}/hits", json={"hits": [{"word": 1, "sound": "kazoo"}]}).status_code == 400
        assert c.put(f"/api/create/videos/{vid}/hits", json={"hits": [{"word": 1, "sound": "freesound:5"}]}).status_code == 400
        c.put(f"/api/create/videos/{vid}/hits", json={"hits_on": False})
        assert store.video(vid)["script"]["hits_on"] is False
        assert c.get("/api/create/sfx").json()["pack"][0] == {"sound": "boom", "name": "Boom"}
        played = c.get("/api/create/sfx/play/boom")
        assert played.status_code == 200 and played.content[:4] == b"RIFF"
        assert c.get("/api/create/sfx/play/kazoo").status_code == 404
