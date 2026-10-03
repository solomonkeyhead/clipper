"""Asking the model for Create: Claude first when there's a key for it (D109; Gemini's
diagrams were judged worse in quality and accuracy), then the stronger free model,
then the everyday one when it rests (runner._correction_backends), at the
temperature the job wants -- some creativity for ideas and scripts, none for
checking them (Claude takes no temperature; its thinking does the job)."""

from __future__ import annotations

import os

from ..utils.logging import get_logger

log = get_logger(__name__)

#: Claude thinks before it answers: a whole script can take a couple of minutes.
CLAUDE_TIMEOUT = 240.0


class CreateError(RuntimeError):
    """Something Create couldn't do; the message says why, for the page."""


def backends(config) -> list:
    from ..llm.base import create as create_backend
    from ..runner import _correction_backends

    chosen = []
    model = config.llm.create_model
    if model and os.environ.get("ANTHROPIC_API_KEY", "").strip():
        try:
            chosen.append(create_backend("anthropic", model=model, max_retries=1,
                                         requests_per_minute=config.llm.requests_per_minute,
                                         timeout=CLAUDE_TIMEOUT))
        except Exception as exc:  # SDK not installed: the free models still answer
            log.warning("create: Claude unavailable (%s); using the free models", exc)
    try:
        chosen += _correction_backends(config, None)
    except Exception as exc:
        if not chosen:
            raise CreateError(f"no AI model is set up: {exc}") from exc
        log.warning("create: no free fallback model (%s)", exc)
    return chosen


def ask(system: str, user: str, schema, *, temperature: float, media: list[tuple[bytes, str]] | None = None) -> str:
    from ..config import Config
    from ..llm.base import LLMRequest, miss_level

    for backend in backends(Config.load()):
        try:
            return backend.complete(LLMRequest(system=system, user=user, temperature=temperature,
                                               response_schema=schema, media=list(media or []))).text
        except Exception as exc:
            log.log(miss_level(exc), "create: %s did not answer (%s)", backend.describe(), str(exc)[:160])
    raise CreateError("no AI model answered; try again in a minute")
