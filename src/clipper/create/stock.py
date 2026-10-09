"""Stock footage from Pexels and Pixabay (both free for commercial use, no credit
required; D109 added Pexels, whose people footage is far better than Pixabay's).

Both searches are loose ("human ear close up" found a lipstick, "man yawning in
an airplane seat" a tiger) and thin on people doing specific things, so each beat
has three searches, most specific first; the model looks at the candidates from all
of them, both libraries, with the sentence, picks one and scores it; under 7 of 10 the
beat gets a chalkboard card (create/diagrams.py) rather than unrelated footage. When no
model can look (the plan's limit, offline), the clip whose library description holds the
search's words is used instead, and the notes say so (D122): turning every footage
sentence into chalk made a video all diagrams.
Green-screen and transparent-background clips are left out. Long enough and unused
in the video first. A library without a key is skipped. Searches are cached for a
day and downloads for good (both ask API users to cache), under data/create/stock/.

D162: a few photos from the same two libraries (same keys, same licence) are offered with the
videos, shown with a slow camera move: a still of exactly the thing beats a loose video of
something near it, and the libraries have far more photos than clips.
"""

from __future__ import annotations

import functools
import hashlib
import io
import json
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
from PIL import Image
from pydantic import BaseModel

from ..paths import data_root, ensure
from ..utils.logging import get_logger
from . import channel as channels
from .ai import CreateError, ask

log = get_logger(__name__)

API = "https://pixabay.com/api/videos/"
PEXELS = "https://api.pexels.com/videos/search"
COVERR = "https://api.coverr.co/videos"
NASA = "https://images-api.nasa.gov/search"
PEXELS_PHOTOS = "https://api.pexels.com/v1/search"
PIXABAY_PHOTOS = "https://pixabay.com/api/"
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


def _coverr_key() -> str:
    return os.environ.get("COVERR_API_KEY", "").strip()


_SPACE = re.compile(r"\b(space|rocket|launch|orbit\w*|astronaut|satellite|moon|lunar|mars|planet\w*|sun|solar|"
                    r"star|stars|galaxy|nebula|comet|asteroid|eclipse|aurora|earth from|iss|nasa|telescope|"
                    r"zero gravity|weightless\w*|shuttle|apollo|black hole|cosmic|universe)\b", re.I)


def search(query: str) -> list[dict]:
    """Every library's videos for `query`, taking turns, each most relevant first: Pexels leads,
    as its people shots were the better ones; NASA's (no key needed: space, rockets, Earth, light)
    comes last, as it fits only some sentences (D130)."""
    lists, errors = [], []
    # NASA costs about 5 requests a search, so only for searches that sound like space (D133).
    space = "free" if _SPACE.search(query) else ""
    for source, key in ((pexels, _pexels_key()), (pixabay, _key()), (coverr, _coverr_key()), (nasa, space)):
        if not key:
            continue
        try:
            lists.append(source(query))
        except CreateError as exc:  # one library down: the others still answer
            errors.append(str(exc))
    if errors and not lists:  # only when every library failed; "no results" isn't a failure
        raise CreateError("; ".join(errors))
    merged = []
    for k in range(max((len(x) for x in lists), default=0)):
        merged += [x[k] for x in lists if k < len(x)]
    return merged


def is_photo(hit: dict) -> bool:
    """A still photo, not a video clip (D162): its id says so, as the picks saved in a script keep only the id."""
    return str(hit.get("id", "")).startswith("photo-")


def photos(query: str) -> list[dict]:
    """Both libraries' photos for `query`, taking turns, most relevant first; [] when neither answers
    (photos are extra: footage is chosen without them)."""
    lists = []
    for source, key in ((pexels_photos, _pexels_key()), (pixabay_photos, _key())):
        if key:
            try:
                lists.append(source(query))
            except CreateError as exc:
                log.info("create: no photos (%s)", exc)
    return [x[k] for k in range(max((len(x) for x in lists), default=0)) for x in lists if k < len(x)]


def _save(path: Path, data: bytes) -> None:
    """Written whole or not at all: footage is looked up on several threads at once (D136), and one
    reading a file another is still writing would find half of it."""
    tmp = path.with_name(f"{path.name}.{threading.get_ident()}.tmp")
    tmp.write_bytes(data)
    tmp.replace(path)


def _cached(source: str):
    """A library's search, remembered a day on disk by its normalised query."""
    def wrap(fetch):
        @functools.wraps(fetch)
        def cached_fetch(query: str) -> list[dict]:
            q = " ".join(query.lower().split())[:100]
            name = hashlib.sha1(q.encode()).hexdigest()[:16]
            path = ensure(_dir() / "search") / f"{source}-v2-{name}.json"   # v2: with previews (D130)
            if path.is_file() and time.time() - path.stat().st_mtime < SEARCH_HOURS * 3600:
                return json.loads(path.read_text(encoding="utf-8"))
            hits = fetch(q)
            _save(path, json.dumps(hits).encode("utf-8"))
            return hits
        return cached_fetch
    return wrap


@_cached("pexels")
def pexels(q: str) -> list[dict]:
    """Pexels' videos for `query` (cached a day). Its ids are kept apart from Pixabay's."""
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
        small = min((f for f in files if min(f["width"] or 0, f["height"] or 0) >= 360), default=best,
                    key=lambda f: f["width"] * f["height"])
        hits.append({"id": f"pexels-{v['id']}", "duration": v.get("duration", 0), "tags": tags,
                     "url": best["url"], "width": best["width"], "height": best["height"],
                     "thumb": v.get("image", ""), "preview": small["url"]})
    return hits


@_cached("pixabay")
def pixabay(q: str) -> list[dict]:
    """Pixabay's videos for `query`, most relevant first (cached a day)."""
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
        small = (h["videos"].get("tiny") or h["videos"].get("small") or {}).get("url") or v["url"]
        hits.append({"id": h["id"], "duration": h.get("duration", 0), "tags": h.get("tags", ""),
                     "url": v["url"], "width": v["width"], "height": v["height"], "thumb": thumb,
                     "preview": small})
    return hits


@_cached("coverr")
def coverr(q: str) -> list[dict]:
    """Coverr's videos for `query` (cached a day): free, no credit needed, a free key from
    coverr.co/developers in COVERR_API_KEY (D130)."""
    try:
        r = httpx.get(COVERR, params={"query": q, "page_size": 12, "urls": "true"},
                      headers={"Authorization": f"Bearer {_coverr_key()}"}, timeout=20)
        r.raise_for_status()
        data = r.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise CreateError(f"Coverr didn't answer: {exc}") from exc
    found = (data.get("hits") or data.get("videos") or data.get("data") or []) if isinstance(data, dict) else data
    hits = []
    for v in found if isinstance(found, list) else []:
        url = (v.get("urls") or {}).get("mp4") or (v.get("urls") or {}).get("mp4_download")
        tags = " ".join([v.get("title") or "", *(t if isinstance(t, str) else t.get("name", "") for t in v.get("tags") or [])])
        if not url or v.get("is_ai_generated") or any(c in tags.lower() for c in CHEAP):  # D133: no AI footage
            continue
        preview = (v.get("urls") or {}).get("mp4_preview") or url
        hits.append({"id": f"coverr-{v.get('id')}", "duration": float(v.get("duration") or 0), "tags": tags.strip(),
                     "url": url, "width": int(v.get("max_width") or 1920), "height": int(v.get("max_height") or 1080),
                     "thumb": v.get("thumbnail") or v.get("poster") or "", "preview": preview})
    return hits


@_cached("pexels-photo")
def pexels_photos(q: str) -> list[dict]:
    """Pexels' photos for `query` (cached a day), asked for 2000 px wide: the original can be 6000."""
    try:
        r = httpx.get(PEXELS_PHOTOS, params={"query": q, "per_page": 8}, headers={"Authorization": _pexels_key()},
                      timeout=20)
        r.raise_for_status()
    except httpx.HTTPError as exc:
        raise CreateError(f"Pexels didn't answer: {exc}") from exc
    hits = []
    for p in r.json().get("photos", []):
        src, w, h = p.get("src") or {}, p.get("width") or 0, p.get("height") or 0
        if not src.get("original") or not w or not h:
            continue
        k = min(1.0, 2000 / w)
        hits.append({"id": f"photo-pexels-{p['id']}", "duration": 0, "tags": p.get("alt") or "",
                     "url": f"{src['original']}?auto=compress&cs=tinysrgb&w=2000", "width": round(w * k),
                     "height": round(h * k), "thumb": src.get("medium") or "", "preview": ""})
    return hits


@_cached("pixabay-photo")
def pixabay_photos(q: str) -> list[dict]:
    """Pixabay's photos for `query` (cached a day), its large size (1280 px on the long side)."""
    try:
        r = httpx.get(PIXABAY_PHOTOS, params={"key": _key(), "q": q, "image_type": "photo", "per_page": 8,
                                              "safesearch": "true"}, timeout=20)
        r.raise_for_status()
    except httpx.HTTPError as exc:
        raise CreateError(f"Pixabay didn't answer: {exc}") from exc
    hits = []
    for p in r.json().get("hits", []):
        w, h = p.get("imageWidth") or 0, p.get("imageHeight") or 0
        if not p.get("largeImageURL") or not w or not h or any(c in p.get("tags", "").lower() for c in CHEAP):
            continue
        k = min(1.0, 1280 / max(w, h))
        hits.append({"id": f"photo-pixabay-{p['id']}", "duration": 0, "tags": p.get("tags", ""),
                     "url": p["largeImageURL"], "width": round(w * k), "height": round(h * k),
                     "thumb": p.get("webformatURL") or "", "preview": ""})
    return hits


def _rendition_named(files: list[str], *names: str) -> str | None:
    """The first of NASA's files named "~<name>." for the names in order of preference."""
    return next((f for n in names for f in files if f"~{n}." in f), None)


@_cached("nasa")
def nasa(q: str) -> list[dict]:
    """NASA's videos for `query` (cached a day): public, no key; NASA's own footage is not under
    copyright (its logos must not suggest it endorses the video). Each video's files are listed in a
    manifest, read for the first few results only (D130)."""
    try:
        r = httpx.get(NASA, params={"q": q, "media_type": "video"}, timeout=20)
        r.raise_for_status()
        items = r.json().get("collection", {}).get("items", [])[:4]
    except (httpx.HTTPError, ValueError) as exc:
        raise CreateError(f"NASA's library didn't answer: {exc}") from exc
    def manifest(it: dict) -> list:
        try:
            return httpx.get(it["href"], timeout=20).json()
        except (httpx.HTTPError, ValueError, KeyError):
            return []

    with ThreadPoolExecutor(max_workers=4) as pool:  # the file lists at once, not one after another
        manifests = list(pool.map(manifest, items))
    hits = []
    for it, files in zip(items, manifests, strict=True):
        meta = (it.get("data") or [{}])[0]
        mp4 = [f for f in files if isinstance(f, str) and f.lower().endswith(".mp4")]
        # "large" first: "orig" was 106 MB against large's 21 MB for one launch, and a 9:16 crop of
        # either is upscaled anyway (D132).
        url, small = _rendition_named(mp4, "large", "orig", "medium"), _rendition_named(mp4, "mobile", "small", "medium")
        tags = " ".join([meta.get("title", ""), *meta.get("keywords", [])[:8]])
        if not url:
            continue
        hits.append({"id": f"nasa-{re.sub(r'[^A-Za-z0-9_-]', '', meta.get('nasa_id', ''))[:50]}", "duration": 30,
                     "tags": tags.strip(), "url": url.replace("http://", "https://"), "width": 1920, "height": 1080,
                     "thumb": next((x.get("href", "") for x in it.get("links", []) if x.get("render") == "image"), ""),
                     "preview": (small or url).replace("http://", "https://")})
    return hits


CANDIDATES = 12
PER_QUERY = 8
#: Photos among a sentence's candidates (D162): after the videos, a few, as a video that fits is better.
PHOTOS = 3

#: One judge for the build and for the picker (D181): it scores every candidate, so a sentence long enough
#: for two clips takes its second from the same scores (it was a call a part), and the picker shows them.
JUDGE = """You score stock footage for one sentence of a short educational video, shown on a phone in a
square-ish panel above the captions. You see numbered thumbnails of candidates, each a video clip or a
photo (a photo is shown with a slow camera move), with what its library says it shows, and the whole
script, so you know what the sentence means in it (a "wall" in a video about sound is a wall music comes
through, not a climbing wall).
Score EVERY candidate for how instantly a viewer would connect it with what the sentence says: the thing
itself, or a person plainly experiencing it, big, clear and close enough to read on a phone. A close,
everyday match counts (headphones for hearing, a microphone for recording); so does real motion that
matches the sentence (water pouring for "pours").
  9-10 exactly what the sentence says;
  7-8  a clear, natural match a viewer gets at once;
  4-6  related, but loose, generic or needs explaining (a lab for "your voice"); or the right thing but
       tiny, far away or half hidden;
  0-3  unrelated, confusing, or cheap-looking (cartoonish, a glossy 3D render or CGI, neon audio-visualiser
       rings, a green background, text, a watermark or a logo burned in, mostly black or too dark to read on
       a phone, a posed stock-photo smile at the camera).
Below 7 a chalkboard drawing is shown instead, which is better than a loose match, so don't round up. Of
two that fit as well, score the video higher than the photo; a photo of exactly the thing beats a video of
something near it.
For each candidate also give center: where across its thumbnail the subject is, 0 = left edge, 0.5 =
middle, 1 = right edge (a wide picture is cropped to the panel around it, so put it on the thing the
sentence is about).
If nothing scores 7 or more, give better: up to 3 new searches, 2 to 4 plain words each, for what stock
libraries really film (objects, places, nature, people in ordinary situations: "man listening to
headphones", "subwoofer speaker vibrating") that would show this sentence. Mix the literal thing with a
person experiencing it; never abstract words or actions nobody films, and none already searched."""

#: The judge's score a clip needs to be used; below it the beat gets a chalk card (D110).
#: Picks were judged "very poor" when any non-zero pick was taken.
GOOD_ENOUGH = 7


class _Ranks(BaseModel):
    scores: list[int] = []
    centers: list[float] = []
    better: list[str] = []      # when nothing fits: searches more likely to find it (D124)


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
            small = io.BytesIO()
            img.save(small, "JPEG", quality=82)
            _save(cached, small.getvalue())
        except OSError:
            return None
    return cached.read_bytes()


def _listing(shown: list[tuple[dict, bytes]]) -> str:
    """The thumbnails' numbers, shape and library tags, as the judge is told them."""
    listed = "\n".join(f"{i}. {'photo' if is_photo(h) else 'video'}, {'tall' if h['height'] > h['width'] else 'wide'}, "
                       f"{h.get('tags') or 'no tags'}" for i, (h, _) in enumerate(shown, start=1))
    return f"Thumbnails 1 to {len(shown)}, in order:\n{listed}"


class _NoAnswer(Exception):
    """The model couldn't look (busy, offline): not the same as "none of these fit"."""


def _scored(sentence: str, hits: list[dict], context: str = "", query: str = "") -> tuple[list[dict], list[str]]:
    """Every candidate with a thumbnail, with the judge's score (0-10, None if it gave none) and where its
    subject sits across the frame, best first (the libraries' order breaks ties), and the better searches it
    suggests when none is good enough. Raises _NoAnswer when no model could look."""
    shown = [(h, t) for h in hits if (t := _thumb(h))]
    if not shown:
        raise _NoAnswer
    try:
        prompt = ((f"The whole script, for context:\n{context}\n\n" if context else "") + f"Sentence: {sentence}\n"
                  + (f"Searched for: {query}\n" if query else "") + _listing(shown))
        answer = ask(JUDGE, prompt, _Ranks, temperature=0.0, media=[(t, "image/jpeg") for _, t in shown], quick=True,
                     job="footage", keep=True)
        got = _Ranks.model_validate(json.loads(answer))
    except (CreateError, ValueError, TypeError) as exc:
        raise _NoAnswer from exc
    out = []
    for k, (h, _) in enumerate(shown):
        score = got.scores[k] if k < len(got.scores) else None
        center = got.centers[k] if k < len(got.centers) else 0.5
        out.append({**h, "score": None if score is None else max(0, min(10, int(score))),
                    "center": min(1.0, max(0.0, float(center)))})
    out.sort(key=lambda h: -(h["score"] if h["score"] is not None else -1))
    return out, [" ".join(str(q).split()[:5]) for q in got.better if str(q).strip()][:3]


def _judge(sentence: str, query: str, hits: list[dict], context: str = "",
           good_enough: int = GOOD_ENOUGH) -> tuple[list[dict], list[str]]:
    """The candidates good enough for the sentence, best first ([] if none is), and the better searches the
    judge suggests when none is."""
    scored, better = _scored(sentence, hits, context, query)
    good = [h for h in scored if h["score"] is not None and h["score"] >= good_enough]
    if not good:
        log.info("create: no footage good enough for %r (best scored %s)", sentence[:60], scored[0]["score"])
        return [], better
    return good, []


#: Sentences whose footage was picked by its search words alone, as no model could look (D122).
unjudged: list[str] = []
_STOP = {"the", "and", "with", "for", "from", "into", "onto", "of", "a", "an", "in", "on", "at", "to", "his",
         "her", "their", "your", "close", "closeup", "up", "shot", "footage", "video", "view"}


def _words(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-z]+", text.lower()) if len(w) >= 3 and w not in _STOP]


def _same(a: str, b: str) -> bool:
    """One word or a form of it ("record", "recording")."""
    return a == b or (min(len(a), len(b)) >= 5 and a[:5] == b[:5])


def by_words(queries: list[str], ranked: list[dict]) -> dict | None:
    """With no model to look: the first candidate (in the libraries' own order of relevance)
    whose description has every word of a search, or all but one of a long one, the most specific
    search first. None if nothing matches that well: a chalk card beats a lipstick for an ear."""
    for query in queries:
        wanted = _words(query)
        if not wanted:
            continue
        need = len(wanted) if len(wanted) <= 2 else len(wanted) - 1
        for hit in ranked:
            have = _words(hit.get("tags", ""))
            if sum(1 for w in wanted if any(_same(w, h) for h in have)) >= need:
                return {**hit, "center": None}
    return None


def _gather(queries: list[str], exclude: set) -> list[dict]:
    """Every search's best results, each once, in the libraries' own order, minus `exclude`: the videos,
    and each search's first two photos (D162)."""
    pool: list[dict] = []
    for q in queries:
        for h in [*search(q)[:PER_QUERY], *photos(q)[:2]]:
            if h["id"] not in exclude and all(h["id"] != p["id"] for p in pool):
                pool.append({**h, "query": q})  # preview: a small file to play on hover (D130)
    return pool


def _shortlist(pool: list[dict], seconds: float, limit: int) -> list[dict]:
    """The videos long enough first, then the vertical ones, keeping the search order within each; then up
    to PHOTOS photos, in the search order (a photo is any length)."""
    stills = [h for h in pool if is_photo(h)][:PHOTOS]
    clips = sorted((h for h in pool if not is_photo(h)), key=lambda h: (h["duration"] < seconds + 0.3,
                                                                       h["height"] <= h["width"]))
    return clips[:limit - len(stills)] + stills


def _pool(queries: list[str], used: set, seconds: float) -> list[dict]:
    return _shortlist(_gather(queries, used), seconds, CANDIDATES)


def choose(queries: list[str], seconds: float, used: set, sentence: str = "", context: str = "",
           good_enough: int = GOOD_ENOUGH, count: int = 1) -> list[dict]:
    """Up to `count` different unused clips for the parts of a beat, each `seconds` long, best first, judged
    against the sentence (and the whole script, `context`) across all its searches at once. When none is good
    enough the judge's own better searches get one more look (D124), the last (D181): the build's further
    rounds, up to five calls a part, almost never found one. [] when nothing fits: the beat gets a chalkboard
    drawing instead, never a generic stand-in ("science laboratory" opened a video once). `good_enough` is
    lower when the user asked for footage on that sentence themselves (D126)."""
    ranked = _pool(queries, used, seconds)
    if not ranked:
        return []
    if not sentence:
        return ranked[:count]
    try:
        good, better = _judge(sentence, " / ".join(queries), ranked, context, good_enough)
        if not good and better:
            tried = {q.lower() for q in queries}
            fresh = [q for q in better if q.lower() not in tried]
            again = _pool(fresh, used, seconds) if fresh else []
            if again:
                log.info("create: footage searched again for %r: %s", sentence[:60], " / ".join(fresh))
                good, _ = _judge(sentence, " / ".join(fresh), again, context, good_enough)
        return good[:count]
    except _NoAnswer:
        hit = by_words(queries, ranked)
        if hit:
            unjudged.append(sentence)
        return [hit] if hit else []


# ---------- better searches, and every candidate scored, for choosing footage (D129) ----------

SEARCHES = """You write stock-footage searches for one sentence of a short narrated {subject} video for
phones. The footage libraries are Pexels and Pixabay: real filmed clips and photos, searched by plain words.
Write searches for things those libraries really film, that a viewer would connect with what the
sentence says AT THAT POINT in the script (you get the whole script; a "wall" in a video about
sound is a wall music comes through). 2 to 4 concrete words each: objects, places, nature, and
people in ordinary situations ("woman wearing headphones", "subwoofer speaker vibrating", "man
talking on phone close up"). Mix the literal thing with a person experiencing it. Never abstract
words ("physics", "energy", "frequency", "concept"), never actions nobody films ("person feeling
bone vibration"). If the user says what they want to see, follow it. Give 5, best first, none of
the ones already tried."""


class _Searches(BaseModel):
    searches: list[str] = []


def plan_searches(sentence: str, context: str = "", wish: str = "", tried: list[str] | None = None) -> list[str]:
    """Up to 5 searches written for this sentence by the footage model (Gemini first, free);
    [] when no model can answer, and the sentence's own searches are used alone."""
    user = ((f"The whole script:\n{context}\n\n" if context else "") + f"Sentence: {sentence}\n"
            + (f"The user wants to see: {wish}\n" if wish else "")
            + (f"Already tried: {', '.join(tried)}\n" if tried else ""))
    try:
        answer = ask(channels.fill(SEARCHES), user, _Searches, temperature=0.3, quick=True, job="footage", keep=True)
        found = _Searches.model_validate(json.loads(answer)).searches
    except (CreateError, ValueError, TypeError) as exc:
        log.info("create: no searches planned (%s)", exc)
        return []
    out = []
    for q in found:
        q = " ".join(str(q).split()[:5]).strip()
        if q and q.lower() not in {x.lower() for x in [*out, *(tried or [])]}:
            out.append(q)
    return out[:5]


def rank(sentence: str, hits: list[dict], context: str = "") -> list[dict]:
    """`hits` with the judge's score (0-10, or None when no model could look) and the subject's place
    across the frame, best first; candidates without a thumbnail are left out."""
    try:
        return _scored(sentence, hits, context)[0]
    except _NoAnswer as exc:
        log.info("create: candidates not scored (%s)", exc.__cause__ or "no thumbnails")
        return [{**h, "score": None, "center": 0.5} for h in hits if _thumb(h)]


def candidates(queries: list[str], seconds: float, exclude: set, sentence: str, context: str = "",
               limit: int = 12) -> list[dict]:
    """Footage to choose from for a sentence: every search's results from both libraries, minus
    `exclude` (the clip being replaced, clips used elsewhere in the video), long enough first,
    then scored, best first (D129)."""
    return rank(sentence, _shortlist(_gather(queries, exclude), min(seconds, 6.0) - 0.3, limit), context)


def thumb_file(clip_id: str) -> Path | None:
    """A candidate's cached thumbnail, for the page."""
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", clip_id):
        return None
    path = _dir() / "thumbs" / f"{clip_id}.jpg"
    return path if path.is_file() else None


def fetch(hit: dict) -> Path:
    """The clip (or photo) on disk, downloaded once."""
    path = _dir() / f"{hit['id']}_{hit['width']}x{hit['height']}.{'jpg' if is_photo(hit) else 'mp4'}"
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
