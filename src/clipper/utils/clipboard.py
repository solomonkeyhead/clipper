"""Putting text on the clipboard, for pasting into forms the tool must not fill in."""

from __future__ import annotations

import subprocess
import sys

from .logging import get_logger

log = get_logger(__name__)


def copy_text(text: str) -> bool:
    """Copy `text` to the clipboard. False (and a log line) where that isn't possible."""
    if sys.platform != "win32":
        log.warning("copying to the clipboard is only supported on Windows")
        return False
    try:
        # clip.exe reads UTF-16LE with a byte-order mark as Unicode.
        subprocess.run(["clip"], input=b"\xff\xfe" + text.encode("utf-16-le"), check=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        log.warning("could not copy to the clipboard: %s", exc)
        return False
    return True
