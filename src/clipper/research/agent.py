"""The Ask chat: Gemini with tools for the web and the user's own data.

Each question runs a short tool loop (at most `MAX_ROUNDS` model calls): the
model may search the web or read the user's clips, stats and campaigns, then
answers citing web results as [1], [2], ... It answers only; it changes nothing.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

from ..utils.logging import get_logger
from . import sources

log = get_logger(__name__)

MODELS = ("gemini-flash-latest", "gemini-flash-lite-latest")
MAX_ROUNDS = 6
HISTORY = 12


class ResearchError(RuntimeError):
    """The chat can't answer (no key, the model is busy); the message says why."""


@dataclass
class Toolbox:
    """The user's own data, as the server provides it."""

    clips: Callable[[str | None], list[dict]]
    campaigns: Callable[[], list[dict]]
    #: An active campaign's rules and its brief as pasted; None if not active.
    brief: Callable[[str], dict | None] = lambda name: None


@dataclass
class Answer:
    text: str
    sources: list[dict] = field(default_factory=list)


SYSTEM = """You are the research assistant inside Clipper, a tool people use to cut long \
videos (TV shows, podcasts, streams) into short vertical clips for paid clipping campaigns \
(Content Rewards on Whop, Vyro) and post them to TikTok, Instagram Reels and YouTube Shorts.

Help with quick questions and research: what's trending in a niche, which hooks and formats \
work, how the user's own clips are doing, which campaigns pay best, what to clip next,
and whether a campaign is worth joining.

Rules:
- Use tools instead of guessing. For anything current (trends, news, what's working now), \
search the web. For the user's own results, use my_clips / my_campaigns. For what a campaign \
allows, requires, pays or rejects, read campaign_brief and answer from it, quoting the brief's \
words for rules; if the brief doesn't say, say so rather than guessing.
- Cite web results inline by their number, like [2]. Never invent a source, a number or a quote.
- If a tool says a source isn't set up, answer from what you have and say in one line what \
adding it would give.
- Be concise and practical: short paragraphs or bullets, lead with the answer. The user is \
a busy clipper, not a researcher.
- Hook lines you suggest are short on-screen text (under 10 words), in the voice of the niche.
- Never suggest reposting footage without a campaign's or creator's permission, buying views, \
or anything against a platform's or campaign's rules."""

def _declarations():
    from google.genai import types

    S = types.Schema

    def fn(name, description, props=None, required=None):
        return types.FunctionDeclaration(
            name=name, description=description,
            parameters=S(type="OBJECT", properties=props or {}, required=required or []))

    return [types.Tool(function_declarations=[
        fn("search_web", "Search the web. Returns numbered results with page text.",
           {"query": S(type="STRING"), "recent_days": S(type="INTEGER",
            description="Only results from roughly the last N days, for trends and news")},
           ["query"]),
        fn("my_clips", "The user's clips: title, campaign, status, Clipper's score (0-10), their "
           "rating (1-5), views, average watch time and 3-second skip rate per platform.",
           {"campaign": S(type="STRING", description="Campaign id to filter by")}),
        fn("my_campaigns", "The user's campaigns: id, title, pay per 1K views, platforms, "
           "clip length, focus, hook lines, clips made, views, estimated earnings."),
        fn("campaign_brief", "One active campaign's full brief as the user pasted it, plus the rules "
           "Clipper follows for it (hashtags, caption and credit text, banned words, clip length, "
           "platforms, payout, deadline, notes). Use it for any question about what a campaign "
           "allows, requires, pays or rejects. Archived campaigns aren't available.",
           {"campaign": S(type="STRING", description="Campaign id or title, from my_campaigns")},
           ["campaign"]),
    ])]


class _Run:
    """One question: the tool calls it made and the sources it found."""

    def __init__(self, box: Toolbox, progress: Callable[[str], None]):
        self.box, self.progress = box, progress
        self.sources: list[dict] = []

    def call(self, name: str, args: dict) -> dict:
        try:
            return getattr(self, f"_{name}")(**args)
        except sources.SourceUnavailable as exc:
            return {"unavailable": str(exc)}
        except TypeError as exc:
            return {"error": f"bad arguments: {exc}"}
        except AttributeError:
            return {"error": f"no tool {name}"}

    def _search_web(self, query: str, recent_days: int | None = None) -> dict:
        self.progress(f"Searching the web: {query}")
        found = sources.search_web(query, days=recent_days)
        out = []
        for r in found:
            existing = next((i for i, s in enumerate(self.sources, 1) if s["url"] == r.url), None)
            if existing is None:
                self.sources.append({"title": r.title, "url": r.url})
                existing = len(self.sources)
            out.append({"n": existing, "title": r.title, "url": r.url, "text": r.content})
        return {"results": out}

    def _my_clips(self, campaign: str | None = None) -> dict:
        self.progress("Reading your clips")
        return {"clips": self.box.clips(campaign)[:60]}

    def _my_campaigns(self) -> dict:
        self.progress("Reading your campaigns")
        return {"campaigns": self.box.campaigns()}

    def _campaign_brief(self, campaign: str) -> dict:
        self.progress(f"Reading the {campaign} brief")
        found = self.box.brief(campaign)
        if found is None:
            return {"error": f"no active campaign {campaign!r}; check my_campaigns for ids"}
        return found


def _client():
    from google import genai
    from google.genai import types

    key = os.environ.get("GEMINI_API_KEY", "").strip() or os.environ.get("GOOGLE_API_KEY", "").strip()
    if not key:
        raise ResearchError("Research needs your AI key: add it in Settings.")
    return genai.Client(api_key=key, http_options=types.HttpOptions(timeout=90_000))


def ask(question: str, history: list[tuple[str, str]], box: Toolbox, *,
        progress: Callable[[str], None] = lambda step: None) -> Answer:
    """Answer `question`, given the conversation's earlier (role, text) turns."""
    from google.genai import types

    client = _client()
    run = _Run(box, progress)
    system = SYSTEM + f"\n\nToday is {datetime.now():%A %d %B %Y}."
    system += ("\nWeb search is set up." if sources.has_web()
               else "\nWeb search is NOT set up (no Tavily key).")
    contents = [types.Content(role="user" if role == "user" else "model",
                              parts=[types.Part(text=text)])
                for role, text in history[-HISTORY:]]
    contents.append(types.Content(role="user", parts=[types.Part(text=question)]))
    config = types.GenerateContentConfig(
        system_instruction=system, tools=_declarations(), temperature=0.4,
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True))

    progress("Thinking")
    for _round in range(MAX_ROUNDS):
        response = _generate(client, contents, config)
        calls = response.function_calls or []
        if not calls:
            text = (response.text or "").strip()
            if not text:
                raise ResearchError("The AI returned an empty answer; try asking again.")
            return Answer(text=text, sources=run.sources)
        contents.append(response.candidates[0].content)
        parts = []
        for call in calls:
            result = run.call(call.name, dict(call.args or {}))
            parts.append(types.Part.from_function_response(name=call.name, response=result))
        contents.append(types.Content(role="user", parts=parts))
        progress("Writing the answer")
    # Out of rounds: ask for an answer from what it has.
    contents.append(types.Content(role="user", parts=[types.Part(
        text="Answer now with what you have; no more tool calls.")]))
    response = _generate(client, contents, config.model_copy(update={"tools": None}))
    return Answer(text=(response.text or "").strip() or "I couldn't finish that; try a narrower question.",
                  sources=run.sources)


def _generate(client, contents, config):
    last = None
    for model in MODELS:
        try:
            return client.models.generate_content(model=model, contents=contents, config=config)
        except Exception as exc:  # busy or over quota: try the next model
            last = exc
            text = str(exc)
            if not any(code in text for code in ("429", "503", "500", "RESOURCE_EXHAUSTED", "UNAVAILABLE")):
                break
            log.info("research: %s unavailable (%s); trying the next model", model, text[:80])
    message = str(last or "")
    if "429" in message or "RESOURCE_EXHAUSTED" in message:
        raise ResearchError("The AI's free quota is used up for now; try again in a minute.") from last
    if "API key not valid" in message:
        raise ResearchError("Your AI key was refused; check it in Settings.") from last
    raise ResearchError("The AI didn't answer (it may be busy); try again in a moment.") from last


def title_for(question: str) -> str:
    words = " ".join(question.split())
    return words if len(words) <= 60 else words[:57].rstrip() + "…"


def dumps(value) -> str:
    return json.dumps(value, ensure_ascii=False)
