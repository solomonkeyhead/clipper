"""Assets fetched at setup time rather than vendored: fonts and the face model.

Both are permissively licensed but neither belongs in git -- the font is ~170 KB
of binary that Google already hosts, and the YuNet weights are a model file. They
are downloaded by ``clipper doctor --fix`` into ``assets/``, which is git-ignored.
"""

from __future__ import annotations

import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from .paths import ensure, fonts_dir, models_dir

_USER_AGENT = "clipper/0.1 (+https://github.com/local/clipper)"


@dataclass(frozen=True)
class Asset:
    """One downloadable file, with a sanity floor on its size."""

    name: str
    url: str
    dest_dir_key: str  # "fonts" or "models"
    min_bytes: int
    note: str = ""

    @property
    def dest(self) -> Path:
        base = fonts_dir() if self.dest_dir_key == "fonts" else models_dir()
        return base / self.name


# Anton is the display face used by the `bold_pop` caption style: single weight,
# very heavy, designed for large headlines -- which is exactly what a caption is.
# Inter covers the quieter styles. Both are SIL Open Font License 1.1.
ASSETS: tuple[Asset, ...] = (
    Asset(
        "Anton-Regular.ttf",
        "https://raw.githubusercontent.com/google/fonts/main/ofl/anton/Anton-Regular.ttf",
        "fonts", 100_000, "OFL 1.1 -- display face for bold_pop captions",
    ),
    Asset(
        "Anton-OFL.txt",
        "https://raw.githubusercontent.com/google/fonts/main/ofl/anton/OFL.txt",
        "fonts", 1_000, "Anton license",
    ),
    Asset(
        "Inter.ttf",
        "https://raw.githubusercontent.com/google/fonts/main/ofl/inter/Inter%5Bopsz%2Cwght%5D.ttf",
        "fonts", 500_000, "OFL 1.1 -- variable face for clean_white / yellow_highlight",
    ),
    Asset(
        "Inter-OFL.txt",
        "https://raw.githubusercontent.com/google/fonts/main/ofl/inter/OFL.txt",
        "fonts", 1_000, "Inter license",
    ),
    Asset(
        "face_detection_yunet.onnx",
        "https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/"
        "face_detection_yunet_2023mar.onnx",
        "models", 100_000, "YuNet face detector for cv2.FaceDetectorYN (Apache-2.0)",
    ),
)


class AssetDownloadError(RuntimeError):
    pass


def missing_assets() -> list[Asset]:
    return [a for a in ASSETS if not (a.dest.is_file() and a.dest.stat().st_size >= a.min_bytes)]


def download(asset: Asset, *, timeout: float = 120.0) -> Path:
    """Fetch one asset to a temp file, then move it into place.

    Downloading to ``<name>.part`` first means an interrupted run cannot leave a
    truncated font that then looks present to `doctor`.
    """
    ensure(asset.dest.parent)
    tmp = asset.dest.with_suffix(asset.dest.suffix + ".part")
    request = urllib.request.Request(asset.url, headers={"User-Agent": _USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = response.read()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise AssetDownloadError(f"could not download {asset.name} from {asset.url}: {exc}") from exc

    if len(data) < asset.min_bytes:
        raise AssetDownloadError(
            f"{asset.name} downloaded only {len(data)} bytes (expected >= {asset.min_bytes}); "
            "the URL may have moved or returned an error page"
        )

    tmp.write_bytes(data)
    tmp.replace(asset.dest)
    return asset.dest


def download_missing(*, timeout: float = 120.0) -> tuple[list[Path], list[str]]:
    """Fetch everything not already present. Returns (downloaded, errors)."""
    downloaded: list[Path] = []
    errors: list[str] = []
    for asset in missing_assets():
        try:
            downloaded.append(download(asset, timeout=timeout))
        except AssetDownloadError as exc:
            errors.append(str(exc))
    return downloaded, errors


def face_model_path() -> Path:
    return models_dir() / "face_detection_yunet.onnx"
