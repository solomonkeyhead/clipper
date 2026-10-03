"""Asking the model for Create: Claude first -- with an API key, or else through Claude
Code on the user's own plan (D109, D113; Gemini's diagrams were judged worse in
quality and accuracy) -- then the stronger free model,
then the everyday one when it rests (runner._correction_backends), at the
temperature the job wants -- some creativity for ideas and scripts, none for
checking them (Claude takes no temperature; its thinking does the job)."""

from __future__ import annotations

import os

from ..utils.logging import get_logger

log = get_logger(__name__)

#: Which model answered last ("anthropic:claude-opus-5-5"), for the page's notes: two
#: videos were judged before anyone knew whether Claude or Gemini had made them.
last_used = ""
#: Why each model that didn't answer failed, latest per model: shown with the notes, so
#: "Claude Code isn't logged in" is seen on the page instead of a quiet fall to Gemini.
misses: dict[str, str] = {}

#: Claude thinks before it answers: a whole script can take a couple of minutes.
CLAUDE_TIMEOUT = 240.0


class CreateError(RuntimeError):
    """Something Create couldn't do; the message says why, for the page."""


def backends(config) -> list:
    from ..llm.base import create as create_backend
    from ..llm.claude_code import cli
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
    elif model and config.llm.create_via_claude_plan and cli():
        # No key: the user's own Claude plan, through Claude Code (D113).
        chosen.append(create_backend("claude_code", model=model, max_retries=1, requests_per_minute=60,
                                     timeout=CLAUDE_TIMEOUT + 60))
    elif model and config.llm.create_via_claude_plan:
        misses["claude_code"] = "Claude Code isn't installed here, or `claude` isn't on this window's PATH"
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

    global last_used
    for backend in backends(Config.load()):
        try:
            text = backend.complete(LLMRequest(system=system, user=user, temperature=temperature,
                                               response_schema=schema, media=list(media or []))).text
            last_used = backend.describe()
            return text
        except Exception as exc:
            log.log(miss_level(exc), "create: %s did not answer (%s)", backend.describe(), str(exc)[:160])
            misses[backend.describe()] = str(exc).splitlines()[0][:200] if str(exc) else type(exc).__name__
    raise CreateError("no AI model answered; try again in a minute")
