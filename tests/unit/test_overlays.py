"""Detecting graphics the source laid over the picture.

Reported with screenshots on real output: a Dumbo card cut in half at the frame
edge, and a subtitle bar cut to its middle 608px so the viewer could not read it.
These tests draw each kind of graphic onto a plain frame at a known position and
check it is found there -- and that the things that must *not* count (short
text such as a book title, a plain rectangle of flat colour) are not.

The detectors were tuned on real frames first (docs/VERIFIED.md, 2026-09-22);
these pin the behaviour so a later change cannot quietly lose it.
"""

from __future__ import annotations

import cv2
import numpy as np

from clipper.render.overlays import (
    OverlayBox,
    detect_overlays,
    persistent_regions,
)

W, H = 1920, 1080


def backdrop() -> np.ndarray:
    """A dark, softly graded frame -- a stand-in for an out-of-focus set."""
    ramp = np.linspace(30, 70, W, dtype=np.float32)
    img = np.repeat(ramp[None, :], H, axis=0)
    return cv2.merge([img, img * 0.9, img * 0.8]).astype(np.uint8)


def with_subtitle(img: np.ndarray, text: str, x: int = 420, y: int = 1000) -> np.ndarray:
    (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 1.1, 2)
    cv2.rectangle(img, (x - 12, y - th - 12), (x + tw + 12, y + 12), (40, 30, 20), -1)
    cv2.putText(img, text, (x, y), cv2.FONT_HERSHEY_SIMPLEX, 1.1, (240, 240, 240), 2,
                cv2.LINE_AA)
    return img


def with_card(img: np.ndarray, x=80, y=70, w=600, h=480) -> np.ndarray:
    cv2.rectangle(img, (x, y), (x + w, y + h), (20, 20, 20), 14)
    cv2.rectangle(img, (x + 7, y + 7), (x + w - 7, y + h - 7), (200, 200, 90), -1)
    # Something drawn on the card, as a picture would be.
    cv2.ellipse(img, (x + w // 2, y + h // 2), (w // 4, h // 6), 0, 0, 360,
                (120, 120, 120), -1)
    return img


def with_circle(img: np.ndarray, cx=330, cy=330, r=280) -> np.ndarray:
    cv2.circle(img, (cx, cy), r, (180, 170, 230), -1)
    cv2.circle(img, (cx, cy), r, (25, 25, 25), 12)
    cv2.line(img, (cx - r // 2, cy), (cx + r // 2, cy + r // 3), (40, 90, 200), 18)
    return img


def kinds(boxes: list[OverlayBox]) -> set[str]:
    return {b.kind for b in boxes}


class TestDetection:
    def test_a_plain_frame_has_no_graphics(self):
        assert detect_overlays(backdrop(), src_w=W, src_h=H) == []

    def test_a_subtitle_line_is_found_where_it_is(self):
        text = "People really do walk in circles when they are lost"
        boxes = detect_overlays(with_subtitle(backdrop(), text), src_w=W, src_h=H)
        lines = [b for b in boxes if b.kind == "text"]
        assert lines, "the subtitle was not detected"
        line = max(lines, key=lambda b: b.width)
        assert line.x < 480 and line.right > 1100
        assert 900 < line.y < 1040

    def test_short_text_is_not_a_graphic_to_keep(self):
        """A book title on the set, a shirt logo: fine to crop through."""
        img = with_subtitle(backdrop(), "JUNGLEKEEPER", x=200, y=900)
        assert "text" not in kinds(detect_overlays(img, src_w=W, src_h=H))

    def test_a_card_is_found(self):
        boxes = detect_overlays(with_card(backdrop()), src_w=W, src_h=H)
        cards = [b for b in boxes if b.kind == "card"]
        assert cards, "the card was not detected"
        card = cards[0]
        assert abs(card.x - 80) < 40 and abs(card.right - 680) < 40

    def test_a_circular_insert_is_found(self):
        boxes = detect_overlays(with_circle(backdrop()), src_w=W, src_h=H)
        circles = [b for b in boxes if b.kind == "circle"]
        assert circles, "the circle was not detected"
        assert abs(circles[0].x + circles[0].width / 2 - 330) < 60

    def test_coordinates_are_in_source_pixels_at_any_input_size(self):
        """Detection runs at a fixed analysis width; boxes must be scaled back."""
        img = with_card(backdrop())
        small = cv2.resize(img, (640, 360), interpolation=cv2.INTER_AREA)
        full = [b for b in detect_overlays(img, src_w=W, src_h=H) if b.kind == "card"]
        reduced = [b for b in detect_overlays(small, src_w=W, src_h=H) if b.kind == "card"]
        assert full and reduced
        assert abs(full[0].x - reduced[0].x) < 20
        assert abs(full[0].width - reduced[0].width) < 20


def box(x: float, kind: str = "card", w: float = 600) -> OverlayBox:
    return OverlayBox(x=x, y=70, width=w, height=480, kind=kind)


class TestPersistence:
    def test_a_graphic_seen_throughout_is_kept(self):
        regions = persistent_regions([[box(80)] for _ in range(20)])
        assert len(regions) == 1

    def test_a_single_detection_is_ignored(self):
        """One spurious hit on one frame must not widen a whole shot."""
        per_sample = [[] for _ in range(20)]
        per_sample[7] = [box(80)]
        assert persistent_regions(per_sample) == []

    def test_an_intermittent_graphic_is_kept(self):
        """The animated Dumbo card was detected in 5 of 19 samples."""
        per_sample = [[box(80)] if i % 4 == 0 else [] for i in range(19)]
        assert len(persistent_regions(per_sample)) == 1

    def test_the_region_covers_every_position_of_a_moving_graphic(self):
        """An animated card bobs; the frame has to hold all of it."""
        regions = persistent_regions([[box(80 + (i % 3) * 20)] for i in range(12)])
        assert len(regions) == 1
        assert regions[0].x == 80
        assert regions[0].right == 80 + 40 + 600

    def test_different_kinds_are_kept_apart(self):
        per_sample = [[box(80, "card"), box(80, "circle")] for _ in range(10)]
        assert kinds(persistent_regions(per_sample)) == {"card", "circle"}

    def test_no_samples(self):
        assert persistent_regions([]) == []
