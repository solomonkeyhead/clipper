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
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
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


def _cached(source: str, query: str) -> tuple[str, Path, list[dict] | None]:
    q = " ".join(query.lower().split())[:100]
    name = hashlib.sha1(q.encode()).hexdigest()[:16]
    cached = ensure(_dir() / "search") / f"{source}-v2-{name}.json"   # v2: with previews (D130)
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
        small = min((f for f in files if min(f["width"] or 0, f["height"] or 0) >= 360), default=best,
                    key=lambda f: f["width"] * f["height"])
        hits.append({"id": f"pexels-{v['id']}", "duration": v.get("duration", 0), "tags": tags,
                     "url": best["url"], "width": best["width"], "height": best["height"],
                     "thumb": v.get("image", ""), "preview": small["url"]})
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
        small = (h["videos"].get("tiny") or h["videos"].get("small") or {}).get("url") or v["url"]
        hits.append({"id": h["id"], "duration": h.get("duration", 0), "tags": h.get("tags", ""),
                     "url": v["url"], "width": v["width"], "height": v["height"], "thumb": thumb,
                     "preview": small})
    cached.write_text(json.dumps(hits), encoding="utf-8")
    return hits


def coverr(query: str) -> list[dict]:
    """Coverr's videos for `query` (cached a day): free, no credit needed, a free key from
    coverr.co/developers in COVERR_API_KEY (D130)."""
    q, cached, hits = _cached("coverr", query)
    if hits is not None:
        return hits
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
    cached.write_text(json.dumps(hits), encoding="utf-8")
    return hits


def _rendition_named(files: list[str], *names: str) -> str | None:
    """The first of NASA's files named "~<name>." for the names in order of preference."""
    return next((f for n in names for f in files if f"~{n}." in f), None)


def nasa(query: str) -> list[dict]:
    """NASA's videos for `query` (cached a day): public, no key; NASA's own footage is not under
    copyright (its logos must not suggest it endorses the video). Each video's files are listed in a
    manifest, read for the first few results only (D130)."""
    q, cached, hits = _cached("nasa", query)
    if hits is not None:
        return hits
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
    cached.write_text(json.dumps(hits), encoding="utf-8")
    return hits


CANDIDATES = 12
PER_QUERY = 8

PICK = """You choose stock footage for one sentence of a short educational video, shown on a
phone. You see numbered thumbnails of candidate clips, each with what its library says it
shows. Find the one a viewer would instantly connect with what the sentence says: the thing
itself, or a person plainly experiencing it, clear and close enough to read on a phone. A
close, everyday match counts (headphones for hearing, a microphone for recording).
Then score how well that best one fits, honestly:
  9-10 it shows exactly what the sentence says;
  7-8  a clear, natural match a viewer gets at once;
  4-6  related, but loose, generic or needs explaining (a lab for "your voice");
  0-3  unrelated, confusing, or cheap-looking (cartoonish, a glossy 3D render or CGI, neon
       audio-visualiser rings, a green background, text or a logo burned in, mostly black or
       too dark to read on a phone, a stock-footage cliche).
Below 7 a chalkboard card is shown instead, which is better than a loose match, so don't
round up. Answer pick = its number (0 if none), score, and center: where across that
thumbnail the subject is, 0 = left edge, 0.5 = middle, 1 = right edge (the video is cropped
to a tall strip around it, so put it on the thing the sentence is about).
You also get the whole script, so you know what the video is about and what the sentence
means in it (a "wall" in a video about sound is a wall music comes through, not a climbing
wall). If nothing scores 7 or more, give better: up to 2 new searches, 2 to 4 plain words each,
for things stock libraries really film ("man listening to headphones", "subwoofer speaker"),
that would show this sentence."""

#: The judge's score a clip needs to be used; below it the beat gets a chalk card (D110).
#: Picks were judged "very poor" when any non-zero pick was taken.
GOOD_ENOUGH = 7


class _Pick(BaseModel):
    pick: int
    score: int = 0
    center: float = 0.5
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
            img.save(cached, "JPEG", quality=82)
        except OSError:
            return None
    return cached.read_bytes()


class _NoAnswer(Exception):
    """The model couldn't look (busy, offline): not the same as "none of these fit"."""


def _judge(sentence: str, query: str, hits: list[dict], context: str = "",
           good_enough: int = GOOD_ENOUGH) -> tuple[dict | None, list[str]]:
    """The candidate the model says shows the sentence (None if none does), and the better
    searches it suggests when none does."""
    shown = [(h, t) for h in hits if (t := _thumb(h))]
    if not shown:
        raise _NoAnswer
    try:
        listed = "\n".join(f"{i}. {'tall' if h['height'] > h['width'] else 'wide'}, {h.get('tags') or 'no tags'}"
                           for i, (h, _) in enumerate(shown, start=1))
        prompt = ((f"The whole script, for context:\n{context}\n\n" if context else "")
                  + f"Sentence: {sentence}\nSearched for: {query}\n"
                  f"Thumbnails 1 to {len(shown)}, in order:\n{listed}")
        answer = ask(PICK, prompt, _Pick, temperature=0.0, media=[(t, "image/jpeg") for _, t in shown], quick=True,
                     footage=True)
        verdict = _Pick.model_validate(json.loads(answer))
    except (CreateError, ValueError, TypeError) as exc:
        raise _NoAnswer from exc
    n = verdict.pick
    better = [" ".join(q.split()[:5]) for q in verdict.better if q.strip()][:2]
    if not 1 <= n <= len(shown) or verdict.score < good_enough:
        log.info("create: no footage good enough for %r (best %s scored %s)", sentence[:60], n, verdict.score)
        return None, better
    return {**shown[n - 1][0], "center": min(1.0, max(0.0, verdict.center))}, []


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


def _pool(queries: list[str], used: set, seconds: float) -> list[dict]:
    pool: list[dict] = []
    for q in queries:
        for h in search(q)[:PER_QUERY]:
            if h["id"] not in used and all(h["id"] != p["id"] for p in pool):
                pool.append(h)
    # Long enough first, then vertical, keeping the search order within each.
    return sorted(pool, key=lambda h: (h["duration"] < seconds + 0.3, h["height"] <= h["width"]))[:CANDIDATES]


def choose(queries: list[str], seconds: float, used: set, sentence: str = "", context: str = "",
           good_enough: int = GOOD_ENOUGH) -> dict | None:
    """The best unused clip for a beat of `seconds`, judged against the sentence (and the whole
    script, `context`) across all its searches at once. When none is good enough the judge's
    own better searches get one more look (D124). None when nothing fits: the beat gets a
    chalkboard card instead, never a generic stand-in ("science laboratory" opened a video once).
    `good_enough` is lower when the user asked for footage on that sentence themselves (D126)."""
    ranked = _pool(queries, used, seconds)
    if not ranked:
        return None
    if not sentence:
        return ranked[0]
    try:
        hit, better = _judge(sentence, " / ".join(queries), ranked, context, good_enough)
        if hit is None and better:
            tried = {q.lower() for q in queries}
            fresh = [q for q in better if q.lower() not in tried]
            again = _pool(fresh, used, seconds) if fresh else []
            if again:
                log.info("create: footage searched again for %r: %s", sentence[:60], " / ".join(fresh))
                hit, _ = _judge(sentence, " / ".join(fresh), again, context, good_enough)
        return hit
    except _NoAnswer:
        hit = by_words(queries, ranked)
        if hit:
            unjudged.append(sentence)
        return hit


# ---------- better searches, and every candidate scored, for choosing footage (D129) ----------

SEARCHES = """You write stock-footage searches for one sentence of a short narrated {subject} video for
phones. The footage libraries are Pexels and Pixabay: real filmed clips, searched by plain words.
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
        answer = ask(channels.fill(SEARCHES), user, _Searches, temperature=0.3, quick=True, footage=True)
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


RANK = """You score stock footage for one sentence of a short educational video shown on a phone.
You see numbered thumbnails of candidate clips, each with what its library says it shows, and the
whole script for context. Score EVERY candidate for how well it shows what the sentence says:
  9-10 exactly what the sentence says; 7-8 a clear, natural match a viewer gets at once;
  4-6 related but loose or generic; 0-3 unrelated, confusing or cheap-looking (cartoonish, CGI,
  neon visualiser rings, green background, burned-in text or logo, too dark to read on a phone).
Don't round up. Also give center for each: where across its thumbnail the subject is, 0 = left,
0.5 = middle, 1 = right (the video is cropped to a tall strip around it)."""


class _Ranks(BaseModel):
    scores: list[int] = []
    centers: list[float] = []


def rank(sentence: str, hits: list[dict], context: str = "") -> list[dict]:
    """`hits` with a model's score (0-10, or None when no model could look) and the subject's place
    across the frame, best first; candidates without a thumbnail are left out."""
    shown = [(h, t) for h in hits if (t := _thumb(h))]
    if not shown:
        return []
    listed = "\n".join(f"{i}. {'tall' if h['height'] > h['width'] else 'wide'}, {h.get('tags') or 'no tags'}"
                       for i, (h, _) in enumerate(shown, start=1))
    try:
        answer = ask(RANK, (f"The whole script:\n{context}\n\n" if context else "") + f"Sentence: {sentence}\n"
                     f"Thumbnails 1 to {len(shown)}, in order:\n{listed}", _Ranks, temperature=0.0,
                     media=[(t, "image/jpeg") for _, t in shown], quick=True, footage=True)
        got = _Ranks.model_validate(json.loads(answer))
    except (CreateError, ValueError, TypeError) as exc:
        log.info("create: candidates not scored (%s)", exc)
        got = _Ranks()
    out = []
    for k, (h, _) in enumerate(shown):
        score = got.scores[k] if k < len(got.scores) else None
        center = got.centers[k] if k < len(got.centers) else 0.5
        out.append({**h, "score": None if score is None else max(0, min(10, int(score))),
                    "center": min(1.0, max(0.0, float(center)))})
    return sorted(out, key=lambda h: -(h["score"] if h["score"] is not None else -1))


def candidates(queries: list[str], seconds: float, exclude: set, sentence: str, context: str = "",
               limit: int = 12) -> list[dict]:
    """Footage to choose from for a sentence: every search's results from both libraries, minus
    `exclude` (the clip being replaced, clips used elsewhere in the video), long enough first,
    then scored, best first (D129)."""
    pool: list[dict] = []
    for q in queries:
        for h in search(q)[:PER_QUERY]:
            if h["id"] not in exclude and all(h["id"] != p["id"] for p in pool):
                pool.append({**h, "query": q})  # preview: a small file to play on hover (D130)
    pool = sorted(pool, key=lambda h: (h["duration"] < min(seconds, 6.0), h["height"] <= h["width"]))[:limit]
    return rank(sentence, pool, context)


def thumb_file(clip_id: str) -> Path | None:
    """A candidate's cached thumbnail, for the page."""
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", clip_id):
        return None
    path = _dir() / "thumbs" / f"{clip_id}.jpg"
    return path if path.is_file() else None


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
