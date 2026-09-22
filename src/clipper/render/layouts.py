"""Turning a 16:9 source into a 9:16 frame.

Three layouts, per BUILD_BRIEF.md section 11.1:

* **follow_crop** -- one dominant face. A tall crop tracks it, smoothed and
  speed-limited so the camera never snaps.
* **two_speaker_stack** -- two persistent faces, one above the other.
* **blurred_fit** -- no reliable face. The source sits full-width over a blurred,
  darkened copy of itself.

This module is pure geometry and contains no FFmpeg calls, so every rule here
(never crop outside the source, never exceed the pan speed limit, keep headroom
above the face) is unit-testable without rendering anything.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..models import CropKeyframe, CropRect, LayoutPlan

# A face looks wrong dead-centre; the eyeline wants to sit above the middle.
# 0.40 puts the face centre at 40% down the frame, leaving headroom above.
FACE_VERTICAL_ANCHOR = 0.40

# A detected face should occupy roughly this fraction of the crop's height.
# Too tight is claustrophobic, too loose wastes the vertical frame.
TARGET_FACE_HEIGHT_RATIO = 0.32

# How far the subject may drift from where the camera is pointing before it
# reframes, as a fraction of the crop's width. At 0.18 a face moving within the
# middle third of the crop produces no camera movement at all -- which is the
# common case for a seated speaker on a fixed webcam, where any movement is
# shake rather than tracking.
DEFAULT_DEADZONE_RATIO = 0.18


@dataclass(frozen=True)
class FaceObservation:
    """One face at one sampled instant, in source pixel coordinates."""

    t: float
    x: float  # centre
    y: float  # centre
    width: float
    height: float
    confidence: float = 1.0

    @property
    def area(self) -> float:
        return self.width * self.height


def crop_size_for_aspect(src_w: int, src_h: int, out_w: int, out_h: int) -> tuple[int, int]:
    """Largest crop of the source that has the output's aspect ratio.

    For 1920x1080 -> 1080x1920 this is 607x1080: the full source height and as
    much width as 9:16 allows. Both dimensions are forced even, because
    ``yuv420p`` chroma subsampling requires it and FFmpeg will otherwise refuse
    or silently round.
    """
    if min(src_w, src_h, out_w, out_h) <= 0:
        raise ValueError("all dimensions must be positive")

    target_aspect = out_w / out_h
    if src_w / src_h > target_aspect:
        # Source is wider than the target: height-limited.
        crop_h = src_h
        crop_w = round(src_h * target_aspect)
    else:
        # Source is taller or equal: width-limited.
        crop_w = src_w
        crop_h = round(src_w / target_aspect)

    crop_w = min(_make_even(crop_w), _make_even(src_w))
    crop_h = min(_make_even(crop_h), _make_even(src_h))
    return crop_w, crop_h


def _make_even(value: int) -> int:
    return value if value % 2 == 0 else value - 1


def clamp_crop_origin(x: float, y: float, crop_w: int, crop_h: int,
                      src_w: int, src_h: int) -> tuple[int, int]:
    """Clamp a crop origin so the rectangle stays inside the source.

    A crop that runs off the edge produces green or black bands, which the QA
    gate would then flag as black frames -- so this is enforced, not trusted.
    """
    max_x = max(0, src_w - crop_w)
    max_y = max(0, src_h - crop_h)
    return round(min(max(x, 0), max_x)), round(min(max(y, 0), max_y))


def centered_crop(src_w: int, src_h: int, out_w: int, out_h: int) -> CropRect:
    """The static, middle-of-frame crop. Used as the fallback trajectory."""
    crop_w, crop_h = crop_size_for_aspect(src_w, src_h, out_w, out_h)
    x, y = clamp_crop_origin((src_w - crop_w) / 2, (src_h - crop_h) / 2,
                             crop_w, crop_h, src_w, src_h)
    return CropRect(x=x, y=y, width=crop_w, height=crop_h)


def crop_origin_for_face(face_x: float, face_y: float, crop_w: int, crop_h: int,
                         src_w: int, src_h: int) -> tuple[int, int]:
    """Crop origin that frames a face centre with headroom above it."""
    x = face_x - crop_w / 2
    y = face_y - crop_h * FACE_VERTICAL_ANCHOR
    return clamp_crop_origin(x, y, crop_w, crop_h, src_w, src_h)


def smooth_trajectory(
    targets: list[tuple[float, float]],
    *,
    alpha: float,
    max_step: float,
    initial: float | None = None,
    deadzone: float = 0.0,
) -> list[float]:
    """Smooth a sequence of target positions into a camera path.

    Three stages, in this order, because order matters:

    1. A **deadzone**: the camera does not move at all until the subject drifts
       further than `deadzone` pixels from where it is already pointing.
    2. An exponential moving average, which removes detector jitter.
    3. A hard per-step cap, which removes the remaining fast swings.

    The deadzone is what makes the result watchable. Without it the camera
    chases every small head movement, and because the trajectory is applied as
    discrete steps at the sampling rate (5 Hz by default), those small
    corrections show up as visible shake rather than as motion. A real operator
    holds the shot still and only reframes when the subject actually leaves the
    frame; this reproduces that. On a fixed webcam it means no movement at all.

    Doing the speed cap *after* the EMA means a genuine jump to a new speaker is
    approached at a constant bounded rate rather than with the EMA's ease-out --
    which reads as a deliberate camera move instead of a lurch.

    `targets` is a list of (time, value); the return is one value per target.
    """
    if not targets:
        return []
    if not 0 < alpha <= 1:
        raise ValueError("alpha must be in (0, 1]")
    if deadzone < 0:
        raise ValueError("deadzone must not be negative")

    current = initial if initial is not None else targets[0][1]
    out: list[float] = []
    prev_t = targets[0][0]

    for t, target in targets:
        dt = max(0.0, t - prev_t)
        error = target - current

        if abs(error) <= deadzone:
            # Inside the comfort zone: hold the shot completely still.
            out.append(current)
            prev_t = t
            continue

        # Outside it, aim for the edge of the deadzone rather than dead centre.
        # Recentring fully would make the camera twitch back and forth every
        # time the subject crosses the boundary.
        aim = target - (deadzone if error > 0 else -deadzone)

        smoothed = current + alpha * (aim - current)
        limit = max_step * dt if dt > 0 else float("inf")
        delta = smoothed - current
        if abs(delta) > limit:
            smoothed = current + (limit if delta > 0 else -limit)
        out.append(smoothed)
        current = smoothed
        prev_t = t
    return out


def plan_follow_crop(
    faces: list[FaceObservation],
    *,
    src_w: int,
    src_h: int,
    out_w: int,
    out_h: int,
    duration: float,
    pan_smoothing: float,
    max_pan_speed: float,
    scene_cuts: list[float] | None = None,
    deadzone_ratio: float = DEFAULT_DEADZONE_RATIO,
) -> LayoutPlan:
    """Plan a single-face follow crop over the clip's duration.

    `faces` holds at most one observation per sampled instant -- the dominant
    face, already chosen. `scene_cuts` are times where tracking should snap
    instead of pan: easing across a hard cut looks like a mistake.
    """
    crop_w, crop_h = crop_size_for_aspect(src_w, src_h, out_w, out_h)
    fallback = centered_crop(src_w, src_h, out_w, out_h)

    if not faces:
        return LayoutPlan(
            kind="follow_crop",
            crop_width=crop_w,
            crop_height=crop_h,
            keyframes=[CropKeyframe(t=0.0, x=fallback.x, y=fallback.y)],
            face_ratio=0.0,
            reason="no faces observed; holding a centred crop",
        )

    faces = sorted(faces, key=lambda f: f.t)
    cuts = sorted(scene_cuts or [])

    # Raw per-observation targets, before smoothing.
    raw: list[tuple[float, float, float]] = []
    for f in faces:
        x, y = crop_origin_for_face(f.x, f.y, crop_w, crop_h, src_w, src_h)
        raw.append((f.t, float(x), float(y)))

    max_step = max_pan_speed * src_w  # pixels per second
    deadzone_x = deadzone_ratio * crop_w
    # Vertical drift is more noticeable than horizontal, and there is usually
    # far less of it, so the vertical deadzone is proportionally larger.
    deadzone_y = deadzone_ratio * crop_h * 1.5

    # Smooth within each shot, restarting at every scene cut.
    xs: list[float] = []
    ys: list[float] = []
    for segment in _split_at_cuts(raw, cuts):
        xs += smooth_trajectory([(t, x) for t, x, _ in segment],
                                alpha=pan_smoothing, max_step=max_step,
                                deadzone=deadzone_x)
        ys += smooth_trajectory([(t, y) for t, _, y in segment],
                                alpha=pan_smoothing, max_step=max_step,
                                deadzone=deadzone_y)

    keyframes = []
    for (t, _, _), x, y in zip(raw, xs, ys, strict=True):
        cx, cy = clamp_crop_origin(x, y, crop_w, crop_h, src_w, src_h)
        keyframes.append(CropKeyframe(t=round(max(0.0, t), 3), x=cx, y=cy))

    keyframes = _dedupe_keyframes(keyframes)
    if keyframes and keyframes[0].t > 0:
        keyframes.insert(0, CropKeyframe(t=0.0, x=keyframes[0].x, y=keyframes[0].y))

    return LayoutPlan(
        kind="follow_crop",
        crop_width=crop_w,
        crop_height=crop_h,
        keyframes=keyframes,
        face_ratio=_face_ratio(faces, duration),
        reason=f"one dominant face across {len(faces)} sampled frames",
    )


def plan_two_speaker_stack(
    top_face: FaceObservation,
    bottom_face: FaceObservation,
    *,
    src_w: int,
    src_h: int,
    out_w: int,
    out_h: int,
    face_ratio: float = 1.0,
) -> LayoutPlan:
    """Two static crops, stacked, each filling half the output height.

    Deliberately static: two panes that both pan at once is visually noisy, and
    a two-shot is usually a fixed camera anyway.
    """
    pane_h = _make_even(out_h // 2)
    crop_w, crop_h = crop_size_for_aspect(src_w, src_h, out_w, pane_h)

    panes = []
    for face in (top_face, bottom_face):
        x, y = crop_origin_for_face(face.x, face.y, crop_w, crop_h, src_w, src_h)
        panes.append(CropRect(x=x, y=y, width=crop_w, height=crop_h))

    return LayoutPlan(
        kind="two_speaker_stack",
        crop_width=crop_w,
        crop_height=crop_h,
        panes=panes,
        face_ratio=face_ratio,
        reason="two persistent faces; stacked two-shot",
    )


def plan_blurred_fit(*, src_w: int, src_h: int, out_w: int, out_h: int,
                     reason: str = "no reliable face") -> LayoutPlan:
    """Full-width source over a blurred fill. Never crops away content."""
    return LayoutPlan(
        kind="blurred_fit",
        crop_width=src_w,
        crop_height=src_h,
        face_ratio=0.0,
        reason=reason,
    )


def choose_layout(
    faces_per_sample: list[list[FaceObservation]],
    *,
    src_w: int,
    src_h: int,
    out_w: int,
    out_h: int,
    duration: float,
    pan_smoothing: float,
    max_pan_speed: float,
    min_face_ratio: float,
    scene_cuts: list[float] | None = None,
    deadzone_ratio: float = DEFAULT_DEADZONE_RATIO,
) -> LayoutPlan:
    """Pick a layout from the sampled face detections.

    Order of preference: two_speaker_stack when two faces persist, follow_crop
    when one face is present often enough, blurred_fit otherwise. A vertical or
    square source skips cropping entirely -- there is nothing to reframe.
    """
    if src_h >= src_w:
        return plan_blurred_fit(src_w=src_w, src_h=src_h, out_w=out_w, out_h=out_h,
                                reason="source is already vertical or square")

    samples = len(faces_per_sample)
    if samples == 0:
        return plan_blurred_fit(src_w=src_w, src_h=src_h, out_w=out_w, out_h=out_h,
                                reason="no frames sampled")

    with_any = sum(1 for s in faces_per_sample if s)
    with_two = sum(1 for s in faces_per_sample if len(s) >= 2)
    ratio_any = with_any / samples
    ratio_two = with_two / samples

    if ratio_any < min_face_ratio:
        return plan_blurred_fit(
            src_w=src_w, src_h=src_h, out_w=out_w, out_h=out_h,
            reason=f"faces in only {ratio_any:.0%} of sampled frames "
                   f"(need {min_face_ratio:.0%})",
        )

    # Two speakers only when the pair persists through most of the clip and they
    # are horizontally separated -- two detections of the same person's face at
    # slightly different scales must not trigger a stack.
    if ratio_two >= 0.6:
        pair = _persistent_pair(faces_per_sample, src_w)
        if pair is not None:
            left, right = pair
            return plan_two_speaker_stack(left, right, src_w=src_w, src_h=src_h,
                                          out_w=out_w, out_h=out_h, face_ratio=ratio_two)

    dominant = [_dominant_face(s) for s in faces_per_sample if s]
    return plan_follow_crop(
        [f for f in dominant if f is not None],
        src_w=src_w, src_h=src_h, out_w=out_w, out_h=out_h,
        duration=duration, pan_smoothing=pan_smoothing,
        max_pan_speed=max_pan_speed, scene_cuts=scene_cuts,
        deadzone_ratio=deadzone_ratio,
    )


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def _dominant_face(faces: list[FaceObservation]) -> FaceObservation | None:
    """The face most likely to be the speaker: biggest, tie-broken by confidence."""
    if not faces:
        return None
    return max(faces, key=lambda f: (f.area, f.confidence))


def _persistent_pair(
    faces_per_sample: list[list[FaceObservation]], src_w: int
) -> tuple[FaceObservation, FaceObservation] | None:
    """Median positions of a left/right face pair, if one persists.

    Returns None when the two largest faces are not clearly separated, which is
    the usual sign of a double-detection on one person rather than a two-shot.
    """
    lefts: list[FaceObservation] = []
    rights: list[FaceObservation] = []
    for sample in faces_per_sample:
        if len(sample) < 2:
            continue
        two = sorted(sample, key=lambda f: f.area, reverse=True)[:2]
        a, b = sorted(two, key=lambda f: f.x)
        if b.x - a.x < src_w * 0.15:
            continue  # too close together to be two speakers
        lefts.append(a)
        rights.append(b)

    if len(lefts) < 2:
        return None
    return _median_face(lefts), _median_face(rights)


def _median_face(faces: list[FaceObservation]) -> FaceObservation:
    """Element-wise median, which ignores the occasional wild detection."""
    def med(values: list[float]) -> float:
        s = sorted(values)
        mid = len(s) // 2
        return s[mid] if len(s) % 2 else (s[mid - 1] + s[mid]) / 2

    return FaceObservation(
        t=med([f.t for f in faces]),
        x=med([f.x for f in faces]),
        y=med([f.y for f in faces]),
        width=med([f.width for f in faces]),
        height=med([f.height for f in faces]),
        confidence=med([f.confidence for f in faces]),
    )


def _face_ratio(faces: list[FaceObservation], duration: float) -> float:
    """Rough fraction of the clip with a face, from observation spacing."""
    if not faces or duration <= 0:
        return 0.0
    return min(1.0, len(faces) / max(1.0, duration * 5.0))


def _split_at_cuts(
    points: list[tuple[float, float, float]], cuts: list[float]
) -> list[list[tuple[float, float, float]]]:
    """Partition observations into per-shot runs at the scene cuts."""
    if not cuts:
        return [points]
    segments: list[list[tuple[float, float, float]]] = []
    current: list[tuple[float, float, float]] = []
    cut_iter = iter(cuts)
    next_cut = next(cut_iter, None)
    for point in points:
        while next_cut is not None and point[0] >= next_cut:
            if current:
                segments.append(current)
                current = []
            next_cut = next(cut_iter, None)
        current.append(point)
    if current:
        segments.append(current)
    return segments


def _dedupe_keyframes(keyframes: list[CropKeyframe]) -> list[CropKeyframe]:
    """Drop keyframes that repeat the previous position.

    A static shot produces hundreds of identical entries; emitting them all
    bloats the sendcmd script and makes it unreadable when debugging.
    """
    out: list[CropKeyframe] = []
    for kf in keyframes:
        if out and out[-1].x == kf.x and out[-1].y == kf.y:
            continue
        out.append(kf)
    return out


def plan_content_stack(
    webcam: CropRect,
    content: CropRect,
    *,
    src_w: int,
    src_h: int,
    out_w: int,
    out_h: int,
    content_share: float = 0.58,
    face_ratio: float = 1.0,
) -> LayoutPlan:
    """Stack screen-share content above the speaker's webcam.

    For a source where the webcam is a small inset and the real subject is
    elsewhere on screen -- gameplay, a board, a slide deck -- cropping to the
    face throws the subject away. This keeps both, in their own panes.

    The split respects the content's own aspect ratio rather than forcing a
    fixed ratio. The content pane is given the height it needs to appear at
    full output width, clamped so it can neither squeeze the speaker out nor
    shrink to a strip, and the webcam absorbs whatever is left. That ordering
    matters: a webcam crops gracefully because its subject is centred with
    slack around it, whereas cropping a board or a slide loses information.
    """
    if out_w <= 0 or out_h <= 0:
        raise ValueError("output dimensions must be positive")

    # Height the content would occupy at full output width.
    natural = out_w * content.height / max(1, content.width)

    # Clamp so neither pane collapses. The lower bound keeps the content
    # dominant on wide content; the upper bound guarantees the speaker a
    # reasonable pane on very tall content.
    lo = int(out_h * min(content_share, 0.45))
    hi = int(out_h * max(content_share, 0.72))
    content_height = _make_even(int(max(lo, min(hi, natural))))
    webcam_height = _make_even(out_h - content_height)
    # Rounding both to even can lose a row; give it to the content pane.
    if content_height + webcam_height != out_h:
        content_height = out_h - webcam_height

    return LayoutPlan(
        kind="content_stack",
        crop_width=content.width,
        crop_height=content.height,
        panes=[content, webcam],
        pane_heights=[content_height, webcam_height],
        face_ratio=face_ratio,
        reason=(
            f"screen-share source: content {content.width}x{content.height} over "
            f"webcam {webcam.width}x{webcam.height}, split {content_height}/{webcam_height}"
        ),
    )
