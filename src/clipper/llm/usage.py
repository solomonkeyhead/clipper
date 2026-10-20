"""What every AI call costs, and the stop when a user's plan has spent its AI allowance (D227).

Each backend records a row in `<data>/usage.sqlite` after every answer: who answered, which job asked, the
tokens and their price from PRICES. Hosted, the gateway writes `plan.json` with `ai_budget_usd`, and
`before_call` refuses a call once this period's rows add up to it: a safety net under the plan's minutes and
Shorts, so a loop or a bug can't run up a bill. No plan.json (the owner's own computer): no limit.

Claude Code rows carry what the call would cost on the API; on the owner's plan nothing is charged per call.
"""

from __future__ import annotations

import contextlib
import contextvars
import json
import sqlite3
import time
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

from ..utils.logging import get_logger
from .base import LLMError

log = get_logger(__name__)

#: $ per 1M tokens (input, output, cache read, cache write) and $ per audio hour, as of October 2026.
#: Thinking tokens are billed as output. Gemini 3.x Flash doubles on 2027-01-01: update these rows then.
PRICES: dict[str, tuple[float, float, float, float, float]] = {
    "claude-opus-5-5": (4.00, 20.00, 0.20, 5.00, 0.0),
    "claude-sonnet-5-5": (2.00, 10.00, 0.20, 2.50, 0.0),
    "claude-haiku-5-5": (0.10, 0.50, 0.01, 0.125, 0.0),
    "gemini-3.8-flash": (0.75, 3.75, 0.075, 0.0, 0.0),
    "gemini-3.7-flash": (0.75, 3.75, 0.075, 0.0, 0.0),
    "gemini-3.6-flash": (0.75, 3.75, 0.075, 0.0, 0.0),
    "gemini-3.5-flash": (0.75, 3.75, 0.075, 0.0, 0.0),
    "gemini-flash-latest": (0.75, 3.75, 0.075, 0.0, 0.0),
    "gemini-3-flash-preview": (0.50, 3.00, 0.05, 0.0, 0.0),
    "gemini-flash-lite-latest": (0.30, 2.50, 0.03, 0.0, 0.0),
    "gemini-3.5-flash-lite": (0.30, 2.50, 0.03, 0.0, 0.0),
    "gemini-2.5-flash-lite": (0.10, 0.40, 0.01, 0.0, 0.0),
    "whisper-large-v3-turbo": (0.0, 0.0, 0.0, 0.0, 0.04),
    "whisper-large-v3": (0.0, 0.0, 0.0, 0.0, 0.111),
}
#: Which models a provider's unknown model is priced among (the dearest of them).
_FAMILY = {"anthropic": "claude-", "claude_code": "claude-", "gemini": "gemini-", "groq": "whisper-"}
_unknown_said: set[str] = set()

_ref: contextvars.ContextVar[str | None] = contextvars.ContextVar("clipper_usage_ref", default=None)


class BudgetExceeded(LLMError):
    """The plan's AI allowance for this period is used up: no call is made, none is retried or passed on."""


def db_path() -> Path:
    from ..paths import data_root

    return data_root() / "usage.sqlite"


def _connect() -> sqlite3.Connection:
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=10)
    db.execute("CREATE TABLE IF NOT EXISTS ai_calls (at REAL, provider TEXT, model TEXT, job TEXT, ref TEXT, "
               "input_tokens INT, output_tokens INT, cache_read INT, cache_write INT, audio_seconds REAL, usd REAL)")
    return db


def price(provider: str, model: str) -> tuple[float, float, float, float, float]:
    """The model's prices; an unknown one is priced as its provider's dearest, so a new model is never free."""
    if model in PRICES:
        return PRICES[model]
    prefix = _FAMILY.get(provider)
    kin = [p for m, p in PRICES.items() if prefix and m.startswith(prefix)]
    if not kin:
        return (0.0, 0.0, 0.0, 0.0, 0.0)   # a local model (Ollama): no bill
    if model not in _unknown_said:
        _unknown_said.add(model)
        log.warning("usage: no price for %s model %r; counted at the dearest %s price until PRICES has it",
                    provider, model, provider)
    return max(kin, key=lambda p: (p[1], p[0], p[4]))


def cost(provider: str, model: str, *, input_tokens: int = 0, output_tokens: int = 0, cache_read: int = 0,
         cache_write: int = 0, audio_seconds: float = 0.0) -> float:
    p_in, p_out, p_read, p_write, p_hour = price(provider, model)
    return (input_tokens * p_in + output_tokens * p_out + cache_read * p_read + cache_write * p_write) / 1e6 \
        + audio_seconds / 3600 * p_hour


def record(provider: str, model: str, job: str, *, input_tokens: int = 0, output_tokens: int = 0,
           cache_read: int = 0, cache_write: int = 0, audio_seconds: float = 0.0, ref: str | None = None) -> float:
    """Store one call and return its cost in USD. Never raises: a full disk mustn't lose the answer."""
    usd = 0.0
    try:
        usd = cost(provider, model, input_tokens=input_tokens, output_tokens=output_tokens, cache_read=cache_read,
                   cache_write=cache_write, audio_seconds=audio_seconds)
        with contextlib.closing(_connect()) as db, db:
            db.execute("INSERT INTO ai_calls VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                       (time.time(), provider, model, job or "", ref if ref is not None else _ref.get(),
                        int(input_tokens), int(output_tokens), int(cache_read), int(cache_write),
                        float(audio_seconds), usd))
    except Exception as exc:
        log.warning("usage: couldn't record a %s call (%s)", provider, exc)
    return usd


@contextlib.contextmanager
def tag(ref: str) -> Iterator[None]:
    """Calls made inside carry `ref` ("video:33", "source:<id>"), to see what one video or source cost.
    A context variable: a thread started inside doesn't inherit it unless run with contextvars.copy_context()."""
    token = _ref.set(ref)
    try:
        yield
    finally:
        _ref.reset(token)


def spent_usd(since: float) -> float:
    try:
        with contextlib.closing(_connect()) as db:
            return float(db.execute("SELECT COALESCE(SUM(usd), 0) FROM ai_calls WHERE at >= ?", (since,)).fetchone()[0])
    except sqlite3.Error as exc:
        log.warning("usage: couldn't read %s (%s)", db_path(), exc)
        return 0.0


def summary(since: float) -> list[dict]:
    """Calls, tokens and USD per job and model since `since`, dearest first."""
    with contextlib.closing(_connect()) as db:
        db.row_factory = sqlite3.Row
        rows = db.execute(
            "SELECT job, model, COUNT(*) AS calls, SUM(input_tokens) AS input_tokens, "
            "SUM(output_tokens) AS output_tokens, SUM(cache_read) AS cache_read, SUM(cache_write) AS cache_write, "
            "SUM(audio_seconds) AS audio_seconds, SUM(usd) AS usd FROM ai_calls WHERE at >= ? "
            "GROUP BY job, model ORDER BY usd DESC", (since,)).fetchall()
    return [dict(r) for r in rows]


def before_call(job: str) -> None:
    """Raise BudgetExceeded when plan.json's `ai_budget_usd` is spent for this period. Called before every request.
    A plan.json that can't be read stops the call too: on a server, failing open would spend without a limit."""
    from ..paths import data_root

    path = data_root() / "plan.json"
    if not path.is_file():
        return
    try:
        plan = json.loads(path.read_text(encoding="utf-8"))
        budget = plan.get("ai_budget_usd")
        if budget is None:
            return
        start = datetime.fromisoformat(plan["period_start"]).timestamp()
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        log.error("usage: plan.json can't be read (%s); no AI call is made", exc)
        raise BudgetExceeded("Your plan's details couldn't be read, so nothing was sent to the AI. "
                             "Try again in a minute, or contact support if it keeps happening.") from exc
    if spent_usd(start) >= float(budget):
        renews = str(plan.get("period_end", ""))[:10]
        log.warning("usage: AI budget of $%.2f reached; %s not asked", float(budget), job or "a call")
        raise BudgetExceeded("Your plan's AI allowance for this period is used up, so nothing more was asked of "
                             f"the AI{' (it renews on ' + renews + ')' if renews else ''}. "
                             "See Billing to add more.")
