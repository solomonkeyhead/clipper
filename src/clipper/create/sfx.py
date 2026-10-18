"""Meme sound hits (D223): a boom right after a surprising line, a record scratch on a turn, a few in a Short.

Automatic by default, manual always there (D120): the first build asks the AI for a few spots (a sentence, the word
the hit comes after and which sound), kept on the script as `Script.hits`; the page shows every one, and the user
moves, swaps, previews, removes or adds them, or turns them off for the video. The sounds come from the built-in
pack, made here by sound.effect like the edit's effects (D197; nothing licensed, nothing for Content ID to match,
docs/SOUND_PACK.md), or from Freesound's library through its official API (FREESOUND_API_KEY in .env), CC0 only.
They are mixed under the voice's level with the music in sound.bed's track, a little lower while the voice speaks.
"""

from __future__ import annotations

import os
import re

import httpx
import numpy as np
from pydantic import BaseModel

from ..paths import data_root, ensure
from ..utils.logging import get_logger
from . import sound
from .ai import CreateError, ask
from .script import Hit, Script
from .voice import Timings

log = get_logger(__name__)

#: The built-in pack: kind -> what the page calls it. All made by sound.effect.
PACK = {"boom": "Boom", "scratch": "Record scratch", "bonk": "Bonk", "boing": "Boing", "ding": "Ding", "pop": "Pop",
        "whoosh": "Whoosh", "riser": "Riser", "thump": "Thump"}
LIBRARY_DB = -10.0     # a library sound's peak (dBFS), like the boom's
UNDER_DB = -6.0        # further, where a hit runs on under the voice
AFTER = 0.05           # s after its word ends
GAP = 4.0              # s at least between two hits
LONGEST = 3.0          # s of a library sound used
FREESOUND = "https://freesound.org/apiv2"
CC0 = 'license:"Creative Commons 0"'


def most(seconds: float) -> int:
    """How many hits a Short gets: about one per 15 s, 2 to 5. A hit on every beat stops being funny."""
    return max(2, min(5, round(seconds / 15)))


def _first_words(script: Script) -> list[int]:
    """The index of each sentence's first word in the timed words."""
    out, k = [], 0
    for b in script.beats:
        out.append(k)
        k += len(b.text.split())
    return out


def _norm(word: str) -> str:
    return re.sub(r"[^a-z0-9]", "", word.lower())


def at(hit: Hit, timings: Timings) -> float | None:
    """When a hit plays, in seconds: just after its word. None for a word the voice doesn't have (words changed)."""
    if not 0 <= hit.word < len(timings.words):
        return None
    return timings.words[hit.word].end + AFTER


class _Spot(BaseModel):
    sentence: int       # 1-based
    word: str           # the word of that sentence the hit comes right after
    sound: str


class _Spots(BaseModel):
    spots: list[_Spot]


SYSTEM = """You are the sound editor of a short vertical video. Pick the few moments where a meme sound hit lands
best: a deep "boom" right after a surprising or absurd line, a "scratch" where the story turns ("but actually..."),
a "bonk" after something dumb, a "boing" after something silly, a "ding" on a correct answer, a "riser" building into
a reveal. Pick at most {most}, fewer if fewer lines deserve one; never two in a row, never the last sentence (its
silence is the joke). For each give the sentence's number, the word of that sentence the hit comes right after
(usually its last word), and the sound: one of {sounds}."""


def _asked(script: Script, most_n: int, skip: set[int]) -> list[tuple[int, str, str]]:
    user = "\n".join(f"{i + 1}. {b.text.strip()}" for i, b in enumerate(script.beats))
    answer = ask(SYSTEM.format(most=most_n, sounds=", ".join(PACK)), user, _Spots, temperature=0.3, quick=True,
                 job="sfx", keep=True)
    return [(s.sentence - 1, s.word, s.sound) for s in _Spots.model_validate_json(answer).spots
            if 0 < s.sentence <= len(script.beats) and s.sentence - 1 not in skip]


TURNS = {"but", "actually", "wrong", "never", "really", "turns", "except", "secretly", "nope", "yet"}


def _guessed(script: Script, skip: set[int]) -> list[tuple[int, str, str]]:
    """Without an AI: the sentences that shout, ask, turn or carry a number, a boom after each, the most first."""
    def score(text: str) -> int:
        words = {_norm(w) for w in text.split()}
        return 2 * text.rstrip().endswith(("!", "?")) + bool(words & TURNS) + any(c.isdigit() for c in text)
    ranked = sorted((i for i in range(len(script.beats)) if i not in skip and score(script.beats[i].text)),
                    key=lambda i: -score(script.beats[i].text))
    return [(i, "", "scratch" if script.beats[i].text.rstrip().endswith("?") else "boom") for i in ranked]


def suggest(script: Script, timings: Timings, skip: set[int] = frozenset()) -> list[Hit]:
    """A few hits for the video (D223), each after one word, at least GAP s apart, never in a sentence of `skip`
    (the punchline's). The AI picks them; if none answers, a guess from the words, so a build never stops here."""
    most_n = most(timings.duration)
    try:
        spots = _asked(script, most_n, set(skip))
    except (CreateError, ValueError) as exc:
        log.info("create: sound hits guessed from the words (%s)", exc)
        spots = _guessed(script, set(skip))
    firsts, out, times = _first_words(script), [], []
    for i, word, kind in spots:
        words = script.beats[i].text.split()
        # The word asked for, else the sentence's last: a hit after the line, never mid-word.
        k = next((j for j in range(len(words) - 1, -1, -1) if word and _norm(words[j]) == _norm(word)), len(words) - 1)
        hit = Hit(word=firsts[i] + k, sound=kind if kind in PACK else "boom")
        t = at(hit, timings)
        if t is None or any(abs(t - o) < GAP for o in times):
            continue
        out.append(hit)
        times.append(t)
        if len(out) >= most_n:
            break
    return sorted(out, key=lambda h: h.word)


def library_file(sound_id: str):
    """Where a library sound is kept once picked (D223)."""
    return ensure(data_root() / "create" / "sfx") / f"freesound-{int(sound_id)}.mp3"


def clip(name: str, seed: int) -> tuple[np.ndarray, float]:
    """One hit's samples at its level, and how long before its moment it starts (a riser ends on it)."""
    if name.startswith("freesound:"):
        path = library_file(name.split(":", 1)[1])
        if not path.is_file():
            raise CreateError(f"the library sound {name} isn't downloaded")
        x = sound.load(path, LONGEST, loop=False)
        if len(x) > 2400:
            x[-2400:] *= np.linspace(1, 0, 2400)
        return x / (np.abs(x).max() or 1.0) * sound._db(LIBRARY_DB), 0.0
    if name not in PACK:
        raise CreateError(f"no sound called {name}")
    x, lead = sound.effect(name, seed)
    return x * sound._db(sound.FX_DB[name]), lead


def track(hits: list[Hit], timings: Timings, seed: int) -> np.ndarray:
    """The hits as one track the length of the voice, to add into sound.bed's: each just after its word, UNDER_DB
    lower where it runs on under speech. A hit that can't be made is left out and said in the log, never a failed
    build."""
    n = int(sound.RATE * timings.duration)
    out = np.zeros(n)
    for k, hit in enumerate(hits):
        moment = at(hit, timings)
        if moment is None:
            continue
        try:
            x, lead = clip(hit.sound, seed + k)
        except (CreateError, OSError, ValueError) as exc:
            log.warning("create: a sound hit was left out (%s)", exc)
            continue
        sound._add(out, x, moment - lead)
    speech = sound._speech([(w.start, w.end) for w in timings.words], n)
    return out * (1 - (1 - sound._db(UNDER_DB)) * speech)


def key() -> str:
    return os.environ.get("FREESOUND_API_KEY", "").strip()


def search(query: str) -> list[dict]:
    """Freesound's CC0 sounds for `query`, 5 s or shorter (D223): free to use in a monetised video, no credit owed.
    Its preview is what's used: the original file needs a sign-in (OAuth), the preview only the key."""
    if not key():
        raise CreateError("no Freesound key yet: add FREESOUND_API_KEY in Settings, Footage and sound libraries")
    try:
        r = httpx.get(f"{FREESOUND}/search/text/", timeout=20, params={
            "query": query[:100], "filter": f"{CC0} duration:[0 TO 5]", "page_size": 12,
            "fields": "id,name,username,duration,license,previews", "token": key()})
        r.raise_for_status()
    except httpx.HTTPError as exc:
        raise CreateError(f"Freesound didn't answer: {exc}") from exc
    return [{"sound": f"freesound:{s['id']}", "name": s.get("name", "")[:80], "by": s.get("username", ""),
             "seconds": round(float(s.get("duration") or 0), 1), "preview": (s.get("previews") or {}).get("preview-hq-mp3", "")}
            for s in r.json().get("results", []) if (s.get("previews") or {}).get("preview-hq-mp3")]


def fetch(sound_id: int) -> dict:
    """A library sound kept for the build (D223): its preview downloaded once, checked CC0 again."""
    path = library_file(str(sound_id))
    try:
        r = httpx.get(f"{FREESOUND}/sounds/{sound_id}/", timeout=20,
                      params={"fields": "id,name,license,previews", "token": key()})
        r.raise_for_status()
        info = r.json()
        if "publicdomain/zero" not in info.get("license", ""):
            raise CreateError("that sound isn't CC0, so it can't go in a video without credit")
        if not path.is_file():
            audio = httpx.get(info["previews"]["preview-hq-mp3"], timeout=30, follow_redirects=True)
            audio.raise_for_status()
            path.write_bytes(audio.content)
    except (httpx.HTTPError, KeyError) as exc:
        raise CreateError(f"Freesound didn't answer: {exc}") from exc
    return {"sound": f"freesound:{sound_id}", "name": info.get("name", "")[:80]}
