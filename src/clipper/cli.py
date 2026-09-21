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
    verbose: VerboseOpt = False,
) -> None:
    """Full pipeline: ingest through manifest."""
    setup_logging(verbose)
    _not_implemented("run", "Phase 4")


@app.command()
def transcribe(
    source: Annotated[str, typer.Argument(help="Local video file or URL.")],
    verbose: VerboseOpt = False,
) -> None:
    """Ingest and transcribe only."""
    setup_logging(verbose)
    _not_implemented("transcribe", "Phase 2")


@app.command()
def candidates(
    source_id: Annotated[str, typer.Argument(help="Source id from a previous ingest.")],
    verbose: VerboseOpt = False,
) -> None:
    """Generate candidate windows from an existing transcript."""
    setup_logging(verbose)
    _not_implemented("candidates", "Phase 2")


@app.command()
def score(
    source_id: Annotated[str, typer.Argument(help="Source id from a previous ingest.")],
    backend: Annotated[str | None, typer.Option("--backend")] = None,
    verbose: VerboseOpt = False,
) -> None:
    """Compute all signals and the composite score."""
    setup_logging(verbose)
    _not_implemented("score", "Phase 3")


@app.command()
def explain(
    source_id: Annotated[str, typer.Argument()],
    candidate_id: Annotated[str, typer.Argument()],
    verbose: VerboseOpt = False,
) -> None:
    """Show every signal behind one candidate, and why it was picked or dropped."""
    setup_logging(verbose)
    _not_implemented("explain", "Phase 3")


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
