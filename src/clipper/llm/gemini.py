"""Google Gemini backend -- the free-tier default.

Model names are **resolved at runtime**, never hardcoded. Verified during Phase 0:
`gemini-2.5-flash-lite`, which is still widely documented, now returns

    404 NOT_FOUND ... This model is no longer available to new users.

so a hardcoded name is a time bomb. `resolve_model` enumerates what this key can
actually see and picks the first match from a preference list, favouring the
moving `*-latest` aliases which cannot 404.

Structured output is enforced server-side with `response_schema`, which in
testing produced valid, pydantic-clean JSON first try.
"""

from __future__ import annotations

import os
import time
from typing import ClassVar

from ..utils.logging import get_logger
from .base import (
    LLMBackend,
    LLMConfigError,
    LLMError,
    LLMRequest,
    LLMResponse,
    RateLimited,
    register,
)

log = get_logger(__name__)

# Tried in order. Aliases first: they track the current model and cannot 404.
# Lite tiers first within a generation -- rubric scoring is a short, structured
# task where the cheaper model measured identically in Phase 0 verification.
MODEL_PREFERENCE: tuple[str, ...] = (
    "gemini-flash-lite-latest",
    "gemini-flash-latest",
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
    "gemini-3.5-flash",
    "gemini-2.5-flash-lite",
    "gemini-2.5-flash",
)

_RATE_LIMIT_MARKERS = ("429", "resource_exhausted", "rate limit", "quota")


@register
class GeminiBackend(LLMBackend):
    name: ClassVar[str] = "gemini"
    supports_schema: ClassVar[bool] = True

    def __init__(self, *, api_key: str | None = None, **kwargs):
        super().__init__(**kwargs)
        self.api_key = api_key or os.environ.get("GEMINI_API_KEY", "").strip() \
            or os.environ.get("GOOGLE_API_KEY", "").strip()
        if not self.api_key:
            raise LLMConfigError(
                "GEMINI_API_KEY is not set.\n"
                "  Get a free key at https://aistudio.google.com/apikey\n"
                "  Then copy .env.example to .env and put it there.\n"
                "  Or choose another backend: --backend ollama"
            )
        self._client = None

    @property
    def client(self):
        if self._client is None:
            try:
                from google import genai
            except ImportError as exc:  # pragma: no cover - declared dependency
                raise LLMConfigError(
                    f"the google-genai SDK is not installed: {exc}\n"
                    '  uv pip install -e ".[dev]"'
                ) from exc
            self._client = genai.Client(api_key=self.api_key)
        return self._client

    def list_models(self) -> list[str]:
        """Model ids this key can actually use for generateContent."""
        try:
            models = list(self.client.models.list())
        except Exception as exc:
            raise LLMError(f"could not list Gemini models: {exc}") from exc
        return [
            m.name.removeprefix("models/")
            for m in models
            if "generateContent" in (m.supported_actions or [])
        ]

    def resolve_model(self) -> str:
        """Pick a model, preferring the configured one, then the preference list."""
        if self.model:
            return self.model

        available = set(self.list_models())
        for candidate in MODEL_PREFERENCE:
            if candidate in available:
                self.model = candidate
                log.info("resolved Gemini model: %s", candidate)
                return candidate

        # Nothing from the preference list: fall back to any flash-ish model
        # rather than failing, since the naming scheme clearly keeps moving.
        fallback = sorted(
            m for m in available
            if "flash" in m and not any(x in m for x in ("image", "tts", "live", "audio"))
        )
        if fallback:
            self.model = fallback[0]
            log.warning(
                "no preferred Gemini model available; falling back to %s. "
                "Consider updating MODEL_PREFERENCE in llm/gemini.py.",
                self.model,
            )
            return self.model

        raise LLMConfigError(
            "this API key can see no usable Gemini text model. Models visible: "
            + (", ".join(sorted(available)[:10]) or "none")
        )

    def _complete(self, request: LLMRequest) -> LLMResponse:
        from google.genai import errors as genai_errors
        from google.genai import types

        model = self.resolve_model()
        config_kwargs = {
            "temperature": request.temperature,
            "system_instruction": request.system,
            "response_mime_type": "application/json",
            # The SDK warns about automatic function calling on every direct
            # generate_content call; we use no tools, so turn it off.
            "automatic_function_calling": types.AutomaticFunctionCallingConfig(disable=True),
        }
        if request.response_schema is not None:
            config_kwargs["response_schema"] = request.response_schema
        if request.max_output_tokens:
            config_kwargs["max_output_tokens"] = request.max_output_tokens

        # Building the config can fail on a malformed response_schema. That is a
        # programming error, not a transient one, so it must not be retried --
        # five exponential backoffs over six batches cost 2.5 minutes before
        # reporting a problem that was never going to fix itself.
        try:
            generate_config = types.GenerateContentConfig(**config_kwargs)
        except Exception as exc:
            raise LLMConfigError(
                f"could not build a Gemini request config: {exc}\n"
                "  This is a bug in clipper, not a problem with your key or quota."
            ) from exc

        started = time.perf_counter()
        try:
            response = self.client.models.generate_content(
                model=model,
                contents=request.user,
                config=generate_config,
            )
        except genai_errors.ClientError as exc:
            message = str(exc)
            if _looks_rate_limited(message):
                raise RateLimited(f"Gemini quota: {message[:200]}") from exc
            if "404" in message and self.model:
                # A configured model that has been retired. Clear it so the next
                # attempt re-resolves instead of failing identically forever.
                log.warning("Gemini model %s is unavailable; re-resolving", self.model)
                self.model = ""
                raise LLMError(f"Gemini model unavailable: {message[:200]}") from exc
            raise LLMError(f"Gemini request failed: {message[:300]}") from exc
        except Exception as exc:
            if _looks_rate_limited(str(exc)):
                raise RateLimited(f"Gemini quota: {str(exc)[:200]}") from exc
            raise LLMError(f"Gemini request failed: {str(exc)[:300]}") from exc

        latency = time.perf_counter() - started
        text = response.text or ""
        if not text.strip():
            raise LLMError(
                f"Gemini returned an empty response (finish reason: "
                f"{_finish_reason(response)}). This usually means a safety filter fired."
            )

        usage = getattr(response, "usage_metadata", None)
        return LLMResponse(
            text=text,
            model=model,
            prompt_tokens=getattr(usage, "prompt_token_count", 0) or 0,
            output_tokens=getattr(usage, "candidates_token_count", 0) or 0,
            latency=latency,
        )


def _looks_rate_limited(message: str) -> bool:
    lowered = message.lower()
    return any(marker in lowered for marker in _RATE_LIMIT_MARKERS)


def _finish_reason(response) -> str:
    try:
        return str(response.candidates[0].finish_reason)
    except (AttributeError, IndexError, TypeError):
        return "unknown"
