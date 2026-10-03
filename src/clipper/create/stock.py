"""Stock footage from Pixabay (free for commercial use, no credit required).

Pixabay's search is loose ("human ear close up" found a lipstick, "man yawning in
an airplane seat" a tiger), so the model looks at the candidates' thumbnails with
the sentence and picks the one that shows it, or none; a query with nothing fitting
is retried with fewer words. Among fits, long enough and unused in the video.
Searches are cached for a day and downloads for good (Pixabay asks API users to
cache), under data/create/stock/.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path

import httpx
from pydantic import BaseModel

from ..paths import data_root, ensure
from .ai import CreateError, ask

API = "https://pixabay.com/api/videos/"
SEARCH_HOURS = 24
FALLBACK = "science laboratory"


def _dir() -> Path:
    return ensure(data_root() / "create" / "stock")


def _key() -> str:
    key = os.environ.get("PIXABAY_API_KEY", "").strip()
    if not key:
        raise CreateError("no Pixabay key: add PIXABAY_API_KEY to Clipper's .env file")
    return key


def search(query: str) -> list[dict]:
    """Pixabay's videos for `query`, most relevant first (cached a day)."""
    q = " ".join(query.lower().split())[:100]
    cached = ensure(_dir() / "search") / f"{hashlib.sha1(q.encode()).hexdigest()[:16]}.json"
    if cached.is_file() and time.time() - cached.stat().st_mtime < SEARCH_HOURS * 3600:
        return json.loads(cached.read_text(encoding="utf-8"))
    try:
        r = httpx.get(API, params={"key": _key(), "q": q, "per_page": 20, "safesearch": "true"}, timeout=20)
        r.raise_for_status()
    except httpx.HTTPError as exc:
        raise CreateError(f"Pixabay didn't answer: {exc}") from exc
    hits = []
    for h in r.json().get("hits", []):
        # The smallest rendition at least 1080 px on its short side; else the biggest.
        renditions = [v for v in h.get("videos", {}).values() if v.get("url")]
        if not renditions:
            continue
        good = [v for v in renditions if min(v["width"], v["height"]) >= 1080]
        v = min(good, key=lambda v: v.get("size", 0)) if good else max(renditions, key=lambda v: v["width"])
        if h.get("isLowQuality"):
            continue
        thumb = (h["videos"].get("tiny") or {}).get("thumbnail") or v.get("thumbnail", "")
        hits.append({"id": h["id"], "duration": h.get("duration", 0), "tags": h.get("tags", ""),
                     "url": v["url"], "width": v["width"], "height": v["height"], "thumb": thumb})
    cached.write_text(json.dumps(hits), encoding="utf-8")
    return hits


CANDIDATES = 6

PICK = """You choose stock footage for one sentence of a short educational video. You see
numbered thumbnails of candidate clips. Pick the one that most literally shows what the
sentence is about, as a viewer would read it in a second. Reject anything unrelated,
misleading or merely decorative. Answer pick = its number, or 0 if none fits."""


class _Pick(BaseModel):
    pick: int


def _thumb(hit: dict) -> bytes | None:
    if not hit.get("thumb"):
        return None
    cached = ensure(_dir() / "thumbs") / f"{hit['id']}.jpg"
    if not cached.is_file():
        try:
            r = httpx.get(hit["thumb"], timeout=20)
            r.raise_for_status()
        except httpx.HTTPError:
            return None
        cached.write_bytes(r.content)
    return cached.read_bytes()


class _NoAnswer(Exception):
    """The model couldn't look (busy, offline): not the same as "none of these fit"."""


def _judge(sentence: str, query: str, hits: list[dict]) -> dict | None:
    """The candidate the model says shows the sentence, or None if none does."""
    shown = [(h, t) for h in hits if (t := _thumb(h))]
    if not shown:
        raise _NoAnswer
    try:
        prompt = f"Sentence: {sentence}\nSearched for: {query}\nThumbnails 1 to {len(shown)}, in order."
        answer = ask(PICK, prompt, _Pick, temperature=0.0, media=[(t, "image/jpeg") for _, t in shown])
        n = _Pick.model_validate(json.loads(answer)).pick
    except (CreateError, ValueError, TypeError) as exc:
        raise _NoAnswer from exc
    return shown[n - 1][0] if 1 <= n <= len(shown) else None


def choose(query: str, seconds: float, used: set[int], sentence: str = "") -> dict:
    """The best unused clip for a beat of `seconds`: one that shows the sentence."""
    words = query.split()
    # The whole query, then without its first word (never down to one vague word), then a fallback.
    tries = [" ".join(words[i:]) for i in range(max(1, len(words) - 1))] + [FALLBACK]
    for q in tries:
        hits = [h for h in search(q) if h["id"] not in used]
        if not hits:
            continue
        # Long enough first, then vertical, keeping Pixabay's relevance order within each.
        ranked = sorted(hits[: CANDIDATES * 2], key=lambda h: (h["duration"] < seconds + 0.3,
                                                                 h["height"] <= h["width"]))[:CANDIDATES]
        if q == FALLBACK or not sentence:
            return ranked[0]
        try:
            picked = _judge(sentence, q, ranked)
        except _NoAnswer:
            return ranked[0]  # no one to ask: Pixabay's own best match for the fullest query
        if picked:
            return picked
    raise CreateError(f"no stock footage found for {query!r}")


def fetch(hit: dict) -> Path:
    """The clip on disk, downloaded once."""
    path = _dir() / f"{hit['id']}_{hit['width']}x{hit['height']}.mp4"
    if path.is_file() and path.stat().st_size > 0:
        return path
    part = path.with_suffix(".part")
    try:
        with httpx.stream("GET", hit["url"], timeout=60, follow_redirects=True) as r:
            r.raise_for_status()
            with part.open("wb") as f:
                for chunk in r.iter_bytes(1 << 16):
                    f.write(chunk)
    except httpx.HTTPError as exc:
        part.unlink(missing_ok=True)
        raise CreateError(f"couldn't download stock clip {hit['id']}: {exc}") from exc
    part.replace(path)
    return path
