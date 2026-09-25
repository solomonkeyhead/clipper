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
import time
from dataclasses import dataclass, field
from pathlib import Path

from .campaign import compliance
from .campaign.manifest import ClipRecord, write_outputs, write_rejection_reason
from .candidates.boundaries import refine
from .config import CampaignConfig, Config
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

    score_started = time.perf_counter()
    outcome = score(source, config, backend_override=backend_override, force=force)
    timings["score"] = time.perf_counter() - score_started

    result = RunResult(
        info=outcome.info,
        signals_available=outcome.available_signals,
        weights_used=outcome.scored.weights_used,
    )

    limit = min(top or config.selection.top_n, campaign.max_clips_per_source)
    selection = choose(outcome, config, limit=limit)
    result.selection_note = selection.stopped_because

    out_dir = ensure(out_root / outcome.info.source_id)
    clips_dir = ensure(out_dir / "clips")
    rejected_dir = out_dir / "rejected"
    work = ensure(out_dir / "work")

    corrector = None
    recheck = None
    if config.llm.correct_captions:
        try:
            corrector = _correction_backends(config, backend_override)
        except Exception as exc:  # no key, backend not installed, ...
            log.warning("caption correction disabled: %s", exc)
        audio = Path(outcome.info.audio_path) if outcome.info.audio_path else None
        if corrector and audio and audio.exists():
            # Loads Whisper only if some proposal survives the text checks.
            recheck = AudioRecheck(audio, config.transcription)

    render_started = time.perf_counter()
    _render_with_replacement(
        selection.picks, selection.reserves, outcome,
        config=config, campaign=campaign, clips_dir=clips_dir,
        rejected_dir=rejected_dir, work=work, draft=draft,
        limit=limit, result=result, corrector=corrector, recheck=recheck,
    )
    timings["render_and_qa"] = time.perf_counter() - render_started

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


SCRIPTED_MAX_SILENCE = 0.55
# TikTok's creative guidance: land the proposition in the first 3 seconds.
SCRIPTED_OPENING_SECONDS = 3.0
SCRIPTED_TARGET = (20.0, 45.0)
SCRIPTED_MAX_SECONDS = 90.0
SCRIPTED_MAX_LEAD_IN = 0.5    # silence before the first word
SCRIPTED_REACTION_TAIL = 1.0  # held after the last line, into silence only
SCRIPTED_MAX_TAIL = 1.5       # never more silence than this at the end


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
    if campaign.scripted:
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
    candidates = config.candidates.model_copy(update=candidate_updates)
    render = config.render.model_copy(update={
        "show_hook_text": config.render.show_hook_text and campaign.hook_overlay,
        # Held through the first 3 seconds, where viewers decide to stay.
        "hook_text_seconds": (max(config.render.hook_text_seconds, SCRIPTED_OPENING_SECONDS)
                              if campaign.scripted else config.render.hook_text_seconds),
        "keep_everyone_in_frame": config.render.keep_everyone_in_frame or campaign.scripted,
        # The first seconds decide whether a viewer stays: fill the screen then.
        "opening_full_screen_seconds": (SCRIPTED_OPENING_SECONDS if campaign.scripted
                                        else config.render.opening_full_screen_seconds),
        # A TV set or window in a scene is not a screen share.
        "detect_screen_share": config.render.detect_screen_share and not campaign.scripted,
    })
    llm = config.llm.model_copy(update={
        "drop_needs_prior_context":
            config.llm.drop_needs_prior_context and not campaign.scripted,
    })
    return config.model_copy(update={"candidates": candidates, "render": render, "llm": llm,
                                     "refine": refine})


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
    """Render, QA, and pull a reserve for each failure until the quota is met."""
    queue = list(picks)
    spare = list(reserves)
    attempted: set[str] = set()
    attempt = 0

    while queue and len(result.accepted) < limit:
        pick = queue.pop(0)
        if pick.candidate.candidate_id in attempted:
            continue
        attempted.add(pick.candidate.candidate_id)

        # `attempt` only ever increases, so ids are unique even when a clip is
        # rejected and replaced. Reusing the accepted-clip rank for this made
        # two rejected files collide on both id and filename.
        attempt += 1
        rank = len(result.accepted) + 1
        record = _produce_one(
            pick, outcome, config=config, campaign=campaign,
            clips_dir=clips_dir, work=work, draft=draft,
            rank=rank, attempt=attempt, corrector=corrector, recheck=recheck,
        )
        if record is None:
            reserve = _next_reserve(spare, result, queue, config.candidates.min_gap_seconds)
            if reserve is not None:
                queue.append(reserve)
            continue

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
            continue

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

    width, height = output_size(config.render, draft=draft)
    source_path = Path(outcome.info.media.path)

    plan = plan.model_copy(update={"layout": plan_layout_for(
        source_path,
        start=plan.start, duration=plan.duration,
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
    )})

    words, fixes = _corrected_words(outcome.transcript.words, plan, corrector, recheck,
                                    config.llm.rejected_fix_pairs)

    slug = slugify(plan.hook_text or plan.text, max_length=40)
    output = clips_dir / f"{plan.clip_id}_{slug}.mp4"

    render = render_clip(
        source=source_path,
        media=outcome.info.media,
        plan=plan,
        words=words,
        config=config,
        work_dir=work,
        output=output,
        draft=draft,
        campaign_credit=(campaign.required_credit_text
                         if campaign.burn_credit_in_video else ""),
        credit_position=campaign.credit_position,
        mask_profanity=campaign.mask_profanity_in_captions,
        normalize_audio=not campaign.keep_original_audio,
    )

    rendered = probe(output)
    context = QAContext(
        plan=plan,
        words=words,
        ass_text=render.ass_path.read_text(encoding="utf-8") if render.ass_path else "",
        duration_bounds=(campaign.duration.min_seconds, campaign.duration.max_seconds),
        expected_width=width,
        expected_height=height,
        expected_fps=config.render.fps,
        pre_roll=config.refine.pre_roll,
        audio_untouched=campaign.keep_original_audio,
    )
    qa = check_clip(output, context, config.qa, config.render)
    rules = compliance.check_clip(plan, campaign, duration=rendered.duration)

    entry = next((s for s in outcome.scored.scored
                  if s.candidate_id == pick.candidate.candidate_id), None)
    values = next((v for v in outcome.signals.values
                   if v.candidate_id == pick.candidate.candidate_id), None)
    weights = config.llm.rubric_weights.as_dict()

    return ClipRecord(
        plan=plan,
        file=output,
        qa=qa,
        compliance=rules,
        components=dict(entry.components) if entry else {},
        raw=dict(entry.raw) if entry else {},
        llm_a_total=values.llm_a.total(weights) if values and values.llm_a else None,
        llm_b_total=values.llm_b.total(weights) if values and values.llm_b else None,
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
    if candidate.scene_start is not None and candidate.scene_end is not None:
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
        hook_text=scores.hook_text if scores else "",
        suggested_caption=scores.suggested_caption if scores else "",
        hashtags=list(scores.hashtags) if scores else [],
        caption_style=config.render.caption_style,
        refine_notes=bounds.notes,
        lead_in=next((round(w.start - bounds.start, 2) for w in transcript.words
                      if bounds.start - 0.05 <= w.start < bounds.end), None),
        hook_shown=bool(config.render.show_hook_text and scores and scores.hook_text),
    )
    return compliance.apply_campaign_caption(plan, campaign)


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
