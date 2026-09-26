"""Getting a source onto disk and into a known shape.

Accepts a local path or a URL. URLs go through yt-dlp, which also hands back the
info dict -- including YouTube's ``heatmap`` ("Most replayed") when it exists,
which becomes the section 9.3 signal.

Everything downstream reads ``info.json`` and ``audio.wav``, never the original
container, so the rest of the pipeline never has to care whether the source came
from a URL or a folder.
"""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlparse

from ..models import MediaInfo, SourceInfo
from ..paths import downloads_dir, ensure, work_dir
from ..render.ffmpeg import run
from ..utils.cache import source_id_for_file
from ..utils.logging import get_logger
from .probe import probe

log = get_logger(__name__)

ANALYSIS_SAMPLE_RATE = 16_000
"""Whisper resamples to 16 kHz mono anyway, so extract it once at that rate."""


class IngestError(RuntimeError):
    """The source could not be fetched, read, or understood."""


def probe_rights(url: str) -> dict[str, str]:
    """The license, channel name and channel id a video's own listing states.

    Read before downloading, so a campaign that requires a license can refuse
    the video without fetching it. Values are "" when the listing has none.
    """
    import yt_dlp

    try:
        with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "noplaylist": True,
                               "skip_download": True}) as ydl:
            meta = ydl.extract_info(url, download=False) or {}
    except Exception as exc:  # yt-dlp raises many types
        raise IngestError(f"could not read the listing for {url}: {exc}") from exc
    if "entries" in meta:
        meta = (meta.get("entries") or [{}])[0] or {}
    return {key: str(meta.get(key) or "") for key in ("license", "channel", "channel_id", "title")}


def is_url(source: str) -> bool:
    """Whether `source` looks like a URL rather than a path.

    Deliberately strict about the scheme: a Windows path like ``C:\\videos\\a.mp4``
    parses with ``scheme='c'``, so checking for a non-empty scheme is not enough.
    """
    parsed = urlparse(str(source))
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def ingest(
    source: str,
    *,
    force: bool = False,
    keep_original: bool = True,
) -> SourceInfo:
    """Resolve a source to a local file, probe it, and extract analysis audio.

    Returns a `SourceInfo` and writes it as ``info.json`` in the source's work
    directory. Re-ingesting an unchanged source reuses both.
    """
    if is_url(source):
        media_path, metadata = _download(source, force=force)
    else:
        media_path = Path(source).expanduser().resolve()
        if not media_path.is_file():
            raise IngestError(
                f"no such file: {media_path}\n"
                "Pass a local video file, or an http(s) URL you are authorized to clip."
            )
        metadata = {}

    sid = source_id_for_file(media_path)
    wdir = ensure(work_dir(sid))
    info_path = wdir / "info.json"

    if info_path.is_file() and not force:
        try:
            cached = SourceInfo.load(info_path)
            if Path(cached.media.path).is_file() and Path(cached.audio_path).is_file():
                log.info("reusing ingest for %s (%s)", sid, cached.title or media_path.name)
                return cached
        except (ValueError, OSError):
            log.debug("cached info.json for %s is unreadable; re-ingesting", sid)

    media = probe(media_path)
    _validate(media, media_path)

    audio_path = wdir / "audio.wav"
    if force or not audio_path.is_file():
        extract_audio(media_path, audio_path)

    info = SourceInfo(
        source_id=sid,
        media=media,
        title=str(metadata.get("title", "") or media_path.stem),
        url=str(metadata.get("webpage_url", "") or (source if is_url(source) else "")),
        uploader=str(metadata.get("uploader", "") or ""),
        upload_date=str(metadata.get("upload_date", "") or ""),
        original_duration=float(metadata.get("duration", 0) or media.duration),
        audio_path=str(audio_path),
        heatmap=normalize_heatmap(metadata.get("heatmap")),
    )
    info.save(info_path)

    log.info(
        "ingested %s: %s (%.0fs, %dx%d, %s)",
        sid, info.title, media.duration, media.width, media.height,
        "heatmap available" if info.has_heatmap else "no heatmap",
    )
    if not keep_original and is_url(source):
        log.debug("keep_original=False, but the download is kept for resumability")
    return info


def _validate(media: MediaInfo, path: Path) -> None:
    """Fail early and clearly on media the pipeline cannot use."""
    if media.duration <= 0:
        raise IngestError(
            f"{path.name} reports a duration of {media.duration}s. The file may be "
            "corrupt or still downloading."
        )
    if not media.has_audio:
        raise IngestError(
            f"{path.name} has no audio track. clipper selects moments from speech, "
            "so a silent source cannot be clipped."
        )


def extract_audio(source: Path, dest: Path, *, sample_rate: int = ANALYSIS_SAMPLE_RATE) -> Path:
    """Extract mono PCM audio for transcription and the audio signal."""
    ensure(dest.parent)
    tmp = dest.with_suffix(".wav.part")
    run([
        "-hide_banner", "-nostdin", "-loglevel", "error",
        "-i", str(source),
        "-vn",
        "-ac", "1",
        "-ar", str(sample_rate),
        "-c:a", "pcm_s16le",
        # The .part extension hides the container from FFmpeg's format guesser,
        # so state it. Writing to .part first means an interrupted extraction
        # cannot leave a truncated audio.wav that later looks cached.
        "-f", "wav",
        "-y", str(tmp),
    ])
    tmp.replace(dest)
    log.debug("extracted %s (%.1f MB)", dest.name, dest.stat().st_size / 1e6)
    return dest


# --------------------------------------------------------------------------
# yt-dlp
# --------------------------------------------------------------------------


def _download(url: str, *, force: bool = False) -> tuple[Path, dict]:
    """Download a URL with yt-dlp and return (file, info dict)."""
    try:
        import yt_dlp
    except ImportError as exc:  # pragma: no cover - declared dependency
        raise IngestError(f"yt-dlp is not installed: {exc}") from exc

    out_dir = ensure(downloads_dir())

    # yt-dlp needs FFmpeg to merge the separate video and audio streams YouTube
    # serves, and it looks for it on PATH -- which is exactly the thing that is
    # unreliable here (a winget install lands on the *user* PATH, which a
    # already-running process does not see). We have already located it, so
    # hand the directory over rather than letting it fail with
    # "ffmpeg is not installed" after the metadata fetch.
    from ..render.ffmpeg import FFmpegNotFound, ffmpeg_path

    try:
        ffmpeg_dir = str(ffmpeg_path().parent)
    except FFmpegNotFound:
        ffmpeg_dir = ""
        log.warning(
            "FFmpeg was not found, so yt-dlp cannot merge video and audio "
            "streams. Run `clipper doctor` for the fix."
        )

    options = {
        # Cap at 1080p: the output is 1080x1920, so a 4K source costs disk and
        # decode time for detail that is cropped or downscaled away.
        "format": "bestvideo[height<=1080]+bestaudio/best[height<=1080]/best",
        "merge_output_format": "mp4",
        "outtmpl": str(out_dir / "%(id)s.%(ext)s"),
        "noprogress": True,
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "retries": 5,
        "fragment_retries": 5,
        "overwrites": bool(force),
        # Windows: force UTF-8 handling of titles in filenames.
        "encoding": "utf-8",
        "restrictfilenames": True,
    }
    if ffmpeg_dir:
        options["ffmpeg_location"] = ffmpeg_dir

    log.info("downloading %s", url)
    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            metadata = ydl.extract_info(url, download=True)
    except Exception as exc:  # yt-dlp raises many types
        raise IngestError(
            f"could not download {url}: {exc}\n"
            "Check the URL, your connection, and that you are authorized to use this "
            "source. Private or age-restricted videos may need cookies."
        ) from exc

    if metadata is None:
        raise IngestError(f"yt-dlp returned no metadata for {url}")
    if "entries" in metadata:  # a playlist slipped through
        metadata = metadata["entries"][0]

    path = _downloaded_path(metadata, out_dir)
    if path is None or not path.is_file():
        raise IngestError(
            f"yt-dlp reported success for {url} but no output file was found in {out_dir}"
        )
    return path, metadata


def _downloaded_path(metadata: dict, out_dir: Path) -> Path | None:
    """Find the file yt-dlp actually produced.

    ``filename`` is the pre-merge name, so after a video+audio merge it points at
    a file that no longer exists; ``requested_downloads`` holds the real one.
    """
    for entry in metadata.get("requested_downloads") or []:
        for key in ("filepath", "_filename", "filename"):
            value = entry.get(key)
            if value and Path(value).is_file():
                return Path(value)

    for key in ("filepath", "_filename", "filename"):
        value = metadata.get(key)
        if value and Path(value).is_file():
            return Path(value)

    video_id = metadata.get("id")
    if video_id:
        matches = sorted(out_dir.glob(f"{video_id}.*"))
        media = [m for m in matches if m.suffix.lower() not in (".part", ".ytdl", ".json")]
        if media:
            return media[0]
    return None


# --------------------------------------------------------------------------
# heatmap
# --------------------------------------------------------------------------


def normalize_heatmap(raw: object) -> list[dict[str, float]] | None:
    """Coerce yt-dlp's ``heatmap`` into a sorted list of typed segments.

    Shape as yt-dlp documents it: a list of dicts with ``start_time``,
    ``end_time`` and ``value``. This is defensive because the field is scraped
    from YouTube's player response and is not a stable API -- a shape change
    should degrade the heatmap signal to "unavailable", not crash ingest.
    """
    if not isinstance(raw, list) or not raw:
        return None

    segments: list[dict[str, float]] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        try:
            start = float(entry["start_time"])
            end = float(entry["end_time"])
            value = float(entry["value"])
        except (KeyError, TypeError, ValueError):
            continue
        if end <= start:
            continue
        segments.append({"start_time": start, "end_time": end, "value": value})

    if not segments:
        log.warning(
            "a heatmap field was present but no usable segments were parsed; "
            "the heatmap signal will be dropped for this source"
        )
        return None

    segments.sort(key=lambda s: s["start_time"])
    return segments


def load_info(source_id: str) -> SourceInfo:
    """Load a previously ingested source, with a useful error if it is absent."""
    path = work_dir(source_id) / "info.json"
    if not path.is_file():
        raise IngestError(
            f"no ingested source with id {source_id!r} (looked for {path}).\n"
            "Run `clipper transcribe <source>` first."
        )
    return SourceInfo.load(path)


def find_source_ids() -> list[str]:
    """Every source id with an ``info.json``, newest first. Used by CLI errors."""
    from ..paths import data_root

    root = data_root() / "work"
    if not root.is_dir():
        return []
    found = [d for d in root.iterdir() if (d / "info.json").is_file()]
    found.sort(key=lambda d: (d / "info.json").stat().st_mtime, reverse=True)
    return [d.name for d in found]


def describe_heatmap(info: SourceInfo) -> str:
    """One line for logs and `explain`."""
    if not info.heatmap:
        return "no heatmap"
    values = [s["value"] for s in info.heatmap]
    return (
        f"{len(info.heatmap)} heatmap segments, "
        f"value range {min(values):.3f}-{max(values):.3f}"
    )


def dump_info_json(info: SourceInfo) -> str:
    """Pretty JSON, for `clipper explain` and debugging."""
    return json.dumps(info.model_dump(mode="json"), indent=2, ensure_ascii=False)
