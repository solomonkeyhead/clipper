"""The LLM rubric signal (BUILD_BRIEF.md section 9.1).

Each candidate is scored by two prompts at low temperature, batched to bound
cost. The combination is::

    llm_score = mean(A, B) - disagreement_penalty * |A - B|

where each side is a weighted rubric sum on the original 0-10 scale.

Keeping the result on an **absolute** 0-10 scale matters beyond this module.
Every other signal becomes a per-video percentile rank, which by construction
says nothing about whether a video contains anything good at all. The raw rubric
total is the one quantity that can say "nothing here is worth clipping", so
`select/pick.py` gates on it. See PLAN.md P1.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from pydantic import BaseModel, Field, ValidationError

from ..config import LLMConfig
from ..llm.base import ContentBlocked, LLMBackend, LLMConfigError, LLMError, LLMRequest
from ..llm.cache import LLMCache
from ..llm.prompts import (
    PROMPT_A,
    PROMPT_B,
    REPAIR_SYSTEM,
    PromptVariant,
    build_user_message,
    with_focus,
    with_language,
    with_taste,
)
from ..models import Candidate, RubricScores
from ..utils.logging import get_logger

log = get_logger(__name__)


class RubricItem(BaseModel):
    """The per-candidate object the model is asked to return.

    Separate from `RubricScores` because it carries `index`, which is transport
    (matching a response back to its candidate), not a score.
    """

    index: int
    hook_strength: int = Field(ge=0, le=10)
    standalone_clarity: int = Field(ge=0, le=10)
    payoff: int = Field(ge=0, le=10)
    emotional_intensity: int = Field(ge=0, le=10)
    quotability: int = Field(ge=0, le=10)
    ending_completeness: int = Field(ge=0, le=10)
    needs_prior_context: bool = False
    is_sponsor_or_ad: bool = False
    policy_risk: str = "none"
    hook_text: str = ""
    suggested_caption: str = ""
    hashtags: list[str] = Field(default_factory=list)

    def to_scores(self) -> RubricScores:
        risk = self.policy_risk.strip().lower()
        return RubricScores(
            hook_strength=self.hook_strength,
            standalone_clarity=self.standalone_clarity,
            payoff=self.payoff,
            emotional_intensity=self.emotional_intensity,
            quotability=self.quotability,
            ending_completeness=self.ending_completeness,
            needs_prior_context=self.needs_prior_context,
            is_sponsor_or_ad=self.is_sponsor_or_ad,
            policy_risk=risk if risk in ("none", "low", "high") else "none",
            hook_text=self.hook_text.strip(),
            suggested_caption=self.suggested_caption.strip(),
            hashtags=[h.strip() for h in self.hashtags if h.strip()],
        )


@dataclass
class LLMSignalResult:
    """Per-candidate rubric outcomes plus what it cost."""

    scores: dict[str, tuple[RubricScores | None, RubricScores | None]] = field(default_factory=dict)
    totals: dict[str, float] = field(default_factory=dict)
    drops: dict[str, str] = field(default_factory=dict)
    unscored: list[str] = field(default_factory=list)

    @property
    def scored_count(self) -> int:
        return len(self.totals)


def score_candidates(
    candidates: list[Candidate],
    backend: LLMBackend,
    cfg: LLMConfig,
    *,
    cache: LLMCache | None = None,
    examples: list[dict] | None = None,
) -> LLMSignalResult:
    """Score every candidate with prompt A and, if enabled, prompt B."""
    result = LLMSignalResult()
    if not candidates:
        return result

    cache = cache if cache is not None else LLMCache()
    variants = [with_language(with_taste(with_focus(v, cfg.campaign_focus), cfg.user_taste), cfg.language)
                for v in [PROMPT_A] + ([PROMPT_B] if cfg.use_second_opinion else [])]

    per_variant: dict[str, dict[str, RubricScores]] = {}
    for variant in variants:
        # Keyed by "a"/"b": a campaign focus lengthens the variant's own key.
        per_variant[variant.key.split(":")[0]] = _score_with_variant(
            candidates, backend, cfg, variant, cache=cache, examples=examples,
        )

    weights = cfg.rubric_weights.as_dict()
    for candidate in candidates:
        cid = candidate.candidate_id
        a = per_variant.get("a", {}).get(cid)
        b = per_variant.get("b", {}).get(cid)
        result.scores[cid] = (a, b)

        if a is None and b is None:
            result.unscored.append(cid)
            continue

        drop = _hard_drop_reason(a, b, single_opinion=not cfg.use_second_opinion,
                                 drop_context=cfg.drop_needs_prior_context)
        if drop:
            result.drops[cid] = drop
            continue

        result.totals[cid] = combine_totals(a, b, weights, cfg.disagreement_penalty)

    _spread_content_drops(candidates, result)

    if result.unscored:
        log.warning(
            "%d of %d candidates could not be scored by the LLM and will be "
            "excluded: %s",
            len(result.unscored), len(candidates), ", ".join(result.unscored[:8]),
        )
    return result


# A candidate sharing at least this share of its own duration with a window
# dropped for its *content* is dropped too.
CONTENT_DROP_OVERLAP = 0.5

# Drops that are about what is said, and so hold for any window containing it.
# "Needs prior context" is deliberately absent: a longer window can supply the
# missing context, so that verdict belongs to the window, not the content.
CONTENT_DROPS = ("high policy risk", "flagged as a sponsor read or advertisement")


def _spread_content_drops(candidates: list[Candidate], result: LLMSignalResult) -> None:
    """Drop every window that mostly overlaps one dropped for its content.

    The raters judge each window separately, and their verdict on the *same*
    material shifts with where the window starts and stops. Measured on a real
    source: four overlapping windows of one story about a suicide attempt, all
    containing the word; three were rated high policy risk by one prompt and
    dropped, the fourth -- trimmed by a few seconds -- was rated low by both,
    survived, and was selected first. The risk is in the story, not the window.
    """
    by_id = {c.candidate_id: c for c in candidates}
    flagged = [(by_id[cid], reason) for cid, reason in result.drops.items()
               if reason in CONTENT_DROPS and cid in by_id]
    if not flagged:
        return
    for cid in list(result.totals):
        candidate = by_id[cid]
        for source, reason in flagged:
            shared = min(candidate.end, source.end) - max(candidate.start, source.start)
            if shared >= CONTENT_DROP_OVERLAP * candidate.duration:
                del result.totals[cid]
                result.drops[cid] = f"{reason} (same material as {source.candidate_id})"
                break


def combine_totals(
    a: RubricScores | None,
    b: RubricScores | None,
    weights: dict[str, float],
    disagreement_penalty: float,
) -> float:
    """``mean(A, B) - penalty * |A - B|``, on the 0-10 rubric scale.

    With only one opinion there is no disagreement to penalise, so the single
    total is returned unchanged.
    """
    totals = [s.total(weights) for s in (a, b) if s is not None]
    if not totals:
        return 0.0
    if len(totals) == 1:
        return round(totals[0], 4)
    mean = sum(totals) / len(totals)
    spread = abs(totals[0] - totals[1])
    return round(max(0.0, mean - disagreement_penalty * spread), 4)


def _hard_drop_reason(
    a: RubricScores | None, b: RubricScores | None, *, single_opinion: bool,
    drop_context: bool = True,
) -> str:
    """Section 9.1's hard drops.

    `needs_prior_context` requires *both* prompts to agree, since a single
    prompt reading a transcript out of context is prone to a false positive.
    With the second opinion disabled there is only one verdict to use, which the
    brief leaves undefined -- recorded in PLAN.md and resolved here.
    """
    opinions = [s for s in (a, b) if s is not None]
    if not opinions:
        return ""

    if any(s.is_sponsor_or_ad for s in opinions):
        return "flagged as a sponsor read or advertisement"
    if any(s.policy_risk == "high" for s in opinions):
        return "high policy risk"

    if not drop_context:
        return ""
    if single_opinion or len(opinions) == 1:
        if opinions[0].needs_prior_context:
            return "needs prior context (single opinion)"
        return ""

    if all(s.needs_prior_context for s in opinions):
        return "both prompts agree it needs prior context"
    return ""


def _score_with_variant(
    candidates: list[Candidate],
    backend: LLMBackend,
    cfg: LLMConfig,
    variant: PromptVariant,
    *,
    cache: LLMCache,
    examples: list[dict] | None,
) -> dict[str, RubricScores]:
    """Score every candidate with one prompt variant, batching and caching."""
    out: dict[str, RubricScores] = {}
    pending: list[Candidate] = []

    # Cache lookups are per candidate, not per batch: a batch that shares seven
    # of eight candidates with an earlier run should still cost one call for the
    # new one, not a full miss.
    for candidate in candidates:
        key = _cache_key(backend, variant, candidate, cache, cfg)
        entry = cache.get(key)
        if entry is None:
            pending.append(candidate)
            continue
        parsed = _parse_single(entry.text)
        if parsed is None:
            pending.append(candidate)
        else:
            out[candidate.candidate_id] = parsed

    if out:
        log.debug("prompt %s: %d cached, %d to score", variant.key, len(out), len(pending))

    for batch in _batches(pending, cfg.batch_size):
        scored = _score_batch(batch, backend, cfg, variant, examples=examples)
        for candidate, scores in scored.items():
            out[candidate] = scores
        # Cache each candidate's own object so future batches of any shape hit.
        for candidate in batch:
            scores = scored.get(candidate.candidate_id)
            if scores is None:
                continue
            key = _cache_key(backend, variant, candidate, cache, cfg)
            cache.put(key, text=json.dumps([_as_item(scores, 0)]), model=backend.model)

    return out


def _cache_key(backend: LLMBackend, variant: PromptVariant, candidate: Candidate,
               cache: LLMCache, cfg: LLMConfig) -> str:
    # Few-shot examples change the prompt, so they belong in the key.
    prompt_key = variant.cache_key + (":fs" if cfg.use_few_shot_examples else "")
    return cache.key(
        backend=backend.name,
        model=backend.cache_model(),
        prompt_key=prompt_key,
        payload=candidate.text.strip(),
    )


def _batches(items: list[Candidate], size: int):
    for i in range(0, len(items), size):
        yield items[i : i + size]


def _score_batch(
    batch: list[Candidate],
    backend: LLMBackend,
    cfg: LLMConfig,
    variant: PromptVariant,
    *,
    examples: list[dict] | None,
) -> dict[str, RubricScores]:
    """One LLM call for up to `batch_size` candidates."""
    if not batch:
        return {}

    payload = [(i, c.duration, c.text) for i, c in enumerate(batch)]
    user = build_user_message(payload, examples=examples)
    request = LLMRequest(
        system=variant.system,
        user=user,
        temperature=cfg.temperature,
        response_schema=list[RubricItem] if backend.supports_schema else None,
    )

    try:
        response = backend.complete(request)
    except LLMConfigError:
        # A missing key or a malformed request will fail identically for every
        # remaining batch, so stop rather than logging the same warning N times.
        raise
    except ContentBlocked as exc:
        # One candidate the provider refuses takes the whole batch down with
        # it. Measured on a sitcom episode: eight candidates lost to one block.
        # Halve and retry, so only the refused candidate goes unscored.
        if len(batch) == 1:
            log.warning("prompt %s: %s was refused by the model and is left "
                        "unscored (%s)", variant.key, batch[0].candidate_id, exc)
            return {}
        middle = len(batch) // 2
        log.info("prompt %s: a batch of %d was refused; scoring it in halves",
                 variant.key, len(batch))
        return {**_score_batch(batch[:middle], backend, cfg, variant, examples=examples),
                **_score_batch(batch[middle:], backend, cfg, variant, examples=examples)}
    except LLMError as exc:
        log.warning("prompt %s failed for a batch of %d: %s", variant.key, len(batch), exc)
        return {}

    items = _parse_items(response.text)
    if items is None:
        items = _repair(backend, request, response.text, variant)
    if items is None:
        log.warning(
            "prompt %s returned unparseable JSON for a batch of %d even after "
            "a repair attempt; those candidates are skipped",
            variant.key, len(batch),
        )
        return {}

    out: dict[str, RubricScores] = {}
    for item in items:
        if not 0 <= item.index < len(batch):
            log.debug("ignoring out-of-range index %d from the model", item.index)
            continue
        out[batch[item.index].candidate_id] = item.to_scores()

    missing = len(batch) - len(out)
    if missing:
        log.debug("prompt %s returned %d of %d expected items", variant.key, len(out), len(batch))
    return out


def _repair(backend: LLMBackend, request: LLMRequest, bad_text: str,
            variant: PromptVariant) -> list[RubricItem] | None:
    """One repair attempt, per section 9.1. Then give up and log."""
    log.debug("prompt %s returned invalid JSON; attempting one repair", variant.key)
    repair = LLMRequest(
        system=REPAIR_SYSTEM + "\n\n" + variant.system,
        user=request.user + "\n\nYour previous reply, which was invalid:\n" + bad_text[:4000],
        temperature=0.0,
        response_schema=request.response_schema,
    )
    try:
        return _parse_items(backend.complete(repair).text)
    except LLMError:
        return None


_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.MULTILINE)


def _parse_items(text: str) -> list[RubricItem] | None:
    """Parse a JSON array of rubric objects, tolerating common wrappers."""
    data = _loads(text)
    if data is None:
        return None

    # Models sometimes wrap the array in an object, e.g. {"clips": [...]}.
    # Only unwrap a list whose elements are objects: a rubric item itself has
    # list-valued keys (`hashtags`), and unwrapping the first list found turned
    # a single valid item into a list of hashtag strings, which then parsed as
    # nothing. That made every cache read miss while reporting a hit.
    if isinstance(data, dict):
        wrapped = next(
            (v for v in data.values()
             if isinstance(v, list) and v and all(isinstance(x, dict) for x in v)),
            None,
        )
        data = wrapped if wrapped is not None else [data]
    if not isinstance(data, list):
        return None

    items: list[RubricItem] = []
    for position, raw in enumerate(data):
        if not isinstance(raw, dict):
            continue
        raw.setdefault("index", position)
        try:
            items.append(RubricItem.model_validate(raw))
        except ValidationError as exc:
            log.debug("dropping an invalid rubric item: %s", exc.errors()[:2])
    return items or None


def _parse_single(text: str) -> RubricScores | None:
    items = _parse_items(text)
    return items[0].to_scores() if items else None


def _loads(text: str):
    """json.loads, after stripping markdown fences and surrounding prose."""
    cleaned = _FENCE_RE.sub("", text).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    # Fall back to the outermost bracketed region.
    for opener, closer in (("[", "]"), ("{", "}")):
        start, end = cleaned.find(opener), cleaned.rfind(closer)
        if start != -1 and end > start:
            try:
                return json.loads(cleaned[start : end + 1])
            except json.JSONDecodeError:
                continue
    return None


def _as_item(scores: RubricScores, index: int) -> dict:
    """Serialise a RubricScores back into the cached wire shape."""
    return {"index": index, **scores.model_dump()}
