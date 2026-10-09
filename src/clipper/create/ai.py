"""Asking the model for Create: Claude first -- with an API key, or else through Claude
Code on the user's own plan (D109, D113; Gemini's diagrams were judged worse in
quality and accuracy) -- then the stronger free model,
then the everyday one when it rests (runner._correction_backends), at the
temperature the job wants -- some creativity for ideas and scripts, none for
checking them (Claude takes no temperature; its thinking does the job)."""

from __future__ import annotations

import os
import re
import time

from ..utils.logging import get_logger

log = get_logger(__name__)

#: Which model answered last ("anthropic:claude-opus-5-5"), for the page's notes: two
#: videos were judged before anyone knew whether Claude or Gemini had made them.
last_used = ""
#: Why each model that didn't answer failed, latest per model: shown with the notes, so
#: "Claude Code isn't logged in" is seen on the page instead of a quiet fall to Gemini.
misses: dict[str, str] = {}
#: When each miss happened, so the page shows a problem only while it's current (D154).
missed_at: dict[str, float] = {}

#: Claude thinks before it answers: a whole script can take a couple of minutes.
CLAUDE_TIMEOUT = 240.0


class CreateError(RuntimeError):
    """Something Create couldn't do; the message says why, for the page."""


def _chosen(config, model: str | None, job: str) -> list:
    """The provider the user picked for `job` in Settings > Who does what (D148), as a backend, or nothing
    when it's automatic or can't be set up (then the usual order answers)."""
    from ..llm.base import create as create_backend
    from ..llm.claude_code import cli

    pick = config.llm.job_providers.get(job, "") if job else ""
    model = model or config.llm.create_model
    try:
        if pick == "claude_api" and os.environ.get("ANTHROPIC_API_KEY", "").strip():
            return [create_backend("anthropic", model=model, max_retries=1, requests_per_minute=config.llm.requests_per_minute,
                                   timeout=CLAUDE_TIMEOUT)]
        if pick == "claude_plan" and cli():
            return [create_backend("claude_code", model=model, max_retries=1, requests_per_minute=60, timeout=CLAUDE_TIMEOUT + 60)]
        if pick == "gemini":
            return free_backends(config, "gemini")
        if pick == "ollama":
            return [create_backend("ollama", max_retries=1, requests_per_minute=60, timeout=CLAUDE_TIMEOUT)]
    except Exception as exc:  # not set up: the usual order answers
        log.warning("create: the %s you chose for %s isn't available (%s)", pick, job, exc)
    return []


def free_backends(config, override: str | None = None) -> list:
    """The free models Create asks: on Gemini, `llm.create_gemini_models` in turn, best first (D162); else
    (or with that list empty) the clipping ones, the correction model then the scoring model. Then, unless
    one provider was asked for, the other free ones with a key in .env (`llm.create_free_models`, D164)."""
    from ..llm.base import create as create_backend
    from ..llm.openai_compat import has_key
    from ..runner import _correction_backends

    out = []
    if (override or config.llm.backend) == "gemini" and config.llm.create_gemini_models:
        for k, model in enumerate(config.llm.create_gemini_models):
            try:
                # The best one gets a second try: a lone 503 sent it resting for 15 minutes (D164). A rate limit
                # longer than a few seconds goes to the next model instead of being waited out (D168).
                out.append(create_backend("gemini", model=model, max_retries=0 if k else 1,
                                          requests_per_minute=config.llm.requests_per_minute,
                                          timeout=config.llm.correction_timeout, max_wait=PATIENCE))
            except Exception as exc:  # no key: the usual ones say why
                log.debug("create: %s not set up (%s)", model, exc)
                break
    if not out:
        try:
            out = _correction_backends(config, override)
        except Exception:
            if override or not any(has_key(p) for p in config.llm.create_free_models):
                raise
    if not override:
        for provider, models in config.llm.create_free_models.items():
            if has_key(provider):
                for model in [models] if isinstance(models, str) else models:
                    out.append(create_backend("openai_compat", provider=provider, model=model, max_retries=0,
                                              requests_per_minute=10, timeout=config.llm.correction_timeout,
                                              max_wait=PATIENCE))
    return out


#: The longest rate-limit wait a Create model is waited for in place (D168): another model answers sooner.
PATIENCE = 10.0


def backends(config, model: str | None = None, job: str = "") -> list:
    """Who can answer `job`: the user's own pick first (D148), then the usual order."""
    first = _chosen(config, model, job)
    rest = _default_backends(config, model, job)
    names = {b.describe() for b in first}
    return first + [b for b in rest if b.describe() not in names]


def _default_backends(config, model: str | None = None, job: str = "") -> list:
    from ..llm.base import create as create_backend
    from ..llm.claude_code import cli

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
        missed_at["claude_code"] = time.time()
    try:
        chosen += free_backends(config)
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
        TypeAdapter(schema).validate_json(re.sub(r"^\s*```(?:json)?\s*|\s*```\s*$", "", text))
    except (ValidationError, ValueError):
        return False
    return True


def _repaired(schema, text: str) -> str | None:
    """A weaker model's answer with the JSON inside it (prose before or after, a fence, a thinking block) cut
    out, if that fits the schema (D167)."""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)
    start, end = text.find("{"), text.rfind("}")
    if 0 <= start < end and (cut := text[start:end + 1]) != text and _valid(schema, cut):
        return cut
    return None


def _ordered(config, job: str = "", quick: bool = False):
    """Who answers `job`, in the order asked: (order, the Claude ones, the user's pick, Gemini first?)."""
    order = backends(config, config.llm.create_quick_model if quick else None, job)
    claude = [b for b in order if b.name in ("anthropic", "claude_code")]
    picked = config.llm.job_providers.get(job, "") if job else ""
    gemini_first = picked in ("gemini", "ollama") if picked else job in config.llm.create_gemini_jobs
    if gemini_first:
        order = [b for b in order if b not in claude] + claude
    return order, claude, picked, gemini_first


def _family(name: str) -> str:
    """Claude on the plan and on the API are one model family: neither is a second opinion on the other."""
    name = name.split(":", 1)[0]
    return "claude" if name in ("anthropic", "claude_code") else name


def first_choice(config, job: str) -> str:
    """The provider `job` goes to first right now ("claude_code", "gemini"...), for Settings' "Automatic
    (now ...)" (D154). Makes no model call."""
    try:
        order = _ordered(config, job)[0]
    except Exception:  # nothing set up: Settings says so elsewhere
        return ""
    return order[0].name if order else ""


def ask(system: str, user: str, schema, *, temperature: float, media: list[tuple[bytes, str]] | None = None,
        quick: bool = False, job: str = "", keep: bool = False, unlike: str = "") -> str:
    """The first model's answer. `job` names what is being done: one in `llm.create_gemini_jobs`
    goes to Gemini first, then Claude if Gemini can't (D136), and is never stopped by
    `create_claude_only`; any other goes to Claude first and stops when Claude can't answer (D117).
    `quick` is for small, many-times jobs (the footage judge), which go to `llm.create_quick_model`
    to spare the plan's usage (D116). `keep`: the answer is remembered by its question, so the same
    question again (a rebuild, a retry, the check pressed twice) costs no call. `unlike` (a `last_used`
    value): another AI family answers first if one is set up, as for the editor's read of a script (D155)."""
    from ..config import Config
    from ..llm.base import LLMRequest, miss_level

    global last_used
    config = Config.load()
    if keep:
        cache, key = _kept(system, user, schema, media, temperature, job)
        if (hit := cache.get(key)) is not None:
            last_used = hit.model
            return hit.text
    order, claude, picked, gemini_first = _ordered(config, job, quick)
    if unlike:
        order = [b for b in order if _family(b.name) != _family(unlike)] + [b for b in order if _family(b.name) == _family(unlike)]
    if gemini_first:
        for b in order:
            if b not in claude and job != "footage":
                b.timeout = max(b.timeout, GEMINI_PATIENCE)
    unread = None   # the first answer that didn't fit its schema, handed back if no other does (D167)
    started, missed = time.monotonic(), []   # what each model that didn't answer cost, for the log (D168)
    for backend in order:
        if config.llm.create_claude_only and claude and backend not in claude and not gemini_first and not picked and not unlike:
            # Claude was there but didn't answer: stop rather than let Gemini draw (D117).
            if unread:
                break
            why = misses.get(claude[0].describe(), "it didn't answer")
            raise CreateError(f"Claude isn't available right now ({why}). Nothing was changed; try again "
                              "later, or set llm.create_claude_only: false to let Gemini do it")
        asked_at = time.monotonic()
        try:
            text = backend.complete(LLMRequest(system=system, user=user, temperature=temperature,
                                               response_schema=schema, media=list(media or []))).text
            if schema is not None and not _valid(schema, text):
                if (fixed := _repaired(schema, text)) is not None:
                    text = fixed
                else:
                    # A weaker model's answer that can't be read: ask the next one rather than fail (D167).
                    unread = unread or (text, backend.describe())
                    log.warning("create: %s answered in a shape that can't be read (%d characters: %r ... %r); "
                                "asking the next", backend.describe(), len(text), text[:120], text[-80:])
                    misses[backend.describe()] = "its answer wasn't in the shape asked for"   # no rest: it answered
                    missed.append(f"{backend.describe()} unreadable {time.monotonic() - asked_at:.1f}s")
                    continue
            last_used = backend.describe()
            misses.pop(last_used, None)  # it answers again: that problem is over
            if keep and _valid(schema, text):
                cache.put(key, text=text, model=last_used)
            log.info("create: %s answered by %s in %.1fs%s", job or "a question", last_used,
                     time.monotonic() - started, f" (before it: {', '.join(missed)})" if missed else "")
            return text
        except Exception as exc:
            log.log(miss_level(exc), "create: %s did not answer (%s)", backend.describe(), str(exc)[:160])
            misses[backend.describe()] = str(exc).splitlines()[0][:200] if str(exc) else type(exc).__name__
            missed_at[backend.describe()] = time.time()
            if (spent := time.monotonic() - asked_at) >= 0.5:   # a resting model costs nothing: not worth a mention
                missed.append(f"{backend.describe()} {spent:.1f}s")
    if unread:
        last_used = unread[1]
        return unread[0]
    raise CreateError("no AI model answered; try again in a minute")
