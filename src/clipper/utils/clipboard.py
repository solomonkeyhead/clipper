"""Putting text on the clipboard, for pasting into forms the tool must not fill in."""

from __future__ import annotations

import shutil
import subprocess
import sys

from .logging import get_logger

log = get_logger(__name__)


def _posix_tool() -> list[str] | None:
    """The clipboard command on a Mac or Linux (D148)."""
    for tool in (["pbcopy"], ["wl-copy"], ["xclip", "-selection", "clipboard"], ["xsel", "--clipboard", "--input"]):
        if shutil.which(tool[0]):
            return tool
    return None


def copy_text(text: str) -> bool:
    """Copy `text` to the clipboard. False (and a log line) where that isn't possible."""
    try:
        if sys.platform == "win32":
            # clip.exe reads UTF-16LE with a byte-order mark as Unicode.
            subprocess.run(["clip"], input=b"\xff\xfe" + text.encode("utf-16-le"), check=True)
        else:
            tool = _posix_tool()
            if tool is None:
                log.warning("no clipboard tool found (pbcopy, wl-copy, xclip or xsel)")
                return False
            subprocess.run(tool, input=text.encode("utf-8"), check=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        log.warning("could not copy to the clipboard: %s", exc)
        return False
    return True
