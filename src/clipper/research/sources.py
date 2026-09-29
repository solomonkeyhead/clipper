"""Where research looks things up: the web and YouTube, through official APIs.

* Web search: Tavily (TAVILY_API_KEY), a search API made for AI assistants, free
  for 1,000 searches a month. (Gemini's own Google Search grounding would do,
  but free Gemini keys have no quota for it: 429 on every model, 2026-09-29.)
* YouTube: the YouTube Data API v3 (YOUTUBE_API_KEY, free, 10,000 units a day;
  a search costs 100) for the most-viewed recent Shorts on a topic.

Nothing here scrapes a website; without a key, the feature says so.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta

from ..utils.logging import get_logger

log = get_logger(__name__)

TAVILY_URL = "https://api.tavily.com/search"
YOUTUBE = "https://www.googleapis.com/youtube/v3"
TIMEOUT = 30


class SourceUnavailable(RuntimeError):
    """No key for this source, or it refused; the message says which."""


@dataclass
class WebResult:
    title: str
    url: str
    content: str

    def view(self) -> dict:
        return asdict(self)


@dataclass
class Short:
    id: str
    title: str
    channel: str
    views: int
    likes: int
    published: str
    seconds: int
    url: str
    thumb: str

    def view(self) -> dict:
        return asdict(self)


def has_web() -> bool:
    return bool(os.environ.get("TAVILY_API_KEY", "").strip())


def has_youtube() -> bool:
    return bool(os.environ.get("YOUTUBE_API_KEY", "").strip())


def _request(url: str, *, data: dict | None = None, headers: dict | None = None) -> dict:
    body = json.dumps(data).encode() if data is not None else None
    request = urllib.request.Request(url, data=body, method="POST" if body else "GET",
                                     headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read().decode("utf-8"))
        except (ValueError, OSError):
            detail = {}
        message = (detail.get("detail") or (detail.get("error") or {}).get("message")
                   or detail.get("message") or f"HTTP {exc.code}")
        if isinstance(message, dict):
            message = message.get("error") or str(message)
        raise SourceUnavailable(str(message)[:200]) from None
    except (urllib.error.URLError, OSError) as exc:
        raise SourceUnavailable(f"can't reach the service ({type(exc).__name__})") from None


def search_web(query: str, *, results: int = 5, days: int | None = None) -> list[WebResult]:
    """Top web results for `query`, with the relevant text of each page."""
    key = os.environ.get("TAVILY_API_KEY", "").strip()
    if not key:
        raise SourceUnavailable("web search needs a Tavily key (Research → Setup)")
    body: dict = {"query": query[:400], "max_results": max(1, min(10, results)),
                  "search_depth": "basic", "include_answer": False}
    if days:
        body["time_range"] = "week" if days <= 7 else "month" if days <= 31 else "year"
    data = _request(TAVILY_URL, data=body, headers={"Authorization": f"Bearer {key}"})
    return [WebResult(title=(r.get("title") or r.get("url") or "")[:200], url=r.get("url") or "",
                      content=" ".join((r.get("content") or "").split())[:1200])
            for r in data.get("results") or [] if r.get("url")]


def _iso_seconds(duration: str) -> int:
    m = re.fullmatch(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", duration or "")
    if not m:
        return 0
    d, h, mi, s = (int(x or 0) for x in m.groups())
    return ((d * 24 + h) * 60 + mi) * 60 + s


def top_shorts(query: str, *, days: int = 7, count: int = 12) -> list[Short]:
    """The most-viewed YouTube Shorts on `query` published in the last `days`."""
    key = os.environ.get("YOUTUBE_API_KEY", "").strip()
    if not key:
        raise SourceUnavailable("top Shorts need a YouTube Data API key (Research → Setup)")
    after = (datetime.now(UTC) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    found = _request(f"{YOUTUBE}/search?" + urllib.parse.urlencode({
        "part": "snippet", "type": "video", "videoDuration": "short", "order": "viewCount",
        "publishedAfter": after, "q": query[:200], "maxResults": 25, "key": key}))
    ids = [i["id"]["videoId"] for i in found.get("items") or [] if i.get("id", {}).get("videoId")]
    if not ids:
        return []
    details = _request(f"{YOUTUBE}/videos?" + urllib.parse.urlencode({
        "part": "snippet,statistics,contentDetails", "id": ",".join(ids), "key": key}))
    shorts = []
    for v in details.get("items") or []:
        seconds = _iso_seconds((v.get("contentDetails") or {}).get("duration", ""))
        if not seconds or seconds > 180:  # Shorts are up to 3 minutes
            continue
        sn, st = v.get("snippet") or {}, v.get("statistics") or {}
        thumbs = sn.get("thumbnails") or {}
        shorts.append(Short(
            id=v["id"], title=sn.get("title", ""), channel=sn.get("channelTitle", ""),
            views=int(st.get("viewCount") or 0), likes=int(st.get("likeCount") or 0),
            published=(sn.get("publishedAt") or "")[:10], seconds=seconds,
            url=f"https://www.youtube.com/shorts/{v['id']}",
            thumb=(thumbs.get("high") or thumbs.get("medium") or thumbs.get("default") or {}).get("url", "")))
    return sorted(shorts, key=lambda s: -s.views)[:count]
