"""Back up the hosted site's databases and plans, or restore a day (D237).

    python scripts/ops/backup.py --root /srv/clipper --dest /backups
    python scripts/ops/backup.py --root /srv/clipper --dest /backups --restore 2026-10-20

A backup copies `<root>/gateway.sqlite` and, per user, `users/*/data/*.db`, `usage.sqlite`,
`meter.sqlite` and `plan.json` into `<dest>/<date>/`, keeping the same folder layout. SQLite files go through
the online backup API, so the site can stay up. The last 14 days are kept. Exit code is
non-zero on any failure. Restore refuses while the app answers /healthz, unless --force.
Stdlib only.
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import re
import shutil
import sqlite3
import sys
import urllib.request
from pathlib import Path

KEEP_DAYS = 14
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def sources(root: Path) -> list[Path]:
    """Every file worth backing up, as paths under root."""
    found = [root / "gateway.sqlite"]
    for data in sorted(root.glob("users/*/data")):
        # meter.sqlite: what the plan meter charged (clipper/meter.py); lost, every source would be billed again.
        found += [*sorted(data.glob("*.db")), data / "usage.sqlite", data / "meter.sqlite", data / "plan.json"]
    return [p for p in found if p.is_file()]


def _copy_sqlite(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    s = sqlite3.connect(f"file:{src.as_posix()}?mode=ro", uri=True)
    d = sqlite3.connect(dst)
    try:
        s.backup(d)
    finally:
        d.close()
        s.close()


def backup(root: Path, dest: Path, today: dt.date | None = None) -> Path:
    day = (today or dt.date.today()).isoformat()
    final, tmp = dest / day, dest / f"{day}.partial"
    shutil.rmtree(tmp, ignore_errors=True)
    try:
        for src in sources(root):
            out = tmp / src.relative_to(root)
            if src.suffix in (".db", ".sqlite"):
                _copy_sqlite(src, out)
            else:
                out.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, out)
    except Exception:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    shutil.rmtree(final, ignore_errors=True)
    tmp.rename(final)
    prune(dest)
    return final


def prune(dest: Path, keep: int = KEEP_DAYS) -> None:
    days = sorted(p for p in dest.iterdir() if p.is_dir() and _DATE.match(p.name))
    for old in days[:-keep]:
        shutil.rmtree(old, ignore_errors=True)


def app_running(port: str) -> bool:
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=2)
        return True
    except Exception:
        return False


def restore(root: Path, dest: Path, day: str, force: bool = False) -> int:
    """Copy a day's files back over the live ones; returns how many were restored."""
    if not _DATE.match(day):
        raise SystemExit(f"{day!r} is not a date like 2026-10-20")
    src_dir = dest / day
    if not src_dir.is_dir():
        raise SystemExit(f"no backup for {day} in {dest}")
    if not force and app_running(os.environ.get("PORT", "8080")):
        raise SystemExit("the app is running: stop it first (docker compose stop app), or use --force")
    n = 0
    for src in sorted(p for p in src_dir.rglob("*") if p.is_file()):
        target = root / src.relative_to(src_dir)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".restoring")
        shutil.copy2(src, tmp)
        # A stale journal from the newer database would corrupt the restored one.
        for ext in ("-wal", "-shm", "-journal"):
            target.with_name(target.name + ext).unlink(missing_ok=True)
        tmp.replace(target)
        n += 1
    return n


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", type=Path, default=Path(os.environ.get("CLIPPER_HOSTED_ROOT", "/srv/clipper")))
    ap.add_argument("--dest", type=Path, default=Path(os.environ.get("CLIPPER_BACKUP_DIR", "/backups")))
    ap.add_argument("--restore", metavar="DATE", help="restore the backup of this day (YYYY-MM-DD)")
    ap.add_argument("--force", action="store_true", help="restore even if the app is running")
    a = ap.parse_args(argv)
    try:
        if a.restore:
            print(f"restored {restore(a.root, a.dest, a.restore, a.force)} files from {a.restore}")
        else:
            a.dest.mkdir(parents=True, exist_ok=True)
            print(f"backed up to {backup(a.root, a.dest)}")
    except SystemExit:
        raise
    except Exception as exc:
        print(f"FAILED: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
