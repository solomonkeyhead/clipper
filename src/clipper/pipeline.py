"""Stage orchestration: running the pipeline and caching its artifacts.

Each function here owns one stage boundary from BUILD_BRIEF.md section 6 --
reading the previous stage's artifact, writing its own, and skipping the work
entirely when the artifact is already fresh. Keeping resumability here rather
than inside each stage means the stages stay pure and testable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .candidates.windows import generate as generate_candidates
from .config import Config
from .ingest.download import ingest as run_ingest
from .llm.base import LLMBackend, LLMConfigError, UsageStats
from .llm.base import create as create_backend
from .llm.cache import LLMCache
from .models import (
    Candidates,
    Scenes,
    Scored,
    Sentences,
    Signals,
    SignalValues,
    SourceInfo,
    Transcript,
)
from .paths import examples_dir, work_dir
from .select.pick import SelectionResult
from .select.pick import select as run_select
from .signals import audio as audio_signal
from .signals import heatmap as heatmap_signal
from .signals import llm as llm_signal
from .signals import text as text_signal
from .signals import visual as visual_signal
from .signals.combine import combine
from .transcribe.segment import segment as run_segment
from .transcribe.whisper import TranscribeStats
from .transcribe.whisper import transcribe as run_transcribe
from .utils.cache import StageCache, content_hash
from .utils.logging import get_logger

log = get_logger(__name__)


@dataclass
class ScoreOutcome:
    """Everything the scoring half of the pipeline produced."""

    info: SourceInfo
    transcript: Transcript
    sentences: Sentences
    candidates: Candidates
    signals: Signals
    scored: Scored
    transcribe_stats: TranscribeStats | None = None
    llm_usage: UsageStats | None = None
    cache_stats: str = ""
    available_signals: list[str] = field(default_factory=list)


def prepare(
    source: str,
    config: Config,
    *,
    cache: StageCache | None = None,
    force: set[str] | None = None,
    backend_override: str | None = None,
) -> tuple[SourceInfo, Transcript, Sentences, Candidates, TranscribeStats | None]:
    """Run ingest -> transcribe -> segment -> candidates, reusing what it can."""
    info = run_ingest(source, force=bool(force and "ingest" in force))
    work = work_dir(info.source_id)
    cache = cache or StageCache(work, forced=force or set())

    transcript, stats = run_transcribe(
        info, config.transcription, force="transcribe" in cache.forced
    )

    sentences_path = work / "sentences.json"
    if cache.is_fresh("segment", "sentences.json"):
        sentences = Sentences.load(sentences_path)
    else:
        sentences = run_segment(transcript)
        sentences.save(sentences_path)

    scenes = None
    if config.candidates.scene_aware:
        scenes = _scenes(info, sentences, config, cache, backend_override)

    candidates_path = work / "candidates.json"
    keys = stage_keys(config, backend_override)
    if cache.is_fresh("candidates", "candidates.json", keys["candidates"]):
        candidates = Candidates.load(candidates_path)
        log.info("reusing %d cached candidates", len(candidates.candidates))
    else:
        candidates = generate_candidates(
            transcript, sentences, config.candidates, source_duration=info.media.duration,
            scenes=scenes,
        )
        candidates.save(candidates_path)
        cache.keep("candidates.json", keys["candidates"])

    return info, transcript, sentences, candidates, stats


def _scenes(info: SourceInfo, sentences: Sentences, config: Config, cache: StageCache,
            backend_override: str | None) -> Scenes:
    """Scene boundaries for scripted sources (see candidates/scenes.py), cached."""
    from .candidates import scenes as scene_mod

    path = work_dir(info.source_id) / "scenes.json"
    if cache.is_fresh("scenes", "scenes.json"):
        return Scenes.load(path)
    scan = scene_mod.scan_cuts(Path(info.media.path))
    proposals = scene_mod.propose_scene_starts(
        sentences.sentences, build_backend(config, override=backend_override),
        cache=LLMCache())
    scenes = scene_mod.build_scenes(info.source_id, sentences.sentences, proposals, scan)
    scenes.save(path)
    return scenes


def stage_keys(config: Config, backend_override: str | None) -> dict[str, str]:
    """What each cached stage was made under (D139): its own settings and every earlier
    stage's. A change redoes the stage; the LLM cache still answers unchanged questions,
    so a rescore after a small change costs only the questions that changed."""
    candidates = content_hash(config.candidates.model_dump(mode="json"))
    signals = content_hash(candidates, config.llm.model_dump(mode="json"), backend_override)
    scored = content_hash(signals, config.weights.model_dump(mode="json"))
    return {"candidates": candidates, "signals": signals, "combine": scored}


def judge_backend(config: Config, backend_override: str | None = None) -> LLMBackend | None:
    """Claude, when the user set `llm.judge_model` and has Claude (D139): by API key, else on
    their plan through Claude Code. None: the scoring model judges, as before."""
    import os

    from .llm.claude_code import cli

    model = config.llm.judge_model
    if not model or backend_override or not _claude_judge_on():
        return None
    try:
        if os.environ.get("ANTHROPIC_API_KEY", "").strip() and "judge" in config.llm.paid_api_jobs:
            return create_backend("anthropic", model=model, max_retries=1, requests_per_minute=50, timeout=300)
        if config.llm.create_via_claude_plan and cli():
            return create_backend("claude_code", model=model, max_retries=1, requests_per_minute=60, timeout=420)
    except Exception as exc:  # the free models still judge
        log.warning("Claude can't judge the moments (%s); Gemini will", exc)
    return None


def _claude_judge_on() -> bool:
    """The Control Center's "Claude judges the moments" setting (on unless turned off, D139)."""
    from .studio import db

    try:
        with db.connect() as con:
            return db.settings(con).get("claude_judge", "1") == "1"
    except Exception as exc:  # no library: the default
        log.debug("judge setting unread: %s", exc)
        return True


def build_backend(config: Config, *, override: str | None = None) -> LLMBackend:
    """Instantiate the configured LLM backend, or the override."""
    name = override or config.llm.backend
    return create_backend(
        name,
        model=config.llm.model,
        max_retries=config.llm.max_retries,
        requests_per_minute=config.llm.requests_per_minute,
    )


def compute_signals(
    info: SourceInfo,
    transcript: Transcript,
    candidates: Candidates,
    config: Config,
    *,
    backend: LLMBackend,
    llm_cache: LLMCache | None = None,
    judge: LLMBackend | None = None,
) -> Signals:
    """Compute all four signal families for every candidate.

    Signals that cannot be computed are simply absent from `Signals.available`,
    and `combine` renormalises the weights over what remains -- so a local file
    with no heatmap is scored on three signals rather than penalised for the
    missing fourth.
    """
    items = candidates.candidates
    if not items:
        return Signals(source_id=info.source_id, available=[], values=[])

    available: list[str] = []

    # Text is free and never fails.
    text_raw, text_detail = text_signal.score_candidates(items)
    available.append("text")

    # Audio needs the extracted WAV.
    audio_raw: dict[str, float] = {}
    audio_detail: dict[str, dict[str, float]] = {}
    try:
        audio_raw, audio_detail = audio_signal.score_candidates(
            items, Path(info.audio_path), transcript
        )
        available.append("audio")
    except (OSError, ValueError) as exc:
        log.warning("audio signal unavailable (%s); continuing without it", exc)

    # Heatmap only exists for some YouTube sources.
    heatmap_raw: dict[str, float] = {}
    heatmap_detail: dict[str, dict[str, float]] = {}
    heatmap_result = heatmap_signal.score_candidates(items, info)
    if heatmap_result is not None:
        heatmap_raw, heatmap_detail = heatmap_result
        available.append("heatmap")

    # The LLM last: it is the slowest and the only one that can cost money, so
    # a failure in a cheap signal surfaces before any quota is spent.
    # Moments with no dialogue have nothing to read; only watching judges them.
    llm_result = _judged([c for c in items if not c.quiet], config, backend, judge, llm_cache)
    if llm_result.totals or llm_result.drops:
        available.append("llm")

    values: list[SignalValues] = []
    for candidate in items:
        cid = candidate.candidate_id
        a, b = llm_result.scores.get(cid, (None, None))
        drop_reason = llm_result.drops.get(cid, "")
        values.append(SignalValues(
            candidate_id=cid,
            llm_a=a,
            llm_b=b,
            llm_total=llm_result.totals.get(cid),
            audio=audio_raw.get(cid),
            heatmap=heatmap_raw.get(cid),
            text=text_raw.get(cid),
            audio_features=audio_detail.get(cid, {}),
            text_features=text_detail.get(cid, {}),
            heatmap_features=heatmap_detail.get(cid, {}),
            dropped=bool(drop_reason),
            drop_reason=drop_reason,
        ))

    _watch(info, items, values, config, backend, llm_cache)
    quiet = {c.candidate_id for c in items if c.quiet}
    for v in values:
        if v.candidate_id in quiet and v.watched is None:
            v.dropped, v.drop_reason = True, "no dialogue, and it couldn't be watched"

    log.info(
        "signals available: %s (%d candidates, %d dropped by the LLM)",
        ", ".join(available), len(values), len(llm_result.drops),
    )
    return Signals(source_id=info.source_id, available=available, values=values)


def _judged(items, config: Config, backend: LLMBackend, judge: LLMBackend | None,
            llm_cache: LLMCache | None) -> llm_signal.LLMSignalResult:
    """The rubric scores: from the judge (Claude) when there is one, and from `backend` for
    any moment the judge couldn't score, or for all of them if it can't run at all."""
    examples = _few_shot_examples(config)
    if judge is None:
        return llm_signal.score_candidates(items, backend, config.llm, cache=llm_cache, examples=examples)
    try:
        result = llm_signal.score_candidates(items, judge, config.llm, cache=llm_cache, examples=examples)
    except LLMConfigError as exc:
        log.warning("%s can't judge (%s); %s will", judge.describe(), exc, backend.describe())
        return llm_signal.score_candidates(items, backend, config.llm, cache=llm_cache, examples=examples)
    log.info("moments judged by %s", judge.describe())
    if result.unscored:
        missed = set(result.unscored)
        rest = llm_signal.score_candidates([c for c in items if c.candidate_id in missed], backend,
                                           config.llm, cache=llm_cache, examples=examples)
        result.unscored = rest.unscored
        for cid in missed:
            if cid in rest.scores:
                result.scores[cid] = rest.scores[cid]
            if cid in rest.totals:
                result.totals[cid] = rest.totals[cid]
            if cid in rest.drops:
                result.drops[cid] = rest.drops[cid]
    return result


def _watch(info: SourceInfo, items, values: list[SignalValues], config: Config,
           backend: LLMBackend, llm_cache: LLMCache | None) -> None:
    """The "watch it" pass over the best-scoring moments (signals/visual.py)."""
    if not config.llm.watch_video or not info.media.width:
        return
    watcher = backend
    if config.llm.watch_model:
        watcher = create_backend(backend.name, model=config.llm.watch_model,
                                 max_retries=config.llm.max_retries,
                                 requests_per_minute=config.llm.requests_per_minute)
    try:
        seen = visual_signal.watch_candidates(
            items, values, Path(info.media.path), watcher, config.llm,
            source_id=info.source_id, cache=llm_cache)
    except Exception as exc:  # a bonus, never a reason to lose the run
        log.warning("the watch pass failed; scoring from the transcript only: %s", exc)
        return
    weights = config.llm.rubric_weights.as_dict()
    for v in values:
        w = seen.get(v.candidate_id)
        if w is not None:
            v.watched = round(w.total(weights), 4)
            v.visual_payoff, v.sees, v.visual_hook = w.visual_payoff, w.sees.strip(), w.hook_text.strip()


def score(
    source: str,
    config: Config,
    *,
    backend_override: str | None = None,
    force: set[str] | None = None,
) -> ScoreOutcome:
    """Run the whole scoring half: ingest through combine."""
    force = force or set()
    info, transcript, sentences, candidates, stats = prepare(
        source, config, force=force, backend_override=backend_override)

    work = work_dir(info.source_id)
    cache = StageCache(work, forced=force)

    signals_path = work / "signals.json"
    scored_path = work / "scored.json"

    backend: LLMBackend | None = None
    llm_cache = LLMCache()

    keys = stage_keys(config, backend_override)
    if cache.is_fresh("signals", "signals.json", keys["signals"]):
        signals = Signals.load(signals_path)
        log.info("reusing cached signals (%s)", ", ".join(signals.available))
    else:
        backend = build_backend(config, override=backend_override)
        log.info("scoring %d candidates with %s", len(candidates.candidates), backend.describe())
        judge = judge_backend(config, backend_override)
        signals = compute_signals(
            info, transcript, candidates, config, backend=backend, llm_cache=llm_cache, judge=judge
        )
        signals.save(signals_path)
        cache.keep("signals.json", keys["signals"])

    if cache.is_fresh("combine", "scored.json", keys["combine"]) and cache.is_fresh("signals", "signals.json", keys["signals"]):
        scored = Scored.load(scored_path)
    else:
        scored = combine(signals, config, durations={
            c.candidate_id: c.duration for c in candidates.candidates})
        scored.save(scored_path)
        cache.keep("scored.json", keys["combine"])

    return ScoreOutcome(
        info=info,
        transcript=transcript,
        sentences=sentences,
        candidates=candidates,
        signals=signals,
        scored=scored,
        transcribe_stats=stats,
        llm_usage=backend.usage if backend else None,
        cache_stats=llm_cache.stats(),
        available_signals=signals.available,
    )


def choose(outcome: ScoreOutcome, config: Config, *, limit: int | None = None) -> SelectionResult:
    """Run selection over an existing scoring outcome."""
    return run_select(
        outcome.scored,
        outcome.candidates.candidates,
        config.selection,
        min_gap_seconds=config.candidates.min_gap_seconds,
        source_duration=outcome.info.media.duration,
        limit=limit,
    )


def load_scored(source_id: str) -> tuple[SourceInfo, Candidates, Signals, Scored]:
    """Load a previously scored source's artifacts."""
    from .ingest.download import load_info

    work = work_dir(source_id)
    return (
        load_info(source_id),
        Candidates.load(work / "candidates.json"),
        Signals.load(work / "signals.json"),
        Scored.load(work / "scored.json"),
    )


def _few_shot_examples(config: Config) -> list[dict] | None:
    """Top-performing clips to calibrate the prompts (section 14.2).

    Off by default. The file is built by `clipper learn` from real view counts,
    so it does not exist until the user has logged some.
    """
    if not config.llm.use_few_shot_examples:
        return None

    path = examples_dir() / "top_clips.jsonl"
    if not path.is_file():
        log.warning(
            "llm.use_few_shot_examples is on but %s does not exist yet; "
            "run `clipper learn` once you have logged performance data", path,
        )
        return None

    import json

    examples: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            examples.append(json.loads(line))
        except json.JSONDecodeError:
            continue
        if len(examples) >= config.llm.few_shot_count:
            break
    return examples or None


__all__ = [
    "LLMConfigError",
    "ScoreOutcome",
    "build_backend",
    "choose",
    "compute_signals",
    "load_scored",
    "prepare",
    "score",
]
