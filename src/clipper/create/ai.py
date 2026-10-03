"""Asking the model for Create: the stronger model first, the everyday one when it
rests (runner._correction_backends), at the temperature the job wants -- some
creativity for ideas and scripts, none for checking them."""

from __future__ import annotations

from ..utils.logging import get_logger

log = get_logger(__name__)


class CreateError(RuntimeError):
    """Something Create couldn't do; the message says why, for the page."""


def ask(system: str, user: str, schema, *, temperature: float, media: list[tuple[bytes, str]] | None = None) -> str:
    from ..config import Config
    from ..llm.base import LLMRequest, miss_level
    from ..runner import _correction_backends

    try:
        backends = _correction_backends(Config.load(), None)
    except Exception as exc:
        raise CreateError(f"no AI model is set up: {exc}") from exc
    for backend in backends:
        try:
            return backend.complete(LLMRequest(system=system, user=user, temperature=temperature,
                                               response_schema=schema, media=list(media or []))).text
        except Exception as exc:
            log.log(miss_level(exc), "create: %s did not answer (%s)", backend.describe(), str(exc)[:160])
    raise CreateError("no AI model answered; try again in a minute")
