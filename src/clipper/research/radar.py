"""The niche radar: a brief per niche the user tracks, refreshed daily.

A brief is the week's most-viewed Shorts on the niche's keywords (YouTube Data
API), what the web says is trending (two recent searches), and the configured
LLM's reading of both: trending topics (each citing its sources) and hook lines
in the niche's voice. With neither source set up, the topics and hooks come
from the model's own knowledge and the brief says so (`live` false).
"""

from __future__ import annotations

import json
from datetime import datetime

from pydantic import BaseModel, Field

from ..utils.logging import get_logger
from . import sources

log = get_logger(__name__)

STALE_HOURS = 24


class _Topic(BaseModel):
    title: str
    why: str
    sources: list[int] = Field(default_factory=list)


class _Reading(BaseModel):
    summary: str
    topics: list[_Topic] = Field(default_factory=list)
    hooks: list[str] = Field(default_factory=list)


SYSTEM = """You brief a short-form video clipper on a niche they make clips in. From the \
material given (the week's top YouTube Shorts on the niche, and numbered web results), write:
- summary: two sentences on what is working in this niche right now.
- topics: 3-6 specific trending topics, moments, formats or angles worth clipping, each with \
why it's working and the numbers of the web results that support it (empty if none do).
- hooks: 8 on-screen hook lines (under 10 words each) in the niche's voice, patterned on what \
is working. No hashtags, no emojis.
Use only the material given for anything factual; never invent view counts or events. If \
there is no material, say so in the summary and base topics and hooks on general knowledge \
of the niche."""


def query_for(niche: dict) -> str:
    words = niche.get("keywords") or []
    return " ".join(words[:4]) if words else niche["name"]


def is_stale(niche: dict) -> bool:
    if not niche.get("brief_at"):
        return True
    try:
        age = datetime.now() - datetime.strptime(niche["brief_at"], "%Y-%m-%d %H:%M")
    except ValueError:
        return True
    return age.total_seconds() > STALE_HOURS * 3600


def refresh(niche: dict, backend, *, progress=lambda step: None) -> dict:
    """A fresh brief for `niche` ({name, description, keywords})."""
    from ..llm.base import LLMRequest

    query = query_for(niche)
    brief: dict = {"shorts": [], "topics": [], "hooks": [], "sources": [], "notes": [],
                   "summary": "", "live": False}
    try:
        progress("Checking top Shorts")
        brief["shorts"] = [s.view() for s in sources.top_shorts(query, days=7)]
    except sources.SourceUnavailable as exc:
        brief["notes"].append(str(exc))
    web: list[sources.WebResult] = []
    try:
        progress("Searching the web")
        for q, days in ((f"{niche['name']} trending on TikTok and Reels this week", 7),
                        (f"{niche['name']} viral short clips what works", 31)):
            for r in sources.search_web(q, results=5, days=days):
                if all(r.url != w.url for w in web):
                    web.append(r)
    except sources.SourceUnavailable as exc:
        brief["notes"].append(str(exc))
    brief["sources"] = [{"title": w.title, "url": w.url} for w in web]
    brief["live"] = bool(web or brief["shorts"])

    material = [f"Niche: {niche['name']}. {niche.get('description') or ''}".strip()]
    if brief["shorts"]:
        material.append("Top Shorts this week:\n" + "\n".join(
            f"- {s['title']} ({s['channel']}, {s['views']:,} views)" for s in brief["shorts"]))
    if web:
        material.append("Web results:\n" + "\n".join(
            f"[{i}] {w.title}: {w.content[:700]}" for i, w in enumerate(web, 1)))
    progress("Writing the brief")
    response = backend.complete(LLMRequest(system=SYSTEM, user="\n\n".join(material),
                                           temperature=0.4, response_schema=_Reading))
    try:
        reading = _Reading.model_validate(json.loads(response.text.strip().strip("`").removeprefix("json")))
    except ValueError as exc:
        raise RuntimeError(f"the AI's brief was unreadable: {exc}") from exc
    brief["summary"] = reading.summary
    brief["topics"] = [{"title": t.title, "why": t.why,
                        "sources": [n for n in t.sources if 1 <= n <= len(web)]}
                       for t in reading.topics]
    brief["hooks"] = [" ".join(h.split()) for h in reading.hooks if h.strip()][:10]
    return brief
