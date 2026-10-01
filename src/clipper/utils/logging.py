"""Console + file logging.

Rich handles the console so progress bars and log lines coexist. Every run also
appends plain, un-styled lines to ``data/logs/clipper.log`` so a failure that
scrolled off the terminal is still recoverable.
"""

from __future__ import annotations

import logging
import logging.handlers
import sys
from pathlib import Path

from rich.console import Console
from rich.logging import RichHandler

from ..paths import ensure, logs_dir

console = Console(stderr=True)
"""Shared console. Status goes to stderr so stdout stays pipeable."""

_LOG_FORMAT = "%(asctime)s %(levelname)-7s %(name)-28s %(message)s"
_configured = False
#: clipper.log is cut at this size, keeping this many older files (clipper.log.1, ...).
LOG_BYTES, LOG_BACKUPS = 5_000_000, 3


def setup_logging(verbose: bool = False, *, log_file: Path | None = None) -> Path:
    """Configure root logging once. Returns the log file path."""
    global _configured

    path = log_file or ensure(logs_dir()) / "clipper.log"
    if _configured:
        return path

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    for handler in list(root.handlers):
        root.removeHandler(handler)

    console_handler = RichHandler(
        console=console,
        show_time=False,
        show_path=verbose,
        rich_tracebacks=True,
        markup=False,
    )
    console_handler.setLevel(logging.DEBUG if verbose else logging.INFO)
    console_handler.setFormatter(logging.Formatter("%(message)s"))
    root.addHandler(console_handler)

    try:
        # Rotated: the Control Center runs for weeks and logs at DEBUG.
        file_handler = logging.handlers.RotatingFileHandler(
            path, maxBytes=LOG_BYTES, backupCount=LOG_BACKUPS, encoding="utf-8")
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(logging.Formatter(_LOG_FORMAT))
        root.addHandler(file_handler)
    except OSError as exc:  # pragma: no cover - read-only or locked data dir
        print(f"warning: could not open log file {path}: {exc}", file=sys.stderr)

    # These libraries are chatty at DEBUG and drown out our own lines.
    for noisy in ("urllib3", "httpx", "httpcore", "faster_whisper", "huggingface_hub"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    _configured = True
    return path


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
