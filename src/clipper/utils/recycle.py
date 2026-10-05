"""Sending files to the Recycle Bin (Trash) instead of deleting them outright.

The user's standing rule: nothing Clipper removes is deleted permanently. On Windows this uses the
shell's own file operation with "allow undo", which is what Explorer's Delete key does; on a Mac, the
Finder's own delete; on Linux, `gio trash` or `trash-put`, else the freedesktop trash folder by hand (D148).
"""

from __future__ import annotations

import ctypes
import shutil
import subprocess
import sys
from ctypes import wintypes
from pathlib import Path

from .logging import get_logger

log = get_logger(__name__)

_FO_DELETE = 3
_FOF_SILENT, _FOF_NOCONFIRMATION, _FOF_ALLOWUNDO, _FOF_NOERRORUI = 0x4, 0x10, 0x40, 0x400


class _SHFILEOPSTRUCTW(ctypes.Structure):
    _fields_ = [("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT),
                ("pFrom", wintypes.LPCWSTR), ("pTo", wintypes.LPCWSTR),
                ("fFlags", ctypes.c_ushort), ("fAnyOperationsAborted", wintypes.BOOL),
                ("hNameMappings", ctypes.c_void_p), ("lpszProgressTitle", wintypes.LPCWSTR)]


def recycle(path: Path) -> bool:
    """Send `path` to the Recycle Bin. False (logged) if that isn't possible."""
    if not path.exists():
        return True
    if sys.platform != "win32":
        return _trash_elsewhere(path)
    op = _SHFILEOPSTRUCTW(wFunc=_FO_DELETE, pFrom=str(path.resolve()) + "\0\0", pTo=None,
                          fFlags=_FOF_ALLOWUNDO | _FOF_NOCONFIRMATION | _FOF_SILENT
                          | _FOF_NOERRORUI)
    result = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
    if result != 0 or op.fAnyOperationsAborted:
        log.warning("could not recycle %s (code %s)", path, result)
        return False
    return True


def _trash_elsewhere(path: Path) -> bool:
    """The Trash on a Mac or Linux. False (logged, the file kept) if nothing can."""
    try:
        if sys.platform == "darwin":
            script = f'tell application "Finder" to delete POSIX file "{path.resolve()}"'
            subprocess.run(["osascript", "-e", script], check=True, capture_output=True)
            return True
        for tool in (["gio", "trash"], ["trash-put"]):
            if shutil.which(tool[0]):
                subprocess.run([*tool, str(path)], check=True, capture_output=True)
                return True
        return _xdg_trash(path)
    except (OSError, subprocess.CalledProcessError) as exc:
        log.warning("could not move %s to the Trash (%s); kept it", path, exc)
        return False


def _xdg_trash(path: Path) -> bool:
    """The freedesktop.org Trash by hand: the file into ~/.local/share/Trash/files, with its .trashinfo."""
    from datetime import datetime
    from urllib.parse import quote

    root = Path.home() / ".local" / "share" / "Trash"
    (root / "files").mkdir(parents=True, exist_ok=True)
    (root / "info").mkdir(parents=True, exist_ok=True)
    name, n = path.name, 1
    while (root / "files" / name).exists():
        n += 1
        name = f"{path.stem} {n}{path.suffix}"
    info = ["[Trash Info]", f"Path={quote(str(path.resolve()))}", f"DeletionDate={datetime.now():%Y-%m-%dT%H:%M:%S}", ""]
    (root / "info" / f"{name}.trashinfo").write_text("\n".join(info), encoding="utf-8")
    shutil.move(str(path), str(root / "files" / name))
    return True
