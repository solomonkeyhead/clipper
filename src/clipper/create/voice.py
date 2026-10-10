"""The voiceover, dropped in after it's made by hand (ElevenLabs), timed word by word.

Speech recognition hears the file with word timings; each word of the approved
script is matched to what was heard, so captions keep the script's spelling
("endolymph", "Newton") while their times come from the actual voice. Words the
recogniser missed get times between their neighbours. Each beat runs from its
first word to the next beat's first word, so cuts land between sentences.
"""

from __future__ import annotations

import difflib
import re
from pathlib import Path

from pydantic import BaseModel

from ..paths import data_root, ensure
from .ai import CreateError
from .script import Script

AUDIO = {".mp3", ".wav", ".m4a", ".ogg", ".flac", ".aac", ".webm", ".opus"}   # webm: recorded in the page (D146)
TAIL = 0.6    # a breath after the last word, then it ends (and loops)


class TimedWord(BaseModel):
    text: str
    start: float
    end: float


class Timings(BaseModel):
    words: list[TimedWord]
    beats: list[tuple[float, float]]
    duration: float           # the whole video: the voice plus a breath
    matched: float            # share of script words heard, a sanity check


def silent(script: Script, words_per_second: float) -> tuple[list[TimedWord], float]:
    """No voice (D146): the script's words spread over the time they'd take to say at
    `words_per_second`, longer words a little longer, as what "was heard"; and how long that is.
    The video is then captions over the pictures, with a silent track."""
    words = [w for b in script.beats for w in b.text.split()]
    weights = [len(w) + 2 for w in words]
    unit = (len(words) / max(0.5, words_per_second)) / max(1, sum(weights))
    at, heard_words = 0.0, []
    for w, weight in zip(words, weights, strict=True):
        heard_words.append(TimedWord(text=w, start=round(at, 3), end=round(at + weight * unit * 0.9, 3)))
        at += weight * unit
    return heard_words, round(at, 3)


def write_silence(path: Path, seconds: float) -> Path:
    """A silent audio file `seconds` long, for a video with no voice."""
    import subprocess

    from ..render.ffmpeg import ffmpeg_path

    subprocess.run([str(ffmpeg_path()), "-y", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono", "-t", f"{seconds + TAIL:.3f}",
                    str(path)], check=True, capture_output=True)
    return path


def folder(video_id: int) -> Path:
    return ensure(data_root() / "create" / "videos" / str(video_id))


def _norm(word: str) -> str:
    return re.sub(r"[^a-z0-9]", "", word.lower())


def heard(path: Path) -> list[TimedWord]:
    """The words in the file, timed (Clipper's own speech recognition)."""
    from ..config import Config
    from ..transcribe.whisper import load_model

    model, *_ = load_model(Config.load().transcription)
    segments, _ = model.transcribe(str(path), language="en", word_timestamps=True, vad_filter=False)
    return [TimedWord(text=w.word.strip(), start=w.start, end=w.end) for s in segments for w in s.words or []]


#: Pauses longer than this are cut down to it (D155); the one before the last sentence keeps up to
#: BEAT_PAUSE, the deadpan beat before the punchline.
MAX_PAUSE = 0.25
BEAT_PAUSE = 0.6
LEAD = 0.1      # silence kept before the first word


def cuts(words: list[TimedWord], last_from: int) -> list[tuple[float, float]]:
    """The stretches of silence to take out: each pause over MAX_PAUSE loses its middle, so the words
    either side keep their tails; `last_from` is the first word of the last sentence."""
    out = []
    if words and words[0].start > LEAD:
        out.append((0.0, words[0].start - LEAD))
    for k in range(1, len(words)):
        keep = BEAT_PAUSE if k == last_from else MAX_PAUSE
        gap = words[k].start - words[k - 1].end
        if gap > keep + 0.02:
            mid = (words[k - 1].end + words[k].start) / 2
            out.append((mid - (gap - keep) / 2, mid + (gap - keep) / 2))
    return out


#: A cut stays where the voice is really quiet (D190): speech recognition ends a word early on a soft last
#: syllable, so a pause cut from the word's heard end took "genera-tor" and "tempera-ture" with it. Quieter than
#: QUIET dB under the voice's loud parts is silence; EDGE is kept after the last sound and before the next.
QUIET, EDGE = 32.0, 0.06


def levels(path: Path) -> list[float]:
    """The voice's loudness every 10 ms, in dB under its loud parts (the 95th percentile)."""
    import subprocess

    import numpy as np

    from ..render.ffmpeg import ffmpeg_path

    raw = subprocess.run([str(ffmpeg_path()), "-hide_banner", "-loglevel", "error", "-i", str(path), "-ac", "1",
                          "-ar", "16000", "-f", "s16le", "-"], capture_output=True, check=True).stdout
    x = np.frombuffer(raw, np.int16).astype(np.float32) / 32768
    n = len(x) // 160
    if not n:
        return []
    db = 10 * np.log10((x[: n * 160].reshape(n, 160) ** 2).mean(1) + 1e-10)
    return (db - np.percentile(db, 95)).tolist()


def quiet_only(gaps: list[tuple[float, float]], loud: list[float]) -> list[tuple[float, float]]:
    """Each cut shrunk to the longest quiet stretch inside it, so a word's tail running into it (and a short dip
    inside a word, "tempera . ture") is kept; a cut with no quiet left is dropped."""
    out = []
    for a, b in gaps:
        i, j = max(0, int(a * 100)), min(len(loud), int(b * 100))
        best, run = (i, i), None
        for k in range(i, j + 1):
            quiet = k < j and loud[k] <= -QUIET
            if quiet and run is None:
                run = k
            elif not quiet and run is not None:
                best = max(best, (run, k), key=lambda r: r[1] - r[0])
                run = None
        s, e = best
        a2 = a if s == i else s / 100 + EDGE
        b2 = b if e == j else e / 100 - EDGE
        if b2 - a2 > 0.03:
            out.append((a2, b2))
    return out


def tighten(path: Path, script: Script, words_heard: list[TimedWord], audio_seconds: float) -> tuple[Path, list[TimedWord], float]:
    """The voice with its long pauses cut (D155): a copy next to the recording (the take itself is kept),
    and the heard words moved to their new times. Nothing to cut: the recording as it is."""
    from ..render.ffmpeg import run

    if path.stem == "voice_tight":   # already cut; ffmpeg can't write the file it reads
        return path, words_heard, audio_seconds
    last_words = len(script.beats[-1].text.split()) if script.beats else 0
    # The last sentence's first word, counted from the end of what was heard (missed words aside).
    last_from = max(0, len(words_heard) - last_words)
    gaps = cuts(words_heard, last_from)
    if gaps:
        gaps = quiet_only(gaps, levels(path))
    if not gaps:
        return path, words_heard, audio_seconds
    keep, at = [], 0.0
    for a, b in gaps:
        keep.append((at, a))
        at = b
    keep.append((at, audio_seconds))
    select = "+".join(f"between(t,{a:.3f},{b:.3f})" for a, b in keep if b > a)
    out = path.with_name("voice_tight.wav")
    run(["-hide_banner", "-nostdin", "-loglevel", "error", "-y", "-i", str(path),
         "-af", f"aselect='{select}',asetpts=N/SR/TB", str(out)])

    def moved(t: float) -> float:
        return round(t - sum(min(b, t) - a for a, b in gaps if a < t), 3)

    words = [w.model_copy(update={"start": moved(w.start), "end": moved(w.end)}) for w in words_heard]
    return out, words, moved(audio_seconds)


def align(script: Script, words_heard: list[TimedWord], audio_seconds: float) -> Timings:
    """The script's words with the voice's times, and each beat's span."""
    said = [w for b in script.beats for w in b.text.split()]
    beat_of = [i for i, b in enumerate(script.beats) for _ in b.text.split()]
    a, b = [_norm(w) for w in said], [_norm(w.text) for w in words_heard]
    times: list[tuple[float, float] | None] = [None] * len(said)
    for block in difflib.SequenceMatcher(None, a, b, autojunk=False).get_matching_blocks():
        for k in range(block.size):
            h = words_heard[block.b + k]
            times[block.a + k] = (h.start, h.end)
    matched = sum(1 for t in times if t) / max(1, len(said))
    if matched < 0.6:
        raise CreateError(f"the voice doesn't match the approved script (only {matched:.0%} of its words "
                          "were heard); was it made from this script?")
    # Fill the gaps: a run of missed words shares the time between the words around it.
    filled = list(times)
    i = 0
    while i < len(filled):
        if filled[i]:
            i += 1
            continue
        j = i
        while j < len(filled) and not filled[j]:
            j += 1
        before = filled[i - 1][1] if i else 0.0
        after = filled[j][0] if j < len(filled) else audio_seconds
        step = max(0.05, after - before) / (j - i)
        for k in range(i, j):
            at = before + step * (k - i)
            filled[k] = (at + step * 0.1, at + step * 0.9)
        i = j
    words = [TimedWord(text=w, start=round(s, 3), end=round(e, 3)) for w, (s, e) in zip(said, filled, strict=True)]
    duration = round(words[-1].end + TAIL, 3) if words else audio_seconds
    starts = [0.0] + [next(w.start for w, bi in zip(words, beat_of, strict=True) if bi == i)
                      for i in range(1, len(script.beats))]
    beats = [(round(s, 3), round(starts[i + 1] if i + 1 < len(starts) else duration, 3)) for i, s in enumerate(starts)]
    return Timings(words=words, beats=beats, duration=duration, matched=round(matched, 3))


def nudged(script: Script, timings: Timings) -> Timings:
    """The cuts moved where the user nudged them (D171): sentence i's picture comes in `nudge` seconds earlier or
    later, the one before ending there; never leaving a picture under half a second. The words keep their times."""
    if len(timings.beats) != len(script.beats) or not any(b.nudge for b in script.beats[1:]):
        return timings
    beats = [list(x) for x in timings.beats]
    for i in range(1, len(beats)):
        if script.beats[i].nudge:
            at = min(max(beats[i][0] + script.beats[i].nudge, beats[i - 1][0] + 0.5), beats[i][1] - 0.5)
            beats[i - 1][1] = beats[i][0] = round(at, 3)
    return timings.model_copy(update={"beats": [tuple(x) for x in beats]})
