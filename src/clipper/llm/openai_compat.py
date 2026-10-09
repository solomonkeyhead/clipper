"""Free models behind OpenAI-style APIs (D164): Mistral, Groq, NVIDIA and OpenRouter, each with a free key
in .env. Create asks them after Gemini, so a busy afternoon on Gemini (every model answering 503) no
longer stops a script, a check or a drawing.

One backend for all four: they share the chat-completions shape. Answers are asked for as a JSON object,
with the schema written into the system prompt: every one of them takes `json_object`, not every one takes
a full JSON schema. Pictures go as data URLs to the models that can see (Mistral Medium, OpenRouter's free
router picks one that can); a model that can't see refuses, and the next one answers.
"""

from __future__ import annotations

import base64
import json
import os
import re
import time
from typing import ClassVar

import httpx

from .base import (
    LLMBackend,
    LLMConfigError,
    LLMError,
    LLMRequest,
    LLMResponse,
    RateLimited,
    RateLimiter,
    register,
)
from .ollama import _schema_for

#: provider -> (API base, the .env key, where to get one)
PROVIDERS: dict[str, tuple[str, str, str]] = {
    "mistral": ("https://api.mistral.ai/v1", "MISTRAL_API_KEY", "console.mistral.ai"),
    "groq": ("https://api.groq.com/openai/v1", "GROQ_API_KEY", "console.groq.com/keys"),
    "nvidia": ("https://integrate.api.nvidia.com/v1", "NVIDIA_API_KEY", "build.nvidia.com"),
    "openrouter": ("https://openrouter.ai/api/v1", "OPENROUTER_API_KEY", "openrouter.ai/settings/keys"),
}

#: Sent with every request to a provider (D168). NVIDIA's Nemotron thinks first unless told not to: a script
#: took 113 s and 10,102 tokens thinking, 2.6 s and 243 tokens without, and read as well.
EXTRA: dict[str, dict] = {
    "nvidia": {"chat_template_kwargs": {"enable_thinking": False}},
}

#: (provider, model) -> the most output tokens one request may ask for, learnt from a "Request too large" (D168):
#: Groq's free qwen allows 1,000 a minute and refused every request that didn't say so.
_caps: dict[tuple[str, str], int] = {}


def has_key(provider: str) -> bool:
    return provider in PROVIDERS and bool(os.environ.get(PROVIDERS[provider][1], "").strip())


@register
class OpenAICompatBackend(LLMBackend):
    name: ClassVar[str] = "openai_compat"
    supports_schema: ClassVar[bool] = True

    def __init__(self, *, provider: str = "mistral", **kwargs):
        if provider not in PROVIDERS:
            raise LLMConfigError(f"unknown provider {provider!r}; known: {', '.join(PROVIDERS)}")
        self.base, env, where = PROVIDERS[provider]
        self.api_key = os.environ.get(env, "").strip()
        if not self.api_key:
            raise LLMConfigError(f"{env} is not set. A free key is at {where}; put it in .env.")
        super().__init__(**kwargs)
        self.name = provider   # each provider its own: its rate limit, its rest after a failure, its family
        self.limiter = RateLimiter.shared(provider, kwargs.get("requests_per_minute", 10))

    def _complete(self, request: LLMRequest) -> LLMResponse:
        system = request.system
        schema = _schema_for(request)
        if schema is not None:
            system += ("\n\nAnswer with one JSON object only, no other text, matching this JSON schema:\n"
                       + json.dumps(schema))
        content: list[dict] | str = request.user
        pictures = [(data, mime) for data, mime in request.media if mime.startswith("image/")]
        if len(pictures) < len(request.media):
            raise LLMError(f"{self.describe()} can't watch video")
        if pictures:
            content = [{"type": "text", "text": request.user}, *[
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{base64.b64encode(data).decode()}"}}
                for data, mime in pictures]]
        body = {"model": self.model, "temperature": request.temperature,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": content}],
                **EXTRA.get(self.name, {})}
        if schema is not None:
            body["response_format"] = {"type": "json_object"}
        cap = _caps.get((self.name, self.model))
        if request.max_output_tokens or cap:
            body["max_tokens"] = min(x for x in (request.max_output_tokens, cap) if x)
        started = time.perf_counter()
        r = self._post(body)
        if r.status_code in (413, 429) and (limit := _output_limit(r.text)) and body.get("max_tokens", limit + 1) > limit:
            # Asked for more output than a minute allows: ask again once, for no more than that (D168).
            _caps[(self.name, self.model)] = body["max_tokens"] = limit
            r = self._post(body)
        if r.status_code == 429:
            raise RateLimited(f"{self.describe()}: rate limited ({_reason(r)})", retry_after=_retry_after(r))
        if r.status_code >= 400:
            raise LLMError(f"{self.describe()}: {r.status_code} {r.text[:200]}")
        try:
            data = r.json()
            text = data["choices"][0]["message"]["content"] or ""
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"{self.describe()} sent an answer that couldn't be read") from exc
        text = _unfenced(text)
        if not text.strip():
            raise LLMError(f"{self.describe()} gave an empty answer")
        usage = data.get("usage") or {}
        return LLMResponse(text=text, model=data.get("model") or self.model,
                           prompt_tokens=int(usage.get("prompt_tokens") or 0),
                           output_tokens=int(usage.get("completion_tokens") or 0),
                           latency=time.perf_counter() - started)


    def _post(self, body: dict) -> httpx.Response:
        try:
            return httpx.post(f"{self.base}/chat/completions", json=body, timeout=self.timeout,
                              headers={"Authorization": f"Bearer {self.api_key}"})
        except httpx.HTTPError as exc:
            raise LLMError(f"{self.describe()} didn't answer: {exc}") from exc


def _output_limit(text: str) -> int | None:
    """The output tokens a minute allows, from Groq's "Request too large ... (OTPM): Limit 1000, Requested 2048"."""
    m = re.search(r"Request too large.*?output tokens.*?Limit (\d+), Requested (\d+)", text, re.S)
    return int(m.group(1)) if m and int(m.group(1)) < int(m.group(2)) else None


def _retry_after(r: httpx.Response) -> float | None:
    """How long a 429 says to wait (D168): the retry-after header ("7.66" too, which isdigit() refused), Groq's
    "try again in 35.6s" in the body, or an hour for a model the key's plan gives no requests at all (Mistral
    answered "limit-req-minute: 0" for every model but the smallest, which no wait fixes)."""
    if r.headers.get("x-ratelimit-limit-req-minute") == "0":
        return 3600.0
    try:
        return float(r.headers.get("retry-after", ""))
    except ValueError:
        pass
    m = re.search(r"try again in (?:(\d+)m)?(\d+(?:\.\d+)?)s", r.text)
    return 60 * int(m.group(1) or 0) + float(m.group(2)) if m else None


def _reason(r: httpx.Response) -> str:
    """The provider's own words for a 429, short, for the page's notes."""
    try:
        err = r.json().get("error") or r.json()
        message = err.get("message", "") if isinstance(err, dict) else str(err)
    except ValueError:
        message = r.text
    if r.headers.get("x-ratelimit-limit-req-minute") == "0":
        return "this model isn't in the key's plan"
    return " ".join(str(message).split())[:120] or "429"


def _unfenced(text: str) -> str:
    """The answer without a ```json fence round it, which some models add even in JSON mode."""
    m = re.fullmatch(r"\s*```(?:json)?\s*(.*?)\s*```\s*", text, re.S)
    return m.group(1) if m else text
