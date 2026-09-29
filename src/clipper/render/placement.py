"""Where faces are on the 9:16 frame, so captions don't cover them (research R5.2).

The face scan behind the framing sees faces in source pixels; the layout says
which part of the source becomes the frame. Together they give each face's box
on the output, which `captions.build_ass` checks each page against: a page that
would cover a face moves below the chin if that stays inside the safe box, else
to the top band (below the hook line while it's up), else stays where it was.
"""

from __future__ import annotations

from ..models import LayoutPlan

Box = tuple[float, float, float, float]   # x0, y0, x1, y1 on the output frame


def _single_at(layout: LayoutPlan, t: float) -> LayoutPlan | None:
    if layout.kind == "per_shot":
        for segment in layout.segments:
            if segment.start <= t < segment.end:
                return segment.layout
        return layout.segments[-1].layout if layout.segments else None
    return layout


def _to_output(layout: LayoutPlan, box: Box, out_w: int, out_h: int) -> Box | None:
    """A source-pixel box on the output frame; None for stacked layouts."""
    if layout.kind not in ("follow_crop", "fit_crop", "blurred_fit") or not layout.crop_width:
        return None
    x = layout.keyframes[0].x if layout.keyframes else 0
    y = layout.keyframes[0].y if layout.keyframes else 0
    x0, y0, x1, y1 = box
    if layout.kind == "follow_crop":
        scale = out_h / layout.crop_height
        top = 0.0
    else:  # the picture is scaled to the full width, centred vertically
        scale = out_w / layout.crop_width
        top = (out_h - layout.crop_height * scale) / 2
    return ((x0 - x) * scale, top + (y0 - y) * scale,
            (x1 - x) * scale, top + (y1 - y) * scale)


def faces_on_screen(scan, layout: LayoutPlan, out_w: int, out_h: int) -> list[tuple[float, list[Box]]]:
    """(clip-relative time, face boxes on the output) for each scanned instant."""
    out = []
    for t, faces in zip(scan.sample_times, scan.per_sample, strict=False):
        single = _single_at(layout, t)
        if single is None:
            continue
        boxes = []
        for f in faces:
            mapped = _to_output(single, (f.x - f.width / 2, f.y - f.height / 2,
                                         f.x + f.width / 2, f.y + f.height / 2), out_w, out_h)
            if mapped and mapped[2] > 0 and mapped[0] < out_w:
                boxes.append(mapped)
        out.append((t, boxes))
    return out


def _overlaps(a: Box, b: Box) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def place(box: Box, faces: list[Box], *, bottom_limit: float, top_limit: float,
          gap: float) -> tuple[str, float] | None:
    """Where a caption box goes: None to leave it, else ("below"|"top", y).

    For "below", y is the caption's new bottom edge; for "top", its top edge.
    Face boxes are grown by 10% first, so text doesn't sit on the hairline.
    """
    grown = [(x0 - (x1 - x0) * 0.05, y0 - (y1 - y0) * 0.05, x1 + (x1 - x0) * 0.05,
              y1 + (y1 - y0) * 0.05) for x0, y0, x1, y1 in faces]
    hits = [f for f in grown if _overlaps(box, f)]
    if not hits:
        return None
    height = box[3] - box[1]
    below = max(f[3] for f in hits) + gap + height
    if below <= bottom_limit:
        return "below", below
    top = top_limit
    moved = (box[0], top, box[2], top + height)
    if not any(_overlaps(moved, f) for f in grown):
        return "top", top
    return None
