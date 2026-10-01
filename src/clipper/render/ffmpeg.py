"""Finding FFmpeg, running it safely on Windows, and choosing an encoder.

Three Windows-specific hazards are handled here, not at the call sites:

1. **Discovery.** winget/choco/scoop installs land in places that are on the
   *user* PATH but not necessarily in this process's environment.
2. **Encoding.** FFmpeg writes non-ASCII to stderr and we feed it non-ASCII
   caption text; everything is forced to UTF-8 with replacement rather than
   letting the console codepage decide.
3. **NVENC.** ``ffmpeg -encoders`` lists ``h264_nvenc`` whenever the *build* has
   it, which says nothing about whether the installed driver can open it. On this
   target machine it lists and then fails. So we probe by actually encoding a
   frame, and cache the answer.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from functools import cache
from pathlib import Path

from ..paths import REPO_ROOT

# Where winget / chocolatey / scoop drop ffmpeg.exe when it is not on this
# process's PATH. Each entry is (base directory, glob relative to it); an empty
# glob means look directly in the base directory.
_FALLBACK_LOCATIONS: tuple[tuple[str, str], ...] = (
    (os.environ.get("LOCALAPPDATA", ""), "Microsoft/WinGet/Packages/*/*/bin"),
    (os.environ.get("LOCALAPPDATA", ""), "Microsoft/WinGet/Packages/*/bin"),
    (os.environ.get("LOCALAPPDATA", ""), "Microsoft/WinGet/Links"),
    (os.environ.get("PROGRAMDATA", ""), "chocolatey/bin"),
    (os.environ.get("USERPROFILE", ""), "scoop/shims"),
    ("C:/", "ffmpeg/bin"),
)


class FFmpegNotFound(RuntimeError):
    """FFmpeg is not installed or not discoverable."""


class FFmpegError(RuntimeError):
    """An FFmpeg/ffprobe invocation failed. Carries the tail of stderr."""

    def __init__(self, message: str, *, returncode: int, stderr: str, args: list[str]):
        super().__init__(message)
        self.returncode = returncode
        self.stderr = stderr
        self.args_run = args  # the command, for whoever catches it


def _search_fallbacks(name: str) -> Path | None:
    """Look in the usual Windows package-manager install trees."""
    exe_name = f"{name}.exe" if os.name == "nt" else name
    for base, pattern in _FALLBACK_LOCATIONS:
        if not base:
            continue
        base_path = Path(base)
        if not base_path.is_dir():
            continue
        try:
            for directory in sorted(base_path.glob(pattern)):
                exe = directory / exe_name
                if exe.is_file():
                    return exe
        except OSError:  # pragma: no cover - permission denied while globbing
            continue
    return None


@cache
def _find(name: str) -> Path:
    """Locate ffmpeg/ffprobe. Honours CLIPPER_FFMPEG_DIR, then PATH, then installs."""
    override = os.environ.get("CLIPPER_FFMPEG_DIR", "").strip()
    if override:
        exe = Path(override) / f"{name}.exe"
        if exe.is_file():
            return exe
        exe = Path(override) / name
        if exe.is_file():
            return exe
        raise FFmpegNotFound(
            f"CLIPPER_FFMPEG_DIR is set to {override!r} but {name} is not there."
        )

    found = shutil.which(name)
    if found:
        return Path(found)

    fallback = _search_fallbacks(name)
    if fallback:
        return fallback

    raise FFmpegNotFound(
        f"{name} was not found.\n"
        "  Install it with:  winget install --id Gyan.FFmpeg\n"
        "  Then open a NEW terminal so PATH refreshes, or set CLIPPER_FFMPEG_DIR "
        "to the folder containing ffmpeg.exe."
    )


def ffmpeg_path() -> Path:
    return _find("ffmpeg")


def ffprobe_path() -> Path:
    return _find("ffprobe")


def run(
    args: list[str | Path],
    *,
    exe: Path | None = None,
    cwd: Path | None = None,
    check: bool = True,
    stdin_data: bytes | None = None,
    capture: bool = True,
    timeout: float | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run FFmpeg with UTF-8 forced on both the child's IO and our decoding."""
    exe = exe or ffmpeg_path()
    cmd = [str(exe), *(str(a) for a in args)]

    env = dict(os.environ)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    # Ask FFmpeg for UTF-8 on stderr regardless of the console codepage.
    env["AV_LOG_FORCE_NOCOLOR"] = "1"

    proc = subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        input=stdin_data,
        capture_output=capture,
        env=env,
        timeout=timeout,
        check=False,
    )
    stdout = (proc.stdout or b"").decode("utf-8", errors="replace") if capture else ""
    stderr = (proc.stderr or b"").decode("utf-8", errors="replace") if capture else ""

    if check and proc.returncode != 0:
        tail = "\n".join(stderr.strip().splitlines()[-12:])
        raise FFmpegError(
            f"{exe.name} exited {proc.returncode}:\n{tail}",
            returncode=proc.returncode,
            stderr=stderr,
            args=cmd,
        )
    return subprocess.CompletedProcess(cmd, proc.returncode, stdout, stderr)


@cache
def version() -> str:
    """The first line of `ffmpeg -version`, e.g. 'ffmpeg version 9.0.1-full_build ...'."""
    return run(["-hide_banner", "-version"]).stdout.splitlines()[0].strip()


@cache
def build_config() -> str:
    """The `configuration:` line, used to check for libass and friends."""
    for line in run(["-hide_banner", "-version"]).stdout.splitlines():
        if line.strip().startswith("configuration:"):
            return line
    return ""


def has_build_flag(flag: str) -> bool:
    """Whether this FFmpeg was compiled with e.g. 'libass' or 'nvenc'."""
    return f"--enable-{flag}" in build_config()


@cache
def has_filter(name: str) -> bool:
    out = run(["-hide_banner", "-filters"]).stdout
    return any(line.split()[1:2] == [name] for line in out.splitlines() if line.strip())


@dataclass(frozen=True)
class EncoderProbe:
    """Result of actually trying to open an encoder."""

    name: str
    usable: bool
    detail: str = ""


@cache
def probe_encoder(name: str) -> EncoderProbe:
    """Encode one real frame to see whether `name` works on this machine.

    Listing an encoder is not the same as being able to open it: FFmpeg 9.x is
    built against NVENC SDK 13.1, which needs driver >= 610, so on an older driver
    ``h264_nvenc`` is listed and then refuses to initialise.
    """
    devnull = "NUL" if os.name == "nt" else "/dev/null"
    args = [
        "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", "color=c=black:s=256x256:r=30:d=0.1",
        "-c:v", name, "-frames:v", "1",
        "-f", "null", devnull,
    ]
    try:
        proc = run(args, check=False, timeout=60)
    except (FFmpegNotFound, subprocess.TimeoutExpired) as exc:
        return EncoderProbe(name, False, str(exc))

    if proc.returncode == 0:
        return EncoderProbe(name, True)

    detail = "\n".join(
        line for line in proc.stderr.splitlines() if line.strip()
    ).strip()[:400]
    return EncoderProbe(name, False, detail or f"exited {proc.returncode}")


def select_video_encoder(requested: str) -> tuple[str, str]:
    """Resolve ``render.encoder`` to a real encoder plus a one-line reason.

    ``auto`` prefers NVENC (much cheaper on CPU) but silently falls back to
    libx264 when the driver cannot open it. An explicit ``h264_nvenc`` that fails
    to probe is an error, not a fallback -- if you asked for it you want to know.
    """
    if requested == "libx264":
        return "libx264", "explicitly requested"

    probe = probe_encoder("h264_nvenc")
    if requested == "h264_nvenc":
        if probe.usable:
            return "h264_nvenc", "explicitly requested, probe passed"
        raise FFmpegError(
            "render.encoder is h264_nvenc but NVENC could not be opened on this "
            f"machine:\n{probe.detail}\n"
            "Set render.encoder to 'auto' or 'libx264', or update the NVIDIA driver.",
            returncode=1,
            stderr=probe.detail,
            args=["h264_nvenc probe"],
        )

    if probe.usable:
        return "h264_nvenc", "auto: NVENC probe passed"
    return "libx264", f"auto: NVENC unavailable ({_short_nvenc_reason(probe.detail)})"


def _short_nvenc_reason(detail: str) -> str:
    """Condense NVENC's multi-line failure into something fit for a status line."""
    for line in detail.splitlines():
        low = line.lower()
        if "driver does not support" in low or "minimum required nvidia driver" in low:
            return line.split("]", 1)[-1].strip()
    return detail.splitlines()[0].strip() if detail else "probe failed"


def escape_filter_path(path: Path | str) -> str:
    """Escape a Windows path for use inside an FFmpeg filter argument.

    ``C:\\dir\\f.ass`` must become ``C\\:/dir/f.ass``: backslashes are filter
    escapes, and the drive colon would otherwise be read as an option separator.
    Verified against ffmpeg 9.0.1 with spaces in the directory name.
    """
    s = str(path).replace("\\", "/")
    for ch in (":", "'", "[", "]", ","):
        s = s.replace(ch, "\\" + ch)
    return s


def bundled_fonts_dir() -> Path:
    """Directory handed to libass via ``fontsdir`` so it finds our bundled font."""
    return REPO_ROOT / "assets" / "fonts"
