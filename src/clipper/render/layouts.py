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

from dataclasses import dataclass, field

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

# A face narrower than this fraction of the frame is an overlay inset rather
# than the subject. Kept in step with `regions.PIP_FACE_WIDTH_RATIO`, which
# gates the screen-share detector that runs first. Defined here, above its use
# as a default argument, because Python evaluates defaults at definition time.
INSET_FACE_WIDTH_RATIO = 0.13


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
    min_subject_face_ratio: float = INSET_FACE_WIDTH_RATIO,
) -> LayoutPlan:
    """Pick a layout from the sampled face detections.

    The guiding rule, learned from real footage: **only crop when there is
    something unambiguous to crop to.** A 9:16 slice of a 16:9 frame keeps under
    a third of the width, so a wrong choice does not degrade gracefully -- it
    discards most of the picture. Every uncertain case resolves to `blurred_fit`.

    Detections are first grouped into per-person tracks and then filtered to
    those large enough to be a *subject*. Both steps matter:

    * Without tracking, the target is recomputed every frame, so two people make
      it teleport between them -- measured at 10 side-flips across 68% of frame
      width in one clip, leaving the camera stranded between them showing
      neither face.
    * Without the size filter, a reaction-cam overlay counts as a second person,
      and a clip with one real speaker plus two tiny overlays is treated as a
      crowd that cannot be framed.
    """
    if src_h >= src_w:
        return plan_blurred_fit(src_w=src_w, src_h=src_h, out_w=out_w, out_h=out_h,
                                reason="source is already vertical or square")

    samples = len(faces_per_sample)
    if samples == 0:
        return plan_blurred_fit(src_w=src_w, src_h=src_h, out_w=out_w, out_h=out_h,
                                reason="no frames sampled")

    tracks = build_tracks(faces_per_sample, src_w)
    subjects = [t for t in tracks
                if t.median_width / src_w >= min_subject_face_ratio]

    if not subjects:
        detected = sum(1 for s in faces_per_sample if s) / samples
        return plan_blurred_fit(
            src_w=src_w, src_h=src_h, out_w=out_w, out_h=out_h,
            reason=(
                f"every tracked face is an overlay inset (under "
                f"{min_subject_face_ratio:.0%} of frame width, seen in "
                f"{detected:.0%} of frames), so the whole frame is the content"
            ),
        )

    # Count only frames where a subject-sized face is present. Judging coverage
    # against frames containing *any* face lets overlays dilute a speaker who is
    # in fact on screen throughout.
    subject_faces = {id(o) for t in subjects for o in t.observations}
    with_subject = sum(
        1 for sample in faces_per_sample
        if any(id(face) in subject_faces for face in sample)
    )
    if with_subject / samples < min_face_ratio:
        return plan_blurred_fit(
            src_w=src_w, src_h=src_h, out_w=out_w, out_h=out_h,
            reason=(
                f"a subject-sized face appears in only {with_subject / samples:.0%} "
                f"of frames (need {min_face_ratio:.0%}); keeping the whole frame"
            ),
        )

    primary = subjects[0]
    others = [t for t in subjects[1:]
              if t.coverage(with_subject) >= SECONDARY_TRACK_COVERAGE]

    # Several subjects sitting close enough to share one crop: that fills the
    # frame *and* holds still, beating both letterboxing and picking one.
    if others:
        group = plan_group_crop(
            [primary, *others], src_w=src_w, src_h=src_h,
            out_w=out_w, out_h=out_h, face_ratio=with_subject / samples,
        )
        if group is not None:
            return group

        second = others[0]
        together = primary.co_presence(second)
        if together >= CO_PRESENCE_FOR_STACK and                 abs(primary.median_x - second.median_x) / src_w >= 0.15:
            left, right = sorted((primary, second), key=lambda t: t.median_x)
            return plan_two_speaker_stack(
                _track_median_face(left), _track_median_face(right),
                src_w=src_w, src_h=src_h, out_w=out_w, out_h=out_h,
                face_ratio=with_subject / samples,
            )

        if together < CO_PRESENCE_FOR_STACK:
            return plan_blurred_fit(
                src_w=src_w, src_h=src_h, out_w=out_w, out_h=out_h,
                reason=(
                    f"subjects appear in different shots rather than together "
                    f"(co-present in {together:.0%} of frames), so no single "
                    "framing serves them; keeping the whole frame"
                ),
            )

        return plan_blurred_fit(
            src_w=src_w, src_h=src_h, out_w=out_w, out_h=out_h,
            reason=(
                f"{len(others) + 1} subjects that neither one crop nor a stack "
                "can hold; keeping the whole frame"
            ),
        )

    # One subject, but only if it is *consistently* the same one.
    coverage = primary.coverage(with_subject)
    if coverage < PRIMARY_TRACK_COVERAGE:
        return plan_blurred_fit(
            src_w=src_w, src_h=src_h, out_w=out_w, out_h=out_h,
            reason=(
                f"no consistent subject to follow (the most-present one appears "
                f"in {coverage:.0%} of frames with a subject); keeping the whole frame"
            ),
        )

    # A seated speaker's whole range of motion usually fits inside the crop, and
    # a shot that never moves beats one that keeps correcting. Panning is the
    # fallback for a subject who genuinely travels, not the default.
    static = plan_group_crop(
        [primary], src_w=src_w, src_h=src_h, out_w=out_w, out_h=out_h,
        face_ratio=with_subject / samples,
    )
    if static is not None:
        return static

    return plan_follow_crop(
        primary.observations,
        src_w=src_w, src_h=src_h, out_w=out_w, out_h=out_h,
        duration=duration, pan_smoothing=pan_smoothing,
        max_pan_speed=max_pan_speed, scene_cuts=scene_cuts,
        deadzone_ratio=deadzone_ratio,
    )


def _track_median_face(track: FaceTrack) -> FaceObservation:
    """A representative observation for a track, robust to stray detections."""
    def median(values: list[float]) -> float:
        ordered = sorted(values)
        return ordered[len(ordered) // 2]

    obs = track.observations
    return FaceObservation(
        t=median([o.t for o in obs]),
        x=median([o.x for o in obs]),
        y=median([o.y for o in obs]),
        width=median([o.width for o in obs]),
        height=median([o.height for o in obs]),
        confidence=median([o.confidence for o in obs]),
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



# --------------------------------------------------------------------------
# face tracking
# --------------------------------------------------------------------------

# A face this far (as a fraction of frame width) from a track's last known
# position is a different person, not that person having moved.
TRACK_MATCH_DISTANCE = 0.12

# The primary track must appear in at least this share of face-bearing samples
# for a single-subject follow-crop to be the right answer. Below it, another
# person is on screen often enough that cropping to one of them is a gamble.
PRIMARY_TRACK_COVERAGE = 0.85

# A second track present in at least this share is "also a subject".
SECONDARY_TRACK_COVERAGE = 0.25

# Two subjects must share this share of frames before they are stacked as a
# two-shot. Tracks that never overlap in time are one person filmed from two
# camera setups, and stacking those shows the same face twice.
CO_PRESENCE_FOR_STACK = 0.4


@dataclass
class FaceTrack:
    """One person followed across sampled frames."""

    observations: list[FaceObservation] = field(default_factory=list)

    @property
    def last(self) -> FaceObservation:
        return self.observations[-1]

    @property
    def median_x(self) -> float:
        return sorted(o.x for o in self.observations)[len(self.observations) // 2]

    @property
    def median_width(self) -> float:
        return sorted(o.width for o in self.observations)[len(self.observations) // 2]

    def extent(self, percentile: float = 0.2) -> tuple[float, float]:
        """Horizontal span the subject typically occupies over time.

        Trimmed at both ends, because the crop does not need to contain every
        position the subject ever reached -- the deadzone absorbs the tail, and
        demanding the full range makes an ordinary seated speaker look
        un-framable. Measured on a real talking-head clip: the untrimmed span
        was 784 px against a 511 px usable crop, while the 20%-trimmed span was
        478 px and fits comfortably.
        """
        lefts = sorted(o.x - o.width / 2 for o in self.observations)
        rights = sorted(o.x + o.width / 2 for o in self.observations)
        lo = int(len(lefts) * percentile)
        hi = max(0, len(rights) - 1 - int(len(rights) * percentile))
        return lefts[lo], rights[hi]

    def coverage(self, samples: int) -> float:
        return len(self.observations) / samples if samples else 0.0

    @property
    def times(self) -> set[float]:
        return {o.t for o in self.observations}

    def co_presence(self, other: FaceTrack) -> float:
        """Share of this track's frames in which `other` also appears.

        Two tracks that never appear together are not two people -- they are one
        person filmed from two camera setups, which is what cuts produce. A
        stacked two-shot of those would show the same person twice.
        """
        mine = self.times
        if not mine:
            return 0.0
        return len(mine & other.times) / len(mine)


def build_tracks(
    faces_per_sample: list[list[FaceObservation]], src_w: int
) -> list[FaceTrack]:
    """Group per-frame detections into per-person tracks, longest first.

    Without this the "dominant face" is recomputed independently every frame,
    so two people in shot make the target teleport between them. Measured on
    real footage: the target flipped sides 10 times across a 36-second clip,
    swinging over 68% of the frame width. The speed limit then prevented the
    camera from ever arriving at either, so it sat between them showing
    *neither* face.

    Association is nearest-neighbour with a distance gate -- enough for a
    handful of faces in a fixed studio shot, which is what this sees.
    """
    gate = TRACK_MATCH_DISTANCE * src_w
    tracks: list[FaceTrack] = []

    for sample in faces_per_sample:
        claimed: set[int] = set()
        for face in sorted(sample, key=lambda f: -f.area):
            best, best_distance = None, gate
            for index, track in enumerate(tracks):
                if index in claimed:
                    continue
                distance = abs(track.last.x - face.x)
                if distance < best_distance:
                    best, best_distance = index, distance
            if best is None:
                tracks.append(FaceTrack([face]))
                claimed.add(len(tracks) - 1)
            else:
                tracks[best].observations.append(face)
                claimed.add(best)

    tracks.sort(key=lambda t: -len(t.observations))
    return tracks


# Breathing room left around a group of subjects, as a fraction of the crop
# width, so faces are not jammed against the edge of the frame.
GROUP_MARGIN_RATIO = 0.08


def plan_group_crop(
    tracks: list[FaceTrack],
    *,
    src_w: int,
    src_h: int,
    out_w: int,
    out_h: int,
    face_ratio: float = 1.0,
) -> LayoutPlan | None:
    """A single static crop holding every subject, or None if they do not fit.

    This is the answer for two or more people who happen to sit close together
    -- a sofa interview, a desk two-shot. It fills the output frame, unlike
    letterboxing the whole picture, and it holds perfectly still, unlike
    following one of them.

    It exists because the interesting failure was not that subjects did not fit:
    on real footage two people spanned 462 px inside a 608 px crop, comfortably.
    The camera swung between them only because the target was recomputed per
    frame. Framing the group settles that by construction -- there is one
    target and it does not move.
    """
    if not tracks:
        return None

    crop_w, crop_h = crop_size_for_aspect(src_w, src_h, out_w, out_h)

    extents = [t.extent() for t in tracks]
    left = min(e[0] for e in extents)
    right = max(e[1] for e in extents)
    margin = crop_w * GROUP_MARGIN_RATIO
    span = (right - left) + 2 * margin
    if span > crop_w:
        return None

    centre_x = (left + right) / 2
    centre_y = sum(t.observations[len(t.observations) // 2].y for t in tracks) / len(tracks)
    x, y = crop_origin_for_face(centre_x, centre_y, crop_w, crop_h, src_w, src_h)

    people = ("one subject" if len(tracks) == 1
              else f"{len(tracks)} subjects")
    return LayoutPlan(
        kind="follow_crop",
        crop_width=crop_w,
        crop_height=crop_h,
        keyframes=[CropKeyframe(t=0.0, x=x, y=y)],
        face_ratio=face_ratio,
        reason=(
            f"{people} spanning {right - left:.0f}px fit inside a {crop_w}px crop; "
            "holding a single static frame on the group"
        ),
    )
