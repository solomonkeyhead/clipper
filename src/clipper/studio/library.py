"""The clip library: data/library/<campaign>/, one place for every finished clip.

Runs file their accepted clips here themselves (`register`), so finding a clip
no longer means knowing which output folder a run wrote to. `import_folder`
brings in the hand-made "-ready" folders from before the library, reading the
captions from their POSTING.md / captions.txt and the posted state from the
"POSTED" prefix the user put on file names.
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from ..paths import data_root, ensure
from ..utils.cache import slugify
from . import db

if TYPE_CHECKING:  # pragma: no cover
    from ..campaign.manifest import ClipRecord
    from ..config import CampaignConfig
    from ..models import SourceInfo


def library_dir() -> Path:
    return data_root() / "library"


def clip_path(relative: str) -> Path:
    return library_dir() / relative


def register(records: list[ClipRecord], *, info: SourceInfo, campaign: CampaignConfig,
             captions: list[str] | None = None) -> list[int]:
    """Copy a run's accepted clips into the library and record them. Returns their ids."""
    from ..campaign.compliance import full_caption

    ids = []
    folder = ensure(library_dir() / campaign.name)
    with db.connect() as con:
        for i, record in enumerate(records):
            plan = record.plan
            slug = slugify(plan.hook_text or plan.text, max_length=40)
            name = f"{plan.clip_id}_{slug}_{info.source_id[:6]}.mp4"
            shutil.copy2(record.file, folder / name)
            ids.append(db.upsert_clip(con, {
                "campaign": campaign.name, "source_id": info.source_id,
                "clip_id": plan.clip_id, "source_title": info.title or "",
                "title": plan.hook_text or plan.text[:60], "file": f"{campaign.name}/{name}",
                "hook": plan.hook_text if plan.hook_shown else "",
                "caption": captions[i] if captions else full_caption(plan),
                "duration_s": record.duration, "start_s": plan.start, "end_s": plan.end,
            }))
    return ids


def thumbnail(video: Path, *, at: float = 1.5) -> Path | None:
    """A cached JPEG still of `video` (library/.thumbs/), remade when the video changes."""
    import subprocess

    from ..render.ffmpeg import ffmpeg_path

    folder = ensure(library_dir() / ".thumbs")
    stat = video.stat()
    still = folder / f"{slugify(str(video.relative_to(library_dir())), max_length=80)}" \
                     f"_{int(stat.st_mtime)}.jpg"
    if still.exists():
        return still
    try:
        subprocess.run([str(ffmpeg_path()), "-v", "error", "-y", "-ss", f"{at}", "-i", str(video),
                        "-frames:v", "1", "-vf", "scale=270:-2", "-q:v", "4", str(still)],
                       check=True, timeout=30, capture_output=True)
    except (OSError, subprocess.SubprocessError):
        return None
    return still if still.exists() else None


@dataclass
class Noted:
    """What a POSTING.md / captions.txt says about one file."""

    caption: str = ""
    hook: str = ""
    duration_s: float | None = None


_POSTED = re.compile(r"^POSTED[\s_-]*", re.IGNORECASE)


def base_name(filename: str) -> str:
    """A file's name without extension or the user's "POSTED" prefix, lower-cased."""
    return _POSTED.sub("", Path(filename).stem).strip().lower()


def read_notes(folder: Path) -> dict[str, Noted]:
    """Captions and hooks from a ready folder's POSTING.md or captions.txt, by base name."""
    notes: dict[str, Noted] = {}
    posting = folder / "POSTING.md"
    if posting.exists():
        text = posting.read_text(encoding="utf-8")
        for block in re.split(r"^## ", text, flags=re.MULTILINE)[1:]:
            head = re.match(r"(\S+?\.mp4)\s*(?:\((\d+(?:\.\d+)?)s\))?", block)
            if not head:
                continue
            hook = re.search(r"^Hook on screen:\s*(.+)$", block, re.MULTILINE)
            caption = re.search(r"```\s*\n(.*?)\n```", block, re.DOTALL)
            notes[base_name(head.group(1))] = Noted(
                caption=caption.group(1).strip() if caption else "",
                hook=hook.group(1).strip() if hook else "",
                duration_s=float(head.group(2)) if head.group(2) else None)
    captions = folder / "captions.txt"
    if captions.exists():
        text = captions.read_text(encoding="utf-8-sig")
        for block in re.split(r"\n\s*\n", text):
            head = re.match(r"\s*(.+?)\s+\((\d+(?:\.\d+)?)s\)\s*$", block.splitlines()[0]
                            if block.strip() else "")
            if not head:
                continue
            hook = re.search(r"On screen:\s*(.+)", block)
            caption = re.search(r"Caption:\s*(.+)", block)
            notes[base_name(head.group(1))] = Noted(
                caption=caption.group(1).strip() if caption else "",
                hook=hook.group(1).strip() if hook else "",
                duration_s=float(head.group(2)))
    return notes


def _key(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").lower()).strip()[:60]


def import_folder(folder: Path, campaign: str, log_rows: list[dict[str, str]]) -> list[int]:
    """Bring a pre-library "-ready" folder's clips into the library.

    Each file is matched to its performance-log row by file name, else by the
    caption its notes give it, which supplies the source and clip ids that
    link it to its posts. A file with no row keeps its own name as its id.
    """
    notes = read_notes(folder)
    target = ensure(library_dir() / campaign)
    rows = [r for r in log_rows if (r.get("platform") or "tiktok") != "instagram"
            and r.get("campaign") == campaign]
    ids = []
    with db.connect() as con:
        for path in sorted(folder.glob("*.mp4")):
            noted = notes.get(base_name(path.name), Noted())
            row = next((r for r in rows if r.get("file") == path.name), None)
            if row is None and noted.caption:
                row = next((r for r in rows if _key(r.get("caption", "")) == _key(noted.caption)),
                           None)
            name = _POSTED.sub("", path.name)
            shutil.copy2(path, target / name)
            posted = bool(_POSTED.match(path.name)) or bool(row and (row.get("url") or "").strip())
            ids.append(db.upsert_clip(con, {
                "campaign": campaign,
                "source_id": (row or {}).get("source_id") or f"import:{folder.name}",
                "clip_id": (row or {}).get("clip_id") or base_name(path.name),
                "source_title": (row or {}).get("source_title", ""),
                "title": (noted.hook or (row or {}).get("hook")
                          or first_sentence((row or {}).get("caption") or noted.caption)
                          or base_name(path.name)),
                "file": f"{campaign}/{name}",
                "hook": noted.hook or (row or {}).get("hook", ""),
                "caption": (row or {}).get("caption") or noted.caption,
                "duration_s": noted.duration_s or _float((row or {}).get("duration_s")),
                "status": "posted" if posted else "ready",
            }))
    return ids


def first_sentence(caption: str, limit: int = 60) -> str:
    """A caption's opening sentence, for a clip with no hook to be named by."""
    text = re.split(r"(?<=[.!?])\s|\s#|\s@", (caption or "").strip(), maxsplit=1)[0].strip()
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def _float(value: str | None) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except ValueError:
        return None
