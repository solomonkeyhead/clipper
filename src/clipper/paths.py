"""Where everything lives on disk, and the Windows-specific hazards around that.

All large artifacts go under one data root. It defaults to ``<repo>/data`` but can
be moved with ``CLIPPER_DATA_DIR`` -- which matters on this target machine because
a cloud-synced folder will happily try to upload every intermediate WAV and MP4.
"""

from __future__ import annotations

import os
from functools import cache
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# Environment variables set by the OneDrive / Dropbox clients, used to warn when
# the data root sits inside a synced tree.
_SYNC_ROOT_ENV_VARS = ("OneDrive", "OneDriveCommercial", "OneDriveConsumer", "Dropbox")


@cache
def data_root() -> Path:
    """Root for downloads, work artifacts, outputs, caches and logs."""
    override = os.environ.get("CLIPPER_DATA_DIR", "").strip()
    return Path(override).expanduser().resolve() if override else REPO_ROOT / "data"


def sync_roots() -> list[Path]:
    """Cloud-sync folders this machine advertises via the environment."""
    roots = []
    for var in _SYNC_ROOT_ENV_VARS:
        value = os.environ.get(var, "").strip()
        if value:
            try:
                roots.append(Path(value).resolve())
            except OSError:  # pragma: no cover - malformed env value
                continue
    return roots


def synced_parent(path: Path) -> Path | None:
    """The cloud-sync root containing `path`, or None. Used by `clipper doctor`."""
    try:
        resolved = path.resolve()
    except OSError:  # pragma: no cover - unreachable path
        return None
    for root in sync_roots():
        if resolved == root or root in resolved.parents:
            return root
    return None


def downloads_dir() -> Path:
    return data_root() / "downloads"


def work_dir(source_id: str) -> Path:
    """Per-source stage artifacts (info.json, transcript.json, ...)."""
    return data_root() / "work" / source_id


def runs_dir() -> Path:
    """Where runs write their reports and renders by default. Working files: the
    finished clips are filed in the library (studio/library.py), which is what
    the user looks at."""
    return data_root() / "work" / "runs"


def logs_dir() -> Path:
    return data_root() / "logs"


def cache_dir() -> Path:
    return data_root() / "cache"


def examples_dir() -> Path:
    """Few-shot examples mined from top-performing clips (see `clipper learn`)."""
    return data_root() / "examples"


def assets_dir() -> Path:
    """Bundled/downloaded fonts and face-detection models. Committed location."""
    return REPO_ROOT / "assets"


def fonts_dir() -> Path:
    return assets_dir() / "fonts"


def models_dir() -> Path:
    return assets_dir() / "models"


def ensure(path: Path) -> Path:
    """mkdir -p, returning the path so it composes in expressions."""
    path.mkdir(parents=True, exist_ok=True)
    return path


@cache
def campaigns_dir() -> Path:
    """Where the user's campaign files live: ``<data>/campaigns`` (D145). They used to sit in the repo's
    own ``campaigns/`` folder, which meant a copy of Clipper carried the owner's campaigns (and a push
    published them). The repo keeps only ``campaigns/example.yaml``, the template; anything else found
    there is moved here once, history included."""
    import shutil

    target = data_root() / "campaigns"
    target.mkdir(parents=True, exist_ok=True)
    legacy = REPO_ROOT / "campaigns"
    if legacy.is_dir() and target.resolve() != legacy.resolve():
        for path in legacy.iterdir():
            if path.name == "example.yaml" or path.name.startswith("."):
                continue
            if path.suffix == ".yaml" and not (target / path.name).exists():
                shutil.move(str(path), str(target / path.name))
        old = legacy / ".history"
        if old.is_dir():
            (target / ".history").mkdir(exist_ok=True)
            for path in old.iterdir():
                if not (target / ".history" / path.name).exists():
                    shutil.move(str(path), str(target / ".history" / path.name))
    return target
