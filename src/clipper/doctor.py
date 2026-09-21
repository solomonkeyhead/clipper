"""`clipper doctor`: prove the environment works, and say exactly how to fix it.

Every check here is a *behavioural* test where a behavioural test is possible.
Listing an encoder is not proof it opens; importing faster-whisper is not proof
it reaches the GPU. Both of those fail in exactly that way on the target machine,
so `doctor` runs the real thing and reports what it actually saw.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from . import paths
from .config import Config

MIN_PYTHON = (3, 11)
MAX_PYTHON_EXCLUSIVE = (3, 13)
MIN_FREE_DISK_GB = 20.0


class Status(StrEnum):
    OK = "ok"
    WARN = "warn"
    FAIL = "fail"


@dataclass
class CheckResult:
    name: str
    status: Status
    detail: str = ""
    fix: str = ""
    # Extra facts worth recording in docs/VERIFIED.md.
    facts: dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status is not Status.FAIL


def _result(name: str, status: Status, detail: str = "", fix: str = "", **facts: str) -> CheckResult:
    return CheckResult(name, status, detail, fix, {k: str(v) for k, v in facts.items()})


# --------------------------------------------------------------------------
# individual checks
# --------------------------------------------------------------------------


def check_python() -> CheckResult:
    v = sys.version_info
    detail = f"{v.major}.{v.minor}.{v.micro} at {sys.executable}"
    if MIN_PYTHON <= (v.major, v.minor) < MAX_PYTHON_EXCLUSIVE:
        return _result("Python", Status.OK, detail, python=f"{v.major}.{v.minor}.{v.micro}")
    return _result(
        "Python",
        Status.FAIL,
        detail,
        fix=(
            "clipper needs Python 3.11 or 3.12 (onnxruntime and CTranslate2 wheel "
            "coverage). Create the venv with:\n"
            "    uv python install 3.12\n"
            "    uv venv --python 3.12 .venv\n"
            "    .\\.venv\\Scripts\\Activate.ps1\n"
            "    uv pip install -e \".[dev]\""
        ),
    )


def check_ffmpeg() -> CheckResult:
    from .render import ffmpeg

    try:
        exe = ffmpeg.ffmpeg_path()
        ver = ffmpeg.version()
    except ffmpeg.FFmpegNotFound as exc:
        return _result("FFmpeg", Status.FAIL, "not found", fix=str(exc))
    return _result("FFmpeg", Status.OK, f"{ver}\n    {exe}", ffmpeg_version=ver, ffmpeg_path=str(exe))


def check_ffprobe() -> CheckResult:
    from .render import ffmpeg

    try:
        exe = ffmpeg.ffprobe_path()
    except ffmpeg.FFmpegNotFound as exc:
        return _result("ffprobe", Status.FAIL, "not found", fix=str(exc))
    return _result("ffprobe", Status.OK, str(exe))


def check_libass() -> CheckResult:
    """libass renders the burned-in captions; without it there is no product."""
    from .render import ffmpeg

    try:
        has_flag = ffmpeg.has_build_flag("libass")
        has_ass_filter = ffmpeg.has_filter("ass")
    except ffmpeg.FFmpegNotFound:
        return _result("libass", Status.FAIL, "FFmpeg missing", fix="See the FFmpeg check above.")

    if has_flag and has_ass_filter:
        extras = [f for f in ("libfreetype", "libfribidi", "libharfbuzz", "fontconfig")
                  if ffmpeg.has_build_flag(f)]
        return _result("libass", Status.OK, f"ass filter available (+{', '.join(extras)})")
    return _result(
        "libass",
        Status.FAIL,
        f"--enable-libass={has_flag}, ass filter={has_ass_filter}",
        fix=(
            "Your FFmpeg build has no libass, so captions cannot be burned in.\n"
            "    winget install --id Gyan.FFmpeg   (the 'full' build includes libass)"
        ),
    )


def check_ffmpeg_filters() -> CheckResult:
    """The QA gate and the audio chain depend on these specific filters."""
    from .render import ffmpeg

    required = ["loudnorm", "silencedetect", "blackdetect", "freezedetect", "gblur", "scale", "crop"]
    try:
        missing = [f for f in required if not ffmpeg.has_filter(f)]
    except ffmpeg.FFmpegNotFound:
        return _result("FFmpeg filters", Status.FAIL, "FFmpeg missing", fix="See the FFmpeg check.")
    if missing:
        return _result(
            "FFmpeg filters",
            Status.FAIL,
            f"missing: {', '.join(missing)}",
            fix="Install the full FFmpeg build: winget install --id Gyan.FFmpeg",
        )
    return _result("FFmpeg filters", Status.OK, f"all present ({len(required)} checked)")


def check_nvenc() -> CheckResult:
    """Probe NVENC for real. A listed encoder that will not open is worse than none."""
    from .render import ffmpeg

    try:
        listed = ffmpeg.has_build_flag("nvenc")
        probe = ffmpeg.probe_encoder("h264_nvenc")
    except ffmpeg.FFmpegNotFound:
        return _result("NVENC", Status.WARN, "FFmpeg missing", fix="See the FFmpeg check.")

    if probe.usable:
        return _result("NVENC", Status.OK, "h264_nvenc opens and encodes", nvenc="usable")
    reason = ffmpeg._short_nvenc_reason(probe.detail)
    return _result(
        "NVENC",
        Status.WARN,
        f"h264_nvenc {'is built in but ' if listed else ''}cannot be opened: {reason}",
        fix=(
            "Not a blocker -- clipper falls back to libx264 (CPU), which is fast enough\n"
            "    for 1080x1920 clips. To get GPU encoding back, either:\n"
            "      - update the NVIDIA driver to the version this FFmpeg build needs, or\n"
            "      - install an older FFmpeg built against an older NVENC SDK."
        ),
        nvenc=f"unusable: {reason}",
    )


def check_gpu_visible() -> CheckResult:
    """nvidia-smi: is there a GPU and what driver is on it."""
    exe = shutil.which("nvidia-smi")
    if not exe:
        return _result(
            "NVIDIA GPU",
            Status.WARN,
            "nvidia-smi not found",
            fix="Without a GPU, transcription runs on CPU and will be much slower.",
        )
    try:
        proc = subprocess.run(
            [exe, "--query-gpu=name,driver_version,memory.total", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=30, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:  # pragma: no cover
        return _result("NVIDIA GPU", Status.WARN, f"nvidia-smi failed: {exc}")
    if proc.returncode != 0:
        return _result("NVIDIA GPU", Status.WARN, proc.stderr.strip()[:200])
    line = proc.stdout.strip().splitlines()[0] if proc.stdout.strip() else "?"
    name, driver, mem = (p.strip() for p in [*line.split(","), "?", "?"][:3])
    return _result("NVIDIA GPU", Status.OK, f"{name}, driver {driver}, {mem}",
                   gpu=name, driver=driver, vram=mem)


def check_transcriber_gpu() -> CheckResult:
    """Does CTranslate2 actually see CUDA, and does float16 work on this card?

    The failure mode this catches is silent: with the cuBLAS/cuDNN DLLs missing,
    CTranslate2 reports zero CUDA devices and faster-whisper falls back to CPU,
    turning a 5-minute transcription into an hour.
    """
    from .utils.cuda import ensure_cuda_dll_path

    registered = ensure_cuda_dll_path()
    try:
        import ctranslate2
    except ImportError as exc:  # pragma: no cover - dependency is required
        return _result("Transcriber GPU", Status.FAIL, f"ctranslate2 not importable: {exc}",
                       fix='uv pip install -e ".[dev]"')

    count = ctranslate2.get_cuda_device_count()
    if count == 0:
        return _result(
            "Transcriber GPU",
            Status.WARN,
            "CTranslate2 sees 0 CUDA devices -- transcription will run on CPU",
            fix=(
                "Install the CUDA 12 runtime wheels CTranslate2 dlopen()s at runtime:\n"
                '    uv pip install "nvidia-cublas-cu12" "nvidia-cudnn-cu12>=9,<10"\n'
                "    (cuBLAS for CUDA 12 and cuDNN 9 for CUDA 12; CTranslate2 4.x needs both)"
            ),
            ctranslate2=ctranslate2.__version__,
        )

    types = sorted(ctranslate2.get_supported_compute_types("cuda", 0))
    detail = f"CTranslate2 {ctranslate2.__version__}, {count} CUDA device(s), compute types: {', '.join(types)}"
    if registered:
        detail += f"\n    CUDA DLLs from {registered[0].parent.parent}"
    status = Status.OK if "float16" in types else Status.WARN
    return _result("Transcriber GPU", status, detail,
                   fix="" if status is Status.OK else "float16 unsupported; set transcription.compute_type to int8.",
                   ctranslate2=ctranslate2.__version__,
                   compute_types=", ".join(types))


def check_yt_dlp() -> CheckResult:
    try:
        import yt_dlp
    except ImportError as exc:
        return _result("yt-dlp", Status.FAIL, str(exc), fix='uv pip install -e ".[dev]"')
    return _result("yt-dlp", Status.OK, f"version {yt_dlp.version.__version__}",
                   yt_dlp=yt_dlp.version.__version__)


def check_opencv() -> CheckResult:
    """OpenCV supplies both frame IO and the YuNet face detector."""
    try:
        import cv2
    except ImportError as exc:
        return _result("OpenCV", Status.FAIL, str(exc), fix='uv pip install -e ".[dev]"')
    if not hasattr(cv2, "FaceDetectorYN"):
        return _result(
            "OpenCV", Status.FAIL, f"{cv2.__version__} has no FaceDetectorYN",
            fix='uv pip install -U "opencv-python-headless>=4.11"',
        )
    return _result("OpenCV", Status.OK, f"{cv2.__version__}, FaceDetectorYN available",
                   opencv=cv2.__version__)


def check_face_model() -> CheckResult:
    """The YuNet ONNX weights are downloaded, not vendored (they are ~350 KB)."""
    model = paths.models_dir() / "face_detection_yunet.onnx"
    if model.is_file() and model.stat().st_size > 10_000:
        return _result("Face model", Status.OK, f"{model.name} ({model.stat().st_size // 1024} KB)")
    return _result(
        "Face model",
        Status.WARN,
        f"missing: {model}",
        fix=(
            "Needed for face-aware reframing; without it every clip falls back to the\n"
            "    blurred-background layout. Fetch it with:  clipper doctor --fix"
        ),
    )


def check_font() -> CheckResult:
    """Captions need a bundled OFL font so output does not depend on system fonts."""
    cfg = Config.load()
    fonts = paths.fonts_dir()
    found = sorted(p.name for p in fonts.glob("*.ttf")) + sorted(p.name for p in fonts.glob("*.otf"))
    if found:
        return _result("Caption font", Status.OK,
                       f"{len(found)} bundled in {fonts}: {', '.join(found[:4])}")
    return _result(
        "Caption font",
        Status.WARN,
        f"no bundled font in {fonts}; libass will fall back to a system font",
        fix=(
            f"Captions are configured to use {cfg.render.caption_font!r}. Download a\n"
            "    permissively licensed (OFL) font with:  clipper doctor --fix"
        ),
    )


def check_api_keys() -> CheckResult:
    """Keys are optional at doctor time: only the configured backend must have one."""
    cfg = Config.load()
    backend = cfg.llm.backend
    present = {
        "GEMINI_API_KEY": bool(os.environ.get("GEMINI_API_KEY", "").strip()),
        "ANTHROPIC_API_KEY": bool(os.environ.get("ANTHROPIC_API_KEY", "").strip()),
    }
    summary = ", ".join(f"{k}={'set' if v else 'unset'}" for k, v in present.items())

    needed = {"gemini": "GEMINI_API_KEY", "anthropic": "ANTHROPIC_API_KEY"}.get(backend)
    if needed and not present.get(needed):
        return _result(
            "API keys",
            Status.WARN,
            f"backend is {backend!r} but {needed} is not set ({summary})",
            fix=(
                f"Copy .env.example to .env and set {needed}.\n"
                "    Gemini free-tier keys: https://aistudio.google.com/apikey\n"
                "    Or switch backends:  clipper run ... --backend ollama"
            ),
        )
    return _result("API keys", Status.OK, f"backend={backend}, {summary}")


def check_data_dir() -> CheckResult:
    """Large artifacts must not land inside a cloud-synced folder."""
    root = paths.data_root()
    synced = paths.synced_parent(root)
    if synced:
        return _result(
            "Data directory",
            Status.WARN,
            f"{root}\n    is inside the synced folder {synced}",
            fix=(
                "Downloads, WAVs and renders will be uploaded to the cloud and may lock\n"
                "    files mid-render. Point CLIPPER_DATA_DIR at a local folder, e.g.\n"
                '    setx CLIPPER_DATA_DIR "C:\\clipper-data"'
            ),
        )
    return _result("Data directory", Status.OK, str(root))


def check_disk_space() -> CheckResult:
    root = paths.data_root()
    probe = root if root.exists() else paths.REPO_ROOT
    usage = shutil.disk_usage(probe)
    free_gb = usage.free / 1024**3
    detail = f"{free_gb:.0f} GB free on {Path(probe).drive or probe}"
    if free_gb < MIN_FREE_DISK_GB:
        return _result(
            "Disk space", Status.WARN, detail,
            fix=(
                f"Under {MIN_FREE_DISK_GB:.0f} GB free. A 60-minute 1080p source plus its\n"
                "    WAV and renders can use 5-10 GB; clear space or move CLIPPER_DATA_DIR."
            ),
        )
    return _result("Disk space", Status.OK, detail, free_disk_gb=f"{free_gb:.0f}")


def check_config() -> CheckResult:
    """A malformed config/default.yaml should fail here, not mid-run."""
    from .config import DEFAULT_CONFIG_PATH

    try:
        cfg = Config.load()
    except Exception as exc:
        return _result(
            "Config", Status.FAIL, f"{DEFAULT_CONFIG_PATH} is invalid:\n    {exc}",
            fix="Fix the YAML, or delete it to fall back to built-in defaults.",
        )
    weights = ", ".join(f"{k}={v}" for k, v in cfg.weights.as_dict().items())
    return _result("Config", Status.OK, f"{DEFAULT_CONFIG_PATH.name} valid; weights: {weights}")


ALL_CHECKS: tuple[Callable[[], CheckResult], ...] = (
    check_python,
    check_config,
    check_ffmpeg,
    check_ffprobe,
    check_libass,
    check_ffmpeg_filters,
    check_nvenc,
    check_gpu_visible,
    check_transcriber_gpu,
    check_yt_dlp,
    check_opencv,
    check_face_model,
    check_font,
    check_api_keys,
    check_data_dir,
    check_disk_space,
)


def run_checks() -> Iterator[CheckResult]:
    """Run every check in order, converting an unexpected crash into a FAIL."""
    for check in ALL_CHECKS:
        try:
            yield check()
        except Exception as exc:  # pragma: no cover - defensive
            yield _result(
                check.__name__.removeprefix("check_").replace("_", " ").title(),
                Status.FAIL,
                f"check raised {type(exc).__name__}: {exc}",
            )
