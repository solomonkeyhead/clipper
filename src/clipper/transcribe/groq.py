"""Speech recognition through Groq's API (D230), so the hosted site needs no GPU.

Gives back what the local backend gives: `Word`s with start, end, text and probability, and the language as an
ISO code, so captions, candidates and the rest never know which one heard the audio.

* Audio goes up as 16 kHz mono FLAC chunks of CHUNK seconds, each with OVERLAP seconds of the next one's start
  (the free tier takes 25 MB a file, the paid 100 MB; a chunk is a few MB). Words are stitched by time: each chunk
  keeps the words whose middle is before the middle of the overlap it shares with the next, so none is twice.
* Groq has no VAD filter like the local one, so a quiet stretch can come back as a made-up "Thank you." Whisper
  marks those segments (no speech likely, low confidence); their words are dropped.
* 429 and 5xx are retried with backoff; the dev tier allows 7,200 audio seconds an hour (checked 2026-10), so a
  long source can wait on `retry-after`.
* Every answer is recorded in the usage log (`llm.usage`, $0.04 an hour for the turbo model; Groq bills 10 s at least).
"""

from __future__ import annotations

import contextlib
import io
import math
import os
import tempfile
import time
import wave
from collections.abc import Callable
from pathlib import Path

import httpx

from ..config import TranscriptionConfig
from ..hosting import hosted
from ..models import Word
from ..utils.logging import get_logger
from .whisper import TranscriptionError

log = get_logger(__name__)

URL = "https://api.groq.com/openai/v1/audio/transcriptions"
CHUNK = 600.0        # seconds of new audio per request
OVERLAP = 6.0        # seconds each chunk runs into the next
MAX_BYTES = 20 * 1024 * 1024   # well under the free tier's 25 MB
RETRIES = 6
MAX_WAIT = 120.0     # the longest we wait on one retry-after
MIN_BILLED = 10.0
#: Whisper's own test for a segment that holds no speech (its defaults).
NO_SPEECH, LOW_LOGPROB = 0.6, -1.0

_sleep = time.sleep   # tests replace it

#: Groq answers with the language's name ("english"), the request takes the code.
LANGUAGES = {
    "english": "en", "german": "de", "spanish": "es", "french": "fr", "italian": "it", "portuguese": "pt",
    "dutch": "nl", "russian": "ru", "polish": "pl", "turkish": "tr", "ukrainian": "uk", "czech": "cs",
    "swedish": "sv", "norwegian": "no", "danish": "da", "finnish": "fi", "greek": "el", "hungarian": "hu",
    "romanian": "ro", "bulgarian": "bg", "croatian": "hr", "serbian": "sr", "slovak": "sk", "slovenian": "sl",
    "catalan": "ca", "arabic": "ar", "hebrew": "he", "persian": "fa", "hindi": "hi", "bengali": "bn",
    "urdu": "ur", "tamil": "ta", "telugu": "te", "thai": "th", "vietnamese": "vi", "indonesian": "id",
    "malay": "ms", "tagalog": "tl", "chinese": "zh", "japanese": "ja", "korean": "ko", "swahili": "sw",
    "afrikaans": "af", "lithuanian": "lt", "latvian": "lv", "estonian": "et", "icelandic": "is",
}

Encode = Callable[[float, float], tuple[bytes, str, str]]   # (start, length) -> (bytes, filename, mime)


def selected(cfg: TranscriptionConfig) -> bool:
    """Groq hears the audio: asked for, or the hosted site with a key and nothing said otherwise."""
    if cfg.backend:
        return cfg.backend == "groq"
    return hosted() and bool(os.environ.get("GROQ_API_KEY", "").strip())


def plan(duration: float) -> list[tuple[float, float]]:
    """(start, length) of each request; the last one reaches the end, so no tail is left to a tiny request."""
    out, at = [], 0.0
    while True:
        out.append((at, min(CHUNK + OVERLAP, duration - at)))
        if at + CHUNK + OVERLAP >= duration:
            return out
        at += CHUNK


def _client() -> httpx.Client:
    return httpx.Client(timeout=httpx.Timeout(300.0, connect=20.0))


def _complain(r: httpx.Response) -> str:
    try:
        detail = r.json()["error"]["message"]
    except Exception:
        detail = r.text[:200]
    if r.status_code in (401, 403):
        return f"Groq refused the API key ({detail}). Check GROQ_API_KEY."
    return f"Groq could not transcribe the audio (HTTP {r.status_code}: {detail})"


def _post(client: httpx.Client, key: str, data: dict, blob: tuple[bytes, str, str], billed: float) -> dict:
    """One chunk, retried on 429 / 5xx / network errors; every other error stops with Groq's own words."""
    from ..llm import usage

    usage.before_call("transcribe")   # the plan's AI allowance (D227): spent, nothing is sent
    last = ""
    for attempt in range(RETRIES):
        wait = min(2.0 ** attempt, MAX_WAIT)
        try:
            r = client.post(URL, data=data, files={"file": (blob[1], blob[0], blob[2])}, headers={"Authorization": f"Bearer {key}"})
        except httpx.TransportError as exc:
            last = f"could not reach Groq ({exc})"
        else:
            if r.status_code == 200:
                usage.record("groq", data["model"], "transcribe", audio_seconds=max(billed, MIN_BILLED))
                return r.json()
            if r.status_code != 429 and r.status_code < 500:
                raise TranscriptionError(_complain(r))
            last = "Groq is limiting requests (the plan's audio hours?)" if r.status_code == 429 \
                else f"Groq had an error (HTTP {r.status_code})"
            with contextlib.suppress(KeyError, ValueError):
                wait = min(float(r.headers["retry-after"]), MAX_WAIT)
        if attempt < RETRIES - 1:
            log.info("%s; trying again in %.0fs", last, wait)
            _sleep(wait)
    raise TranscriptionError(f"{last}; gave up after {RETRIES} tries. Try again in a few minutes.")


def _words(resp: dict, offset: float) -> list[Word]:
    """Groq's words as ours, `offset` later, without those of segments that hold no speech."""
    segs = resp.get("segments") or []
    out = []
    for w in resp.get("words") or []:
        text = str(w.get("word", "")).strip()
        if not text:
            continue
        start = float(w["start"])
        end = max(float(w["end"]), start)
        mid = (start + end) / 2
        seg = next((s for s in segs if s.get("start", 0) <= mid <= s.get("end", 0)), None)
        prob = 1.0
        if seg:
            logprob = float(seg.get("avg_logprob", 0.0))
            if float(seg.get("no_speech_prob", 0.0)) > NO_SPEECH and logprob < LOW_LOGPROB:
                continue
            prob = min(1.0, max(0.0, math.exp(logprob)))
        out.append(Word(start=offset + start, end=offset + end, text=text, probability=prob))
    return out


def transcribe_chunks(duration: float, encode: Encode, cfg: TranscriptionConfig, *, language: str | None = None,
                      prompt: str | None = None) -> tuple[list[Word], str]:
    """The words of `duration` seconds of audio, and the language code. `encode(start, length)` cuts one chunk."""
    key = os.environ.get("GROQ_API_KEY", "").strip()
    if not key:
        raise TranscriptionError("GROQ_API_KEY is not set, so Groq can't hear the audio. "
                                 "Add it to .env, or set transcription.backend: local.")
    if duration <= 0:
        raise TranscriptionError("there is no audio to transcribe")
    language = language or (None if cfg.language == "auto" else cfg.language)
    spans = plan(duration)
    words: list[Word] = []
    heard = ""
    with _client() as client:
        for k, (start, length) in enumerate(spans):
            data: dict = {"model": cfg.groq_model, "response_format": "verbose_json",
                          "timestamp_granularities[]": ["word", "segment"], "temperature": "0"}
            if language:
                data["language"] = language
            if prompt:
                data["prompt"] = prompt
            resp = _post(client, key, data, encode(start, length), length)
            # Held after the first chunk: a quiet one can otherwise be taken for another language (as locally).
            if not language and resp.get("language"):
                name = str(resp["language"]).strip().lower()
                language = LANGUAGES.get(name, name if len(name) == 2 else None)
                heard = language or name
            lo = start + OVERLAP / 2 if k else -1.0
            hi = start + CHUNK + OVERLAP / 2 if k < len(spans) - 1 else math.inf
            words += [w for w in _words(resp, start) if lo <= (w.start + w.end) / 2 < hi]
            log.debug("groq chunk %d/%d: %d words kept", k + 1, len(spans), len(words))
    return words, heard or language or ""


def _duration(path: Path) -> float:
    from ..render.ffmpeg import ffprobe_path, run

    out = run(["-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path], exe=ffprobe_path())
    return float(out.stdout.strip() or 0)


def _flac(path: Path) -> Encode:
    from ..render.ffmpeg import run

    def encode(start: float, length: float) -> tuple[bytes, str, str]:
        with tempfile.TemporaryDirectory() as d:
            for ext, codec, mime in (("flac", ["-c:a", "flac"], "audio/flac"), ("mp3", ["-b:a", "64k"], "audio/mpeg")):
                out = Path(d) / f"chunk.{ext}"
                run(["-hide_banner", "-nostdin", "-loglevel", "error", "-y", "-ss", f"{start:.3f}", "-t",
                     f"{length:.3f}", "-i", path, "-vn", "-ac", "1", "-ar", "16000", *codec, out])
                data = out.read_bytes()
                if len(data) <= MAX_BYTES or ext == "mp3":   # loud, busy audio can make FLAC big: 64 kbps MP3 never is
                    return data, out.name, mime
        raise AssertionError("unreachable")
    return encode


def transcribe_file(path: Path | str, cfg: TranscriptionConfig, *, duration: float | None = None,
                    language: str | None = None, prompt: str | None = None) -> tuple[list[Word], str]:
    """Any audio or video file that ffmpeg reads."""
    path = Path(path)
    return transcribe_chunks(duration or _duration(path), _flac(path), cfg, language=language, prompt=prompt)


def transcribe_samples(samples, rate: int, cfg: TranscriptionConfig, *, language: str | None = None,
                       prompt: str | None = None) -> tuple[list[Word], str]:
    """Mono float samples in [-1, 1] (a span cut from the analysis audio, the caption-fix recheck)."""
    import numpy as np

    pcm = (np.clip(samples, -1.0, 1.0) * 32767).astype(np.int16)

    def encode(start: float, length: float) -> tuple[bytes, str, str]:
        buf = io.BytesIO()
        with wave.open(buf, "wb") as f:
            f.setnchannels(1)
            f.setsampwidth(2)
            f.setframerate(rate)
            f.writeframes(pcm[int(start * rate): int((start + length) * rate)].tobytes())
        return buf.getvalue(), "chunk.wav", "audio/wav"

    return transcribe_chunks(len(pcm) / rate, encode, cfg, language=language, prompt=prompt)
