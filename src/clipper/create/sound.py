"""A Short's music bed and chalk sounds, made here (D156): nothing licensed, so nothing for Content ID to match on
any platform. Written as one WAV track at a fixed level under the voice, which assemble() mixes in after the voice
is normalised.

The levels follow the second research report (2026-10-07): music about 20 dB under the voice, a further 6 dB lower
while the voice speaks, silent from the pause before the punchline to the end; chalk taps 10 to 14 dB under the
voice, at most 6, never two within 5 s. The report found no study of either for educational Shorts, so both are
off unless a channel or video turns them on.
"""

from __future__ import annotations

import random
import wave
from pathlib import Path

import numpy as np

RATE = 48_000
#: The voice is normalised to -14 LUFS, about -16 dBFS RMS; these sit under that.
MUSIC_DB = -36.0          # music's RMS between words
DUCK_DB = -6.0            # further, while the voice speaks
TAP_DB = -26.0            # peak of a chalk tap
MAX_TAPS = 6
TAP_GAP = 5.0
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


def taps(times: list[float]) -> list[float]:
    """The tap times kept: in order, at most MAX_TAPS, none within TAP_GAP of the one before."""
    kept: list[float] = []
    for at in sorted(times):
        if len(kept) < MAX_TAPS and (not kept or at - kept[-1] >= TAP_GAP):
            kept.append(at)
    return kept


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


def bed(seconds: float, *, words: list[tuple[float, float]], quiet_from: float, tap_times: list[float],
        logo_at: float | None, with_music: bool, seed: int, track: Path | None = None) -> np.ndarray:
    """The whole track under the voice: music (the user's `track`, else made here; ducked under speech, silent from
    `quiet_from`), taps and the two-note sound logo at `logo_at`."""
    n = int(RATE * seconds)
    out = np.zeros(n)
    if with_music:
        level = _db(MUSIC_DB) * (1 - (1 - _db(DUCK_DB)) * _speech(words, n))
        fade = np.clip((quiet_from - np.arange(n) / RATE) / 0.3, 0, 1)   # out over 0.3 s before the punchline
        m = load(track, seconds) if track else music(seconds, seed)
        m = m / (np.sqrt(np.mean(m ** 2)) or 1.0)                         # to RMS 1, then to MUSIC_DB
        out += m * level * fade
    for k, at in enumerate(tap_times):
        s = tap(seed + k) * _db(TAP_DB)
        i = int(at * RATE)
        out[i:i + len(s)] += s[: max(0, n - i)]
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
