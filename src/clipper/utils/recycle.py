"""Sending files to the Windows Recycle Bin instead of deleting them outright.

The user's standing rule: nothing Clipper removes is deleted permanently. This
uses the shell's own file operation with "allow undo", which is what Explorer's
Delete key does.
"""

from __future__ import annotations

import ctypes
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
        log.warning("no Recycle Bin on this platform; kept %s", path)
        return False
    op = _SHFILEOPSTRUCTW(wFunc=_FO_DELETE, pFrom=str(path.resolve()) + "\0\0", pTo=None,
                          fFlags=_FOF_ALLOWUNDO | _FOF_NOCONFIRMATION | _FOF_SILENT
                          | _FOF_NOERRORUI)
    result = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
    if result != 0 or op.fAnyOperationsAborted:
        log.warning("could not recycle %s (code %s)", path, result)
        return False
    return True
