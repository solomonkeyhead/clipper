"""The end-to-end run: refine, reframe, render, QA, replace, write outputs.

The replacement loop is the part worth reading. Rendering is expensive, so
clips are rendered one at a time and checked immediately; a failure pulls the
next-best reserve and tries again. That costs one extra render per failure
rather than re-rendering a whole batch, and it means the quota of clips is
filled with things that actually passed rather than things that were merely
selected.

A reserve is only ever a candidate that already cleared the quality gate
(`select/pick.py`), so replacing a failure can never quietly substitute filler.
"""

from __future__ import annotations

import dataclasses
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from .campaign import compliance, rotation
from .campaign.manifest import ClipRecord, write_outputs, write_rejection_reason
from .candidates.boundaries import RefinedBounds, refine
from .config import CampaignConfig, Config
from .ingest.download import IngestError, is_url, probe_rights
from .ingest.probe import probe
from .llm.base import LLMBackend
from .llm.base import create as create_backend
from .llm.cache import LLMCache
from .models import ClipPlan, Sentences, SourceInfo, Transcript, Word
from .paths import ensure
from .pipeline import ScoreOutcome, build_backend, choose, score
from .qa.checks import QAContext, check_clip, summarize
from .render.clip import render_clip
from .render.faces import plan_layout_for
from .render.graph import output_size
from .select.pick import Pick
from .transcribe.correct import WordFix, correct_words, names_in
from .transcribe.recheck import AudioRecheck
from .utils.cache import slugify
from .utils.logging import get_logger
from .utils.timecode import to_slug_timestamp

log = get_logger(__name__)
#: Clips rendered at once (D75): an 8-core CPU and the GPU encoder had room for more.
RENDER_WORKERS = 3
#: How much the picture must add (0-10) before its hook line replaces the transcript's.
VISUAL_HOOK_MIN = 6


@dataclass
class RunResult:
    """Everything a run produced."""

    info: SourceInfo
    accepted: list[ClipRecord] = field(default_factory=list)
    rejected: list[ClipRecord] = field(default_factory=list)
    outputs: dict[str, Path] = field(default_factory=dict)
    timings: dict[str, float] = field(default_factory=dict)
    selection_note: str = ""
    signals_available: list[str] = field(default_factory=list)
    weights_used: dict[str, float] = field(default_factory=dict)
    # What happened to every moment, for the Control Center (select/report.py).
    report: dict = field(default_factory=dict)

    @property
    def clip_count(self) -> int:
        return len(self.accepted)


def run(
    source: str,
    *,
    config: Config,
    campaign: CampaignConfig,
    out_root: Path,
    top: int | None = None,
    draft: bool = False,
    backend_override: str | None = None,
    force: set[str] | None = None,
) -> RunResult:
    """Ingest through manifest for one source."""
    started = time.perf_counter()
    timings: dict[str, float] = {}
    config = campaign_config(config, campaign)
    check_rights(source, campaign)

    limit = clip_limit(top, campaign)
    # Watch at least twice as many moments as will be made (up to 40), so a
    # long source asked for many clips isn't ranked on its words alone.
    shortlist = min(40, max(config.llm.watch_shortlist, 2 * min(limit, 20)))
    config = config.model_copy(update={"llm": config.llm.model_copy(update={"watch_shortlist": shortlist})})
    score_started = time.perf_counter()
    outcome = score(source, config, backend_override=backend_override, force=force)
    timings["score"] = time.perf_counter() - score_started

    result = RunResult(
        info=outcome.info,
        signals_available=outcome.available_signals,
        weights_used=outcome.scored.weights_used,
    )

    selection = choose(outcome, config, limit=limit)
    result.selection_note = selection.stopped_because

    out_dir = ensure(out_root / outcome.info.source_id)
    clips_dir = ensure(out_dir / "clips")
    rejected_dir = out_dir / "rejected"
    work = ensure(out_dir / "work")

    corrector, recheck = _correction(config, backend_override, outcome.info)

    render_started = time.perf_counter()
    _render_with_replacement(
        selection.picks, selection.reserves, outcome,
        config=config, campaign=campaign, clips_dir=clips_dir,
        rejected_dir=rejected_dir, work=work, draft=draft,
        limit=limit, result=result, corrector=corrector, recheck=recheck,
    )
    timings["render_and_qa"] = time.perf_counter() - render_started
    from .select import report as run_report

    result.report = run_report.build(outcome, selection, result,
                                     bar=config.selection.min_llm_total, limit=limit)

    if outcome.transcribe_stats:
        timings["transcribe"] = outcome.transcribe_stats.wall_seconds
    timings["total"] = time.perf_counter() - started
    result.timings = timings

    result.outputs = write_outputs(
        result.accepted,
        info=outcome.info,
        campaign=campaign,
        out_dir=out_dir,
        rejected=result.rejected,
        selection_note=result.selection_note,
        signals_available=result.signals_available,
        weights_used=result.weights_used,
        timings=timings,
    )

    log.info(
        "run complete: %d clip(s) accepted, %d rejected, %.1fs total",
        len(result.accepted), len(result.rejected), timings["total"],
    )
    return result


#: "No limit": selection stops only when nothing else clears the quality bar.
EVERY_GOOD_MOMENT = 500


def clip_limit(top: int | None, campaign: CampaignConfig) -> int:
    """How many clips to make at most: an explicit count wins over the campaign's
    cap (asking for 10 used to give 4 when the campaign said 4); with neither,
    every moment good enough (D71)."""
    return top or campaign.max_clips_per_source or EVERY_GOOD_MOMENT


def cut(
    source: str,
    ranges: list[tuple[float, float]],
    *,
    config: Config,
    campaign: CampaignConfig,
    out_root: Path,
    draft: bool = False,
    backend_override: str | None = None,
    first_rank: int = 1,
) -> RunResult:
    """Render exact, hand-picked ranges with the campaign's framing and captions.

    `first_rank` continues the campaign's hook and caption rotation from an
    earlier cut, so clips from two episodes don't open with the same line.

    Selection works from dialogue, so it cannot see a scene that plays out in
    looks: the Chad Powers brief's Episode 4 field scene is some 40 seconds
    without a line, and never became a candidate. A brief that names its
    moments gets them cut as given -- no refinement, no scene clamp, no silence
    trim -- and still goes through the same render, QA and compliance checks.
    """
    from .pipeline import run_ingest, run_transcribe

    started = time.perf_counter()
    config = campaign_config(config, campaign)
    check_rights(source, campaign)

    info = run_ingest(source)
    transcript, stats = run_transcribe(info, config.transcription)
    result = RunResult(info=info, selection_note="ranges chosen by hand")

    out_dir = ensure(out_root / info.source_id)
    clips_dir = ensure(out_dir / "clips")
    work = ensure(out_dir / "work")

    corrector, recheck = _correction(config, backend_override, info)

    audio = Path(info.audio_path) if info.audio_path else None
    listener = recheck or (AudioRecheck(audio, config.transcription)
                           if audio and audio.exists() else None)

    for attempt, (start, end) in enumerate(ranges, start=1):
        start, end = max(0.0, start), min(end, info.media.duration)
        words = transcript.words
        if listener is not None:
            words = with_range_transcript(words, start, end, listener.words_between(start, end))
        plan = manual_plan(start, end, words, config=config, campaign=campaign,
                           rank=first_rank + len(result.accepted), attempt=attempt)
        record = _render_plan(plan, info, words, config=config, campaign=campaign,
                              clips_dir=clips_dir, work=work, draft=draft,
                              corrector=corrector, recheck=recheck)
        if record.qa.status == "fail" or record.compliance.status == "fail":
            _reject(record, out_dir / "rejected", result)
            continue
        result.accepted.append(record)
        log.info("%s accepted: %s", record.plan.clip_id, summarize(record.qa))

    if stats:
        result.timings["transcribe"] = stats.wall_seconds
    result.timings["total"] = time.perf_counter() - started
    from .select import report as run_report

    result.report = run_report.manual(ranges, result)
    result.outputs = write_outputs(
        result.accepted, info=info, campaign=campaign, out_dir=out_dir,
        rejected=result.rejected, selection_note=result.selection_note,
        timings=result.timings,
    )
    return result


class RerenderError(RuntimeError):
    """A clip couldn't be made again; the message says why."""


def rerender(clip: dict, hook: str, *, config: Config, campaign: CampaignConfig,
             out_root: Path, backend_override: str | None = None) -> Path:
    """Make a library clip again with a different on-screen hook (D90).

    Same range, framing and edits; only the hook line changes. Works from the
    video's kept working files (transcript, info), so the source must still be on
    this PC. Returns the new file, rendered in the work area; the caller files it.
    """
    from .paths import work_dir

    work_src = work_dir(clip["source_id"])
    if not (work_src / "info.json").exists() or not (work_src / "transcript.json").exists():
        raise RerenderError("this clip's working files are gone; clip its video again instead")
    info = SourceInfo.load(work_src / "info.json")
    if not Path(info.media.path).exists():
        raise RerenderError(f"its video isn't on this PC any more ({Path(info.media.path).name})")
    if clip.get("start_s") is None or clip.get("end_s") is None:
        raise RerenderError("this clip has no time range on record")
    transcript = Transcript.load(work_src / "transcript.json")
    config = campaign_config(config, campaign)
    start, end = float(clip["start_s"]), float(clip["end_s"])
    corrector, recheck = _correction(config, backend_override, info)
    audio = Path(info.audio_path) if info.audio_path else None
    listener = recheck or (AudioRecheck(audio, config.transcription) if audio and audio.exists() else None)
    words = transcript.words
    if listener is not None:
        words = with_range_transcript(words, start, end, listener.words_between(start, end))
    # The brief's lines aren't handed out again (campaign/rotation.py): the hook is
    # the one asked for, and the clip keeps the caption it has.
    plan = manual_plan(start, end, words, config=config,
                       campaign=campaign.model_copy(update={"hook_texts": (), "fallback_captions": ()}),
                       rank=1, attempt=1)
    if campaign.censor_flagged_words:
        from .campaign.safety import clean

        hook = clean(hook, campaign)
    plan = plan.model_copy(update={"clip_id": clip["clip_id"], "hook_text": hook.strip(),
                                   "hook_shown": bool(hook.strip()) and config.render.show_hook_text})
    out_dir = ensure(out_root / clip["source_id"] / "rerender")
    # No new description: the clip keeps its caption, and it's an AI call saved.
    record = _render_plan(plan, info, words, config=config,
                          campaign=campaign.model_copy(update={"long_description": False}),
                          clips_dir=ensure(out_dir / "clips"), work=ensure(out_dir / "work"), draft=False,
                          corrector=corrector, recheck=recheck)
    if record.qa.status == "fail":
        raise RerenderError(f"the new render failed its checks: {summarize(record.qa)}")
    return record.file


def with_range_transcript(words: list[Word], start: float, end: float,
                          heard: list[Word] | None) -> list[Word]:
    """`words` with `start`-`end` replaced by `heard`, when that heard more.

    More words is the test because the failure being fixed is words *missing*
    (see `AudioRecheck.words_between`); a range-only pass that heard fewer is
    not trusted over the episode's.
    """
    inside = [w for w in words if start <= (w.start + w.end) / 2 < end]
    heard = [w for w in heard or [] if start <= (w.start + w.end) / 2 < end]
    if len(heard) <= len(inside):
        return words
    log.info("range %.1f-%.1fs re-transcribed on its own: %d words, was %d",
             start, end, len(heard), len(inside))
    kept = [w for w in words if not start <= (w.start + w.end) / 2 < end]
    return sorted([*kept, *heard], key=lambda w: w.start)


def manual_plan(start: float, end: float, words: list[Word], *, config: Config,
                campaign: CampaignConfig, rank: int, attempt: int) -> ClipPlan:
    """A plan for a hand-picked range, captioned the way the campaign asks."""
    text = " ".join(w.text for w in words if start <= (w.start + w.end) / 2 < end).strip()
    plan = ClipPlan(
        clip_id=f"{attempt:03d}_{to_slug_timestamp(start)}",
        candidate_id="manual",
        rank=rank,
        start=start,
        end=end,
        text=text,
        hook_text=rotation.pick(campaign, "hook", campaign.hook_texts) if campaign.hook_texts else "",
        caption_style=config.render.caption_style,
        refine_notes=["range chosen by hand"],
        lead_in=next((round(w.start - start, 2) for w in words
                      if start - 0.05 <= w.start < end), None),
        hook_shown=bool(config.render.show_hook_text and campaign.hook_texts),
    )
    return compliance.apply_campaign_caption(plan, campaign, pick=rotation.pick)


def parse_range(text: str) -> tuple[float, float]:
    """``"24:45-26:05"``, ``"1:02:03-1:02:40"`` or ``"1485-1560"`` as seconds."""
    def seconds(part: str) -> float:
        value = 0.0
        for piece in part.strip().split(":"):
            value = value * 60 + float(piece)
        return value

    try:
        first, second = text.split("-")
        start, end = seconds(first), seconds(second)
    except ValueError as exc:
        raise ValueError(f"not a time range: {text!r} (use e.g. 24:45-26:05)") from exc
    if end <= start:
        raise ValueError(f"range ends before it starts: {text!r}")
    return start, end


SCRIPTED_MAX_SILENCE = 0.55
# TikTok's creative guidance: land the proposition in the first 3 seconds.
SCRIPTED_OPENING_SECONDS = 3.0
SCRIPTED_TARGET = (20.0, 45.0)
SCRIPTED_MAX_SECONDS = 90.0
SCRIPTED_MAX_LEAD_IN = 0.15   # silence before the first word (research R1.1, D59)
SCRIPTED_REACTION_TAIL = 1.0  # held after the last line, into silence only
SCRIPTED_MAX_TAIL = 1.5       # never more silence than this at the end
SCRIPTED_MAX_SHOTS = 32       # separately framed shots per clip


def campaign_config(config: Config, campaign: CampaignConfig) -> Config:
    """The run's config with the campaign's own limits applied.

    The campaign's clip-length window used to be checked only after rendering,
    while candidates were cut to the global 20-55s. Under a brief requiring
    30s-2min, most candidates would have been rendered only to be rejected, and
    nothing between 55s and 2 minutes could ever be made. The window now shapes
    the candidates themselves.
    """
    low, high = campaign.duration.min_seconds, campaign.duration.max_seconds
    t_low, t_high = config.candidates.target_seconds
    target_low = min(max(t_low, low), high)
    target_high = max(min(t_high, high), target_low)
    candidate_updates = {
        "min_seconds": low, "max_seconds": high,
        "target_seconds": (target_low, target_high),
        "scene_aware": config.candidates.scene_aware or campaign.scripted,
    }
    if campaign.scripted:
        # The head/tail trim skips podcast intros and outros. An edited sitcom
        # episode opens straight into a scene, often its best one.
        candidate_updates["edge_trim_seconds"] = 0.0
    if campaign.scripted:
        # Silence here means gaps between spoken words. Scripted TV is full of
        # non-dialogue beats -- reactions, physical comedy, music -- often the
        # funny part. Measured: sitcom windows run a median 38-41% without
        # dialogue against 13-18% in podcasts; at the default 25% cut-off only
        # 4-10% of an episode survived, all from its few talky stretches.
        # 55% keeps about 90% and still drops stretches with next to no speech.
        candidate_updates["max_silence_ratio"] = max(
            config.candidates.max_silence_ratio, SCRIPTED_MAX_SILENCE)
    refine = config.refine
    if campaign.scripted or campaign.short_form_timing:
        # Short-form retention research (docs/DECISIONS.md D52): one bit per
        # clip, 20-45s by default, never more than 90s; open on dialogue and
        # end on the reaction, a beat after the last line.
        cap = max(low + 1, min(high, SCRIPTED_MAX_SECONDS))
        target_low = min(max(SCRIPTED_TARGET[0], low), cap)
        candidate_updates.update({
            "max_seconds": cap,
            "target_seconds": (target_low, max(min(SCRIPTED_TARGET[1], cap), target_low)),
            "prefer_target_length": True,
        })
        refine = refine.model_copy(update={
            "post_roll": SCRIPTED_REACTION_TAIL, "tail_guard": 0.15,
            "max_lead_in": SCRIPTED_MAX_LEAD_IN, "max_tail": SCRIPTED_MAX_TAIL})
    if campaign.target_seconds:
        low_t, high_t = campaign.target_seconds
        candidate_updates["target_seconds"] = (max(low, low_t), min(high, max(high_t, low_t)))
        candidate_updates["prefer_target_length"] = True
    candidates = config.candidates.model_copy(update=candidate_updates)
    from .campaign.edits import permissions

    allowed = permissions(campaign)
    render = config.render.model_copy(update={
        "show_hook_text": config.render.show_hook_text and allowed.added_text,
        # Held through the first 3 seconds, where viewers decide to stay.
        "hook_text_seconds": (max(config.render.hook_text_seconds, SCRIPTED_OPENING_SECONDS)
                              if campaign.scripted or campaign.short_form_timing
                              else config.render.hook_text_seconds),
        "keep_everyone_in_frame": config.render.keep_everyone_in_frame or campaign.scripted,
        # The first seconds decide whether a viewer stays: fill the screen then.
        "opening_full_screen_seconds": (
            SCRIPTED_OPENING_SECONDS if campaign.scripted or campaign.short_form_timing
            else config.render.opening_full_screen_seconds),
        # A TV set or window in a scene is not a screen share.
        "detect_screen_share": config.render.detect_screen_share and not campaign.scripted,
        # Shot/reverse-shot cuts every few seconds: at the default 8, a 60s
        # Chad Powers scene had its close-ups merged in pairs, and a merged shot
        # widens to hold both faces -- letterboxed. Branches cost no buffering
        # (each trims to its own window of an in-order decode).
        "max_shots": (max(config.render.max_shots, SCRIPTED_MAX_SHOTS) if campaign.scripted
                      else config.render.max_shots),
    })
    weights, taste = _learning(config, campaign)
    llm = config.llm.model_copy(update={
        "campaign_focus": campaign.selection_focus,
        "user_taste": taste,
        **({"rubric_weights": config.llm.rubric_weights.model_copy(update=weights)}
           if weights else {}),
        "drop_needs_prior_context":
            config.llm.drop_needs_prior_context and not campaign.scripted,
    })
    return config.model_copy(update={"candidates": candidates, "render": render, "llm": llm,
                                     "refine": refine})


def _learning(config: Config, campaign: CampaignConfig) -> tuple[dict[str, float] | None, str]:
    """What the user's ratings teach this run: rubric weights (None: keep the
    defaults) and the taste block for the scoring prompt. Nothing while the
    Control Center's "Learn from my ratings" setting is off."""
    from .learn import feedback
    from .studio import db

    try:
        with db.connect() as con:
            if db.settings(con).get("learn_from_feedback", "1") != "1":
                return None, ""
            rows = db.clips(con)
    except Exception as exc:  # learning is optional; a run must not fail on it
        log.warning("not using clip ratings: %s", exc)
        return None, ""
    try:  # how posted clips did, once their views have settled (D86)
        from .learn import log as perf_log
        from .studio import stats

        performance = stats.clip_performance(rows, perf_log.read())
    except Exception as exc:  # optional, like the rest of learning
        log.warning("not using post views to learn: %s", exc)
        performance = {}
    clips = feedback.from_rows(rows, performance=performance)
    weights, n = feedback.learned_weights(clips, config.llm.rubric_weights.as_dict())
    if n >= feedback.MIN_FOR_WEIGHTS:
        log.info("rubric weights learnt from %d rated clip(s): %s", n,
                 ", ".join(f"{k} {v:.2f}" for k, v in weights.items()))
    return (weights if n >= feedback.MIN_FOR_WEIGHTS else None), feedback.taste(clips, campaign.name)


def _render_with_replacement(
    picks: list[Pick],
    reserves: list[Pick],
    outcome: ScoreOutcome,
    *,
    config: Config,
    campaign: CampaignConfig,
    clips_dir: Path,
    rejected_dir: Path,
    work: Path,
    draft: bool,
    limit: int,
    result: RunResult,
    corrector: list[LLMBackend] | None = None,
    recheck: AudioRecheck | None = None,
) -> None:
    """Render, QA, and pull a reserve for each failure until the quota is met.

    Clips render RENDER_WORKERS at a time (D75): each is FFmpeg, a face scan and
    some AI calls, and one after another left most of the CPU and the encoder
    idle. A wave starts only as many as the quota still needs, so nothing extra
    is rendered; a rejection queues a reserve for the next wave.
    """
    queue = list(picks)
    spare = list(reserves)
    attempted: set[str] = set()
    attempt = 0
    prefix = f"{threading.current_thread().name}-render"

    with ThreadPoolExecutor(max_workers=RENDER_WORKERS, thread_name_prefix=prefix) as pool:
        while queue and len(result.accepted) < limit:
            wave = []
            while queue and len(wave) < min(RENDER_WORKERS, limit - len(result.accepted)):
                pick = queue.pop(0)
                if pick.candidate.candidate_id in attempted:
                    continue
                attempted.add(pick.candidate.candidate_id)
                # `attempt` only ever increases, so ids are unique even when a
                # clip is rejected and replaced. Reusing the accepted-clip rank
                # made two rejected files collide on both id and filename.
                attempt += 1
                rank = len(result.accepted) + len(wave) + 1
                wave.append(pool.submit(
                    _produce_one, pick, outcome, config=config, campaign=campaign,
                    clips_dir=clips_dir, work=work, draft=draft,
                    rank=rank, attempt=attempt, corrector=corrector, recheck=recheck))
            for future in wave:
                _settle(future.result(), queue, spare, result, rejected_dir, config)


def _settle(record, queue: list[Pick], spare: list[Pick], result: RunResult,
            rejected_dir: Path, config: Config) -> None:
    """Keep one rendered clip, or reject it and queue a replacement."""
    if record is None:
        reserve = _next_reserve(spare, result, queue, config.candidates.min_gap_seconds)
        if reserve is not None:
            queue.append(reserve)
        return

    if record.qa.status == "fail" or record.compliance.status == "fail":
        _reject(record, rejected_dir, result)
        replacement = _next_reserve(spare, result, queue,
                                    config.candidates.min_gap_seconds)
        if replacement is not None:
            log.info(
                "replacing %s with reserve %s",
                record.plan.clip_id, replacement.candidate.candidate_id,
            )
            queue.append(replacement)
        else:
            log.warning(
                "%s failed QA and no reserve is available; the run will "
                "return fewer clips", record.plan.clip_id,
            )
        return

    result.accepted.append(record)
    log.info("%s accepted: %s", record.plan.clip_id, summarize(record.qa))


def _next_reserve(spare: list[Pick], result: RunResult, queue: list[Pick],
                  min_gap: float) -> Pick | None:
    """The best remaining reserve that does not clash with a kept or queued clip.

    Reserves used to be taken in order with no check. Selection spaces clips
    at least `min_gap` apart, but a reserve standing in for a rejected clip can
    sit on top of one already accepted -- measured on two real episodes: clips
    sharing 45s and 35s of footage, near-duplicates on one account.
    """
    taken = [(r.plan.start, r.plan.end) for r in result.accepted]
    taken += [(p.candidate.start, p.candidate.end) for p in queue]
    for i, pick in enumerate(spare):
        start, end = pick.candidate.start, pick.candidate.end
        if all(end + min_gap <= a or b + min_gap <= start for a, b in taken):
            return spare.pop(i)
    return None


def _produce_one(
    pick: Pick,
    outcome: ScoreOutcome,
    *,
    config: Config,
    campaign: CampaignConfig,
    clips_dir: Path,
    work: Path,
    draft: bool,
    rank: int,
    attempt: int,
    corrector: list[LLMBackend] | None = None,
    recheck: AudioRecheck | None = None,
) -> ClipRecord | None:
    """Refine, reframe, render and check one candidate. None if it was dropped."""
    plan = _build_plan(pick, outcome, config=config, campaign=campaign,
                       rank=rank, attempt=attempt)
    if plan is None:
        return None
    entry = next((s for s in outcome.scored.scored
                  if s.candidate_id == pick.candidate.candidate_id), None)
    values = next((v for v in outcome.signals.values
                   if v.candidate_id == pick.candidate.candidate_id), None)
    weights = config.llm.rubric_weights.as_dict()
    record = _render_plan(plan, outcome.info, outcome.transcript.words, config=config,
                          campaign=campaign, clips_dir=clips_dir, work=work, draft=draft,
                          corrector=corrector, recheck=recheck)
    record.components = dict(entry.components) if entry else {}
    record.raw = dict(entry.raw) if entry else {}
    record.llm_a_total = values.llm_a.total(weights) if values and values.llm_a else None
    record.llm_b_total = values.llm_b.total(weights) if values and values.llm_b else None
    record.rubric = average_rubric(values)
    if values is not None:
        record.sees, record.visual_payoff = values.sees, values.visual_payoff
    ranked = sorted((s for s in outcome.scored.scored if not s.dropped),
                    key=lambda s: s.composite, reverse=True)
    record.pool = len(ranked)
    record.pool_rank = next((i for i, s in enumerate(ranked, 1)
                             if s.candidate_id == pick.candidate.candidate_id), None)
    return record


RUBRIC_FIELDS = ("hook_strength", "standalone_clarity", "payoff", "emotional_intensity",
                 "quotability", "ending_completeness")


def average_rubric(values) -> dict[str, float]:
    """A candidate's six rubric scores, averaged over the prompts that answered."""
    answers = [a for a in ((values.llm_a, values.llm_b) if values else ()) if a is not None]
    if not answers:
        return {}
    return {f: round(sum(getattr(a, f) for a in answers) / len(answers), 2)
            for f in RUBRIC_FIELDS}


def _render_plan(
    plan: ClipPlan,
    info: SourceInfo,
    transcript_words: list[Word],
    *,
    config: Config,
    campaign: CampaignConfig,
    clips_dir: Path,
    work: Path,
    draft: bool,
    corrector: list[LLMBackend] | None = None,
    recheck: AudioRecheck | None = None,
) -> ClipRecord:
    """Reframe, edit, render and check one planned clip (edits per docs/DECISIONS.md D59)."""
    from .campaign.edits import permissions
    from .render.placement import faces_on_screen
    from .render.prepare import darkness, first_bright, join_segments, lift_for
    from .render.tighten import keep_segments, plan_cuts, remap_words

    width, height = output_size(config.render, draft=draft)
    source_path = Path(info.media.path)
    allowed = permissions(campaign)

    # Never open on a black frame (research R1.1) -- but never skip speech either.
    skip = first_bright(source_path, plan.start)
    if skip > 0:
        first_word = next((w.start for w in transcript_words
                           if plan.start <= w.start < plan.end), plan.end)
        start = min(plan.start + skip, max(plan.start, first_word - 0.05))
        if start > plan.start + 0.01:
            plan = plan.model_copy(update={
                "start": start, "refine_notes": [*plan.refine_notes,
                                                 f"skipped {start - plan.start:.2f}s of black"]})

    words, fixes = _corrected_words(transcript_words, plan, corrector, recheck,
                                    config.llm.rejected_fix_pairs)
    if campaign.long_description:
        plan = plan.model_copy(update={"description": _description(
            plan, words, campaign, corrector, config)})

    # Dead air and fillers (podcasts; `internal_cuts`). The kept pieces are joined
    # into one file and everything below runs on it, on the tightened timeline.
    render_source, render_media, render_plan, render_words = source_path, info.media, plan, words
    if allowed.internal_cuts and plan.candidate_id != "manual":
        cuts = plan_cuts(words, plan.start, plan.end, _loudness(info),
                         min_length=campaign.duration.min_seconds)
        if cuts:
            segments = keep_segments(cuts, plan.start, plan.end)
            render_source = join_segments(
                source_path, segments, work / f"{plan.clip_id}_tight.mkv",
                width=info.media.width, height=info.media.height,
                has_audio=info.media.has_audio, punch_in=allowed.visual_effects)
            render_media = probe(render_source)
            removed = plan.duration - sum(b - a for a, b in segments)
            kinds = sorted({c.reason.split()[0] for c in cuts})
            plan = plan.model_copy(update={"refine_notes": [
                *plan.refine_notes,
                f"tightened: {len(cuts)} cut(s), {removed:.1f}s removed ({', '.join(kinds)})"]})
            render_words = remap_words(words, segments)
            render_plan = plan.model_copy(update={"start": 0.0,
                                                  "end": sum(b - a for a, b in segments)})
            log.info("%s %s", plan.clip_id, plan.refine_notes[-1])

    # Dark footage, lifted gently where visual effects are allowed (research R6.3).
    lift = None
    if allowed.visual_effects:
        lift = lift_for(darkness(render_source, render_plan.start, render_plan.duration))
        if lift:
            plan = plan.model_copy(update={"refine_notes": [*plan.refine_notes,
                                                            f"dark footage lifted (gamma {lift})"]})

    scans: list = []
    layout = plan_layout_for(
        render_source,
        scan_out=scans,
        start=render_plan.start, duration=render_plan.duration,
        out_width=width, out_height=height,
        sample_fps=config.render.face_sample_fps,
        min_face_ratio=config.qa.min_face_ratio,
        content_pane_share=config.render.content_pane_share,
        detect_screen_share=config.render.detect_screen_share,
        min_subject_face_ratio=config.render.min_subject_face_ratio,
        keep_everyone=config.render.keep_everyone_in_frame,
        per_shot_framing=config.render.per_shot_framing,
        opening_seconds=config.render.opening_full_screen_seconds,
        min_shot_seconds=config.render.min_shot_seconds,
        max_shots=config.render.max_shots,
    )
    plan = plan.model_copy(update={"layout": layout})
    render_plan = render_plan.model_copy(update={"layout": layout,
                                                 "refine_notes": plan.refine_notes})

    slug = slugify(plan.hook_text or plan.text, max_length=40)
    output = clips_dir / f"{plan.clip_id}_{slug}.mp4"

    render = render_clip(
        source=render_source,
        media=render_media,
        plan=render_plan,
        words=render_words,
        config=config,
        work_dir=work,
        output=output,
        draft=draft,
        campaign_credit=(campaign.required_credit_text
                         if campaign.burn_credit_in_video else ""),
        credit_position=campaign.credit_position,
        mask_profanity=campaign.mask_profanity_in_captions,
        # Loudness normalisation is a container edit: the audio's content is
        # untouched, so even "keep the original audio" briefs get it (R7.1).
        normalize_audio=True,
        lift_gamma=lift,
        show_captions=allowed.captions,
        # Where faces sit on the 9:16 frame, so captions move off them (R5.2).
        faces=faces_on_screen(scans[0], layout, width, height) if scans else None,
    )

    rendered = probe(output)
    context = QAContext(
        plan=render_plan,
        words=render_words,
        ass_text=render.ass_path.read_text(encoding="utf-8") if render.ass_path else "",
        duration_bounds=(campaign.duration.min_seconds, campaign.duration.max_seconds),
        expected_width=width,
        expected_height=height,
        expected_fps=config.render.fps,
        pre_roll=config.refine.pre_roll,
        captions_expected=allowed.captions,
    )
    qa = check_clip(output, context, config.qa, config.render)
    rules = compliance.check_clip(plan, campaign, duration=rendered.duration)

    return ClipRecord(
        plan=plan,
        file=output,
        qa=qa,
        compliance=rules,
        rendered_duration=rendered.duration,
        caption_fixes=fixes,
    )


def _build_plan(
    pick: Pick,
    outcome: ScoreOutcome,
    *,
    config: Config,
    campaign: CampaignConfig,
    rank: int,
    attempt: int,
) -> ClipPlan | None:
    """Refine a selected candidate's boundaries into a renderable plan."""
    sentences: Sentences = outcome.sentences
    transcript: Transcript = outcome.transcript
    candidate = pick.candidate

    if candidate.quiet:
        # Found by its silence and judged by watching (D68): refinement snaps to
        # sentences and trims wordless edges, which would cut the moment itself.
        end = min(candidate.end, candidate.start + campaign.duration.max_seconds)
        bounds = RefinedBounds(candidate.start, end, notes=["no dialogue: cut as found"])
        if end - candidate.start < campaign.duration.min_seconds:
            return None
    else:
        bounds = refine(
            candidate.start, candidate.end,
            transcript=transcript,
            sentences=sentences.sentences,
            cfg=config.refine,
            min_duration=campaign.duration.min_seconds,
            max_duration=campaign.duration.max_seconds,
            source_duration=outcome.info.media.duration,
        )
    if bounds.dropped:
        log.info("%s dropped during refinement: %s", candidate.candidate_id,
                 bounds.drop_reason)
        return None
    if (not candidate.quiet and candidate.scene_start is not None
            and candidate.scene_end is not None):
        # Refinement pads and snaps edges; for scripted TV that must never
        # reach into the neighbouring scene -- the "unrelated scene at the
        # beginning or end" reported on real clips.
        start = max(bounds.start, candidate.scene_start)
        end = min(bounds.end, candidate.scene_end)
        # A window that opens or closes with its scene keeps the scene's cut.
        # Refinement trims wordless lead-ins, which here cut 2.3s off a scene
        # opening on a sign reading "I don't just want to have sex with you",
        # so the clip began mid-line on "just want to...".
        if candidate.start <= candidate.scene_start + 0.05:
            start = candidate.scene_start
        if candidate.end >= candidate.scene_end - 0.05:
            end = candidate.scene_end
        # ...but not a long silent stretch: the first three FX posts all lost
        # most viewers at 0:01, one opening on a wordless shot of a house.
        start, end = trim_silent_edges(start, end, transcript.words,
                                       lead=config.refine.max_lead_in,
                                       tail=config.refine.max_tail)
        if end - start < campaign.duration.min_seconds:
            log.info("%s dropped: only %.1fs once kept inside its scene",
                     candidate.candidate_id, end - start)
            return None
        bounds = dataclasses.replace(bounds, start=start, end=end)

    values = next((v for v in outcome.signals.values
                   if v.candidate_id == candidate.candidate_id), None)
    scores = (values.llm_a or values.llm_b) if values else None

    text = " ".join(
        w.text for w in transcript.words
        if bounds.start <= (w.start + w.end) / 2 < bounds.end
    ).strip() or candidate.text

    plan = ClipPlan(
        clip_id=f"{attempt:03d}_{to_slug_timestamp(bounds.start)}",
        candidate_id=candidate.candidate_id,
        rank=rank,
        start=bounds.start,
        end=bounds.end,
        text=text,
        composite=pick.scored.composite,
        hook_text=(rotation.pick(campaign, "hook", campaign.hook_texts)
                   if campaign.hook_texts else _hook(values, scores)),
        suggested_caption=scores.suggested_caption if scores else "",
        hashtags=list(scores.hashtags) if scores else [],
        caption_style=config.render.caption_style,
        refine_notes=bounds.notes,
        lead_in=next((round(w.start - bounds.start, 2) for w in transcript.words
                      if bounds.start - 0.05 <= w.start < bounds.end), None),
        hook_shown=bool(config.render.show_hook_text
                        and (campaign.hook_texts or _hook(values, scores))),
    )
    return compliance.apply_campaign_caption(plan, campaign, pick=rotation.pick)


def _hook(values, scores) -> str:
    """The on-screen hook: the watched one when the picture carries the moment."""
    if values is not None and values.visual_hook and (values.visual_payoff or 0) >= VISUAL_HOOK_MIN:
        return values.visual_hook
    return scores.hook_text if scores else ""


class RightsError(IngestError):
    """The source does not carry the licence or come from the channel a campaign requires."""


def check_rights(source: str, campaign: CampaignConfig, *, probe_fn=None) -> None:
    """Refuse a source a campaign's licence rules do not cover, before any download.

    Only campaigns with `require_license` or `allowed_channels` are checked; the
    others rely on `source_authorization` (a campaign brief, a creator's
    permission) as before.
    """
    if not campaign.require_license and not campaign.allowed_channels:
        return
    if not is_url(source):
        raise RightsError(
            f"campaign {campaign.name!r} requires a verified licence, which a local file "
            "cannot show. Run it on the video's URL so its listing can be checked.")
    rights = (probe_fn or probe_rights)(source)
    need = (campaign.require_license or "").lower()
    if need and need not in rights["license"].lower():
        raise RightsError(
            f"{rights['title'] or source}: its listing says licence "
            f"{rights['license'] or 'none (standard YouTube licence)'!r}, but campaign "
            f"{campaign.name!r} requires {campaign.require_license!r}. Not clipping it.")
    allowed = {c.strip().lower() for c in campaign.allowed_channels if c.strip()}
    if allowed and not ({rights["channel"].lower(), rights["channel_id"].lower()} & allowed):
        raise RightsError(
            f"{rights['title'] or source}: uploaded by {rights['channel'] or 'unknown'!r}, "
            f"not the owner channel(s) campaign {campaign.name!r} allows. A licence on "
            "someone else's re-upload grants nothing. Not clipping it.")
    log.info("licence checked: %r on %r (%s)", rights["license"], rights["channel"],
             rights["channel_id"])


def trim_silent_edges(start: float, end: float, words: list[Word], *,
                      lead: float | None, tail: float | None) -> tuple[float, float]:
    """Cut silence before the first word to `lead` and after the last to `tail`.

    `None` leaves that edge alone. Words are the clip's own: the first starting
    at or after `start`, the last ending at or before `end`.
    """
    inside = [w for w in words if w.start >= start - 0.05 and w.end <= end + 0.05]
    if not inside:
        return start, end
    if lead is not None and inside[0].start - start > lead:
        start = inside[0].start - lead
    if tail is not None and end - inside[-1].end > tail:
        end = inside[-1].end + tail
    return start, end


def _reject(record: ClipRecord, rejected_dir: Path, result: RunResult) -> None:
    """Move a failed clip to `rejected/` with its reason file."""
    ensure(rejected_dir)
    destination = rejected_dir / record.file.name
    try:
        record.file.replace(destination)
    except OSError as exc:  # pragma: no cover - locked by a viewer
        log.warning("could not move %s to rejected/: %s", record.file.name, exc)
        destination = record.file

    moved = ClipRecord(
        plan=record.plan, file=destination, qa=record.qa,
        compliance=record.compliance, components=record.components, raw=record.raw,
        llm_a_total=record.llm_a_total, llm_b_total=record.llm_b_total,
        rendered_duration=record.rendered_duration,
    )
    write_rejection_reason(moved, destination.with_suffix(".reason.json"))
    result.rejected.append(moved)
    log.warning("%s rejected: %s", record.plan.clip_id,
                summarize(record.qa) if record.qa.status == "fail"
                else record.compliance.summary())


# Words either side of a clip shown to the corrector as context. About a
# sentence each way: enough to know the topic, cheap enough to send per clip.
CORRECTION_CONTEXT_WORDS = 30


_LOUDNESS: dict[str, object] = {}


def _loudness(info: SourceInfo):
    """The source's short-window loudness (for tightening), read once per source."""
    from .render.tighten import Loudness

    path = info.audio_path
    if not path or not Path(path).exists():
        return None
    if path not in _LOUDNESS:
        try:
            _LOUDNESS.clear()  # one source at a time: the arrays are large
            _LOUDNESS[path] = Loudness.from_wav(Path(path))
        except (OSError, ValueError) as exc:
            log.warning("no loudness for tightening (%s); cutting on timings alone", exc)
            return None
    return _LOUDNESS[path]


def _description(plan: ClipPlan, words: list[Word], campaign: CampaignConfig,
                 backends: list[LLMBackend] | None, config: Config) -> str:
    """The clip's searchable description (campaign/description.py), or "" on any failure."""
    from .campaign.description import describe

    text = " ".join(w.text for w in words
                    if plan.start <= (w.start + w.end) / 2 < plan.end).strip()
    if not backends:
        try:
            backends = [build_backend(config)]
        except Exception as exc:  # no key, backend not installed, ...
            log.warning("no description: %s", exc)
            return ""
    return describe(text, campaign, plan.suggested_caption, backends)


def _correction(config: Config, override: str | None, info) -> tuple[list[LLMBackend] | None, AudioRecheck | None]:
    """The caption corrector and its audio re-listener, if correction is on and possible."""
    if not config.llm.correct_captions:
        return None, None
    try:
        corrector = _correction_backends(config, override)
    except Exception as exc:  # no key, backend not installed, ...
        log.warning("caption correction disabled: %s", exc)
        return None, None
    audio = Path(info.audio_path) if info.audio_path else None
    # Loads Whisper only if some proposal survives the text checks.
    return corrector, (AudioRecheck(audio, config.transcription) if audio and audio.exists() else None)


def _correction_backends(config: Config, override: str | None) -> list[LLMBackend]:
    """The preferred correction model, then the scoring model as a fallback.

    The preferred model gets no retries and a short timeout: the stronger free
    models were measured taking ~90s per call and returning 503 "high demand"
    and 429 quota errors. Waiting through that for every clip turned a 3-minute
    run into 22; falling back promptly keeps the run fast.
    """
    default = build_backend(config, override=override)
    preferred = config.llm.correction_model
    if not preferred or preferred == default.model:
        return [default]
    strong = create_backend(default.name, model=preferred, max_retries=0,
                            requests_per_minute=config.llm.requests_per_minute,
                            timeout=config.llm.correction_timeout)
    return [strong, default]


def _corrected_words(words: list[Word], plan: ClipPlan,
                     corrector: list[LLMBackend] | None,
                     recheck: AudioRecheck | None = None,
                     rejected: frozenset[tuple[str, str]] = frozenset(),
                     ) -> tuple[list[Word], list[WordFix]]:
    """The transcript with this clip's misheard words fixed, and what changed.

    Only the clip's own words can change; the rest of the transcript is passed
    through untouched, so the fix cannot leak into another clip's captions.
    """
    if corrector is None:
        return words, []
    inside = [i for i, w in enumerate(words) if plan.start <= (w.start + w.end) / 2 < plan.end]
    if not inside:
        return words, []
    lo, hi = inside[0], inside[-1] + 1
    fixed, fixes = correct_words(
        words[lo:hi],
        before=words[max(0, lo - CORRECTION_CONTEXT_WORDS):lo],
        after=words[hi:hi + CORRECTION_CONTEXT_WORDS],
        backend=corrector, cache=LLMCache(), recheck=recheck,
        rejected=rejected, names=names_in(words),
    )
    if not fixes:
        return words, []
    return [*words[:lo], *fixed, *words[hi:]], fixes
