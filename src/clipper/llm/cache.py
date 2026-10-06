"""Disk cache for LLM responses, keyed by content.

The key is a hash of (backend, model, prompt version, candidate text), per
docs/BUILD_BRIEF.md section 6. Crucially it is *not* keyed by source id or candidate
id, so the same moment re-scored after re-transcription, or an identical clip in
a different video, is a cache hit.

Keying on the prompt version means editing `prompts.py` invalidates the cache
automatically -- the alternative, silently reusing scores from a prompt that no
longer exists, would make eval results meaningless.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

from ..paths import cache_dir, ensure
from ..utils.cache import content_hash
from ..utils.logging import get_logger

log = get_logger(__name__)

CACHE_FORMAT = 1
"""Bump if the stored envelope changes shape."""


@dataclass
class CacheEntry:
    text: str
    model: str
    created_at: float
    prompt_tokens: int = 0
    output_tokens: int = 0


class LLMCache:
    """One JSON file per cached response, under ``data/cache/llm/``.

    One file per entry rather than a single index, because a crashed run then
    costs at most one entry instead of the whole cache.
    """

    def __init__(self, root: Path | None = None, *, enabled: bool = True):
        self.root = root or (cache_dir() / "llm")
        self.enabled = enabled
        self.hits = 0
        self.misses = 0

    def key(self, *, backend: str, model: str, prompt_key: str, payload: str) -> str:
        return content_hash(CACHE_FORMAT, backend, model, prompt_key, payload)

    def _path(self, key: str) -> Path:
        # Shard by the first two characters: a long-running install can
        # accumulate thousands of entries, and Windows directory listings get
        # slow well before a filesystem limit is reached.
        return self.root / key[:2] / f"{key}.json"

    def get(self, key: str) -> CacheEntry | None:
        if not self.enabled:
            return None
        path = self._path(key)
        if not path.is_file():
            self.misses += 1
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            entry = CacheEntry(
                text=data["text"],
                model=data.get("model", ""),
                created_at=float(data.get("created_at", 0.0)),
                prompt_tokens=int(data.get("prompt_tokens", 0)),
                output_tokens=int(data.get("output_tokens", 0)),
            )
        except (json.JSONDecodeError, KeyError, OSError, TypeError, ValueError):
            log.debug("discarding unreadable cache entry %s", path.name)
            self.misses += 1
            return None
        self.hits += 1
        return entry

    def forget(self, key: str) -> None:
        """Drop one entry, e.g. an answer that turned out unusable."""
        self._path(key).unlink(missing_ok=True)

    def put(self, key: str, *, text: str, model: str,
            prompt_tokens: int = 0, output_tokens: int = 0) -> None:
        if not self.enabled:
            return
        path = self._path(key)
        ensure(path.parent)
        payload = {
            "format": CACHE_FORMAT,
            "text": text,
            "model": model,
            "created_at": time.time(),
            "prompt_tokens": prompt_tokens,
            "output_tokens": output_tokens,
        }
        tmp = path.with_suffix(".json.tmp")
        try:
            tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            tmp.replace(path)
        except OSError as exc:  # pragma: no cover - disk full / permissions
            log.warning("could not write LLM cache entry: %s", exc)

    def clear(self) -> int:
        """Delete every entry. Returns how many were removed."""
        if not self.root.is_dir():
            return 0
        removed = 0
        for path in self.root.rglob("*.json"):
            try:
                path.unlink()
                removed += 1
            except OSError:  # pragma: no cover
                continue
        return removed

    def stats(self) -> str:
        total = self.hits + self.misses
        rate = self.hits / total if total else 0.0
        return f"{self.hits} hit / {self.misses} miss ({rate:.0%})"
