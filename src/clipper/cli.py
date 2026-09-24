"""`clipper` command line surface (Typer).

Commands that are not implemented yet exit with code 2 and say which phase of
BUILD_BRIEF.md covers them, rather than pretending to work.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from . import __version__
from .utils.logging import console, setup_logging

app = typer.Typer(
    name="clipper",
    help="Turn one long video into ranked, captioned 9:16 clips.",
    add_completion=False,
    rich_markup_mode=None,
)

VerboseOpt = Annotated[bool, typer.Option("--verbose", "-v", help="Debug-level console logging.")]

_STATUS_STYLE = {"ok": "green", "warn": "yellow", "fail": "red"}
_STATUS_GLYPH = {"ok": "PASS", "warn": "WARN", "fail": "FAIL"}


def _not_implemented(command: str, phase: str) -> None:
    console.print(
        f"[yellow]`clipper {command}` is not implemented yet.[/yellow] "
        f"It lands in {phase} (see BUILD_BRIEF.md section 15)."
    )
    raise typer.Exit(code=2)


def _work(source_id: str) -> Path:
    from .paths import work_dir

    return work_dir(source_id)


def _resolve_source(source: str) -> str:
    """Accept either a source id or a path/URL.

    `clipper score <id>` is the documented form, but re-typing a path is the
    natural thing to do, and an id that has already been ingested resolves back
    to its own media file without re-downloading anything.
    """
    from .paths import work_dir

    info_path = work_dir(source) / "info.json"
    if info_path.is_file():
        from .models import SourceInfo

        return SourceInfo.load(info_path).media.path
    return source


def _require(path: Path, model_cls, source_id: str, producing_command: str):
    """Load a stage artifact, or explain which command produces it.

    A missing artifact is the most common way a partially-run pipeline fails, so
    the error names the exact command to run rather than raising FileNotFoundError.
    """
    if not path.is_file():
        from .ingest.download import find_source_ids

        known = find_source_ids()
        hint = (
            f"\nKnown source ids: {', '.join(known[:5])}"
            if known else "\nNo sources have been ingested yet."
        )
        console.print(
            f"[red]{path.name} not found for source {source_id!r}.[/red]\n"
            f"Run `clipper {producing_command} <source>` first.{hint}"
        )
        raise typer.Exit(code=1)
    return model_cls.load(path)


@app.callback(invoke_without_command=True)
def _root(
    ctx: typer.Context,
    version: Annotated[
        bool, typer.Option("--version", help="Print the version and exit.")
    ] = False,
) -> None:
    if version:
        console.print(f"clipper {__version__}")
        raise typer.Exit()
    # `clipper` with no subcommand shows help and succeeds; Typer's own
    # no_args_is_help would exit 2, which reads as an error to a shell script.
    if ctx.invoked_subcommand is None:
        console.print(ctx.get_help())
        raise typer.Exit()


@app.command()
def doctor(
    verbose: VerboseOpt = False,
    fix: Annotated[
        bool, typer.Option("--fix", help="Download the missing font and face-detection model.")
    ] = False,
) -> None:
    """Check that this machine can actually run the pipeline."""
    setup_logging(verbose)
    from . import assets
    from .doctor import Status, run_checks

    if fix:
        missing = assets.missing_assets()
        if not missing:
            console.print("[green]Nothing to fix: all assets are present.[/green]")
        else:
            console.print(f"Downloading {len(missing)} missing asset(s)...")
            downloaded, errors = assets.download_missing()
            for path in downloaded:
                console.print(f"  [green]+[/green] {path.name}  ({path.stat().st_size // 1024} KB)")
            for err in errors:
                console.print(f"  [red]![/red] {err}")
        console.print()

    table = Table(show_header=True, header_style="bold", box=None, pad_edge=False)
    table.add_column("", width=4)
    table.add_column("Check", style="bold", min_width=18)
    table.add_column("Detail", overflow="fold")

    results = list(run_checks())
    for r in results:
        style = _STATUS_STYLE[r.status.value]
        table.add_row(
            Text(_STATUS_GLYPH[r.status.value], style=style),
            r.name,
            r.detail or "-",
        )
    console.print(table)

    problems = [r for r in results if r.status is not Status.OK and r.fix]
    if problems:
        console.print()
        for r in problems:
            colour = _STATUS_STYLE[r.status.value]
            console.print(
                Panel(
                    r.fix,
                    title=f"[{colour}]{_STATUS_GLYPH[r.status.value]}[/{colour}] {r.name}",
                    title_align="left",
                    border_style=colour,
                )
            )

    fails = sum(1 for r in results if r.status is Status.FAIL)
    warns = sum(1 for r in results if r.status is Status.WARN)
    oks = len(results) - fails - warns
    console.print()
    if fails:
        console.print(f"[red]{fails} blocking problem(s)[/red], {warns} warning(s), {oks} ok.")
        raise typer.Exit(code=1)
    if warns:
        console.print(f"[yellow]{warns} warning(s)[/yellow], {oks} ok. clipper can run.")
    else:
        console.print(f"[green]All {oks} checks passed.[/green]")


@app.command()
def run(
    source: Annotated[str, typer.Argument(help="Local video file, or a URL you are authorized to clip.")],
    campaign: Annotated[Path, typer.Option("--campaign", "-c", help="Path to campaigns/<name>.yaml.")],
    top: Annotated[int, typer.Option("--top", help="Maximum clips to produce.")] = 5,
    out: Annotated[Path | None, typer.Option("--out", help="Output directory.")] = None,
    draft: Annotated[bool, typer.Option("--draft", help="Fast 540x960 render for iteration.")] = False,
    backend: Annotated[str | None, typer.Option("--backend", help="gemini | ollama | anthropic | mock")] = None,
    force: Annotated[str | None, typer.Option("--force", help="Stage to invalidate, or 'all'.")] = None,
    verbose: VerboseOpt = False,
) -> None:
    """Full pipeline: ingest through manifest."""
    setup_logging(verbose)
    from . import runner
    from .config import AUTHORIZATION_REMINDER, CampaignConfig, Config
    from .ingest.download import IngestError
    from .llm.base import LLMConfigError, LLMError
    from .paths import data_root
    from .transcribe.whisper import TranscriptionError
    from .utils.timecode import format_duration

    cfg = Config.load()
    try:
        campaign_cfg = CampaignConfig.load(campaign)
    except (FileNotFoundError, ValueError) as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc

    # Section 2: the authorization is surfaced on every run, not buried in a file.
    console.print(f"[bold]Campaign:[/bold] {campaign_cfg.name}")
    console.print(f"[bold]Authorization:[/bold] {campaign_cfg.source_authorization}")
    console.print(f"[dim]{AUTHORIZATION_REMINDER}[/dim]\n")

    out_root = out or (data_root() / "out")
    forced = {s.strip() for s in force.split(",")} if force else set()

    try:
        result = runner.run(
            _resolve_source(source),
            config=cfg,
            campaign=campaign_cfg,
            out_root=out_root,
            top=top,
            draft=draft,
            backend_override=backend,
            force=forced,
        )
    except (IngestError, TranscriptionError, LLMConfigError) as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc
    except LLMError as exc:
        console.print(f"[red]LLM scoring failed: {exc}[/red]")
        raise typer.Exit(code=1) from exc

    _print_run_summary(result, format_duration)

    if not result.accepted:
        raise typer.Exit(code=1)


def _print_run_summary(result, format_duration) -> None:
    from .campaign.compliance import full_caption

    console.print()
    if result.accepted:
        table = Table(box=None, pad_edge=False, header_style="bold")
        table.add_column("clip")
        table.add_column("start", justify="right")
        table.add_column("dur", justify="right")
        table.add_column("comp", justify="right")
        table.add_column("layout")
        table.add_column("qa")
        table.add_column("hook", overflow="ellipsis", max_width=36, no_wrap=True)

        from .utils.timecode import to_slug_timestamp

        for record in result.accepted:
            plan = record.plan
            table.add_row(
                plan.clip_id,
                to_slug_timestamp(plan.start),
                f"{record.duration:.0f}s",
                f"{plan.composite:.3f}",
                plan.layout.describe if plan.layout else "-",
                ("[green]pass[/green]" if record.qa.status == "pass"
                 else f"[yellow]{record.qa.status}[/yellow]"),
                plan.hook_text or "-",
            )
        console.print(table)
        console.print()
        for record in result.accepted:
            console.print(f"[dim]{record.plan.clip_id}:[/dim] {full_caption(record.plan)}")
    else:
        console.print("[yellow]No clips were produced.[/yellow]")
        console.print(result.selection_note)
        console.print(
            "\n[dim]This is deliberate. Returning nothing beats returning filler, "
            "which earns no views and risks originality flags.[/dim]"
        )

    if result.rejected:
        console.print(f"\n[yellow]{len(result.rejected)} clip(s) failed QA[/yellow] "
                      "and were moved to rejected/ with a reason file:")
        for record in result.rejected:
            reasons = "; ".join(c.detail for c in record.qa.failures) \
                or record.compliance.summary()
            console.print(f"  {record.plan.clip_id}: {reasons}")

    console.print()
    console.print("  ".join(
        f"[bold]{name}[/bold] {format_duration(value)}"
        for name, value in result.timings.items()
    ))
    if result.outputs:
        console.print(f"\nOutputs in [bold]{result.outputs['report_md'].parent}[/bold]")
        for key in ("report_md", "manifest_csv", "performance_csv"):
            if key in result.outputs:
                console.print(f"  {result.outputs[key].name}")


@app.command()
def transcribe(
    source: Annotated[str, typer.Argument(help="Local video file, or a URL you are authorized to clip.")],
    force: Annotated[bool, typer.Option("--force", help="Re-ingest and re-transcribe, ignoring cache.")] = False,
    model: Annotated[str | None, typer.Option("--model", help="Override transcription.model.")] = None,
    verbose: VerboseOpt = False,
) -> None:
    """Ingest a source and transcribe it to word-level timestamps."""
    setup_logging(verbose)
    from .config import Config
    from .ingest.download import IngestError, describe_heatmap, ingest
    from .transcribe.segment import segment
    from .transcribe.whisper import TranscriptionError
    from .transcribe.whisper import transcribe as run_transcribe
    from .utils.timecode import format_duration

    cfg = Config.load()
    if model:
        cfg = cfg.model_copy(
            update={"transcription": cfg.transcription.model_copy(update={"model": model})}
        )

    try:
        info = ingest(source, force=force)
    except IngestError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc

    console.print(
        f"[bold]{info.title}[/bold]  {format_duration(info.media.duration)}  "
        f"{info.media.width}x{info.media.height}  ({describe_heatmap(info)})"
    )

    try:
        transcript, stats = run_transcribe(info, cfg.transcription, force=force)
    except TranscriptionError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc

    sentences = segment(transcript)
    sentences.save(_work(info.source_id) / "sentences.json")

    console.print(
        f"{len(transcript.words)} words, {len(sentences.sentences)} sentences, "
        f"language={transcript.language}"
    )
    if stats:
        console.print(
            f"transcribed in {stats.wall_seconds:.1f}s "
            f"([bold]{stats.realtime_factor:.1f}x realtime[/bold], "
            f"{stats.model}/{stats.compute_type} on {stats.device})"
        )
    console.print(f"source_id: [bold]{info.source_id}[/bold]")


@app.command()
def candidates(
    source_id: Annotated[str, typer.Argument(help="Source id from a previous transcribe.")],
    force: Annotated[bool, typer.Option("--force", help="Regenerate, ignoring cache.")] = False,
    limit: Annotated[int, typer.Option("--limit", help="How many to print.")] = 15,
    verbose: VerboseOpt = False,
) -> None:
    """Generate candidate windows from an existing transcript."""
    setup_logging(verbose)
    from .candidates.windows import generate
    from .config import Config
    from .models import Candidates, Sentences, Transcript

    cfg = Config.load()
    work = _work(source_id)
    transcript = _require(work / "transcript.json", Transcript, source_id, "transcribe")
    sentences = _require(work / "sentences.json", Sentences, source_id, "transcribe")

    out_path = work / "candidates.json"
    if out_path.is_file() and not force:
        result = Candidates.load(out_path)
        console.print(f"[dim]reusing {out_path.name}; pass --force to regenerate[/dim]")
    else:
        result = generate(transcript, sentences, cfg.candidates,
                          source_duration=transcript.duration)
        result.save(out_path)

    if not result.candidates:
        console.print(
            "[yellow]No candidates survived the hard filters.[/yellow] Common causes: "
            "the source is mostly silence, it is shorter than "
            f"{cfg.candidates.min_seconds:.0f}s of usable speech, or every window "
            f"exceeds candidates.max_silence_ratio ({cfg.candidates.max_silence_ratio:.0%}). "
            "Re-run with --verbose to see the per-reason drop counts."
        )
        raise typer.Exit(code=1)

    table = Table(box=None, pad_edge=False, header_style="bold")
    table.add_column("id")
    table.add_column("start", justify="right")
    table.add_column("dur", justify="right")
    table.add_column("silence", justify="right")
    table.add_column("pre", justify="right")
    table.add_column("text", overflow="ellipsis", max_width=64)

    from .utils.timecode import to_slug_timestamp

    for candidate in result.candidates[:limit]:
        table.add_row(
            candidate.candidate_id,
            to_slug_timestamp(candidate.start),
            f"{candidate.duration:.0f}s",
            f"{candidate.silence_ratio:.0%}",
            f"{candidate.pre_score:.2f}",
            candidate.text,
        )
    console.print(table)
    console.print(
        f"\n{len(result.candidates)} candidates"
        + (f" (showing {limit})" if len(result.candidates) > limit else "")
    )


@app.command()
def score(
    source: Annotated[str, typer.Argument(help="A source id, or a file/URL to ingest first.")],
    backend: Annotated[str | None, typer.Option("--backend", help="gemini | ollama | anthropic | mock")] = None,
    top: Annotated[int, typer.Option("--top", help="How many rows to print.")] = 12,
    force: Annotated[str | None, typer.Option("--force", help="Stage to invalidate, or 'all'.")] = None,
    verbose: VerboseOpt = False,
) -> None:
    """Compute every signal, the composite score, and the selection."""
    setup_logging(verbose)
    from . import pipeline
    from .config import Config
    from .llm.base import LLMConfigError, LLMError
    from .utils.timecode import format_duration, to_slug_timestamp

    cfg = Config.load()
    forced = {s.strip() for s in force.split(",")} if force else set()

    try:
        outcome = pipeline.score(
            _resolve_source(source), cfg, backend_override=backend, force=forced
        )
    except LLMConfigError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc
    except LLMError as exc:
        console.print(f"[red]LLM scoring failed: {exc}[/red]")
        raise typer.Exit(code=1) from exc

    if not outcome.candidates.candidates:
        console.print("[yellow]No candidates to score.[/yellow]")
        raise typer.Exit(code=1)

    selection = pipeline.choose(outcome, cfg)
    picked = {p.scored.candidate_id for p in selection.picks}
    by_id = {c.candidate_id: c for c in outcome.candidates.candidates}

    table = Table(box=None, pad_edge=False, header_style="bold")
    table.add_column("")
    table.add_column("id")
    table.add_column("start", justify="right")
    table.add_column("dur", justify="right")
    table.add_column("comp", justify="right")
    for name in outcome.available_signals:
        table.add_column(name[:4], justify="right")
    table.add_column("llm/10", justify="right")
    table.add_column("text", overflow="ellipsis", max_width=52, no_wrap=True)

    for entry in outcome.scored.scored[:top]:
        candidate = by_id.get(entry.candidate_id)
        if candidate is None:
            continue
        marker = "[green]*[/green]" if entry.candidate_id in picked else " "
        row = [
            marker,
            entry.candidate_id,
            to_slug_timestamp(candidate.start),
            f"{candidate.duration:.0f}s",
            "-" if entry.dropped else f"{entry.composite:.3f}",
        ]
        row += [f"{entry.components.get(n, 0):.2f}" if not entry.dropped else "-"
                for n in outcome.available_signals]
        row.append(f"{entry.raw.get('llm', 0):.1f}" if "llm" in entry.raw else "-")
        row.append(entry.drop_reason if entry.dropped else candidate.text)
        table.add_row(*row)

    console.print(table)
    console.print(
        f"\n[green]*[/green] = selected. "
        f"{selection.count} clip(s) selected of {len(outcome.scored.scored)} scored; "
        f"{selection.stopped_because}."
    )
    console.print(
        f"signals: {', '.join(outcome.available_signals)}   weights: "
        + ", ".join(f"{k}={v:.2f}" for k, v in outcome.scored.weights_used.items())
    )
    if outcome.transcribe_stats:
        s = outcome.transcribe_stats
        console.print(
            f"transcription: {format_duration(s.audio_seconds)} in {s.wall_seconds:.1f}s "
            f"({s.realtime_factor:.1f}x realtime, {s.model}/{s.compute_type})"
        )
    if outcome.llm_usage:
        usage = outcome.llm_usage
        if usage.calls:
            console.print(f"LLM: {usage.summary()}   cache: {outcome.cache_stats}")
        elif usage.failed:
            console.print(
                f"[red]LLM: every call failed ({usage.failed} batch(es)); "
                f"candidates were scored without the LLM signal.[/red]"
            )
        else:
            # Worth saying explicitly: a fully cached run makes no calls at all,
            # and silence here reads like the LLM step was skipped.
            console.print(f"LLM: no calls needed, every score came from cache ({outcome.cache_stats})")
    console.print(f"source_id: [bold]{outcome.info.source_id}[/bold]")


@app.command()
def explain(
    source_id: Annotated[str, typer.Argument(help="Source id from a previous score.")],
    candidate_id: Annotated[str, typer.Argument(help="Candidate id, e.g. c003.")],
    verbose: VerboseOpt = False,
) -> None:
    """Show every signal behind one candidate, and why it was picked or dropped."""
    setup_logging(verbose)
    from . import pipeline
    from .config import Config
    from .utils.timecode import format_duration, to_ffmpeg

    cfg = Config.load()
    try:
        info, candidates, signals, scored = pipeline.load_scored(source_id)
    except FileNotFoundError as exc:
        console.print(
            f"[red]{source_id!r} has not been scored yet.[/red]\n"
            f"Run `clipper score {source_id}` first."
        )
        raise typer.Exit(code=1) from exc

    candidate = next((c for c in candidates.candidates if c.candidate_id == candidate_id), None)
    if candidate is None:
        known = ", ".join(c.candidate_id for c in candidates.candidates[:12])
        console.print(f"[red]No candidate {candidate_id!r}.[/red]\nKnown ids: {known}")
        raise typer.Exit(code=1)

    entry = next((s for s in scored.scored if s.candidate_id == candidate_id), None)
    values = next((v for v in signals.values if v.candidate_id == candidate_id), None)

    console.print(Panel(
        candidate.text,
        title=(
            f"{candidate_id}  {to_ffmpeg(candidate.start)} - {to_ffmpeg(candidate.end)}  "
            f"({format_duration(candidate.duration)})"
        ),
        title_align="left",
    ))

    if values is not None:
        _print_rubric(values)
        _print_features("Audio features", values.audio_features)
        _print_features("Text features", values.text_features)
        _print_features("Heatmap features", values.heatmap_features)

    if entry is None:
        console.print("[yellow]This candidate has no composite score.[/yellow]")
        return

    _print_composite(entry, scored)

    selection = pipeline.choose(
        pipeline.ScoreOutcome(
            info=info, transcript=None, sentences=None, candidates=candidates,
            signals=signals, scored=scored,
        ),
        cfg,
    )
    verdict = next((p for p in selection.picks if p.scored.candidate_id == candidate_id), None)
    if verdict is not None:
        console.print(f"\n[green]SELECTED[/green] as clip #{verdict.rank}.")
    else:
        reason = selection.rejections.get(candidate_id, "not among the top candidates")
        console.print(f"\n[yellow]NOT SELECTED[/yellow]: {reason}")


def _print_rubric(values) -> None:
    from .models import RubricScores

    opinions = [("A (editor)", values.llm_a), ("B (viewer)", values.llm_b)]
    if not any(s for _, s in opinions):
        console.print("\n[dim]No LLM scores for this candidate.[/dim]")
        return

    table = Table(title="LLM rubric", box=None, title_justify="left",
                  header_style="bold", pad_edge=False)
    table.add_column("criterion")
    for label, scores in opinions:
        if scores is not None:
            table.add_column(label, justify="right")

    fields = [f for f in RubricScores.model_fields if f.endswith(
        ("strength", "clarity", "payoff", "intensity", "quotability", "completeness"))]
    for field_name in fields:
        row = [field_name.replace("_", " ")]
        for _, scores in opinions:
            if scores is not None:
                row.append(f"{getattr(scores, field_name)}/10")
        table.add_row(*row)
    console.print()
    console.print(table)

    if values.llm_total is not None:
        console.print(f"  weighted total: [bold]{values.llm_total:.2f}/10[/bold]")

    for label, scores in opinions:
        if scores is None:
            continue
        flags = []
        if scores.needs_prior_context:
            flags.append("needs prior context")
        if scores.is_sponsor_or_ad:
            flags.append("sponsor/ad")
        if scores.policy_risk != "none":
            flags.append(f"policy risk: {scores.policy_risk}")
        if flags:
            console.print(f"  {label} flags: {', '.join(flags)}")

    first = values.llm_a or values.llm_b
    if first and first.hook_text:
        console.print(f"  hook: [italic]{first.hook_text}[/italic]")
    if first and first.suggested_caption:
        tags = " ".join(first.hashtags)
        console.print(f"  caption: {first.suggested_caption} {tags}")


def _print_features(title: str, features: dict) -> None:
    if not features:
        return
    console.print(f"\n[bold]{title}[/bold]")
    for key, value in features.items():
        console.print(f"  {key:<30} {value:>9.4f}")


def _print_composite(entry, scored) -> None:
    console.print("\n[bold]Composite[/bold]")
    if entry.dropped:
        console.print(f"  [red]dropped[/red]: {entry.drop_reason}")
        return

    for name, component in entry.components.items():
        weight = scored.weights_used.get(name, 0.0)
        raw = entry.raw.get(name)
        raw_text = f"raw {raw:.4f}" if raw is not None else ""
        console.print(
            f"  {name:<9} percentile {component:.3f}  x weight {weight:.2f}"
            f"  = {component * weight:.4f}   {raw_text}"
        )
    if entry.penalty < 1.0:
        console.print(f"  penalty   x{entry.penalty:.3f}  ({'; '.join(entry.penalty_reasons)})")
    console.print(f"  [bold]composite {entry.composite:.4f}[/bold]")


@app.command()
def render(
    source_id: Annotated[str, typer.Argument()],
    clips: Annotated[str | None, typer.Option("--clips", help="Comma-separated clip numbers, e.g. 1,3,4")] = None,
    draft: Annotated[bool, typer.Option("--draft")] = False,
    verbose: VerboseOpt = False,
) -> None:
    """Render selected clips from an existing selection."""
    setup_logging(verbose)
    _not_implemented("render", "Phase 4")


@app.command()
def watch(
    test_push: Annotated[bool, typer.Option("--test-push", help="Send one test push and exit.")] = False,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Judge new mail and print the verdicts; push nothing, remember nothing.")] = False,
    verbose: VerboseOpt = False,
) -> None:
    """Check the campaign mailbox once and push new campaigns that fit you."""
    setup_logging(verbose)
    from .config import Config
    from .pipeline import build_backend
    from .watch import mailbox, notify, watcher

    cfg = Config.load()
    try:
        if test_push:
            watcher.test_push(cfg.watch)
            console.print("[green]Test push sent.[/green] Check your phone.")
            return
        result = watcher.run_pass(cfg.watch, build_backend(cfg), dry_run=dry_run)
    except (watcher.WatchConfigError, mailbox.MailboxError, notify.PushError) as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc

    label = "would push" if dry_run else "pushed"
    console.print(f"{result.read} new message(s), {result.campaigns} campaign(s), "
                  f"{len(result.pushed)} {label}, {result.failed} to retry.")
    for v in result.pushed:
        console.print(f"  [green]{label}[/green] {v.source}: {v.name} ({v.rate}) -- {v.why}")
    for v, reason in result.skipped:
        if v.is_new_campaign:
            console.print(f"  [dim]skipped {v.source}: {v.name} -- {reason}[/dim]")


@app.command(name="eval")
def eval_cmd(
    videos: Annotated[Path, typer.Option("--videos")] = Path("eval/videos.yaml"),
    verbose: VerboseOpt = False,
) -> None:
    """Measure the ranking against YouTube 'most replayed' data."""
    setup_logging(verbose)
    _not_implemented("eval", "Phase 5")


@app.command()
def learn(
    performance: Annotated[Path, typer.Option("--performance", help="Your filled-in performance.csv.")],
    apply: Annotated[bool, typer.Option("--apply", help="Write the proposed weights to config.")] = False,
    verbose: VerboseOpt = False,
) -> None:
    """Propose new signal weights from logged view counts."""
    setup_logging(verbose)
    _not_implemented("learn", "Phase 6")


def main() -> None:
    # Load .env before any command reads an API key. override=False so a key
    # exported in the shell wins over a stale one in the file.
    from dotenv import load_dotenv

    from .paths import REPO_ROOT

    load_dotenv(REPO_ROOT / ".env", override=False)
    app()


if __name__ == "__main__":
    main()
