"""Finding graphics the source's editor laid over the picture.

Edited podcasts put things *beside* the speaker, in the half of the 16:9 frame a
vertical crop throws away: subtitle bars explaining a term, pop-up cards, circular
photo inserts. Cropping to the speaker cuts straight through them. Reported with
screenshots on real output: a Dumbo card sliced in half at the frame edge, and a
1128px-wide subtitle bar cut to its middle 608px so it could not be read.

Three detectors, one per kind of graphic seen in real footage, each chosen after
simpler ideas failed on that footage (docs/VERIFIED.md, 2026-09-22):

* **Temporal cues do not work.** "Sharp and pixel-static" also describes the set's
  in-focus posters, and "appears part-way through the shot" misses both reported
  cases: the card and the subtitle bar were each on screen for their entire shot,
  and the card is animated, so it is never pixel-static either.
* **Text lines** -- morphological gradient, Otsu threshold, horizontal closing, then
  line-shaped components. Only lines at least a quarter of the frame wide count:
  the goal is sentences a viewer needs to read, and the same detector also finds
  a book title on the set, a shirt logo and stadium signage, which may be cropped
  freely.
* **Cards** -- closed, convex contours with a rectangular fill.
* **Circles** -- a Hough transform. Circular inserts sit over busy backgrounds, so
  their outlines merge with neighbouring edges and never form a clean contour.

On nine hand-checked real frames the three together found every insert present
and flagged nothing on frames without one, including the posters and the
speakers' heads. That is a small sample; a single detection is therefore never
trusted on its own -- `persistent_regions` requires a graphic to recur across a
shot before it may change the framing.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

# Detection runs at this width regardless of the source resolution, so the pixel
# thresholds below mean the same thing on every source.
ANALYSIS_WIDTH = 640

# A text line must span at least this fraction of the frame width to count.
# Measured on real footage: subtitle lines 44-59%; stadium signage in b-roll
# ("NEWERACAP.COM") 20%, which an earlier 0.20 cut-off admitted and which then
# widened a whole shot for no reason; a book title on the set 13%; a shirt logo 4%.
MIN_TEXT_WIDTH_RATIO = 0.25

# Cards: fraction of the frame area. The Dumbo card measured 9%.
MIN_CARD_AREA_RATIO = 0.02
MAX_CARD_AREA_RATIO = 0.35

# A graphic must be detected in at least this share of a shot's samples, and in
# no fewer than MIN_OVERLAY_SAMPLES of them, before the framing makes room for it.
MIN_OVERLAY_SHARE = 0.2
MIN_OVERLAY_SAMPLES = 3


@dataclass(frozen=True)
class OverlayBox:
    """One detected graphic, in source pixel coordinates."""

    x: float
    y: float
    width: float
    height: float
    kind: str  # "text" | "card" | "circle"

    @property
    def right(self) -> float:
        return self.x + self.width

    @property
    def bottom(self) -> float:
        return self.y + self.height

    def iou(self, other: OverlayBox) -> float:
        ix = max(0.0, min(self.right, other.right) - max(self.x, other.x))
        iy = max(0.0, min(self.bottom, other.bottom) - max(self.y, other.y))
        inter = ix * iy
        union = self.width * self.height + other.width * other.height - inter
        return inter / union if union > 0 else 0.0


def detect_overlays(frame, *, src_w: int, src_h: int) -> list[OverlayBox]:
    """Every graphic found in one BGR frame, scaled back to source pixels."""
    import cv2

    h, w = frame.shape[:2]
    if w != ANALYSIS_WIDTH:
        scale_h = max(1, round(h * ANALYSIS_WIDTH / w))
        frame = cv2.resize(frame, (ANALYSIS_WIDTH, scale_h), interpolation=cv2.INTER_AREA)
    ah, aw = frame.shape[:2]
    sx, sy = src_w / aw, src_h / ah
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

    found = [*_text_lines(gray), *_cards(gray), *_circles(gray)]
    return [OverlayBox(x=x * sx, y=y * sy, width=bw * sx, height=bh * sy, kind=kind)
            for x, y, bw, bh, kind in found]


def _text_lines(gray) -> list[tuple[int, int, int, int, str]]:
    import cv2

    ah, aw = gray.shape[:2]
    grad = cv2.morphologyEx(gray, cv2.MORPH_GRADIENT,
                            cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)))
    _, bw = cv2.threshold(grad, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
    # Wide enough to bridge the gap between letters and words, short enough not
    # to merge one line of text into the next.
    closed = cv2.morphologyEx(bw, cv2.MORPH_CLOSE,
                              cv2.getStructuringElement(cv2.MORPH_RECT, (10, 3)))
    count, _, stats, _ = cv2.connectedComponentsWithStats(closed)

    min_h, max_h = round(ah * 0.011), round(ah * 0.075)
    lines = []
    for i in range(1, count):
        x, y, w, h, _ = stats[i]
        if not (min_h <= h <= max_h and w >= 3 * h and w >= aw * MIN_TEXT_WIDTH_RATIO):
            continue
        # Text strokes fill a moderate share of their line's box; a solid bar or
        # a lone edge does not.
        fill = bw[y:y + h, x:x + w].mean() / 255
        if 0.25 <= fill <= 0.85:
            lines.append((int(x), int(y), int(w), int(h), "text"))
    return lines


def _cards(gray) -> list[tuple[int, int, int, int, str]]:
    import cv2

    ah, aw = gray.shape[:2]
    frame_area = aw * ah
    edges = cv2.Canny(cv2.GaussianBlur(gray, (3, 3), 0), 50, 150)
    edges = cv2.dilate(edges, np.ones((3, 3), np.uint8))
    contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)

    cards: list[tuple[int, int, int, int]] = []
    for contour in contours:
        area = cv2.contourArea(contour)
        if not MIN_CARD_AREA_RATIO * frame_area <= area <= MAX_CARD_AREA_RATIO * frame_area:
            continue
        hull_area = cv2.contourArea(cv2.convexHull(contour))
        x, y, w, h = cv2.boundingRect(contour)
        solidity = area / max(1.0, hull_area)
        extent = area / max(1, w * h)
        if solidity > 0.95 and extent > 0.85:
            cards.append((int(x), int(y), int(w), int(h)))

    # A bordered card yields an inner and an outer contour; keep the outer one.
    cards.sort(key=lambda b: -b[2] * b[3])
    kept: list[tuple[int, int, int, int]] = []
    for x, y, w, h in cards:
        nested = any(x >= kx - 4 and y >= ky - 4 and x + w <= kx + kw + 4
                     and y + h <= ky + kh + 4 for kx, ky, kw, kh in kept)
        if not nested:
            kept.append((x, y, w, h))
    return [(x, y, w, h, "card") for x, y, w, h in kept]


def _circles(gray) -> list[tuple[int, int, int, int, str]]:
    import cv2

    ah = gray.shape[0]
    found = cv2.HoughCircles(
        cv2.medianBlur(gray, 5), cv2.HOUGH_GRADIENT_ALT, dp=1.5, minDist=60,
        param1=300, param2=0.9, minRadius=int(ah * 0.12), maxRadius=int(ah * 0.45),
    )
    if found is None:
        return []
    return [(int(cx - r), int(cy - r), int(2 * r), int(2 * r), "circle")
            for cx, cy, r in found[0]]


def persistent_regions(
    per_sample: list[list[OverlayBox]],
    *,
    min_share: float = MIN_OVERLAY_SHARE,
    min_samples: int = MIN_OVERLAY_SAMPLES,
) -> list[OverlayBox]:
    """Graphics that recur across a shot, each as the union of its detections.

    Detections are grouped by overlap. A group is kept only if it recurs in
    enough samples, which is what stops one spurious detection on one frame
    from widening a whole shot. The union, not an average, is returned: an
    animated card bobs a little, and the frame has to hold all of it.
    """
    samples = len(per_sample)
    if samples == 0:
        return []
    groups: list[tuple[OverlayBox, set[int]]] = []
    for index, boxes in enumerate(per_sample):
        for box in boxes:
            for g, (region, seen) in enumerate(groups):
                if region.kind == box.kind and region.iou(box) >= 0.3:
                    groups[g] = (_union(region, box), seen | {index})
                    break
            else:
                groups.append((box, {index}))

    needed = max(min_samples, int(np.ceil(samples * min_share)))
    return [region for region, seen in groups if len(seen) >= needed]


def _union(a: OverlayBox, b: OverlayBox) -> OverlayBox:
    x, y = min(a.x, b.x), min(a.y, b.y)
    return OverlayBox(x=x, y=y, width=max(a.right, b.right) - x,
                      height=max(a.bottom, b.bottom) - y, kind=a.kind)
