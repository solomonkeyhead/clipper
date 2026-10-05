"""Keeping Clipper up to date by itself (D112): `clipper studio` pulls the latest code from
GitHub before it starts, so a restart is all the user does after a change is pushed.

Only a fast-forward on a clean checkout: local edits, another branch or no network
just skip the update (said in the window), never a merge or anything overwritten.
When the dependencies changed it reinstalls them. CLIPPER_NO_UPDATE=1 turns it off.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys

from .paths import REPO_ROOT
from .utils.logging import get_logger

log = get_logger(__name__)


def _git(*args: str, timeout: float = 30) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(REPO_ROOT), *args], capture_output=True, text=True, timeout=timeout)


def pull() -> str:
    """Pull new commits if it's safe; returns what happened, in a line for the window:
    "" (nothing new, or off), "updated ..." or "not updated: why"."""
    if os.environ.get("CLIPPER_NO_UPDATE", "").strip() in ("1", "true", "yes"):
        return ""
    if not (REPO_ROOT / ".git").exists():
        return ""
    try:
        if _git("status", "--porcelain", "--untracked-files=no").stdout.strip():
            return "not updated: you have local changes to Clipper's code"
        branch = _git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
        if branch != "master":
            return f"not updated: on branch {branch}, not master"
        before = _git("rev-parse", "HEAD").stdout.strip()
        if _git("fetch", "-q", "origin", "master", timeout=60).returncode:
            return "not updated: couldn't reach GitHub"
        merged = _git("merge", "--ff-only", "-q", "origin/master")
        if merged.returncode:
            return "not updated: this copy has its own commits; pull by hand"
        after = _git("rev-parse", "HEAD").stdout.strip()
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"not updated: {exc}"
    if before == after:
        return ""
    changed = _git("diff", "--name-only", before, after).stdout.split()
    if "pyproject.toml" in changed or "uv.lock" in changed:
        # The venv Clipper is installed in is uv's, which has no pip: uv installs into it.
        uv = shutil.which("uv")
        install = [uv, "pip", "install", "-q", "--python", sys.executable] if uv else [sys.executable, "-m", "pip", "install", "-q"]
        done = subprocess.run([*install, "-e", str(REPO_ROOT)], capture_output=True, text=True)
        if done.returncode:
            log.warning("update: dependencies not reinstalled: %s", done.stderr[-300:])
    count = _git("rev-list", "--count", f"{before}..{after}").stdout.strip()
    return f"updated to the latest version ({count} new change{'s' if count != '1' else ''})"


def restart() -> None:
    """Run the same command again with the new code, then exit with its result."""
    raise SystemExit(subprocess.call([sys.executable, "-c", "from clipper.cli import main; main()", *sys.argv[1:]],
                                     env={**os.environ, "CLIPPER_NO_UPDATE": "1"}))
