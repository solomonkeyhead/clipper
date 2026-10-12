"""A Short's music bed and sound effects, made here (D156, D197): nothing licensed, so nothing for Content ID to
match on any platform. Written as one WAV track at a fixed level under the voice, which assemble() mixes in after the
voice is normalised.

The levels follow the second research report (2026-10-07): music about 20 dB under the voice, a further 6 dB lower
while the voice speaks, silent from the pause before the punchline to the end. The effects (D197) mark the edit: a
whoosh on a whip, a thump on a punch-in, chalk as each part of a drawing comes, a pop for the hook, a riser into the
punchline; each 12 dB or more under the voice, spaced, the most important kept (pick()). Off unless a channel or
video turns them on.
"""

from __future__ import annotations

import random
import wave
from pathlib import Path

import numpy as np
from scipy import signal

RATE = 48_000
#: The voice is normalised to -14 LUFS, about -16 dBFS RMS; these sit under that.
MUSIC_DB = -36.0          # music's RMS between words
DUCK_DB = -6.0            # further, while the voice speaks
TAP_DB = -26.0            # peak of a chalk tap (the sound logo's)
#: A pentatonic scale in A (Hz), low to high: no note in it clashes with another.
NOTES = [220.0, 246.9, 277.2, 329.6, 370.0, 440.0, 493.9, 554.4, 659.3]
CHORDS = [(220.0, 277.2, 329.6), (185.0, 220.0, 277.2), (146.8, 185.0, 220.0), (164.8, 207.7, 246.9)]


def _db(x: float) -> float:
    return 10 ** (x / 20)


def _pluck(freq: float, seconds: float = 0.9) -> np.ndarray:
    t = np.arange(int(RATE * seconds)) / RATE
    tone = np.sin(2 * np.pi * freq * t) + 0.35 * np.sin(4 * np.pi * freq * t) + 0.12 * np.sin(6 * np.pi * freq * t)
    return tone * np.exp(-t * 5.0) * np.minimum(1.0, t * 400)


def music(seconds: float, seed: int, bpm: float = 88.0) -> np.ndarray:
    """A quiet plucked tune over a soft pad, different per seed, at 0 dB peak before levelling."""
    rnd = random.Random(seed)
    n = int(RATE * seconds)
    out = np.zeros(n + RATE)
    beat = 60.0 / bpm
    t = np.arange(n + RATE) / RATE
    bar = 4 * beat
    for k in range(int(seconds / bar) + 1):   # pad: one chord a bar, slow in and out
        start, chord = k * bar, CHORDS[k % len(CHORDS)]
        span = (t >= start) & (t < start + bar)
        local = t[span] - start
        env = np.sin(np.pi * local / bar) ** 2
        out[span] += 0.18 * env * sum(np.sin(2 * np.pi * f * t[span]) for f in chord)
    step = rnd.choice([0, 2])
    at = 0.0
    while at < seconds:                       # plucks on most eighth notes, a walk up and down the scale
        if rnd.random() < 0.7:
            step = max(0, min(len(NOTES) - 1, step + rnd.choice([-2, -1, 1, 2])))
            note = _pluck(NOTES[step])
            i = int(at * RATE)
            out[i:i + len(note)] += 0.5 * note[: len(out) - i]
        at += beat / 2
    out = out[:n]
    return out / (np.abs(out).max() or 1.0)


def tap(seed: int = 0, pitch: float = 1.0) -> np.ndarray:
    """A chalk tap: a short burst of filtered noise with a small knock."""
    rng = np.random.default_rng(seed)
    t = np.arange(int(RATE * 0.06)) / RATE
    noise = np.convolve(rng.standard_normal(len(t)), np.ones(6) / 6, mode="same")
    knock = np.sin(2 * np.pi * 900 * pitch * t)
    sound = (0.7 * noise + 0.5 * knock) * np.exp(-t * 90)
    return sound / np.abs(sound).max()


#: Sound effects on the edit (D197), made here like the music: nothing for Content ID to match. The peak (dBFS) of
#: each while the voice (-14 LUFS) speaks, FX_LIFT dB fuller between words. On video 25's voice the effects alone
#: came to -29 LUFS and the mix stayed at -14.0.
FX_DB = {"whoosh": -14.0, "zoom": -15.0, "swish": -20.0, "thump": -12.0, "pop": -16.0, "ding": -19.0,
         "scratch": -14.0, "bonk": -13.0, "boing": -14.0, "riser": -18.0, "swell": -18.0, "chalk": -22.0}
FX_LIFT = 3.0
#: The most important first: of two within FX_GAP s, the one earlier in this list stays.
FX_ORDER = ("riser", "swell", "scratch", "bonk", "boing", "whoosh", "zoom", "thump", "pop", "ding", "swish")
FX_GAP, FX_SAME_GAP, FX_EVERY = 0.8, 2.5, 2.5   # s apart; s apart for one kind; at most one per FX_EVERY s
FX_MOST = {"swish": 3, "ding": 2}
CHALK_GAP, MAX_CHALK = 0.6, 16


def spaced(times: list[float], gap: float, most: int) -> list[float]:
    """The times kept: in order, at most `most`, none within `gap` of the one before (was taps(), D156)."""
    kept: list[float] = []
    for at in sorted(times):
        if len(kept) < most and (not kept or at - kept[-1] >= gap):
            kept.append(at)
    return kept


def pick(events: list[tuple[float, str]], seconds: float,
         quiet: tuple[float, float] = (0.0, 0.0)) -> list[tuple[float, str]]:
    """The sound effects kept (D197): the most important first (FX_ORDER), none within FX_GAP s of one already kept
    (FX_SAME_GAP of its own kind), at most FX_MOST of a kind and one per FX_EVERY s in all, none while the
    punchline is said (`quiet`, from the pause before it to its last word). Chalk on its own: a texture, quieter."""
    kept: list[tuple[float, str]] = []
    budget = max(4, int(seconds / FX_EVERY))
    for at, kind in sorted((e for e in events if e[1] in FX_ORDER), key=lambda e: (FX_ORDER.index(e[1]), e[0])):
        if quiet[0] < at < quiet[1] or len(kept) >= budget \
                or sum(k == kind for _, k in kept) >= FX_MOST.get(kind, budget) \
                or any(abs(at - t) < (FX_SAME_GAP if k == kind else FX_GAP) for t, k in kept):
            continue
        kept.append((at, kind))
    chalk = spaced([at for at, kind in events if kind == "chalk" and not quiet[0] < at < quiet[1]], CHALK_GAP, MAX_CHALK)
    return sorted(kept + [(at, "chalk") for at in chalk])


def _band(noise: np.ndarray, centres: list[float], width: float) -> np.ndarray:
    """Noise through a band-pass whose centre (Hz) moves through `centres` over its length: shaped in the STFT."""
    f, t, z = signal.stft(noise, RATE, nperseg=1024, noverlap=896)
    fc = np.interp(t, np.linspace(t[0], t[-1], len(centres)), centres)
    z = z * np.exp(-0.5 * ((np.log(np.maximum(f, 20))[:, None] - np.log(fc)[None, :]) / width) ** 2)
    return signal.istft(z, RATE, nperseg=1024, noverlap=896)[1][: len(noise)]


def _rise_fall(n: int, peak: float, fall: float) -> np.ndarray:
    """Up as a square to its peak (a share of the length), then down over `fall` s."""
    t = np.arange(n) / RATE
    tp = peak * n / RATE
    return np.where(t < tp, (t / tp) ** 2, np.exp(-(t - tp) / fall))


def _glide(f: np.ndarray) -> np.ndarray:
    """A sine following the frequency `f` (Hz, one a sample)."""
    return np.sin(2 * np.pi * np.cumsum(f) / RATE)


def effect(kind: str, seed: int) -> tuple[np.ndarray, float]:
    """One sound effect (D197) at peak 1, and how many seconds before its moment it starts: a whoosh peaks on the
    cut, a riser and the swell end on theirs, chalk starts as the part does. Each a little different by `seed`."""
    rng = np.random.default_rng(seed)
    j = lambda lo, hi: rng.uniform(lo, hi)   # noqa: E731
    noise = lambda s: rng.standard_normal(int(RATE * s))   # noqa: E731
    if kind in ("whoosh", "swish"):   # filtered noise swept up and back down, peaking on the cut
        d, lo, hi, end = (0.42 * j(0.9, 1.15), 250, 2600 * j(0.8, 1.25), 500) if kind == "whoosh" else \
                         (0.24 * j(0.9, 1.1), 700, 2000 * j(0.9, 1.1), 1200)
        sweep = [*np.geomspace(lo, hi, 20), *np.geomspace(hi, end, 12)]
        x = _band(noise(d), sweep if seed % 2 else sweep[::-1], 0.45)   # every other one swept the other way
        x, lead = x * _rise_fall(len(x), 0.55, d * 0.16), 0.55 * d
    elif kind == "zoom":              # swept up only, a low glide under it, stopping on the cut
        d = 0.32 * j(0.9, 1.1)
        x = _band(noise(d), list(np.geomspace(300, 3800 * j(0.85, 1.2), 24)), 0.4)
        x = (x / np.abs(x).max() + 0.25 * _glide(np.geomspace(120, 480, len(x)))) * _rise_fall(len(x), 0.82, 0.03)
        lead = 0.82 * d
    elif kind == "thump":             # a low sine falling in pitch, a click on top; saturated so phones hear it
        t = np.arange(int(RATE * 0.4)) / RATE
        body = _glide(j(50, 64) + 70 * np.exp(-t / 0.03)) * np.exp(-t / j(0.09, 0.14))
        click = signal.sosfilt(signal.butter(2, 2500, "low", fs=RATE, output="sos"), noise(0.4)) * np.exp(-t / 0.006)
        x, lead = np.tanh(2.2 * (body + 0.6 * click / np.abs(click).max())), 0.0
    elif kind == "pop":               # a short blip dropping fast in pitch
        t = np.arange(int(RATE * 0.1)) / RATE
        x = _glide(420 + (j(1100, 1600) - 420) * np.exp(-t / 0.012)) * np.minimum(1, t / 0.002) * np.exp(-t / 0.022)
        lead = 0.0
    elif kind == "ding":              # a small bell on a note of the music's scale (A pentatonic, two octaves up)
        f, t = NOTES[(5 + seed) % 3 + 6] * 2, np.arange(int(RATE * 0.9)) / RATE
        x = (np.sin(2 * np.pi * f * t) * np.exp(-t / 0.35) + 0.5 * np.sin(2 * np.pi * 2.76 * f * t) * np.exp(-t / 0.12)
             + 0.25 * np.sin(2 * np.pi * 5.4 * f * t) * np.exp(-t / 0.05)) * np.minimum(1, t / 0.001)
        lead = 0.0
    elif kind == "scratch":           # a record scratch: noise scrubbed down and back up, twice
        d = 0.32
        t = np.arange(int(RATE * d)) / RATE
        x = _band(noise(d), [2200, 900, 400, 900, 1800, 700], 0.3)
        x, lead = x * np.abs(np.sin(2 * np.pi * t / d)) ** 0.5 * np.exp(-t / 0.25), 0.0
    elif kind == "boing":             # a wobbling spring
        t = np.arange(int(RATE * 0.5)) / RATE
        f = 190 * j(0.9, 1.1) * (1 + 0.7 * t) * (1 + 0.12 * np.sin(2 * np.pi * 13 * t) * np.exp(-t / 0.2))
        x, lead = np.tanh(1.8 * _glide(f)) * np.exp(-t / 0.18) * np.minimum(1, t / 0.004), 0.0
    elif kind == "bonk":              # a wood block on the head
        t, k = np.arange(int(RATE * 0.25)) / RATE, j(0.92, 1.08)
        x = (np.sin(2 * np.pi * 720 * k * t) * np.exp(-t / 0.04) + 0.6 * np.sin(2 * np.pi * 1930 * k * t) * np.exp(-t / 0.02)
             + 0.8 * np.sin(2 * np.pi * 170 * k * t) * np.exp(-t / 0.06))
        x, lead = np.tanh(1.5 * x), 0.0
    elif kind == "riser":             # noise and a tone climbing two octaves, cut dead on its moment
        d = 1.5 * j(0.85, 1.15)
        t = np.arange(int(RATE * d)) / RATE
        x = _band(noise(d), list(np.geomspace(300, 5000, 30)), 0.35)
        x = (x / np.abs(x).max() + 0.35 * _glide(np.geomspace(110, 440, len(t)) * (1 + 0.01 * np.sin(12 * np.pi * t))))
        x *= _db(-30) ** (1 - (t / d) ** 1.5)
        x[-960:] *= np.linspace(1, 0, 960)   # 20 ms
        lead = len(x) / RATE
    elif kind == "swell":             # a reversed reverb of a soft chord, rising into the loop's first frame
        d = 0.5
        tail = signal.sosfilt(signal.butter(2, [300, 5000], "band", fs=RATE, output="sos"),
                              noise(d) * np.exp(-np.arange(int(RATE * d)) / RATE / 0.16))
        t = np.arange(int(RATE * 0.05)) / RATE
        chord = sum(np.sin(2 * np.pi * f * t) for f in CHORDS[0] + (440.0,))
        x = np.convolve(chord, tail)[: len(tail)][::-1]
        lead = len(x) / RATE
    elif kind == "chalk":             # chalk on the board: a scratchy hiss in strokes, grit in it
        d = 0.45
        t = np.arange(int(RATE * d)) / RATE
        grit = (rng.random(len(t)) < 300 / RATE) * rng.standard_normal(len(t)) * 3
        raw = noise(d) * np.abs(np.sin(np.pi * j(7, 12) * t + j(0, 3))) ** 1.5 \
            + 0.5 * np.convolve(grit, np.exp(-np.arange(48) / 8), mode="same")
        x = signal.sosfilt(signal.butter(2, [1800, 6500], "band", fs=RATE, output="sos"), raw)
        x, lead = x * np.minimum(1, np.minimum(t / 0.02, (d - t) / 0.05)), 0.15
    else:
        raise ValueError(kind)
    return x / (np.abs(x).max() or 1.0), lead


def _add(out: np.ndarray, sound: np.ndarray, at: float) -> None:
    """`sound` added into `out` from `at` seconds, cut to fit at either end."""
    i = round(at * RATE)
    a, b = max(0, -i), min(len(sound), len(out) - i)
    if b > a:
        out[i + a:i + b] += sound[a:b]


def _speech(words: list[tuple[float, float]], n: int) -> np.ndarray:
    """1 while the voice speaks (gaps under 0.3 s joined), 0 otherwise, eased over 0.3 s down and 0.5 s up."""
    on = np.zeros(n)
    for a, b in words:
        on[int(a * RATE):int((b + 0.3) * RATE)] = 1.0
    attack, release = int(0.3 * RATE), int(0.5 * RATE)
    out, level = np.zeros(n), 0.0
    # ponytail: a per-block loop (10 ms), not per sample; fine for a minute of audio
    block = RATE // 100
    for i in range(0, n, block):
        target = on[i:i + block].max()
        level += (target - level) * min(1.0, block / (attack if target > level else release))
        out[i:i + block] = level
    return out


def load(path: Path, seconds: float) -> np.ndarray:
    """An audio file of the user's own (a track from YouTube's Audio Library, say) as mono samples, looped to length."""
    import subprocess

    from ..render.ffmpeg import ffmpeg_path

    raw = subprocess.run([str(ffmpeg_path()), "-loglevel", "error", "-i", str(path), "-ac", "1", "-ar", str(RATE),
                          "-f", "s16le", "-"], capture_output=True, check=True, timeout=120).stdout
    data = np.frombuffer(raw, dtype="<i2").astype(float) / 32768
    n = int(RATE * seconds)
    return np.tile(data, n // max(1, len(data)) + 1)[:n] if len(data) else np.zeros(n)


def bed(seconds: float, *, words: list[tuple[float, float]], quiet_from: float, effects: list[tuple[float, str]],
        logo_at: float | None, with_music: bool, seed: int, track: Path | None = None) -> np.ndarray:
    """The whole track under the voice: music (the user's `track`, else made here; ducked under speech, silent from
    `quiet_from`), the sound effects ((seconds, kind), D197) and the two-note sound logo at `logo_at`."""
    n = int(RATE * seconds)
    out = np.zeros(n)
    speech = _speech(words, n)
    if with_music:
        level = _db(MUSIC_DB) * (1 - (1 - _db(DUCK_DB)) * speech)
        fade = np.clip((quiet_from - np.arange(n) / RATE) / 0.3, 0, 1)   # out over 0.3 s before the punchline
        m = load(track, seconds) if track else music(seconds, seed)
        m = m / (np.sqrt(np.mean(m ** 2)) or 1.0)                         # to RMS 1, then to MUSIC_DB
        out += m * level * fade
    fx = np.zeros(n)
    for k, (at, kind) in enumerate(effects):
        s, lead = effect(kind, seed + k)
        _add(fx, s * _db(FX_DB[kind]), at - lead)
    out += fx * _db(FX_LIFT * (1 - speech))   # a little fuller between words
    if logo_at is not None:
        for k, pitch in enumerate((1.0, 1.5)):   # two taps 0.15 s apart, the second higher
            s = tap(99 + k, pitch) * _db(TAP_DB + 2)
            i = int((logo_at + 0.15 * k) * RATE)
            out[i:i + len(s)] += s[: max(0, n - i)]
    return out


def write(track: np.ndarray, path: Path) -> Path:
    data = (np.clip(track, -1, 1) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(RATE)
        f.writeframes(data.tobytes())
    return path
