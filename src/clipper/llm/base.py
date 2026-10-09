"""The LLM backend interface, rate limiting and retries.

Backends differ in one way that matters: whether the provider can enforce a
response schema server-side. Gemini can (`response_schema`), Ollama can (its
`format` parameter), a bare completion API cannot. Backends that can are far
more reliable, so the interface exposes it rather than hiding it -- and the
rubric scorer still validates every response with pydantic regardless.

Rate limiting is client-side and deliberately conservative: the default backend
is a free tier, and the failure mode for exceeding it is a run that dies
part-way rather than a bill.
"""

from __future__ import annotations

import random
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, ClassVar

from pydantic import BaseModel

from ..utils.logging import get_logger

log = get_logger(__name__)


class LLMError(RuntimeError):
    """A backend could not produce a usable response."""


class Resting(LLMError):
    """A try-first model sitting out after a failure: expected, and the fallback answers."""


def miss_level(exc: Exception) -> int:
    """How loudly to log a backend that didn't answer: a resting model is routine."""
    import logging

    return logging.DEBUG if isinstance(exc, Resting) else logging.WARNING


class LLMConfigError(LLMError):
    """The backend is missing a key, model, or server. Not retryable."""


class ContentBlocked(LLMError):
    """The provider refused the input itself. Retrying cannot change that.

    Seen on real sitcom transcripts: Gemini blocked a batch of eight candidates
    as PROHIBITED_CONTENT -- a category its safety settings cannot relax -- and
    the run retried it five times with backoff before giving up.
    """


class RateLimited(LLMError):
    """The provider refused for quota reasons. Retryable with backoff."""

    def __init__(self, message: str, *, retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after


#: A quota wait longer than this is a day's limit, not a minute's: the model sits it out and the next one
#: answers (D168). Gemini's free tier said "retry in 20h25m" and each call asked again, twice.
LONG_WAIT = 60.0
#: How long a try-first model sits out a minute's rate limit that names no wait of its own (D168).
SHORT_REST = 60.0
#: How long a try-first model that has a retry of its own sits out a failure (D188).
FAILED_REST = 5 * 60.0


def _timed_out(exc: Exception) -> bool:
    """Whether a call ran out of time, on our side or the provider's (a 504 came after the full 120 s)."""
    text = str(exc).lower()
    return "timed out" in text or "504" in text or "deadline_exceeded" in text


@dataclass
class LLMRequest:
    """One completion request."""

    system: str
    user: str
    temperature: float = 0.2
    max_output_tokens: int | None = None
    # When set and the backend supports it, the provider enforces this schema.
    # Either a pydantic model class or `list[Model]` -- the google-genai SDK
    # rejects a plain `[Model]` list literal with a pydantic ValidationError.
    response_schema: type[BaseModel] | Any | None = None
    # Video or image to look at alongside `user`: (bytes, mime type) pairs. Only
    # backends with `supports_video` accept them (signals/visual.py).
    media: list[tuple[bytes, str]] = field(default_factory=list)


@dataclass
class LLMResponse:
    text: str
    model: str
    prompt_tokens: int = 0
    output_tokens: int = 0
    latency: float = 0.0


@dataclass
class UsageStats:
    """Cumulative cost of a run, reported at the end (section 15 / Phase 3)."""

    calls: int = 0
    cached: int = 0
    failed: int = 0
    prompt_tokens: int = 0
    output_tokens: int = 0
    total_latency: float = 0.0
    rate_limit_waits: float = 0.0

    def record(self, response: LLMResponse) -> None:
        self.calls += 1
        self.prompt_tokens += response.prompt_tokens
        self.output_tokens += response.output_tokens
        self.total_latency += response.latency

    @property
    def mean_latency(self) -> float:
        return self.total_latency / self.calls if self.calls else 0.0

    def summary(self) -> str:
        return (
            f"{self.calls} LLM call(s), {self.cached} cache hit(s), {self.failed} failed; "
            f"{self.prompt_tokens} in / {self.output_tokens} out tokens; "
            f"mean latency {self.mean_latency:.2f}s"
            + (f"; {self.rate_limit_waits:.0f}s waiting on rate limits"
               if self.rate_limit_waits else "")
        )


class RateLimiter:
    """A simple requests-per-minute gate, safe across threads.

    `shared` hands every backend for the same provider the same gate (D75): two
    jobs at once, or a job's parallel calls, share the account's per-minute
    budget instead of each assuming it has all of it and drawing 429s.
    """

    _shared: ClassVar[dict[tuple[str, int], RateLimiter]] = {}
    _shared_lock: ClassVar[threading.Lock] = threading.Lock()

    @classmethod
    def shared(cls, provider: str, requests_per_minute: int) -> RateLimiter:
        with cls._shared_lock:
            key = (provider, requests_per_minute)
            if key not in cls._shared:
                cls._shared[key] = cls(requests_per_minute)
            return cls._shared[key]

    def __init__(self, requests_per_minute: int):
        self.min_interval = 60.0 / max(1, requests_per_minute)
        self._lock = threading.Lock()
        self._last = 0.0

    def acquire(self) -> float:
        """Block until the next request is allowed. Returns seconds waited."""
        with self._lock:
            now = time.monotonic()
            wait = max(0.0, self._last + self.min_interval - now)
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()
            return wait


class LLMBackend(ABC):
    """Base class handling retries, backoff and rate limiting.

    Subclasses implement `_complete` and, if needed, `_is_rate_limit`.
    """

    name: ClassVar[str] = "base"
    supports_schema: ClassVar[bool] = False
    supports_video: ClassVar[bool] = False

    def __init__(
        self,
        *,
        model: str | None = None,
        max_retries: int = 5,
        requests_per_minute: int = 10,
        # A hung request cost 2+ minutes at 120s; calls, video included, take ~5-30s (D75).
        timeout: float = 60.0,
        # The longest rate-limit wait sat out in place before retrying; a longer one goes to the next model.
        max_wait: float = LONG_WAIT,
    ):
        self.model = model or ""
        self.max_retries = max_retries
        self.timeout = timeout
        self.max_wait = max_wait
        # One budget per model: a provider's free quotas are per model, and four Gemini models sharing one
        # 6-second gap made each fall-through wait for the one before (D168).
        self.limiter = RateLimiter.shared(f"{self.name}:{self.model}" if self.model else self.name, requests_per_minute)
        self.usage = UsageStats()

    @abstractmethod
    def _complete(self, request: LLMRequest) -> LLMResponse:
        """Perform one call. Raise `RateLimited` for quota errors."""

    def describe(self) -> str:
        return f"{self.name}:{self.model or 'auto'}"

    def resolve_model(self) -> str:
        """The concrete model this backend will call. Runtime-choosing backends override."""
        return self.model

    def cache_model(self) -> str:
        """The model name to put in a cache key.

        Resolved *before* use. Keys used to be built from `self.model`, which a
        backend that picks its model at runtime leaves empty until its first
        call -- so every lookup, made before any call, used "auto", while every
        entry, written after one, used the real name. Measured: the key for a
        cached candidate missed before resolution and hit after it. The cache
        had never served a scoring request, so each run re-scored everything
        (15 minutes and repeated quota errors on an 84-minute source) and the
        ratings changed from run to run.
        """
        try:
            return self.resolve_model() or "auto"
        except Exception:  # offline, no key: the key must still be computable
            return self.model or "auto"

    #: (backend, model) -> when a "try first" model, or one out of quota, may be tried again (D75, D168).
    _resting: ClassVar[dict[tuple[str, str], float]] = {}
    #: Why it rests, for the message, and the ones out of quota, which rest for every caller.
    _why: ClassVar[dict[tuple[str, str], str]] = {}
    _out: ClassVar[set[tuple[str, str]]] = set()
    #: How long a try-first model that failed sits out.
    REST_SECONDS: ClassVar[float] = 15 * 60

    def complete(self, request: LLMRequest) -> LLMResponse:
        """Call the provider, retrying transient failures with backoff.

        A backend with no retries is a "try first, fall back" model (the stronger
        preview models for caption fixes and descriptions). When one fails it
        rests for REST_SECONDS: an overloaded preview model timed out on every
        clip, costing about a minute a clip before the fallback answered. A rate
        limit rests it only as long as the provider said (D168), and a quota out
        for hours rests any model that long, so no call asks it again meanwhile.
        """
        key = (self.name, self.model)
        # A failure or a minute's limit rests only the models that fall through rather than wait (the same model
        # as a run's main one waits it out); a quota out for hours rests every one.
        falls_through = self.max_retries == 0 or self.max_wait < LONG_WAIT
        if time.monotonic() < self._resting.get(key, 0.0) and (falls_through or key in self._out):
            raise Resting(f"{self.describe()} {self._why.get(key, 'is resting after a recent failure')}")
        try:
            return self._complete_with_retries(request)
        except LLMError as exc:
            limited = exc if isinstance(exc, RateLimited) else exc.__cause__
            wait = (getattr(limited, "retry_after", None) or 0.0) if isinstance(limited, RateLimited) else None
            if wait is not None and wait > LONG_WAIT:
                # Asked again after an hour at most, in case the quota comes back sooner than it said.
                until = time.strftime("%H:%M", time.localtime(time.time() + wait))
                self._rest(key, min(wait, 3600), f"is out of its quota until {until}", everyone=True)
            elif wait is not None and (falls_through or wait > self.max_wait):
                self._rest(key, max(wait, SHORT_REST), "is rate limited for a minute")
            elif self.max_retries == 0:
                self._rest(key, self.REST_SECONDS, "is resting after a recent failure")
            elif falls_through:
                # The try-first model with a retry of its own rested after nothing (D188): an overloaded Gemini 3.8
                # Flash made every call of a script wait two 120 s timeouts on it again, 20 minutes a script.
                self._rest(key, FAILED_REST, "is resting after a recent failure")
            raise

    def _rest(self, key: tuple[str, str], seconds: float, why: str, everyone: bool = False) -> None:
        self._resting[key] = time.monotonic() + seconds
        self._why[key] = why
        (self._out.add if everyone else self._out.discard)(key)

    def _complete_with_retries(self, request: LLMRequest) -> LLMResponse:
        self.usage.rate_limit_waits += self.limiter.acquire()

        last: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                response = self._complete(request)
                self.usage.record(response)
                return response
            except (LLMConfigError, ContentBlocked):
                raise  # a missing key, or a refused input, will not fix itself
            except RateLimited as exc:
                last = exc
                delay = exc.retry_after if exc.retry_after else self._backoff(attempt)
                if delay > self.max_wait:
                    self.usage.failed += 1
                    raise   # hours, or longer than this model is worth waiting for: complete() rests it (D168)
                if attempt == self.max_retries:
                    break
                log.warning(
                    "%s rate limited (attempt %d/%d); waiting %.0fs",
                    self.describe(), attempt + 1, self.max_retries, delay,
                )
                time.sleep(delay)
                self.usage.rate_limit_waits += delay
            except LLMError as exc:
                last = exc
                if attempt == self.max_retries or _timed_out(exc):
                    break   # a model that couldn't answer in the time given won't in the next try either (D188)
                delay = self._backoff(attempt)
                log.warning(
                    "%s call failed (attempt %d/%d): %s; retrying in %.1fs",
                    self.describe(), attempt + 1, self.max_retries, exc, delay,
                )
                time.sleep(delay)

        self.usage.failed += 1
        raise LLMError(
            f"{self.describe()} failed after {self.max_retries + 1} attempts: {last}"
        ) from last

    @staticmethod
    def _backoff(attempt: int) -> float:
        """Exponential backoff with jitter, capped so a run cannot stall for ever."""
        base = min(60.0, 2.0 ** attempt)
        return base * (0.5 + random.random() * 0.5)


class MockBackend(LLMBackend):
    """Deterministic backend for tests.

    Scores are derived from a hash of the candidate text, so the whole scoring
    pipeline is reproducible without a network call or an API key. Sentences
    containing the words in `boost` score higher, which lets a test assert that
    a genuinely better clip outranks a worse one.
    """

    name: ClassVar[str] = "mock"
    supports_schema: ClassVar[bool] = True
    supports_video: ClassVar[bool] = True

    #: Words that push a candidate's scores up, simulating a good moment.
    BOOST: ClassVar[tuple[str, ...]] = (
        "mistake", "lost", "changed everything", "nobody", "why", "secret",
        "never", "truth", "actually", "percent",
    )
    #: Words that push scores down.
    PENALTY: ClassVar[tuple[str, ...]] = ("um", "anyway", "as i said", "uh")

    def __init__(self, *, responses: list[str] | None = None, fail_times: int = 0, **kwargs):
        kwargs["requests_per_minute"] = 100_000
        super().__init__(**kwargs)
        # Forced, not defaulted: callers pass the configured RPM explicitly, and
        # a throttled mock makes the test suite sleep for no reason.
        self.limiter = RateLimiter(100_000)
        self.model = self.model or "mock-1"
        self.scripted = list(responses or [])
        self.fail_times = fail_times
        self.calls: list[LLMRequest] = []

    def _complete(self, request: LLMRequest) -> LLMResponse:
        self.calls.append(request)
        if self.fail_times > 0:
            self.fail_times -= 1
            raise RateLimited("mock rate limit")
        text = self.scripted.pop(0) if self.scripted else self._synthesise(request)
        return LLMResponse(text=text, model=self.model, prompt_tokens=len(request.user) // 4,
                           output_tokens=len(text) // 4, latency=0.0)

    def _synthesise(self, request: LLMRequest) -> str:
        import hashlib
        import json
        import re

        blocks = re.findall(r"^\[(\d+)\]\s*(.*?)(?=^\[\d+\]|\Z)", request.user,
                            re.MULTILINE | re.DOTALL)
        items = []
        for index, body in blocks:
            lowered = body.lower()
            seed = int(hashlib.blake2b(body.strip().encode(), digest_size=4).hexdigest(), 16)
            base = 3 + seed % 3  # 3-5, matching the "be harsh" instruction
            base += 3 * sum(1 for w in self.BOOST if w in lowered)
            base -= sum(1 for w in self.PENALTY if w in lowered)
            clamp = lambda v: max(0, min(10, int(v)))  # noqa: E731
            items.append({
                "index": int(index),
                "hook_strength": clamp(base),
                "standalone_clarity": clamp(base - 1),
                "payoff": clamp(base),
                "emotional_intensity": clamp(base - 1),
                "quotability": clamp(base - 2),
                "ending_completeness": clamp(base + 1),
                "needs_prior_context": "as i said" in lowered,
                "is_sponsor_or_ad": "promo code" in lowered or "sponsored by" in lowered,
                "policy_risk": "none",
                "hook_text": " ".join(body.split()[1:7]),  # skip the "(26s)" prefix
                "suggested_caption": "A mock caption",
                "hashtags": ["#mock", "#test", "#clip"],
            })
        return json.dumps(items)


_REGISTRY: dict[str, type[LLMBackend]] = {}


def register(cls: type[LLMBackend]) -> type[LLMBackend]:
    _REGISTRY[cls.name] = cls
    return cls


def available_backends() -> list[str]:
    _load_builtins()
    return sorted(_REGISTRY)


def _load_builtins() -> None:
    """Import backend modules lazily, so a missing optional SDK is not fatal."""
    from . import claude_code, gemini, ollama  # noqa: F401

    try:
        from . import anthropic_backend  # noqa: F401
    except ImportError:  # pragma: no cover - optional paid dependency
        log.debug("anthropic SDK not installed; that backend is unavailable")


def create(name: str, **kwargs) -> LLMBackend:
    """Instantiate a backend by name."""
    _load_builtins()
    _REGISTRY.setdefault(MockBackend.name, MockBackend)
    try:
        cls = _REGISTRY[name]
    except KeyError:
        raise LLMConfigError(
            f"unknown LLM backend {name!r}; available: {', '.join(available_backends())}"
        ) from None
    return cls(**kwargs)


register(MockBackend)
