"""What this computer can do, and the speech-recognition settings that suit it (D148).

Clipper was built on one machine with an NVIDIA GPU, and its defaults (Whisper large-v3 in float16) assume
one. On a laptop without a GPU that default is painfully slow, or runs out of memory. `detect` reads the
hardware, `recommend` picks the model and precision that fit it, and `apply` writes them to
`<data>/config.auto.yaml` (laid under the user's own `config.yaml`, so a hand edit always wins).

The speed figures are rough: how many minutes of real time an hour of footage takes to transcribe, from
faster-whisper's published benchmarks and our own runs (an RTX 2070 Super does large-v3 in about 3 minutes
an hour). They are for setting expectations, not promises.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
from dataclasses import asdict, dataclass

from .paths import data_root


@dataclass
class Hardware:
    gpu: str = ""              # "" when there is no NVIDIA GPU Clipper can use
    vram_gb: float = 0.0
    cpu_cores: int = 1
    ram_gb: float = 0.0
    system: str = ""


@dataclass
class Plan:
    device: str                # "cuda" | "cpu"
    model: str
    compute_type: str
    label: str                 # in words, for the page
    minutes_per_hour: float    # real minutes to transcribe an hour of footage (rough)


def _ram_gb() -> float:
    try:
        if hasattr(os, "sysconf") and "SC_PHYS_PAGES" in os.sysconf_names:
            return os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE") / 1e9
        import ctypes

        class _Mem(ctypes.Structure):
            _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong), ("total", ctypes.c_ulonglong),
                        ("avail", ctypes.c_ulonglong), ("pt", ctypes.c_ulonglong), ("pa", ctypes.c_ulonglong),
                        ("vt", ctypes.c_ulonglong), ("va", ctypes.c_ulonglong), ("ve", ctypes.c_ulonglong)]

        mem = _Mem(length=ctypes.sizeof(_Mem))
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(mem))  # type: ignore[attr-defined]
        return mem.total / 1e9
    except Exception:  # unknown is fine: it only sharpens the advice
        return 0.0


def detect() -> Hardware:
    hw = Hardware(cpu_cores=os.cpu_count() or 1, ram_gb=round(_ram_gb(), 1),
                  system=f"{platform.system()} {platform.machine()}")
    exe = shutil.which("nvidia-smi")
    if exe:
        try:
            out = subprocess.run([exe, "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
                                 capture_output=True, text=True, timeout=10, check=True).stdout.strip().splitlines()
            if out:
                name, mem = [x.strip() for x in out[0].split(",")][:2]
                hw.gpu, hw.vram_gb = name, round(float(mem) / 1024, 1)
        except (OSError, subprocess.SubprocessError, ValueError):
            pass
    return hw


def recommend(hw: Hardware) -> Plan:
    """The model and precision that fit this hardware."""
    if hw.gpu and hw.vram_gb >= 7.5:
        return Plan("cuda", "large-v3", "float16", f"{hw.gpu}: the best model, full precision", 3)
    if hw.gpu and hw.vram_gb >= 4:
        return Plan("cuda", "large-v3", "int8_float16", f"{hw.gpu} ({hw.vram_gb:g} GB): the best model, compressed to fit", 4)
    if hw.gpu:
        return Plan("cuda", "medium", "int8_float16", f"{hw.gpu} ({hw.vram_gb:g} GB): a smaller model to fit its memory", 6)
    if hw.cpu_cores >= 12:
        return Plan("cpu", "small", "int8", f"No GPU, {hw.cpu_cores} CPU cores: a small model", 20)
    if hw.cpu_cores >= 6:
        return Plan("cpu", "base", "int8", f"No GPU, {hw.cpu_cores} CPU cores: a base model, so it finishes", 14)
    return Plan("cpu", "tiny", "int8", f"No GPU, {hw.cpu_cores} CPU cores: the smallest model, quality drops", 10)


def auto_path():
    return data_root() / "config.auto.yaml"


def current(config) -> dict:
    t = config.transcription
    return {"device": t.device, "model": t.model, "compute_type": t.compute_type}


def apply(plan: Plan) -> None:
    """Write the plan's settings where the config reads them (never over the user's own config.yaml)."""
    from .config import write_auto

    write_auto({"transcription": {"device": plan.device, "model": plan.model, "compute_type": plan.compute_type}})


def as_dict(hw: Hardware, plan: Plan) -> dict:
    return {"hardware": asdict(hw), "recommended": asdict(plan)}
