"""A campaign's brand kit (D240): its own caption font, caption colours and a small logo.

Uploads land in ``<data>/campaigns/assets/`` under a name made from their content (16 hex digits of a
SHA-256 and the type), and a campaign file stores only that name. A render reads them only from that folder
and only under a name of that shape, so nobody can point a render at another file, typed or forged; that
matters hosted, where the page is someone else's. A font must load as a font (Pillow), and a logo is decoded
and written again as a plain PNG, which leaves out anything a file could carry besides its pixels.
"""

from __future__ import annotations

import hashlib
import io
import re
from pathlib import Path

from ..utils.logging import get_logger

log = get_logger(__name__)

FONT_CAP = 8 * 1024 * 1024     # a caption font is a few hundred KB; a CJK one can be several MB
LOGO_CAP = 5 * 1024 * 1024
LOGO_PIXELS = 1024             # longest side kept: it's shown at under 200 px
NAME = re.compile(r"^[0-9a-f]{16}\.(ttf|otf|png)$")


class BrandError(ValueError):
    """An upload that isn't a usable font or picture; the message says why."""


def assets_dir() -> Path:
    from ..paths import campaigns_dir

    return campaigns_dir() / "assets"


def path_of(name: str) -> Path | None:
    """The stored file called `name`, or None: only a name an upload made, only in the assets folder."""
    if not name or not NAME.match(name):
        return None
    path = assets_dir() / name
    return path if path.is_file() else None


def _store(data: bytes, suffix: str) -> str:
    name = f"{hashlib.sha256(data).hexdigest()[:16]}{suffix}"
    folder = assets_dir()
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_bytes(data)
    return name


def save_font(data: bytes, filename: str) -> dict:
    """Keep an uploaded .ttf/.otf: {file, family}. Raises BrandError unless Pillow can load it as a font."""
    from PIL import ImageFont

    suffix = Path(filename).suffix.lower()
    if suffix not in (".ttf", ".otf"):
        raise BrandError("Upload a font file: .ttf or .otf")
    if len(data) > FONT_CAP:
        raise BrandError(f"That font is over {FONT_CAP // 1024 // 1024} MB")
    try:
        family = ImageFont.truetype(io.BytesIO(data), 40).getname()[0]
    except (OSError, ValueError) as exc:
        raise BrandError("That file isn't a font Clipper can read") from exc
    if not family:
        raise BrandError("That font has no name inside it, so captions can't ask for it")
    return {"file": _store(data, suffix), "family": family}


def save_logo(data: bytes) -> dict:
    """Keep an uploaded PNG or JPG as a fresh PNG of its pixels only: {file, width, height}."""
    from PIL import Image, UnidentifiedImageError

    if len(data) > LOGO_CAP:
        raise BrandError(f"That picture is over {LOGO_CAP // 1024 // 1024} MB")
    try:
        with Image.open(io.BytesIO(data)) as probe:
            if probe.format not in ("PNG", "JPEG"):
                raise BrandError("Upload the logo as a PNG or JPG")
            if probe.width * probe.height > 40_000_000:
                raise BrandError("That picture is too big")
            probe.verify()
        with Image.open(io.BytesIO(data)) as img:
            img.load()
            clean = img.convert("RGBA")
    except (OSError, UnidentifiedImageError, Image.DecompressionBombError, SyntaxError) as exc:
        raise BrandError("That file isn't a picture Clipper can read") from exc
    clean.thumbnail((LOGO_PIXELS, LOGO_PIXELS))
    out = io.BytesIO()
    clean.save(out, "PNG", optimize=True)   # pixels only: no text chunks, profiles or anything appended
    return {"file": _store(out.getvalue(), ".png"), "width": clean.width, "height": clean.height}


def family_of(name: str) -> str:
    """The family name an uploaded font answers to in libass; "" if it isn't there."""
    from PIL import ImageFont

    path = path_of(name)
    if path is None:
        return ""
    try:
        return ImageFont.truetype(str(path), 40).getname()[0] or ""
    except OSError:
        return ""


def render_updates(campaign) -> dict:
    """RenderConfig updates for a campaign's brand kit; none when it has no kit (D240)."""
    kit = campaign.brand
    out: dict = {}
    if path_of(kit.font):
        out["brand_font"] = kit.font
    if path_of(kit.logo):
        out["brand_logo"] = kit.logo
        out["logo_corner"] = kit.logo_corner
    for key in ("text_colour", "highlight_colour", "outline_colour"):
        if getattr(kit, key):
            out[f"caption_{key}"] = getattr(kit, key)
    return out
