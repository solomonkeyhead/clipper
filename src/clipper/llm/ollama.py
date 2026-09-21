"""Local Ollama backend.

Runs entirely on this machine, so it has no quota and no key -- but it shares
the 8 GB card with Whisper. ``free_gpu_memory()`` must have run before this is
used, which the pipeline guarantees by running stages sequentially.

Talks to the HTTP API directly rather than through the `ollama` package: it is
three endpoints, and this avoids another dependency for an optional backend.
"""

from __future__ import annotations

import json
import os
import time
from typing import ClassVar, get_args, get_origin

import httpx

from ..utils.logging import get_logger
from .base import (
    LLMBackend,
    LLMConfigError,
    LLMError,
    LLMRequest,
    LLMResponse,
    RateLimiter,
    register,
)

log = get_logger(__name__)

DEFAULT_HOST = "http://127.0.0.1:11434"

# Models that fit in 8 GB and follow JSON instructions reliably. Tried in order.
# Not verified on this machine -- Ollama is not installed here (docs/VERIFIED.md).
MODEL_PREFERENCE: tuple[str, ...] = (
    "qwen2.5:7b-instruct",
    "llama3.1:8b-instruct-q4_K_M",
    "llama3.1:8b",
    "mistral-nemo:12b-instruct-2407-q4_K_M",
    "gemma2:9b-instruct-q4_K_M",
    "qwen2.5:14b-instruct-q4_K_M",
)


@register
class OllamaBackend(LLMBackend):
    name: ClassVar[str] = "ollama"
    supports_schema: ClassVar[bool] = True

    def __init__(self, *, host: str | None = None, **kwargs):
        super().__init__(**kwargs)
        # Local inference has no quota, so it is never throttled -- overriding
        # after super() because callers pass the configured RPM explicitly.
        self.limiter = RateLimiter(100_000)
        self.host = (host or os.environ.get("OLLAMA_HOST", "") or DEFAULT_HOST).rstrip("/")

    def list_models(self) -> list[str]:
        try:
            response = httpx.get(f"{self.host}/api/tags", timeout=10.0)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise LLMConfigError(
                f"could not reach Ollama at {self.host}: {exc}\n"
                "  Install it from https://ollama.com/download, then:\n"
                f"    ollama pull {MODEL_PREFERENCE[0]}\n"
                "  Or use the default backend: --backend gemini"
            ) from exc
        return [m.get("name", "") for m in response.json().get("models", [])]

    def resolve_model(self) -> str:
        if self.model:
            return self.model

        installed = self.list_models()
        if not installed:
            raise LLMConfigError(
                f"Ollama is running at {self.host} but has no models installed.\n"
                f"    ollama pull {MODEL_PREFERENCE[0]}"
            )

        # Match on the bare family name too, so a preference for
        # "qwen2.5:7b-instruct" also accepts an installed quantised variant.
        for preferred in MODEL_PREFERENCE:
            family = preferred.split(":")[0]
            for name in installed:
                if name == preferred or name.startswith(family + ":"):
                    self.model = name
                    log.info("resolved Ollama model: %s", name)
                    return name

        self.model = installed[0]
        log.warning(
            "no preferred Ollama model installed; using %s. JSON reliability may vary.",
            self.model,
        )
        return self.model

    def _complete(self, request: LLMRequest) -> LLMResponse:
        model = self.resolve_model()
        payload = {
            "model": model,
            "prompt": request.user,
            "system": request.system,
            "stream": False,
            # Ollama enforces a JSON schema server-side when given one, and
            # plain JSON mode otherwise.
            "format": _schema_for(request) or "json",
            "options": {"temperature": request.temperature},
        }
        if request.max_output_tokens:
            payload["options"]["num_predict"] = request.max_output_tokens

        started = time.perf_counter()
        try:
            response = httpx.post(f"{self.host}/api/generate", json=payload,
                                  timeout=self.timeout)
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPError as exc:
            raise LLMError(f"Ollama request failed: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise LLMError(f"Ollama returned an unparseable envelope: {exc}") from exc

        text = data.get("response", "")
        if not text.strip():
            raise LLMError("Ollama returned an empty response")

        return LLMResponse(
            text=text,
            model=model,
            prompt_tokens=int(data.get("prompt_eval_count", 0) or 0),
            output_tokens=int(data.get("eval_count", 0) or 0),
            latency=time.perf_counter() - started,
        )


def _schema_for(request: LLMRequest) -> dict | None:
    """Convert a pydantic response schema into the JSON Schema Ollama expects.

    Handles `list[Model]` (the form the google-genai SDK requires, so it is what
    callers pass) as well as a bare model class.
    """
    schema = request.response_schema
    if schema is None:
        return None
    try:
        if get_origin(schema) is list:
            args = get_args(schema)
            if args and hasattr(args[0], "model_json_schema"):
                return {"type": "array", "items": args[0].model_json_schema()}
            return None
        if hasattr(schema, "model_json_schema"):
            return schema.model_json_schema()
    except (AttributeError, TypeError):  # pragma: no cover - defensive
        return None
    return None
