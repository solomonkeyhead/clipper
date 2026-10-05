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
