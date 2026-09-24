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

    from ..learn import log as perf
    from ..paths import REPO_ROOT, ensure, logs_dir
    from ..utils.logging import get_logger, setup_logging
    from . import api, sync

    load_dotenv(REPO_ROOT / ".env", override=False)
    setup_logging(log_file=ensure(logs_dir()) / "tiktok.log")
    log = get_logger("clipper.tiktok")
    try:
        videos = api.list_videos(api.access_token())
        rows = perf.read()
        result = sync.apply(videos, rows)
        perf.write(rows)
    except PermissionError:
        log.warning("performance log is open in Excel; will sync next run")
        return 0
    except Exception:
        log.exception("tiktok sync failed")
        return 1
    log.info("tiktok sync: %d video(s), %d matched, %d not in the log",
             len(videos), len(result.matched), len(result.unmatched))
    return 0


if __name__ == "__main__":
    sys.exit(main())
