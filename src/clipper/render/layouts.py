"""Turning a 16:9 source into a 9:16 frame.

Every framing is **static**: one position per shot, changing only where the
source itself cuts (see `shots.py`). There is no panning camera. It was removed
after it produced both of the worst failures seen on real footage -- shake from
chasing small head movements, and a face half out of frame because the camera,
speed-limited to avoid that shake, trailed a subject who leaned across the shot
(measured: the face fully inside the crop in 12 of 27 samples).

The layouts:

* **follow_crop** -- a 9:16 slice holding the subject(s). Fills the frame.
* **fit_crop** -- a slice wider than 9:16, over a blurred copy of itself, used
  when the subject's range or an on-screen graphic will not fit in 9:16. It
  keeps everything in frame at the cost of blurred bars above and below.
* **two_speaker_stack** -- two people far apart, one above the other.
* **content_stack** -- screen-share content above the speaker's webcam.
* **blurred_fit** -- the whole frame over a blurred copy of itself.

This module is pure geometry and contains no FFmpeg calls, so every rule here
(never crop outside the source, always hold the whole face) is unit-testable
without rendering anything.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ..models import CropKeyframe, CropRect, LayoutPlan
from .speakers import clear_talker

if TYPE_CHECKING:
    from .overlays import OverlayBox

# A face looks wrong dead-centre; the eyeline wants to sit above the middle.
# 0.40 puts the face centre at 40% down the frame, leaving headroom above.
FACE_VERTICAL_ANCHOR = 0.40

# YuNet's box covers the face but not the hair or ears. The frame must hold the
# head, so each side of the box is padded by this fraction of the face width.
HEAD_PADDING_RATIO = 0.12

# Space left between the subject and the frame edge, as a fraction of the crop.
FRAME_MARGIN_RATIO = 0.03

# The subject's range over a shot is trimmed by this much at each end before
# the frame is sized to it. Enough to ignore a stray detection or two, not
# enough to let the face leave the frame: an earlier 20% trim did exactly that,
# measured at the face fully in frame in only 81-91% of samples on static crops.
EXTENT_TRIM = 0.05

# A frame needing at least this share of the source width shows all of it.
WHOLE_FRAME_RATIO = 0.95

# One face at least this share of a 9:16 slice's width is a close-up, cropped to
# fill the screen rather than widened to keep every hair (`plan_group_crop`).
CLOSE_UP_FACE_SHARE = 0.5
# ...provided its centre moves less than this share of the slice width: a held
# close-up drifts 0.01-0.04 of the frame per sample; a podcast host swinging
# across 340px (test_layouts' 003 regression) is held by a wider frame instead.
CLOSE_UP_MAX_TRAVEL = 0.35

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
    # Normalised mouth and eye patches, for telling who is talking (see
    # `speakers.py`). Opaque data, left out of comparison and repr.
    mouth: Any = field(default=None, compare=False, repr=False)
    eyes: Any = field(default=None, compare=False, repr=False)

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
    min_face_ratio: float,
    min_subject_face_ratio: float = INSET_FACE_WIDTH_RATIO,
    overlays: list[OverlayBox] | tuple[OverlayBox, ...] = (),
    keep_everyone: bool = False,
    opening: bool = False,
) -> LayoutPlan:
    """Pick a static layout for one shot from its sampled face detections.

    `keep_everyone` is for scripted TV: every person on screen stays in frame,
    and there is no stacking and no picking the one who is talking (see
    `_frame_everyone`). With `opening`, the shot is a clip's first seconds and
    must fill the screen instead (see `_frame_opening`).

    `overlays` are graphics the source laid over the picture during this shot
    (see `overlays.py`). Any framing that crops must keep them whole.

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
    if opening:
        return _frame_opening(tracks, samples, src_w=src_w, src_h=src_h,
                              out_w=out_w, out_h=out_h)
    if keep_everyone:
        return _frame_everyone(tracks, samples, src_w=src_w, src_h=src_h, out_w=out_w,
                               out_h=out_h, min_face_ratio=min_face_ratio,
                               overlays=overlays)
    subjects = [t for t in tracks
                if t.median_width / src_w >= min_subject_face_ratio]
    subjects += _companions(tracks, subjects, src_w)
    subjects.sort(key=lambda t: -len(t.observations))

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

    face_ratio = with_subject / samples

    # Several subjects sitting close enough to share one crop: that fills the
    # frame *and* holds still, beating both letterboxing and picking one.
    if others:
        group = plan_group_crop(
            [primary, *others], src_w=src_w, src_h=src_h,
            out_w=out_w, out_h=out_h, face_ratio=face_ratio, overlays=overlays,
        )
        if group is not None:
            return group

        # Everyone does not fit one frame. If one of them is clearly the one
        # talking, frame them: that is who the viewer is listening to.
        talker = clear_talker([primary, *others])
        if talker is not None:
            speaker, score, runner_up = talker
            framed = plan_group_crop(
                [speaker], src_w=src_w, src_h=src_h, out_w=out_w, out_h=out_h,
                face_ratio=face_ratio, overlays=overlays, allow_wider=True,
            )
            if framed is not None:
                return framed.model_copy(update={"reason": (
                    f"framing the person talking (mouth movement {score:.2f} vs "
                    f"{runner_up:.2f}); {framed.reason}")})

        # A stack shows two faces and nothing else, so it would drop any
        # graphic on screen. With one present, frame everything instead.
        if overlays:
            wide = plan_group_crop(
                [primary, *others], src_w=src_w, src_h=src_h, out_w=out_w,
                out_h=out_h, face_ratio=face_ratio, overlays=overlays,
                allow_wider=True,
            )
            if wide is not None:
                return wide

        second = others[0]
        together = primary.co_presence(second)
        if together >= CO_PRESENCE_FOR_STACK and                 abs(primary.median_x - second.median_x) / src_w >= 0.15:
            left, right = sorted((primary, second), key=lambda t: t.median_x)
            return plan_two_speaker_stack(
                _track_median_face(left), _track_median_face(right),
                src_w=src_w, src_h=src_h, out_w=out_w, out_h=out_h,
                face_ratio=face_ratio,
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

    # One static frame sized to hold the subject's whole range of movement and
    # any graphic beside them. It widens rather than cutting either.
    framed = plan_group_crop(
        [primary], src_w=src_w, src_h=src_h, out_w=out_w, out_h=out_h,
        face_ratio=face_ratio, overlays=overlays, allow_wider=True,
    )
    if framed is None:  # pragma: no cover - allow_wider always frames a subject
        return plan_blurred_fit(src_w=src_w, src_h=src_h, out_w=out_w, out_h=out_h)
    return framed


# Scripted TV: a face at least this wide, seen in at least this share of a
# shot's samples, is a person on screen who must stay in frame. Smaller or
# rarer faces are background extras or stray detections.
SCRIPTED_PERSON_WIDTH = 0.05
SCRIPTED_PERSON_COVERAGE = 0.15
# ...unless no one reaches that size: then faces down to this width that are on
# screen through most of the shot are its people. A wide shot of two leads on a
# bench (Chad Powers Ep 6, faces 4.7% of the width, in every sample) otherwise
# fell back to the whole frame, a 16:9 strip across the middle of the screen.
SCRIPTED_SMALL_PERSON_WIDTH = 0.03
SCRIPTED_SMALL_PERSON_COVERAGE = 0.5


def _frame_everyone(tracks: list[FaceTrack], samples: int, *, src_w: int, src_h: int,
                    out_w: int, out_h: int, min_face_ratio: float,
                    overlays) -> LayoutPlan:
    """Keep every person in shot, widening as far as it takes.

    The podcast rules failed on a real sitcom, measured on the 16 clips of the
    FX campaign:

    * A two-person stack uses full-height panes 1,214px wide, so unless the two
      sit at opposite edges both panes show the same footage -- 42-74% of each
      pane was shared in every stacked shot. It read as the picture duplicated.
    * Framing the person whose mouth moves most holds them while the other
      answers, and faces under 13% of frame width were treated as overlays --
      in TV wide shots they are the cast. Up to 20% of face-time was cropped out.

    TV is already composed for its frame, and the clips the user liked were
    mostly wide or whole frames. So: no stack, no choosing, everyone in.
    """
    people = [t for t in tracks
              if t.median_width / src_w >= SCRIPTED_PERSON_WIDTH
              and t.coverage(samples) >= SCRIPTED_PERSON_COVERAGE]
    if not people:
        people = [t for t in tracks
                  if t.median_width / src_w >= SCRIPTED_SMALL_PERSON_WIDTH
                  and t.coverage(samples) >= SCRIPTED_SMALL_PERSON_COVERAGE]
    if not people:
        # No face to keep, so nothing to lose by filling more of the screen. The
        # whole frame here was a 16:9 strip over 32% of the screen -- in the dark
        # Chad Powers romance scenes (a crowd under blue light, profiles in a
        # kiss, where the detector finds no face) a murky band. 4:5 fills 70%.
        return _centre_four_five(src_w=src_w, src_h=src_h,
                                 reason="no face found in this shot: centre, 4:5 at most")
    seen = {o.t for t in people for o in t.observations}
    face_ratio = len(seen) / samples
    if face_ratio < min_face_ratio:
        return plan_blurred_fit(
            src_w=src_w, src_h=src_h, out_w=out_w, out_h=out_h,
            reason=(f"people on screen in only {face_ratio:.0%} of frames; "
                    "keeping the whole frame"))
    framed = plan_group_crop(people, src_w=src_w, src_h=src_h, out_w=out_w, out_h=out_h,
                             face_ratio=face_ratio, overlays=overlays, allow_wider=True)
    if framed is None:  # pragma: no cover - allow_wider always frames people
        return plan_blurred_fit(src_w=src_w, src_h=src_h, out_w=out_w, out_h=out_h)
    return framed


#: The widest picture a clip's opening may show, as width/height: 4:5 fills 70%
#: of a 9:16 screen. A 16:9 picture over blurred bars fills 32%, with faces a
#: third of their full-screen size -- two of the first three FX posts opened
#: that way, and all three lost most viewers at 0:01.
OPENING_MAX_ASPECT = 4 / 5


def _frame_opening(tracks: list[FaceTrack], samples: int, *, src_w: int, src_h: int,
                   out_w: int, out_h: int) -> LayoutPlan:
    """Fill the screen for a clip's first seconds, on the person talking.

    The rest of a scripted clip keeps everyone in frame (`_frame_everyone`),
    zooming out when it must. The opening may not: the first frame decides
    whether a viewer stays. So it frames the speaker full-screen, widening to at
    most 4:5 to take in whoever is beside them, and accepts that someone further
    away is out of shot for these seconds.
    """
    people = [t for t in tracks
              if t.median_width / src_w >= SCRIPTED_PERSON_WIDTH
              and t.coverage(samples) >= SCRIPTED_PERSON_COVERAGE]
    limit = _make_even(int(src_h * OPENING_MAX_ASPECT))
    if not people:
        return _centre_four_five(src_w=src_w, src_h=src_h,
                                 reason="opening with no one in shot: centre, 4:5 at most")
    talker = clear_talker(people) if len(people) > 1 else None
    primary = talker[0] if talker else max(
        people, key=lambda t: (t.median_width, len(t.observations)))
    group = [primary]
    for other in sorted(people, key=lambda t: -t.median_width):
        if other is primary:
            continue
        spans = [subject_span(t) for t in (*group, other)]
        width = max(hi for _, hi in spans) - min(lo for lo, _ in spans)
        if width + 2 * src_h * (9 / 16) * FRAME_MARGIN_RATIO <= limit:
            group.append(other)
    seen = {o.t for t in group for o in t.observations}
    plan = plan_group_crop(group, src_w=src_w, src_h=src_h, out_w=out_w, out_h=out_h,
                           face_ratio=len(seen) / samples, allow_wider=True, max_width=limit)
    assert plan is not None  # a group always frames with allow_wider
    who = "the speaker" if talker else "the most prominent person"
    return plan.model_copy(update={"reason": f"opening: {who} full-screen; {plan.reason}"})


def _centre_four_five(*, src_w: int, src_h: int, reason: str) -> LayoutPlan:
    """The centre of the frame at `OPENING_MAX_ASPECT`, over a blurred copy."""
    limit = _make_even(int(src_h * OPENING_MAX_ASPECT))
    x, y = clamp_crop_origin((src_w - limit) / 2, 0, limit, src_h, src_w, src_h)
    return LayoutPlan(kind="fit_crop", crop_width=limit, crop_height=_make_even(src_h),
                      keyframes=[CropKeyframe(t=0.0, x=x, y=y)], face_ratio=0.0,
                      reason=reason)


def _companions(tracks: list[FaceTrack], subjects: list[FaceTrack],
                src_w: int) -> list[FaceTrack]:
    """Faces just under the subject size that are on screen *with* a subject.

    The size threshold separates people from reaction-cam overlays, measured at
    6.5-7.7% of frame width. But on a real b-roll interview the player answering
    measured 12.5% beside an interviewer at 13.6%, so the size line alone threw
    out the one person talking. Two similar-sized faces sharing the screen are a
    two-shot, not an inset and a subject.
    """
    if not subjects:
        return []
    return [
        t for t in tracks
        if t not in subjects
        and t.median_width / src_w >= COMPANION_MIN_WIDTH_RATIO
        and any(t.median_width >= COMPANION_SIZE_MATCH * s.median_width
                and t.co_presence(s) >= CO_PRESENCE_FOR_STACK for s in subjects)
    ]


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

# A face under the subject size still counts if it is at least this wide and
# at least COMPANION_SIZE_MATCH the width of a subject it shares the screen with.
# Overlay cams measured 6.5-7.7%; a real interviewee beside an interviewer 12.5%.
COMPANION_MIN_WIDTH_RATIO = 0.09
COMPANION_SIZE_MATCH = 0.75

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

    def extent(self, percentile: float = EXTENT_TRIM) -> tuple[float, float]:
        """Horizontal span of the face box over time, lightly trimmed.

        Trimmed only enough to ignore a stray detection. This used to trim 20%
        from each end so that more subjects would squeeze into a 9:16 crop, with
        a panning camera meant to absorb the rest; the effect on static crops was
        a face partly out of frame in 3-19% of samples. The frame now widens
        instead (`plan_group_crop`), so there is no reason to trim hard.
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


def subject_span(track: FaceTrack, trim: float = EXTENT_TRIM) -> tuple[float, float]:
    """Horizontal range the frame must hold for one subject: head, not just face."""
    left, right = track.extent(trim)
    pad = track.median_width * HEAD_PADDING_RATIO
    return left - pad, right + pad


def plan_group_crop(
    tracks: list[FaceTrack],
    *,
    src_w: int,
    src_h: int,
    out_w: int,
    out_h: int,
    face_ratio: float = 1.0,
    overlays: list[OverlayBox] | tuple[OverlayBox, ...] = (),
    allow_wider: bool = False,
    max_width: int | None = None,
) -> LayoutPlan | None:
    """One static frame holding every subject and every on-screen graphic.

    The frame is sized to the content rather than the other way round. If it all
    fits a 9:16 slice, that slice fills the output (`follow_crop`). If not, and
    `allow_wider` is set, the slice widens just enough (`fit_crop`) and sits over
    a blurred copy of itself -- up to the whole frame (`blurred_fit`). Without
    `allow_wider`, content that does not fit returns None so the caller can try
    another arrangement, such as stacking two people.

    Widening, not trimming, is the point. Every earlier version of this chose a
    9:16 slice and accepted whatever fell outside it; measured across every
    cropped shot of a real clip set, the face was fully in frame in only 84% of
    samples, and graphics beside the speaker were routinely cut in half.
    """
    if not tracks and not overlays:
        return None

    crop_w, crop_h = crop_size_for_aspect(src_w, src_h, out_w, out_h)
    spans = [subject_span(t) for t in tracks]
    spans += [(o.x, o.right) for o in overlays]
    left = max(0.0, min(lo for lo, _ in spans))
    right = min(float(src_w), max(hi for _, hi in spans))
    margin = crop_w * FRAME_MARGIN_RATIO
    needed = (right - left) + 2 * margin
    centre_x = (left + right) / 2

    subjects = ("one subject" if len(tracks) == 1
                else f"{len(tracks)} subjects" if tracks else "no subject")
    graphics = f" and {len(overlays)} on-screen graphic(s)" if overlays else ""

    if needed <= crop_w:
        x, y = clamp_crop_origin(centre_x - crop_w / 2, 0, crop_w, crop_h, src_w, src_h)
        return LayoutPlan(
            kind="follow_crop",
            crop_width=crop_w,
            crop_height=crop_h,
            keyframes=[CropKeyframe(t=0.0, x=x, y=y)],
            face_ratio=face_ratio,
            reason=(f"{subjects}{graphics} spanning {right - left:.0f}px fit a "
                    f"{crop_w}px crop; holding one static frame"),
        )

    if not allow_wider:
        return None

    # A close-up: one face so large that the head, its movement and margins
    # overflow the slice. The face *is* the picture, so fill the screen with it
    # and let hair and shoulders go. Widening instead letterboxed every dark
    # close-up of the Chad Powers romance scenes (faces 30% of the frame width).
    # Only if the face stays put: a speaker swinging ~340px across the shot
    # would leave a static slice (see CLOSE_UP_MAX_TRAVEL).
    travel = 0.0
    if len(tracks) == 1:
        lo, hi = tracks[0].extent()
        travel = (hi - lo) - tracks[0].median_width
    if (len(tracks) == 1 and not overlays
            and tracks[0].median_width >= crop_w * CLOSE_UP_FACE_SHARE
            and travel <= crop_w * CLOSE_UP_MAX_TRAVEL):
        x, y = clamp_crop_origin(tracks[0].median_x - crop_w / 2, 0, crop_w, crop_h,
                                 src_w, src_h)
        return LayoutPlan(
            kind="follow_crop",
            crop_width=crop_w,
            crop_height=crop_h,
            keyframes=[CropKeyframe(t=0.0, x=x, y=y)],
            face_ratio=face_ratio,
            reason=(f"close-up: a {tracks[0].median_width:.0f}px face fills the "
                    f"{crop_w}px crop; centred on it"),
        )

    width = min(_make_even(int(needed) + 1), _make_even(src_w))
    if max_width is not None:
        width = min(width, max_width)
    # Within a few percent of the full width, a crop only shaves slivers off the
    # edges; show the whole frame instead.
    if width >= src_w * WHOLE_FRAME_RATIO:
        plan = plan_blurred_fit(
            src_w=src_w, src_h=src_h, out_w=out_w, out_h=out_h,
            reason=(f"{subjects}{graphics} span {right - left:.0f}px, the whole "
                    "frame; keeping all of it"),
        )
        return plan.model_copy(update={"face_ratio": face_ratio})

    height = _make_even(src_h)
    x, y = clamp_crop_origin(centre_x - width / 2, 0, width, height, src_w, src_h)
    return LayoutPlan(
        kind="fit_crop",
        crop_width=width,
        crop_height=height,
        keyframes=[CropKeyframe(t=0.0, x=x, y=y)],
        face_ratio=face_ratio,
        reason=(f"{subjects}{graphics} span {right - left:.0f}px, wider than a "
                f"{crop_w}px crop; framing {width}px so nothing is cut"),
    )
