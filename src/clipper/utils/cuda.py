"""CUDA DLL discovery for CTranslate2 on Windows.

CTranslate2 (the engine behind faster-whisper) needs cuBLAS for CUDA 12 and
cuDNN 9 for CUDA 12 at *runtime*, but it does not declare them as dependencies.
We install them as the `nvidia-cublas-cu12` / `nvidia-cudnn-cu12` wheels, which
drop their DLLs under ``site-packages/nvidia/<lib>/bin`` -- a directory Windows
does not search by default.

`ensure_cuda_dll_path()` registers those directories with the loader. It MUST be
called before the first ``import ctranslate2`` (and therefore before
``import faster_whisper``), which is why `clipper.utils` is import-light and this
module touches nothing heavy.
"""

from __future__ import annotations

import os
import sys
from functools import cache
from pathlib import Path

# Subdirectories of site-packages/nvidia that ship the DLLs CTranslate2 dlopen()s.
_NVIDIA_DLL_SUBDIRS = ("cublas/bin", "cudnn/bin", "cuda_runtime/bin", "cuda_nvrtc/bin")


def _site_packages_dirs() -> list[Path]:
    """Every sys.path entry that looks like a site-packages directory."""
    return [Path(p) for p in sys.path if p and Path(p).name == "site-packages"]


def nvidia_dll_dirs() -> list[Path]:
    """Existing ``site-packages/nvidia/*/bin`` directories, in load order."""
    found: list[Path] = []
    for site in _site_packages_dirs():
        nvidia = site / "nvidia"
        if not nvidia.is_dir():
            continue
        for sub in _NVIDIA_DLL_SUBDIRS:
            candidate = nvidia / sub
            if candidate.is_dir() and candidate not in found:
                found.append(candidate)
    return found


@cache
def ensure_cuda_dll_path() -> list[Path]:
    """Make pip-installed CUDA 12 DLLs loadable. Returns the dirs registered.

    Idempotent and safe to call on non-Windows or on a machine with no GPU; it
    simply finds nothing and returns an empty list.
    """
    dirs = nvidia_dll_dirs()
    if sys.platform != "win32":
        return dirs

    for d in dirs:
        # add_dll_directory covers DLLs loaded by absolute name from Python;
        # PATH covers ones a native library resolves by bare filename.
        os.add_dll_directory(str(d))
    if dirs:
        joined = os.pathsep.join(str(d) for d in dirs)
        os.environ["PATH"] = joined + os.pathsep + os.environ.get("PATH", "")
    return dirs
