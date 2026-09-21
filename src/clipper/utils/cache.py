"""Source identity and stage caching.

Every stage writes a JSON artifact into ``data/work/<source_id>/``. A stage is
skipped when its artifact already exists and nothing upstream has changed, so
re-running a finished source is near-instant (BUILD_BRIEF.md section 6).

`source_id` must be stable across runs and cheap on a 3 GB file, so it hashes
size plus the first and last megabyte rather than the whole thing. Two different
videos colliding on all three would be remarkable.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

CHUNK = 1024 * 1024
ID_LENGTH = 16


def source_id_for_file(path: Path | str) -> str:
    """Stable id for a local media file, from its size and edge bytes."""
    path = Path(path)
    size = path.stat().st_size
    h = hashlib.blake2b(digest_size=16)
    h.update(str(size).encode())

    with path.open("rb") as f:
        h.update(f.read(CHUNK))
        if size > 2 * CHUNK:
            f.seek(-CHUNK, 2)
            h.update(f.read(CHUNK))
    return h.hexdigest()[:ID_LENGTH]


def source_id_for_url(url: str) -> str:
    """Stable id for a URL, used before the file is downloaded."""
    return hashlib.blake2b(url.strip().encode(), digest_size=16).hexdigest()[:ID_LENGTH]


def content_hash(*parts: Any) -> str:
    """Hash arbitrary JSON-able parts. Used for LLM cache keys."""
    payload = json.dumps(parts, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.blake2b(payload.encode("utf-8"), digest_size=16).hexdigest()


def slugify(text: str, *, max_length: int = 48) -> str:
    """Filename-safe slug for clip names, from the clip's own words."""
    out: list[str] = []
    for ch in text.lower():
        if ch.isalnum():
            out.append(ch)
        elif out and out[-1] != "-":
            out.append("-")
    slug = "".join(out).strip("-")[:max_length].rstrip("-")
    return slug or "clip"


class StageCache:
    """Which pipeline stages may be skipped for one source.

    `forced` names stages the user invalidated with ``--force``. Forcing a stage
    also forces everything downstream of it, because a stale downstream artifact
    computed from different inputs is worse than no artifact at all.
    """

    ORDER: tuple[str, ...] = (
        "ingest", "transcribe", "segment", "candidates",
        "signals", "combine", "select", "render",
    )

    def __init__(self, work_dir: Path, forced: Iterable[str] = ()):
        self.work_dir = Path(work_dir)
        self.forced = self._expand(forced)

    @classmethod
    def _expand(cls, forced: Iterable[str]) -> set[str]:
        """A forced stage invalidates itself and every later stage."""
        requested = {s.strip().lower() for s in forced if s and s.strip()}
        unknown = requested - set(cls.ORDER) - {"all"}
        if unknown:
            raise ValueError(
                f"unknown stage(s) to force: {', '.join(sorted(unknown))}. "
                f"Valid stages: {', '.join(cls.ORDER)}, all"
            )
        if "all" in requested:
            return set(cls.ORDER)

        expanded: set[str] = set()
        for stage in requested:
            expanded.update(cls.ORDER[cls.ORDER.index(stage):])
        return expanded

    def path(self, artifact: str) -> Path:
        return self.work_dir / artifact

    def is_fresh(self, stage: str, artifact: str) -> bool:
        """True when `stage` can be skipped and `artifact` reused."""
        if stage in self.forced:
            return False
        path = self.path(artifact)
        return path.is_file() and path.stat().st_size > 0
