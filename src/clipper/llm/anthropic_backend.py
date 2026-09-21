"""Anthropic backend -- optional and paid.

Never required: BUILD_BRIEF.md section 2 makes free-and-local the default and
paid APIs a swappable extra. The SDK is an optional dependency, so importing
this module without it installed raises ImportError, which `base._load_builtins`
catches and treats as "backend unavailable".
"""

from __future__ import annotations

import os
import time
from typing import ClassVar

import anthropic

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

DEFAULT_MODEL = "claude-sonnet-5"


@register
class AnthropicBackend(LLMBackend):
    name: ClassVar[str] = "anthropic"
    # Schema enforcement would need a tool-use round trip. The prompt already
    # asks for bare JSON and the repair retry covers the rest.
    supports_schema: ClassVar[bool] = False

    def __init__(self, *, api_key: str | None = None, **kwargs):
        super().__init__(**kwargs)
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "").strip()
        if not self.api_key:
            raise LLMConfigError(
                "ANTHROPIC_API_KEY is not set.\n"
                "  This backend is optional and billed. Get a key at\n"
                "  https://console.anthropic.com/settings/keys, or use the free\n"
                "  default: --backend gemini"
            )
        self.model = self.model or DEFAULT_MODEL
        self._client: anthropic.Anthropic | None = None

    @property
    def client(self) -> anthropic.Anthropic:
        if self._client is None:
            self._client = anthropic.Anthropic(api_key=self.api_key, timeout=self.timeout)
        return self._client

    def _complete(self, request: LLMRequest) -> LLMResponse:
        started = time.perf_counter()
        try:
            message = self.client.messages.create(
                model=self.model,
                max_tokens=request.max_output_tokens or 8192,
                temperature=request.temperature,
                system=request.system,
                messages=[{"role": "user", "content": request.user}],
            )
        except anthropic.RateLimitError as exc:
            raise RateLimited(f"Anthropic rate limit: {exc}") from exc
        except anthropic.APIStatusError as exc:
            if exc.status_code == 429:
                raise RateLimited(f"Anthropic rate limit: {exc}") from exc
            raise LLMError(f"Anthropic request failed ({exc.status_code}): {exc}") from exc
        except anthropic.APIError as exc:
            raise LLMError(f"Anthropic request failed: {exc}") from exc

        text = "".join(block.text for block in message.content if block.type == "text")
        if not text.strip():
            raise LLMError(f"Anthropic returned no text (stop reason: {message.stop_reason})")

        return LLMResponse(
            text=text,
            model=self.model,
            prompt_tokens=message.usage.input_tokens,
            output_tokens=message.usage.output_tokens,
            latency=time.perf_counter() - started,
        )
