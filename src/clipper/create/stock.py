"""Stock footage from Pexels and Pixabay (both free for commercial use, no credit
required; D109 added Pexels, whose people footage is far better than Pixabay's).

Both searches are loose ("human ear close up" found a lipstick, "man yawning in
an airplane seat" a tiger) and thin on people doing specific things, so each beat
has three searches, most specific first; the model looks at the candidates from all
of them, both libraries, with the sentence, picks one and scores it; under 7 of 10
(or when no model can look) the beat gets a chalkboard card (create/diagrams.py) rather than unrelated footage.
Green-screen and transparent-background clips are left out. Long enough and unused
in the video first. A library without a key is skipped. Searches are cached for a
day and downloads for good (both ask API users to cache), under data/create/stock/.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import time
from pathlib import Path

import httpx
from PIL import Image
from pydantic import BaseModel

from ..paths import data_root, ensure
from ..utils.logging import get_logger
from .ai import CreateError, ask

log = get_logger(__name__)

API = "https://pixabay.com/api/videos/"
PEXELS = "https://api.pexels.com/videos/search"
SEARCH_HOURS = 24
#: Footage that looks cheap on a phone: an unkeyed green screen, a transparent background, a matte.
CHEAP = ("green screen", "greenscreen", "chroma", "blue screen", "alpha channel", "transparent",
         # D110: white-on-black silhouettes (a matte) closed a video once
         "silhouette", "matte", "luma")


def _dir() -> Path:
    return ensure(data_root() / "create" / "stock")


def _key() -> str:
    return os.environ.get("PIXABAY_API_KEY", "").strip()


def _pexels_key() -> str:
    return os.environ.get("PEXELS_API_KEY", "").strip()


def _rendition(files: list[dict]) -> dict | None:
    """The smallest rendition at least 1080 px on its short side; else the biggest."""
    files = [f for f in files if f.get("url") and f.get("width") and f.get("height")]
    if not files:
        return None
    good = [f for f in files if min(f["width"], f["height"]) >= 1080]
    return min(good, key=lambda f: f.get("size") or f["width"] * f["height"]) if good else \
        max(files, key=lambda f: f["width"])


def search(query: str) -> list[dict]:
    """Both libraries' videos for `query`, alternating, each most relevant first: Pexels
    leads, as its people shots were the better ones."""
    if not _key() and not _pexels_key():
        raise CreateError("no stock footage key: add PEXELS_API_KEY or PIXABAY_API_KEY to Clipper's .env file")
    lists, errors = [], []
    for source, key in ((pexels, _pexels_key()), (pixabay, _key())):
        if not key:
            continue
        try:
            lists.append(source(query))
        except CreateError as exc:  # one library down: the other still answers
            errors.append(str(exc))
    if errors and not lists:
        raise CreateError("; ".join(errors))
    merged = []
    for k in range(max((len(x) for x in lists), default=0)):
        merged += [x[k] for x in lists if k < len(x)]
    return merged


def _cached(source: str, query: str) -> tuple[str, Path, list[dict] | None]:
    q = " ".join(query.lower().split())[:100]
    name = hashlib.sha1(q.encode()).hexdigest()[:16]
    cached = ensure(_dir() / "search") / (f"{source}-{name}.json" if source != "pixabay" else f"{name}.json")
    if cached.is_file() and time.time() - cached.stat().st_mtime < SEARCH_HOURS * 3600:
        return q, cached, json.loads(cached.read_text(encoding="utf-8"))
    return q, cached, None


def pexels(query: str) -> list[dict]:
    """Pexels' videos for `query` (cached a day). Its ids are kept apart from Pixabay's."""
    q, cached, hits = _cached("pexels", query)
    if hits is not None:
        return hits
    try:
        r = httpx.get(PEXELS, params={"query": q, "per_page": 15, "size": "medium"},
                      headers={"Authorization": _pexels_key()}, timeout=20)
        r.raise_for_status()
    except httpx.HTTPError as exc:
        raise CreateError(f"Pexels didn't answer: {exc}") from exc
    hits = []
    for v in r.json().get("videos", []):
        files = [{"url": f.get("link", ""), "width": f.get("width"), "height": f.get("height"),
                  "size": (f.get("width") or 0) * (f.get("height") or 0)}
                 for f in v.get("video_files", []) if f.get("file_type") == "video/mp4"]
        best = _rendition(files)
        # Pexels has no tags; its page address names what the clip shows.
        slug = (v.get("url") or "").rstrip("/").rsplit("/", 1)[-1]
        tags = " ".join(w for w in slug.split("-") if not w.isdigit())
        if best is None or any(c in tags.lower() for c in CHEAP):
            continue
        hits.append({"id": f"pexels-{v['id']}", "duration": v.get("duration", 0), "tags": tags,
                     "url": best["url"], "width": best["width"], "height": best["height"],
                     "thumb": v.get("image", "")})
    cached.write_text(json.dumps(hits), encoding="utf-8")
    return hits


def pixabay(query: str) -> list[dict]:
    """Pixabay's videos for `query`, most relevant first (cached a day)."""
    q, cached, hits = _cached("pixabay", query)
    if hits is not None:
        return hits
    try:
        r = httpx.get(API, params={"key": _key(), "q": q, "per_page": 20, "safesearch": "true"}, timeout=20)
        r.raise_for_status()
    except httpx.HTTPError as exc:
        raise CreateError(f"Pixabay didn't answer: {exc}") from exc
    hits = []
    for h in r.json().get("hits", []):
        v = _rendition(list(h.get("videos", {}).values()))
        if v is None or h.get("isLowQuality") or any(c in h.get("tags", "").lower() for c in CHEAP):
            continue
        thumb = (h["videos"].get("tiny") or {}).get("thumbnail") or v.get("thumbnail", "")
        hits.append({"id": h["id"], "duration": h.get("duration", 0), "tags": h.get("tags", ""),
                     "url": v["url"], "width": v["width"], "height": v["height"], "thumb": thumb})
    cached.write_text(json.dumps(hits), encoding="utf-8")
    return hits


CANDIDATES = 12
PER_QUERY = 6

PICK = """You choose stock footage for one sentence of a short educational video, shown on a
phone. You see numbered thumbnails of candidate clips, each with what its library says it
shows. Find the one a viewer would instantly connect with what the sentence says: the thing
itself, or a person plainly experiencing it, clear and close enough to read on a phone. A
close, everyday match counts (headphones for hearing, a microphone for recording).
Then score how well that best one fits, honestly:
  9-10 it shows exactly what the sentence says;
  7-8  a clear, natural match a viewer gets at once;
  4-6  related, but loose, generic or needs explaining (a lab for "your voice");
  0-3  unrelated, confusing, or cheap-looking (cartoonish, a green background, text or a
       logo burned in, a stock-footage cliche).
Below 7 a chalkboard card is shown instead, which is better than a loose match, so don't
round up. Answer pick = its number (0 if none) and score."""

#: The judge's score a clip needs to be used; below it the beat gets a chalk card (D110).
#: Picks were judged "very poor" when any non-zero pick was taken.
GOOD_ENOUGH = 7


class _Pick(BaseModel):
    pick: int
    score: int = 0


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
        try:  # small: Pexels sends full-size frames, and the judge needs only a glance
            img = Image.open(io.BytesIO(r.content)).convert("RGB")
            img.thumbnail((360, 360))
            img.save(cached, "JPEG", quality=82)
        except OSError:
            return None
    return cached.read_bytes()


class _NoAnswer(Exception):
    """The model couldn't look (busy, offline): not the same as "none of these fit"."""


def _judge(sentence: str, query: str, hits: list[dict]) -> dict | None:
    """The candidate the model says shows the sentence, or None if none does."""
    shown = [(h, t) for h in hits if (t := _thumb(h))]
    if not shown:
        raise _NoAnswer
    try:
        listed = "\n".join(f"{i}. {'tall' if h['height'] > h['width'] else 'wide'}, {h.get('tags') or 'no tags'}"
                           for i, (h, _) in enumerate(shown, start=1))
        prompt = (f"Sentence: {sentence}\nSearched for: {query}\n"
                  f"Thumbnails 1 to {len(shown)}, in order:\n{listed}")
        answer = ask(PICK, prompt, _Pick, temperature=0.0, media=[(t, "image/jpeg") for _, t in shown])
        verdict = _Pick.model_validate(json.loads(answer))
    except (CreateError, ValueError, TypeError) as exc:
        raise _NoAnswer from exc
    n = verdict.pick
    if not 1 <= n <= len(shown) or verdict.score < GOOD_ENOUGH:
        log.info("create: no footage good enough for %r (best %s scored %s)", sentence[:60], n, verdict.score)
        return None
    return shown[n - 1][0]


def choose(queries: list[str], seconds: float, used: set, sentence: str = "") -> dict | None:
    """The best unused clip for a beat of `seconds`, judged against the sentence across all
    its searches at once; None when nothing fits (the beat gets a chalkboard card instead,
    never a generic stand-in: "science laboratory" footage opened a video once)."""
    pool: list[dict] = []
    for q in queries:
        for h in search(q)[:PER_QUERY]:
            if h["id"] not in used and all(h["id"] != p["id"] for p in pool):
                pool.append(h)
    if not pool:
        return None
    # Long enough first, then vertical, keeping the search order within each.
    ranked = sorted(pool, key=lambda h: (h["duration"] < seconds + 0.3, h["height"] <= h["width"]))[:CANDIDATES]
    if not sentence:
        return ranked[0]
    try:
        return _judge(sentence, " / ".join(queries), ranked)
    except _NoAnswer:
        return None  # no one to judge: a chalk card, as the search alone picked lipsticks for ears


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
