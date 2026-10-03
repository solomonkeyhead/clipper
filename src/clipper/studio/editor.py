"""The editor's back end (docs/DECISIONS.md D103).

The page edits a source video's words and timeline; what it needs from here:

* whether the video is ready (ingested and transcribed; a "prepare" job does it);
* a light copy to scrub: 360p with a keyframe every 12 frames, so any frame is a
  few decodes away and the page can jump around a 40-minute episode freely;
* the sound's shape, as peaks for the waveform;
* quick draft renders of an edit (the real pipeline, small and fast), so the
  preview is exactly what the clip will be: framing, captions, hook and all.

Deletions go to the Recycle Bin (utils/recycle), like every file Clipper removes.
"""

from __future__ import annotations

import json
import threading
import wave
from pathlib import Path

import numpy as np

from ..paths import work_dir
from ..utils.logging import get_logger

log = get_logger(__name__)

PROXY = "editor_proxy.mp4"
PEAKS = "editor_peaks.json"
PEAKS_PER_SECOND = 20
PROXY_HEIGHT = 360
PROXY_GOP = 12

_making: set[str] = set()
_lock = threading.Lock()


def prepared(source_id: str) -> bool:
    """Ingested and transcribed: its words and info are on this PC."""
    d = work_dir(source_id)
    return (d / "info.json").is_file() and (d / "transcript.json").is_file()


def proxy_path(source_id: str) -> Path:
    return work_dir(source_id) / PROXY


def proxy_ready(source_id: str) -> bool:
    return proxy_path(source_id).is_file()


def make_proxy(source_id: str, publish) -> None:
    """Start making the light copy in the background, once; `publish` says when it's done."""
    with _lock:
        if proxy_ready(source_id) or source_id in _making:
            return
        _making.add(source_id)
    threading.Thread(target=_proxy, args=(source_id, publish), name="editor-proxy", daemon=True).start()


def _proxy(source_id: str, publish) -> None:
    from ..ingest.download import load_info
    from ..render.ffmpeg import run

    out = proxy_path(source_id)
    part = out.with_suffix(".part.mp4")
    try:
        info = load_info(source_id)
        args = ["-hide_banner", "-nostdin", "-loglevel", "error", "-y", "-i", info.media.path,
                "-vf", f"scale=-2:{PROXY_HEIGHT}", "-c:v", "libx264", "-preset", "veryfast", "-crf", "30",
                "-g", str(PROXY_GOP), "-keyint_min", str(PROXY_GOP), "-pix_fmt", "yuv420p"]
        args += ["-c:a", "aac", "-b:a", "96k"] if info.media.has_audio else ["-an"]
        run([*args, "-movflags", "+faststart", str(part)])
        part.replace(out)
        log.info("editor copy of %s made", source_id)
    except Exception as exc:  # the page shows the source isn't scrubbable yet
        log.warning("couldn't make the editor's copy of %s: %s", source_id, exc)
    finally:
        with _lock:
            _making.discard(source_id)
        publish("editor.ready", {"source_id": source_id})


def peaks(source_id: str) -> list[int]:
    """The loudest sample in each 1/20 s, 0-100, from the analysis audio; kept once made."""
    from ..ingest.download import load_info

    cached = work_dir(source_id) / PEAKS
    if cached.is_file():
        return json.loads(cached.read_text(encoding="utf-8"))
    info = load_info(source_id)
    if not info.audio_path or not Path(info.audio_path).is_file():
        return []
    with wave.open(str(info.audio_path), "rb") as w:
        rate, frames = w.getframerate(), w.readframes(w.getnframes())
    samples = np.abs(np.frombuffer(frames, dtype=np.int16).astype(np.float32))
    step = max(1, rate // PEAKS_PER_SECOND)
    usable = len(samples) // step * step
    loud = samples[:usable].reshape(-1, step).max(axis=1) if usable else np.zeros(0)
    top = float(np.percentile(loud, 99.5)) if loud.size else 0.0
    out = [int(v) for v in np.clip(loud / max(top, 1.0) * 100, 0, 100).round()]
    cached.write_text(json.dumps(out), encoding="utf-8")
    return out


def words(source_id: str) -> list[dict]:
    from ..models import Transcript

    transcript = Transcript.load(work_dir(source_id) / "transcript.json")
    return [{"start": round(w.start, 3), "end": round(w.end, 3), "text": w.text} for w in transcript.words]


def preview_dir(source_id: str) -> Path:
    from ..paths import runs_dir

    return runs_dir() / source_id / "preview" / "clips"


def clear_previews(source_id: str) -> None:
    """The last preview goes before a new one is made (to the Recycle Bin)."""
    from ..utils.recycle import recycle

    folder = preview_dir(source_id)
    for old in folder.glob("*.mp4") if folder.is_dir() else []:
        recycle(old)
