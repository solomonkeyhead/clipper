"""faster-whisper wrapper producing word-level timestamps.

Three things this handles that a bare `WhisperModel(...)` call does not:

* **GPU visibility.** The CUDA DLL directories must be registered before
  ctranslate2 is imported, or the model silently runs on CPU. `ensure_cuda_dll_path`
  is called at the top of `load_model`, before the import.
* **OOM fallback.** 8 GB is shared with everything else on the card, so a CUDA
  OOM drops to `int8_float16`, then to a smaller model, rather than failing.
* **Long sources.** Six-hour podcasts are transcribed in chunks with the offsets
  reapplied, so peak memory does not scale with source length.
"""

from __future__ import annotations

import gc
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..config import TranscriptionConfig
from ..models import SourceInfo, Transcript, Word
from ..paths import work_dir
from ..utils.logging import get_logger

if TYPE_CHECKING:
    from faster_whisper import WhisperModel

log = get_logger(__name__)

# Tried in order when the configured compute type runs out of memory.
COMPUTE_FALLBACKS = ("float16", "int8_float16", "int8")

# Tried in order when even int8 will not fit.
MODEL_FALLBACKS = ("large-v3", "distil-large-v3", "medium", "small", "base")


class TranscriptionError(RuntimeError):
    pass


class NoSpeechError(TranscriptionError):
    """The source contains no transcribable speech."""


@dataclass
class TranscribeStats:
    """Measured, not estimated. Reported at the end of a run."""

    audio_seconds: float
    wall_seconds: float
    model: str
    compute_type: str
    device: str
    word_count: int

    @property
    def realtime_factor(self) -> float:
        """How many seconds of audio per second of wall clock."""
        return self.audio_seconds / self.wall_seconds if self.wall_seconds else 0.0


def resolve_device(configured: str) -> str:
    """Decide between cuda and cpu, proving CUDA is actually usable."""
    if configured != "auto":
        return configured

    from ..utils.cuda import ensure_cuda_dll_path

    ensure_cuda_dll_path()
    try:
        import ctranslate2

        if ctranslate2.get_cuda_device_count() > 0:
            return "cuda"
    except (ImportError, RuntimeError) as exc:  # pragma: no cover - env dependent
        log.debug("CUDA probe failed: %s", exc)
    log.warning(
        "no CUDA device visible to CTranslate2; transcribing on CPU, which is "
        "far slower. Run `clipper doctor` for the fix."
    )
    return "cpu"


def _configure_hf_cache() -> None:
    """Stop huggingface_hub from using symlinks on Windows.

    By default it symlinks snapshot files to blobs, which needs Developer Mode
    or admin. Without either, downloading a model dies with
    ``WinError 1314: A required privilege is not held by the client`` -- after
    the multi-gigabyte download has already completed. Verified on this machine
    with ``large-v3``. Copying instead costs disk, not a failed run.
    """
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS", "1")
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")


def load_model(cfg: TranscriptionConfig) -> tuple[WhisperModel, str, str, str]:
    """Load a WhisperModel, degrading on OOM. Returns (model, name, compute, device).

    The degradation ladder is compute type first (a large model at int8 beats a
    small model at float16 for accuracy), then model size.
    """
    from ..utils.cuda import ensure_cuda_dll_path

    _configure_hf_cache()
    ensure_cuda_dll_path()
    from faster_whisper import WhisperModel

    device = resolve_device(cfg.device)
    compute_types = _fallback_chain(cfg.compute_type, COMPUTE_FALLBACKS) if device == "cuda" else [
        "int8" if cfg.compute_type.startswith("int8") else "float32"
    ]
    models = _fallback_chain(cfg.model, MODEL_FALLBACKS)

    last_error: Exception | None = None
    for model_name in models:
        for compute_type in compute_types:
            try:
                started = time.perf_counter()
                model = WhisperModel(model_name, device=device, compute_type=compute_type)
                log.info(
                    "loaded %s (%s, %s) in %.1fs",
                    model_name, compute_type, device, time.perf_counter() - started,
                )
                return model, model_name, compute_type, device
            except Exception as exc:
                if not _is_oom(exc):
                    raise TranscriptionError(
                        f"could not load whisper model {model_name!r} "
                        f"({compute_type}, {device}): {exc}"
                    ) from exc
                log.warning(
                    "out of memory loading %s at %s; trying a smaller configuration",
                    model_name, compute_type,
                )
                last_error = exc
                free_gpu_memory()

    raise TranscriptionError(
        "every model and compute-type combination ran out of GPU memory. "
        "Close other GPU applications, or set transcription.device to 'cpu'.\n"
        f"Last error: {last_error}"
    )


def _fallback_chain(preferred: str, ladder: tuple[str, ...]) -> list[str]:
    """`preferred` first, then the ladder entries at or below it."""
    if preferred in ladder:
        index = ladder.index(preferred)
        return list(ladder[index:])
    return [preferred, *ladder]


def _is_oom(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(s in text for s in ("out of memory", "cuda_error_out_of_memory", "cublas_status_alloc"))


#: Whisper work on the GPU, one at a time (D75). Two clipping jobs run at once
#: and a job renders clips in parallel; all of them may want Whisper (the main
#: transcription, caption-fix rechecks) and an 8 GB card can't hold several.
GPU_LOCK = threading.RLock()


_models: dict[tuple, tuple] = {}


def shared_model(cfg: TranscriptionConfig) -> tuple[WhisperModel, str, str, str]:
    """The process's one Whisper model for `cfg`, loaded on first use (D75).

    Loading large-v3 took ~4s for every video in a batch, and caption-fix
    rechecks loaded their own copy as well; one resident copy serves both.
    """
    key = (cfg.model, cfg.device, cfg.compute_type)
    with GPU_LOCK:
        if key not in _models:
            _models[key] = load_model(cfg)
        return _models[key]


def free_gpu_memory() -> None:
    """Release VRAM between stages. The brief requires stages not to overlap."""
    gc.collect()


def transcribe(
    info: SourceInfo,
    cfg: TranscriptionConfig,
    *,
    force: bool = False,
) -> tuple[Transcript, TranscribeStats | None]:
    """Transcribe an ingested source to word-level timestamps.

    Returns the transcript and, when it was actually computed rather than loaded
    from cache, the measured speed.
    """
    out_path = work_dir(info.source_id) / "transcript.json"
    if out_path.is_file() and not force:
        try:
            cached = Transcript.load(out_path)
            # The cache is only valid for the model that produced it. Without
            # this check, `--model large-v3` on a source transcribed earlier with
            # `small` silently returns the small transcript.
            cached_model = cached.model.split("/")[0]
            if cached.words and cached_model and cached_model != cfg.model:
                log.info(
                    "cached transcript was made with %s but %s was requested; re-transcribing",
                    cached_model, cfg.model,
                )
            elif cached.words:
                log.info("reusing transcript for %s (%d words)", info.source_id, len(cached.words))
                return cached, None
        except (ValueError, OSError):
            log.debug("cached transcript is unreadable; re-transcribing")

    audio = Path(info.audio_path)
    if not audio.is_file():
        raise TranscriptionError(f"analysis audio missing: {audio}. Re-run ingest.")

    with GPU_LOCK:
        model, model_name, compute_type, device = shared_model(cfg)
        started = time.perf_counter()
        words, language, language_probability = _run(model, audio, info.media.duration, cfg)

    wall = time.perf_counter() - started
    if not words:
        raise NoSpeechError(
            f"{info.title or info.source_id} produced no transcribable speech. "
            "If the source really does contain speech, try transcription.vad_filter: false."
        )

    transcript = Transcript(
        source_id=info.source_id,
        language=language,
        language_probability=language_probability,
        model=f"{model_name}/{compute_type}",
        duration=info.media.duration,
        words=words,
    )
    transcript.save(out_path)

    stats = TranscribeStats(
        audio_seconds=info.media.duration,
        wall_seconds=wall,
        model=model_name,
        compute_type=compute_type,
        device=device,
        word_count=len(words),
    )
    log.info(
        "transcribed %.0fs of audio in %.1fs (%.1fx realtime), %d words, language=%s",
        stats.audio_seconds, stats.wall_seconds, stats.realtime_factor,
        stats.word_count, language,
    )
    return transcript, stats


def _run(
    model: WhisperModel, audio: Path, duration: float, cfg: TranscriptionConfig
) -> tuple[list[Word], str, float]:
    """Transcribe, chunking when the source is long enough to warrant it."""
    if duration <= cfg.chunk_seconds:
        return _transcribe_span(model, audio, cfg, offset=0.0)

    chunks = int(duration // cfg.chunk_seconds) + 1
    log.info(
        "source is %.1f hours; transcribing in %d chunks of %d min",
        duration / 3600, chunks, cfg.chunk_seconds // 60,
    )
    all_words: list[Word] = []
    language, probability = "", 0.0

    for index in range(chunks):
        offset = index * cfg.chunk_seconds
        if offset >= duration:
            break
        span = min(cfg.chunk_seconds, duration - offset)
        words, lang, prob = _transcribe_span(
            model, audio, cfg, offset=offset, length=span,
            # Detect the language once, then hold it: a near-silent chunk can
            # otherwise be detected as a different language mid-source.
            language=language or None,
        )
        if not language and lang:
            language, probability = lang, prob
        all_words.extend(words)
        log.debug("chunk %d/%d: %d words", index + 1, chunks, len(words))

    return all_words, language or "en", probability


def _transcribe_span(
    model: WhisperModel,
    audio: Path,
    cfg: TranscriptionConfig,
    *,
    offset: float,
    length: float | None = None,
    language: str | None = None,
) -> tuple[list[Word], str, float]:
    """One faster-whisper pass, with `offset` added back to every timestamp."""
    options: dict[str, Any] = {
        "beam_size": cfg.beam_size,
        "word_timestamps": True,
        "vad_filter": cfg.vad_filter,
        "language": language or (None if cfg.language == "auto" else cfg.language),
    }
    if offset or length is not None:
        options["clip_timestamps"] = [offset, offset + (length or 0.0)]

    segments, info = model.transcribe(str(audio), **options)

    words: list[Word] = []
    for segment in segments:
        for word in segment.words or []:
            text = word.word.strip()
            if not text:
                continue
            words.append(Word(
                start=float(word.start),
                end=max(float(word.end), float(word.start)),
                text=text,
                probability=float(getattr(word, "probability", 1.0) or 1.0),
            ))

    return words, info.language or "", float(info.language_probability or 0.0)
