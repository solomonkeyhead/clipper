"""Windowless entry point for the scheduled watcher: `pythonw -m clipper.watch`.

Task Scheduler runs this every few minutes. `pythonw` opens no console window,
so there is no stdout: output goes to data/logs/watch.log, and any failure is
logged there rather than lost.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def main() -> int:
    # pythonw has no console; anything printed would raise on a None stream.
    if sys.stdout is None or sys.stderr is None:
        sys.stdout = sys.stderr = Path(os.devnull).open("w", encoding="utf-8")  # noqa: SIM115

    from dotenv import load_dotenv

    from ..config import Config
    from ..paths import REPO_ROOT, ensure, logs_dir
    from ..pipeline import build_backend
    from ..utils.logging import get_logger, setup_logging
    from . import watcher

    load_dotenv(REPO_ROOT / ".env", override=False)
    setup_logging(log_file=ensure(logs_dir()) / "watch.log")
    log = get_logger("clipper.watch")
    try:
        cfg = Config.load()
        result = watcher.run_pass(cfg.watch, build_backend(cfg))
    except Exception:
        log.exception("watch pass failed")
        return 1
    log.info("watch pass: %d read, %d campaign(s), %d pushed, %d to retry",
             result.read, result.campaigns, len(result.pushed), result.failed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
