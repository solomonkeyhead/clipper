"""Web search for the Ask chat, through Tavily's API (TAVILY_API_KEY).

Tavily is a search API made for AI assistants, free for 1,000 searches a month.
Gemini's own Google Search grounding would do, but free Gemini keys have no
quota for it (429 on every model, 2026-09-29); a hosted Clipper on a paid key
would use that instead. Nothing here scrapes a website; without a key, the
chat says so.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass

from ..utils.logging import get_logger

log = get_logger(__name__)

TAVILY_URL = "https://api.tavily.com/search"
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


def has_web() -> bool:
    return bool(os.environ.get("TAVILY_API_KEY", "").strip())


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
        raise SourceUnavailable("web search needs a Tavily key (Ask → Set up web search)")
    body: dict = {"query": query[:400], "max_results": max(1, min(10, results)),
                  "search_depth": "basic", "include_answer": False}
    if days:
        body["time_range"] = "week" if days <= 7 else "month" if days <= 31 else "year"
    data = _request(TAVILY_URL, data=body, headers={"Authorization": f"Bearer {key}"})
    return [WebResult(title=(r.get("title") or r.get("url") or "")[:200], url=r.get("url") or "",
                      content=" ".join((r.get("content") or "").split())[:1200])
            for r in data.get("results") or [] if r.get("url")]
