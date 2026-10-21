"""`clipper` command line surface (Typer).

Commands that are not implemented yet exit with code 2 and say which phase of
docs/BUILD_BRIEF.md covers them, rather than pretending to work.
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
        f"It lands in {phase} (see docs/BUILD_BRIEF.md section 15)."
    )
    raise typer.Exit(code=2)


def _work(source_id: str) -> Path:
    from .paths import work_dir

    return work_dir(source_id)


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
def learn(
    performance: Annotated[Path | None, typer.Option(
        "--performance", help="The performance log (default: data/performance.xlsx).")] = None,
    apply: Annotated[bool, typer.Option("--apply", help="Write the proposed weights to config.")] = False,
    verbose: VerboseOpt = False,
) -> None:
    """Compare how posted clips did with how the tool scored them."""
    setup_logging(verbose)
    from .config import DEFAULT_CONFIG_PATH, Config
    from .learn import analyze
    from .learn import log as perf

    path = performance or perf.log_path()
    rows = perf.read(path)
    if not rows:
        console.print(f"[red]No performance log at {path}.[/red] Each `clipper run` adds its "
                      "clips there; fill in the numbers for the ones you post.")
        raise typer.Exit(code=1)
    cfg = Config.load()
    result = analyze.analyse(rows, cfg.weights.as_dict())
    if not result.joined:
        console.print(f"No results logged yet in {path}. Fill in views and average watch "
                      "time for the clips you posted, then run this again.")
        return

    _print_performance(result.joined)
    for row in result.unmatched:
        console.print(f"[yellow]Could not find the scores for {row.get('source_id', '?')} "
                      f"{row.get('clip_id', '?')}; left out.[/yellow]")
    if result.note:
        console.print(f"\n{result.note}")
        console.print("[dim]With this few clips, differences between them are mostly chance "
                      "and account reach, not evidence about what works.[/dim]")
        return

    table = Table(title="Rank correlation with watch-through (95% interval)", box=None,
                  header_style="bold", pad_edge=False)
    for col in ("score", "n", "rho", "interval"):
        table.add_column(col, justify="left" if col == "score" else "right")
    for c in result.correlations:
        if c.target == "watch_through":
            table.add_row(c.name, str(c.n), f"{c.rho:+.2f}", f"{c.low:+.2f} .. {c.high:+.2f}")
    console.print(table)

    if result.proposal is None:
        console.print("No weight change proposed: the evidence does not favour any signal.")
        return
    console.print("\nProposed signal weights (a small step toward the evidence):")
    for key, new in result.proposal.proposed.items():
        old = result.proposal.current.get(key, 0.0)
        if abs(new - old) >= 0.0005:
            console.print(f"  {key}: {old:.3f} -> {new:.3f}")
    if not apply:
        console.print("\nNothing written. Run again with --apply to use these weights.")
        return
    analyze.apply(result.proposal, DEFAULT_CONFIG_PATH, n=len(result.joined))
    examples = analyze.write_examples(result.joined)
    console.print(f"[green]Weights written to {DEFAULT_CONFIG_PATH.name}; history in "
                  f"{analyze.HISTORY.name}.[/green]")
    if examples:
        console.print(f"Best-watched clips saved as examples in {examples}.")


tiktok_app = typer.Typer(help="Your TikTok posts' stats, via TikTok's official Display API.",
                         no_args_is_help=True)
app.add_typer(tiktok_app, name="tiktok")


@tiktok_app.command("login")
def tiktok_login(verbose: VerboseOpt = False) -> None:
    """Connect your TikTok account once (opens TikTok's consent page)."""
    setup_logging(verbose)
    from .tiktok import api

    console.print("Opening TikTok in your browser. Approve access there; this waits up "
                  "to 5 minutes. (Connecting another account adds it alongside.)")
    try:
        api.login()
    except api.TikTokError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc
    console.print("[green]Connected.[/green] Run `clipper tiktok sync` to fill the "
                  "performance log.")


@tiktok_app.command("sync")
def tiktok_sync(
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Show what would change; write nothing.")] = False,
    verbose: VerboseOpt = False,
) -> None:
    """Fill the performance log with each posted video's current stats."""
    setup_logging(verbose)
    from .learn import log as perf
    from .tiktok import api, sync

    try:
        if not api.token_files():
            raise api.TikTokError("not connected yet: run `clipper tiktok login` once")
        videos = [v for path in api.token_files() for v in api.list_videos(api.access_token(path))]
    except api.TikTokError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc
    rows = perf.read()
    result = sync.apply(videos, rows)
    for caption, filled in result.matched:
        console.print(f"  {caption}...  [dim]{filled}[/dim]")
    for video in result.unmatched:
        console.print(f"  [dim]not in the log: {video.caption[:60]!r}[/dim]")
    for video in result.ambiguous:
        console.print(f"  [yellow]several rows share the caption {video.caption[:60]!r}; "
                      "skipped[/yellow]")
    console.print(f"{len(videos)} video(s) on TikTok, {len(result.matched)} matched to the log.")
    if dry_run:
        console.print("Dry run: nothing written.")
        return
    try:
        perf.write(rows)
    except PermissionError as exc:
        console.print(f"[red]{perf.log_path().name} is open in Excel; close it and run this "
                      "again.[/red]")
        raise typer.Exit(code=1) from exc
    console.print(f"[green]Updated {perf.log_path()}[/green]")


@app.command()
def studio(
    no_browser: Annotated[bool, typer.Option("--no-browser", help="Don't open a browser tab.")] = False,
    port: Annotated[int, typer.Option("--port", help="Local port.")] = 8765,
    verbose: VerboseOpt = False,
) -> None:
    """Open the Control Center: every campaign's clips, captions, links and stats."""
    setup_logging(verbose)
    from . import selfupdate

    news = selfupdate.pull()
    if news:
        console.print(f"Clipper {news}")
    if news.startswith("updated"):
        selfupdate.restart()  # the code just pulled, not what this process already loaded
    from .studio import server

    console.print(f"Control Center at [bold]http://127.0.0.1:{port}/[/bold]  "
                  "(close this window to stop it)")
    server.serve(open_browser=not no_browser, port=port)


@app.command(name="ai-check")
def ai_check() -> None:
    """Which AI Create will use, tried for real: the first thing to run when the diagrams
    look like Gemini's (D115)."""
    import os
    import shutil
    import subprocess

    from pydantic import BaseModel

    from .config import Config
    from .create import ai
    from .llm.base import LLMRequest
    from .llm.claude_code import cli
    from .paths import REPO_ROOT

    version = subprocess.run(["git", "-C", str(REPO_ROOT), "log", "-1", "--format=%h %s %cr"],
                             capture_output=True, text=True).stdout.strip()
    console.print(f"Clipper version: {version or 'unknown'}")
    console.print(f"git on PATH: {shutil.which('git') or 'NO'}")
    console.print(f"claude on PATH: {cli() or 'NO (Claude Code not installed, or not on this window PATH)'}")
    config = Config.load()
    console.print(f"create_model: {config.llm.create_model}   create_via_claude_plan: "
                  f"{config.llm.create_via_claude_plan}   API key set: {bool(os.environ.get('ANTHROPIC_API_KEY'))}   "
                  f"paid API only for: {', '.join(config.llm.paid_api_jobs) or 'nothing'}")
    try:
        order = ai.backends(config, job="sketch")
    except Exception as exc:
        console.print(f"[red]no AI set up: {exc}[/red]")
        raise typer.Exit(1) from exc
    console.print("Create asks, in order: " + ", ".join(b.describe() for b in order))
    for name, why in ai.misses.items():
        console.print(f"  {name}: {why}")

    class Ok(BaseModel):
        ok: bool

    first = order[0]
    console.print(f"Trying {first.describe()} (up to a few minutes)...")
    try:
        reply = first.complete(LLMRequest(system="Answer the question.", user="Is 2 + 2 = 4?", response_schema=Ok))
        console.print(f"[green]It answered: {reply.text}[/green]")
    except Exception as exc:
        console.print(f"[red]It failed: {exc}[/red]")
        raise typer.Exit(1) from exc


@app.command()
def usage(days: Annotated[int, typer.Option("--days", min=1, help="How many days back.")] = 30) -> None:
    """What the AI calls cost, by job and model, from usage.sqlite (D227). Claude Code rows show what the
    same calls would cost on the API; on your plan nothing is charged per call."""
    import time

    from .llm import usage as ai_usage

    rows = ai_usage.summary(time.time() - days * 86400)
    if not rows:
        console.print(f"No AI calls recorded in the last {days} days.")
        return
    table = Table(show_header=True, header_style="bold", box=None, pad_edge=False)
    for name in ("job", "model", "calls", "input", "output", "cache read", "cache write", "USD"):
        table.add_column(name, justify="left" if name in ("job", "model") else "right")
    for r in rows:
        table.add_row(r["job"] or "(unnamed)", r["model"], str(r["calls"]), f"{r['input_tokens']:,}",
                      f"{r['output_tokens']:,}", f"{r['cache_read']:,}", f"{r['cache_write']:,}", f"{r['usd']:.4f}")
    console.print(table)
    console.print(f"Total: {sum(r['calls'] for r in rows)} calls, ${sum(r['usd'] for r in rows):.2f} "
                  f"in the last {days} days.")


instagram_app = typer.Typer(help="Your Instagram Reels' stats and links, via Instagram's "
                                 "official API.", no_args_is_help=True)
app.add_typer(instagram_app, name="instagram")


@instagram_app.command("login")
def instagram_login(verbose: VerboseOpt = False) -> None:
    """Connect Instagram with a token from the Meta App Dashboard (asked for, not shown)."""
    setup_logging(verbose)
    from .instagram import api

    console.print("In the Meta App Dashboard: Instagram -> API setup with Instagram business "
                  "login -> Generate token. Copy it, then paste it here (it won't show).")
    token = typer.prompt("Token", hide_input=True)
    try:
        username = api.login(token)
    except api.InstagramError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc
    console.print(f"[green]Connected as @{username}.[/green] Run `clipper instagram sync` "
                  "to fill the performance log.")


@instagram_app.command("sync")
def instagram_sync(
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Show what would change; write nothing.")] = False,
    verbose: VerboseOpt = False,
) -> None:
    """Fill the performance log with each Reel's link and current stats."""
    setup_logging(verbose)
    from .instagram import api, sync
    from .learn import log as perf

    rows = perf.read()
    try:
        if not api.token_files():
            raise api.InstagramError("not connected yet: run `clipper instagram login` once")
        results = []
        for path in api.token_files():
            found = api.list_reels(api.access_token(path))
            results.append((found, sync.apply(found, rows, account=api.username(path))))
    except api.InstagramError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc
    reels = [r for found, _ in results for r in found]
    result = sync.SyncResult(
        matched=[m for _, r in results for m in r.matched],
        unmatched=[u for _, r in results for u in r.unmatched],
        ambiguous=[a for _, r in results for a in r.ambiguous])
    for caption, filled in result.matched:
        console.print(f"  {caption}...  [dim]{filled}[/dim]")
    for reel in result.unmatched:
        console.print(f"  [dim]not in the log: {reel.caption[:60]!r}[/dim]")
    for reel in result.ambiguous:
        console.print(f"  [yellow]several clips share the caption {reel.caption[:60]!r}; "
                      "skipped[/yellow]")
    console.print(f"{len(reels)} Reel(s) on Instagram, {len(result.matched)} matched to the log.")
    if dry_run:
        console.print("Dry run: nothing written.")
        return
    try:
        perf.write(rows)
    except PermissionError as exc:
        console.print(f"[red]{perf.log_path().name} is open in Excel; close it and run this "
                      "again.[/red]")
        raise typer.Exit(code=1) from exc
    console.print(f"[green]Updated {perf.log_path()}[/green]")


def _print_performance(joined) -> None:
    def pct(value):
        return "" if value is None else f"{value * 100:.0f}%"

    def num(value):
        return "" if value is None else f"{value:,.0f}"

    ranked = sorted(joined, key=lambda j: j.scores.get("composite", 0.0), reverse=True)
    table = Table(title="Posted clips, in the order the tool ranked them", box=None,
                  header_style="bold", pad_edge=False)
    # Short headers: the table must fit an 80-column terminal without wrapping.
    for col in ("post", "len", "24h", "7d", "now", "watched", "full", "engaged", "score"):
        table.add_column(col, justify="left" if col == "post" else "right", no_wrap=True)
    for j in ranked:
        table.add_row(j.label, f"{j.duration:.0f}s" if j.duration else "",
                      num(j.get("views_24h")), num(j.get("views_7d")),
                      num(j.get("views_latest")), pct(j.watch_through),
                      pct(j.watched_full), pct(j.engagement),
                      f"{j.scores.get('composite', 0.0):.2f}")
    console.print(table)
    console.print("[dim]24h/7d/now: views. watched: average share of the clip watched. "
                  "full: watched to the end. engaged: likes+comments+shares+saves per view. "
                  "score: the tool's own rating when it picked the clip.[/dim]")
    wt = [j.watch_through for j in joined if j.watch_through is not None]
    if wt:
        console.print(f"Average watch-through: {sum(wt) / len(wt) * 100:.0f}% of each clip "
                      f"({len(wt)} clip(s)). Above 100% means people rewatched.")

    from .learn import analyze

    comparisons = analyze.compare(joined)
    if comparisons:
        console.print("\n[bold]Posts with and without each retention change[/bold]")
        for name, arms in comparisons:
            parts = [f"{a.value}: {a.watch_through * 100:.0f}% watched"
                     + (f", most left at {a.drop_off:g}s" if a.drop_off is not None else "")
                     + f" (n={a.n})" for a in arms]
            console.print(f"  {name}: " + " | ".join(parts))
        if min(a.n for _, arms in comparisons for a in arms) < 5:
            console.print("[dim]  Fewer than 5 posts on a side: read these as hints, "
                          "not results.[/dim]")


def main() -> None:
    # Load .env before any command reads an API key. override=False so a key
    # exported in the shell wins over a stale one in the file.
    from dotenv import load_dotenv

    from .paths import REPO_ROOT

    load_dotenv(REPO_ROOT / ".env", override=False)
    app()


if __name__ == "__main__":
    main()
