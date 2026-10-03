"""Anthropic backend -- optional and paid.

Never required: BUILD_BRIEF.md section 2 makes free-and-local the default and
paid APIs a swappable extra. The SDK is an optional dependency, so importing
this module without it installed raises ImportError, which `base._load_builtins`
catches and treats as "backend unavailable".

Create writes with it first when a key is set (D109): Gemini's diagrams were
judged worse in both quality and accuracy. A pydantic schema is enforced by the
API (structured outputs), and images ride along for the stock-footage judge.
Current models take no temperature (the 1.x SDK has no such parameter), so a
request's temperature is not sent; their thinking is always on.
"""

from __future__ import annotations

import base64
import os
import time
from typing import ClassVar

import anthropic
from pydantic import BaseModel

from ..utils.logging import get_logger
from .base import (
    ContentBlocked,
    LLMBackend,
    LLMConfigError,
    LLMError,
    LLMRequest,
    LLMResponse,
    RateLimited,
    register,
)

log = get_logger(__name__)

DEFAULT_MODEL = "claude-opus-5-5"
IMAGE_TYPES = ("image/jpeg", "image/png", "image/gif", "image/webp")


@register
class AnthropicBackend(LLMBackend):
    name: ClassVar[str] = "anthropic"
    supports_schema: ClassVar[bool] = True

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

    @staticmethod
    def content(request: LLMRequest) -> str | list[dict]:
        """The user turn: images first, then the words. Claude reads images, not video."""
        if not request.media:
            return request.user
        blocks = []
        for data, mime in request.media:
            if mime not in IMAGE_TYPES:
                raise LLMConfigError(f"Claude can't take {mime}; images only")
            blocks.append({"type": "image", "source": {
                "type": "base64", "media_type": mime, "data": base64.standard_b64encode(data).decode("ascii")}})
        blocks.append({"type": "text", "text": request.user})
        return blocks

    def _complete(self, request: LLMRequest) -> LLMResponse:
        started = time.perf_counter()
        schema = request.response_schema
        kwargs = {"model": self.model, "max_tokens": request.max_output_tokens or 16000,
                  "system": request.system, "messages": [{"role": "user", "content": self.content(request)}]}
        try:
            if isinstance(schema, type) and issubclass(schema, BaseModel):
                message = self.client.messages.parse(output_format=schema, **kwargs)
            else:
                message = self.client.messages.create(**kwargs)
        except anthropic.RateLimitError as exc:
            raise RateLimited(f"Anthropic rate limit: {exc}") from exc
        except anthropic.BadRequestError as exc:
            raise LLMConfigError(f"Anthropic refused the request: {exc}") from exc
        except anthropic.APIStatusError as exc:
            if exc.status_code == 429:
                raise RateLimited(f"Anthropic rate limit: {exc}") from exc
            raise LLMError(f"Anthropic request failed ({exc.status_code}): {exc}") from exc
        except anthropic.APIError as exc:
            raise LLMError(f"Anthropic request failed: {exc}") from exc
        except ValueError as exc:  # structured output that didn't validate against the schema
            raise LLMError(f"Anthropic's answer didn't fit the schema: {exc}") from exc

        if message.stop_reason == "refusal":
            raise ContentBlocked("Claude declined this request")
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
