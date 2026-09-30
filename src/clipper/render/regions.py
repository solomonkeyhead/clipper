"""Finding the *content* region when a face is only a picture-in-picture webcam.

A 9:16 crop centred on the speaker is the right answer for a talking head that
fills the frame. It is the wrong answer for screen-share content -- gameplay, a
chess board, a slide deck, a code editor -- where the webcam is a small inset and
everything the clip is actually *about* lives elsewhere. Cropping to the face
there throws away the subject of the video.

The detector works on frames the face scan already decodes, so it costs one pass
over small downscaled arrays rather than another decode.

**What separates content from background is how much a region differs from the
frame's dominant background tone**, not how much it moves. That was the second
design: the first used temporal variance and edge density, and it failed on real
footage because a chess board is *static* between moves and its flat squares
carry few edges at grid resolution. Measured on that frame, luminance found the
board as a single 1120x1080 blob (aspect 1.04) and the webcam as a separate
740x540 one, while the variance map spread thinly across everything.

Comparing against the *modal* tone rather than against black means this works on
a light background (a slide deck) as well as a dark one.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..models import CropRect
from ..utils.logging import get_logger

log = get_logger(__name__)

# Resolution of the content map. Coarse on purpose: we want the region, not its
# exact pixel edges, and a small grid is robust to noise.
GRID_WIDTH = 96
GRID_HEIGHT = 54

# A face this small relative to the frame is an inset webcam, not the subject.
# Measured on real screen-share footage: a chess video's facecam was 7.2% of
# frame width, while a talking head typically runs 15-35%.
PIP_FACE_WIDTH_RATIO = 0.13

# How far a cell's luminance must sit from the background tone to count as
# content, in 0-255 units.
BACKGROUND_TOLERANCE = 28.0

# The content region must be at least this fraction of the frame to be worth
# showing; anything smaller is a stray UI element.
MIN_CONTENT_AREA_RATIO = 0.08

# ...and no larger than this, since a region covering nearly everything means
# the split failed and there is no distinct content area.
MAX_CONTENT_AREA_RATIO = 0.92


@dataclass
class ContentMap:
    """Per-cell measure of how much a region looks like content."""

    score: np.ndarray        # (GRID_HEIGHT, GRID_WIDTH), normalised to [0, 1]
    luminance: np.ndarray    # mean luminance per cell, 0-255
    background: float        # the frame's dominant background tone
    frame_width: int
    frame_height: int

    def cell_to_pixels(self, x0: int, y0: int, x1: int, y1: int) -> CropRect:
        """Convert an inclusive cell box into a source-pixel rectangle."""
        sx = self.frame_width / self.score.shape[1]
        sy = self.frame_height / self.score.shape[0]
        px0, py0 = int(x0 * sx), int(y0 * sy)
        px1, py1 = int((x1 + 1) * sx), int((y1 + 1) * sy)
        return CropRect(
            x=max(0, px0), y=max(0, py0),
            width=max(2, min(self.frame_width, px1) - max(0, px0)),
            height=max(2, min(self.frame_height, py1) - max(0, py0)),
        )


class ActivityAccumulator:
    """Builds a `ContentMap` incrementally, one sampled frame at a time.

    Running sums rather than stored frames: a 45-second clip at 5 fps is 225
    frames, and keeping them all would cost far more than the result is worth.
    """

    def __init__(self, frame_width: int, frame_height: int):
        self.frame_width = frame_width
        self.frame_height = frame_height
        self._count = 0
        self._sum = np.zeros((GRID_HEIGHT, GRID_WIDTH), dtype=np.float64)
        self._sum_sq = np.zeros((GRID_HEIGHT, GRID_WIDTH), dtype=np.float64)

    def add(self, frame) -> None:
        """Accumulate one BGR frame."""
        import cv2

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        small = cv2.resize(gray, (GRID_WIDTH, GRID_HEIGHT),
                           interpolation=cv2.INTER_AREA).astype(np.float64)
        self._sum += small
        self._sum_sq += small * small
        self._count += 1

    def build(self) -> ContentMap | None:
        if self._count < 1:
            return None

        luminance = self._sum / self._count
        variance = np.maximum(0.0, self._sum_sq / self._count - luminance * luminance)
        background = _background_tone(luminance)

        # Distance from the background tone is the primary signal; temporal
        # variation is a small bonus so a moving element on a background-toned
        # surface is not missed entirely.
        distance = np.abs(luminance - background)
        score = _normalise(distance) + 0.25 * _normalise(np.sqrt(variance))

        return ContentMap(
            score=_normalise(score),
            luminance=luminance,
            background=background,
            frame_width=self.frame_width,
            frame_height=self.frame_height,
        )


def _mode_tone(values: np.ndarray) -> float:
    """Dominant tone, as the peak of a coarse luminance histogram."""
    counts, edges = np.histogram(values, bins=16, range=(0.0, 255.0))
    peak = int(np.argmax(counts))
    return float((edges[peak] + edges[peak + 1]) / 2)


def _background_tone(luminance: np.ndarray) -> float:
    """The frame's background tone, taken from its border.

    Background is, by definition, what *surrounds* the content, so the outer
    ring of the frame is a far stronger prior than the frame as a whole. Using
    the global mode instead breaks whenever the content area is large and
    flat-toned: it then wins the histogram and gets classified as background,
    inverting the whole detection. Real footage hid this -- a chess board's
    alternating squares spread across several histogram bins while the black
    surround concentrated in one -- but a plain slide or a solid-colour game
    background would have triggered it.

    The mean of the border is deliberately not used: a border that is half dark
    surround and half bright content would land between the two and match
    neither.
    """
    rows, cols = luminance.shape
    if rows < 3 or cols < 3:
        return _mode_tone(luminance)

    border = np.concatenate([
        luminance[0, :], luminance[-1, :],
        luminance[1:-1, 0], luminance[1:-1, -1],
    ])
    return _mode_tone(border)


def _normalise(values: np.ndarray) -> np.ndarray:
    lo, hi = float(values.min()), float(values.max())
    if hi - lo < 1e-9:
        return np.zeros_like(values)
    return (values - lo) / (hi - lo)


def is_picture_in_picture(face_width: float, frame_width: int) -> bool:
    """Whether a detected face is small enough to be an inset webcam."""
    if frame_width <= 0:
        return False
    return (face_width / frame_width) < PIP_FACE_WIDTH_RATIO


# A boundary row or column of a region must be at least this densely filled to
# be kept. Trimming sparse edges stops a thin strip of adjacent UI -- an overlay
# title, a scoreboard -- from stretching the box across dead space.
EDGE_DENSITY_FLOOR = 0.55


def _trim_sparse_edges(mask: np.ndarray) -> tuple[int, int, int, int] | None:
    """Shrink a blob's bounding box until its edges are solidly filled.

    Connected-component boxes are generous: at grid resolution a bright title
    caption can bridge to the main content and drag the box out to include a
    column that is mostly empty. Measured on real footage, this pulled a chess
    board's box from 1320 wide out to include the overlay title beside it.
    """
    ys, xs = np.nonzero(mask)
    if ys.size == 0:
        return None
    y0, y1 = int(ys.min()), int(ys.max())
    x0, x1 = int(xs.min()), int(xs.max())

    changed = True
    while changed and x1 > x0 and y1 > y0:
        changed = False
        height = y1 - y0 + 1
        width = x1 - x0 + 1
        if mask[y0:y1 + 1, x0].sum() / height < EDGE_DENSITY_FLOOR:
            x0 += 1
            changed = True
        if x1 > x0 and mask[y0:y1 + 1, x1].sum() / height < EDGE_DENSITY_FLOOR:
            x1 -= 1
            changed = True
        if mask[y0, x0:x1 + 1].sum() / width < EDGE_DENSITY_FLOOR:
            y0 += 1
            changed = True
        if y1 > y0 and mask[y1, x0:x1 + 1].sum() / width < EDGE_DENSITY_FLOOR:
            y1 -= 1
            changed = True

    if x1 <= x0 or y1 <= y0:
        return None
    return x0, y0, x1, y1


def _components(content: ContentMap) -> list[tuple[CropRect, int]]:
    """Connected blobs of content, largest first, as (rect, cell count)."""
    import cv2

    mask = np.abs(content.luminance - content.background) > BACKGROUND_TOLERANCE
    if not mask.any():
        return []

    count, labels, stats, _ = cv2.connectedComponentsWithStats(
        mask.astype(np.uint8), connectivity=4
    )
    out: list[tuple[CropRect, int]] = []
    for label in range(1, count):  # 0 is background
        area = int(stats[label, cv2.CC_STAT_AREA])
        trimmed = _trim_sparse_edges(labels == label)
        if trimmed is None:
            continue
        x0, y0, x1, y1 = trimmed
        out.append((content.cell_to_pixels(x0, y0, x1, y1), area))
    out.sort(key=lambda pair: -pair[1])
    return out


def _contains(rect: CropRect, x: float, y: float) -> bool:
    return rect.x <= x <= rect.x + rect.width and rect.y <= y <= rect.y + rect.height


def detect_layout_regions(
    content: ContentMap | None,
    face_x: float,
    face_y: float,
    face_w: float,
    face_h: float,
) -> tuple[CropRect, CropRect] | None:
    """Return (webcam_region, content_region) when this looks like screen-share.

    The webcam is identified as the content blob *containing the face*, and the
    main content as the largest blob that does not -- so neither needs a guessed
    margin around the face, which an earlier version got badly wrong (it
    inferred a 685x802 webcam box from a 138x182 face and masked out most of the
    frame).

    None means "not a picture-in-picture layout": the caller should fall back to
    a normal follow-crop.
    """
    if content is None:
        return None
    if not is_picture_in_picture(face_w, content.frame_width):
        return None

    blobs = _components(content)
    if len(blobs) < 2:
        return None

    webcam = next((rect for rect, _ in blobs if _contains(rect, face_x, face_y)), None)
    if webcam is None:
        return None

    frame_area = content.frame_width * content.frame_height
    main = None
    for rect, _ in blobs:
        if rect is webcam:
            continue
        ratio = (rect.width * rect.height) / frame_area
        if MIN_CONTENT_AREA_RATIO <= ratio <= MAX_CONTENT_AREA_RATIO:
            main = rect
            break

    if main is None:
        return None

    log.info(
        "picture-in-picture layout: webcam %dx%d at (%d,%d), content %dx%d at (%d,%d)",
        webcam.width, webcam.height, webcam.x, webcam.y,
        main.width, main.height, main.x, main.y,
    )
    return webcam, main
