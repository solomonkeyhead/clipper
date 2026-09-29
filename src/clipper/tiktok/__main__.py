"""Windowless scheduled sync: `pythonw -m clipper.tiktok` (Task Scheduler, daily).

Logs to data/logs/tiktok.log. If the spreadsheet is open in Excel the write is
skipped and the next run catches up; the view windows are wide enough
(views_24h is taken any time from 1 to 3 days after posting) for that.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def main() -> int:
    if sys.stdout is None or sys.stderr is None:  # pythonw has no console
        sys.stdout = sys.stderr = Path(os.devnull).open("w", encoding="utf-8")  # noqa: SIM115

    from dotenv import load_dotenv

    from ..paths import REPO_ROOT, ensure, logs_dir
    from ..studio import stats
    from ..utils.logging import get_logger, setup_logging

    load_dotenv(REPO_ROOT / ".env", override=False)
    setup_logging(log_file=ensure(logs_dir()) / "tiktok.log")
    log = get_logger("clipper.tiktok")
    # Every connected TikTok and Instagram account, the same sync the Control
    # Center runs (snapshots included).
    try:
        result = stats.run_sync()
    except Exception:
        log.exception("sync failed")
        return 1
    for problem in result.get("problems", []):
        log.warning("%s", problem)
    log.info("sync done: last synced %s", result.get("last_synced", ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
