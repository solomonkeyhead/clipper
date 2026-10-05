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


def _chosen(config, model: str | None, job: str) -> list:
    """The provider the user picked for `job` in Settings > Who does what (D148), as a backend, or nothing
    when it's automatic or can't be set up (then the usual order answers)."""
    from ..llm.base import create as create_backend
    from ..llm.claude_code import cli
    from ..runner import _correction_backends

    pick = config.llm.job_providers.get(job, "") if job else ""
    model = model or config.llm.create_model
    try:
        if pick == "claude_api" and os.environ.get("ANTHROPIC_API_KEY", "").strip():
            return [create_backend("anthropic", model=model, max_retries=1, requests_per_minute=config.llm.requests_per_minute,
                                   timeout=CLAUDE_TIMEOUT)]
        if pick == "claude_plan" and cli():
            return [create_backend("claude_code", model=model, max_retries=1, requests_per_minute=60, timeout=CLAUDE_TIMEOUT + 60)]
        if pick == "gemini":
            return _correction_backends(config, "gemini")
        if pick == "ollama":
            return [create_backend("ollama", max_retries=1, requests_per_minute=60, timeout=CLAUDE_TIMEOUT)]
    except Exception as exc:  # not set up: the usual order answers
        log.warning("create: the %s you chose for %s isn't available (%s)", pick, job, exc)
    return []


def backends(config, model: str | None = None, job: str = "") -> list:
    """Who can answer `job`: the user's own pick first (D148), then the usual order."""
    first = _chosen(config, model, job)
    rest = _default_backends(config, model, job)
    names = {b.describe() for b in first}
    return first + [b for b in rest if b.describe() not in names]


def _default_backends(config, model: str | None = None, job: str = "") -> list:
    from ..llm.base import create as create_backend
    from ..llm.claude_code import cli
    from ..runner import _correction_backends

    chosen = []
    model = model or config.llm.create_model
    # The paid API only for the jobs the user allowed it (D142); the rest on their plan or Gemini.
    if model and os.environ.get("ANTHROPIC_API_KEY", "").strip() and job in config.llm.paid_api_jobs:
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


#: A Gemini answer for a whole script or a drawing review takes longer than the 30 s that
#: caption fixes get before falling back (a fallback model would write the script).
GEMINI_PATIENCE = 120.0


def _kept(system: str, user: str, schema, media, temperature: float, job: str):
    """The cache and key for one question: the same words, pictures and job get the same answer."""
    import hashlib

    from ..llm.cache import LLMCache

    cache = LLMCache()
    pictures = ",".join(f"{mime}:{hashlib.sha1(data).hexdigest()}" for data, mime in media or [])
    name = getattr(schema, "__name__", str(schema))
    key = cache.key(backend="create", model="", prompt_key=f"{job}:{name}:{temperature}",
                    payload=f"{system}\x00{user}\x00{pictures}")
    return cache, key


def _valid(schema, text: str) -> bool:
    """Whether an answer fits its schema: only those are kept (a bad one would come back for ever)."""
    from pydantic import TypeAdapter, ValidationError

    if schema is None:
        return bool(text.strip())
    try:
        TypeAdapter(schema).validate_json(text)
    except (ValidationError, ValueError):
        return False
    return True


def ask(system: str, user: str, schema, *, temperature: float, media: list[tuple[bytes, str]] | None = None,
        quick: bool = False, job: str = "", keep: bool = False) -> str:
    """The first model's answer. `job` names what is being done: one in `llm.create_gemini_jobs`
    goes to Gemini first, then Claude if Gemini can't (D136), and is never stopped by
    `create_claude_only`; any other goes to Claude first and stops when Claude can't answer (D117).
    `quick` is for small, many-times jobs (the footage judge), which go to `llm.create_quick_model`
    to spare the plan's usage (D116). `keep`: the answer is remembered by its question, so the same
    question again (a rebuild, a retry, the check pressed twice) costs no call."""
    from ..config import Config
    from ..llm.base import LLMRequest, miss_level

    global last_used
    config = Config.load()
    if keep:
        cache, key = _kept(system, user, schema, media, temperature, job)
        if (hit := cache.get(key)) is not None:
            last_used = hit.model
            return hit.text
    order = backends(config, config.llm.create_quick_model if quick else None, job)
    claude = [b for b in order if b.name in ("anthropic", "claude_code")]
    picked = config.llm.job_providers.get(job, "") if job else ""
    gemini_first = picked in ("gemini", "ollama") if picked else job in config.llm.create_gemini_jobs
    if gemini_first:
        order = [b for b in order if b not in claude] + claude
        for b in order:
            if b not in claude and job != "footage":
                b.timeout = max(b.timeout, GEMINI_PATIENCE)
    for backend in order:
        if config.llm.create_claude_only and claude and backend not in claude and not gemini_first and not picked:
            # Claude was there but didn't answer: stop rather than let Gemini draw (D117).
            why = misses.get(claude[0].describe(), "it didn't answer")
            raise CreateError(f"Claude isn't available right now ({why}). Nothing was changed; try again "
                              "later, or set llm.create_claude_only: false to let Gemini do it")
        try:
            text = backend.complete(LLMRequest(system=system, user=user, temperature=temperature,
                                               response_schema=schema, media=list(media or []))).text
            last_used = backend.describe()
            if keep and _valid(schema, text):
                cache.put(key, text=text, model=last_used)
            return text
        except Exception as exc:
            log.log(miss_level(exc), "create: %s did not answer (%s)", backend.describe(), str(exc)[:160])
            misses[backend.describe()] = str(exc).splitlines()[0][:200] if str(exc) else type(exc).__name__
    raise CreateError("no AI model answered; try again in a minute")
