"""Face detection and scene-cut detection for reframing.

Uses OpenCV's YuNet (`cv2.FaceDetectorYN`) rather than MediaPipe: it ships in
OpenCV core, so it adds no dependency, and it returns five landmarks which is
enough for the headroom framing in `layouts.py` (docs/DECISIONS.md D3).

Frames are sampled at `render.face_sample_fps` (5 fps by default), not at the
full frame rate. This is the *only* place the pipeline decodes frames in Python;
the render itself is a single FFmpeg process driven by the trajectory this
module produces (docs/DECISIONS.md D11).

Scene cuts come from a histogram difference rather than PySceneDetect, which
would drag in a conflicting OpenCV build (docs/DECISIONS.md D4). They are
detected on the same sampled frames, so they cost nothing extra.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

from ..assets import face_model_path
from ..utils.logging import get_logger
from .layouts import FaceObservation
from .regions import ActivityAccumulator, ContentMap

log = get_logger(__name__)

# YuNet's confidence floor. Below this, detections on textured backgrounds
# (bookshelves, patterned walls) start appearing.
MIN_CONFIDENCE = 0.75

# A detection smaller than this fraction of frame height is background --
# someone walking past, a face on a monitor -- not the speaker.
MIN_FACE_HEIGHT_RATIO = 0.06

# Frame-to-frame histogram correlation below this is treated as a scene cut.
SCENE_CUT_CORRELATION = 0.70

# YuNet is trained at 320x320. Running it at the source resolution is far
# slower for no accuracy gain on faces of any reasonable size, so frames are
# downscaled to this width before detection and the boxes scaled back up.
DETECT_SIZE = (320, 320)
DETECT_WIDTH = 640


@dataclass
class FaceScan:
    """Everything one pass over a clip's frames produced."""

    #: One list of faces per sampled instant, in time order.
    per_sample: list[list[FaceObservation]]
    #: Times (relative to the clip) where the shot changed.
    scene_cuts: list[float]
    sample_times: list[float]
    frame_width: int
    frame_height: int
    #: Where content sits in the frame, used to spot a screen-share layout.
    activity: ContentMap | None = None

    @property
    def face_ratio(self) -> float:
        """Fraction of sampled frames containing at least one usable face."""
        if not self.per_sample:
            return 0.0
        return sum(1 for s in self.per_sample if s) / len(self.per_sample)


class FaceDetectionUnavailable(RuntimeError):
    """The detector could not be built. Callers fall back to a blurred fit."""


def _load_detector(width: int, height: int):
    """Build a YuNet detector, or explain why it is unavailable."""
    try:
        import cv2
    except ImportError as exc:  # pragma: no cover - declared dependency
        raise FaceDetectionUnavailable(f"OpenCV is not importable: {exc}") from exc

    if not hasattr(cv2, "FaceDetectorYN"):  # pragma: no cover - very old OpenCV
        raise FaceDetectionUnavailable(
            f"OpenCV {cv2.__version__} has no FaceDetectorYN; "
            'run: uv pip install -U "opencv-python-headless>=4.11"'
        )

    model = face_model_path()
    if not model.is_file():
        raise FaceDetectionUnavailable(
            f"the YuNet model is missing ({model}).\n"
            "  Fetch it with:  clipper doctor --fix\n"
            "  Without it every clip falls back to the blurred-background layout."
        )

    try:
        return cv2.FaceDetectorYN.create(
            str(model), "", DETECT_SIZE,
            score_threshold=MIN_CONFIDENCE, nms_threshold=0.3, top_k=10,
        )
    except cv2.error as exc:
        raise FaceDetectionUnavailable(f"could not create the YuNet detector: {exc}") from exc


def scan(
    video: Path | str,
    *,
    start: float,
    duration: float,
    sample_fps: float = 5.0,
    min_confidence: float = MIN_CONFIDENCE,
) -> FaceScan:
    """Sample a span of video and detect faces and scene cuts in it.

    Times in the result are **relative to `start`**, because that is what the
    render's filter graph sees once the input is seeked.
    """
    import cv2

    video = Path(video)
    capture = cv2.VideoCapture(str(video))
    if not capture.isOpened():
        raise FaceDetectionUnavailable(f"OpenCV could not open {video.name}")

    try:
        frame_width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH)) or 0
        frame_height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT)) or 0
        detector = _load_detector(frame_width, frame_height)

        # Detect on a downscaled copy; coordinates are scaled back to source
        # pixels so everything downstream stays in one coordinate space.
        scale = min(1.0, DETECT_WIDTH / frame_width) if frame_width else 1.0
        detect_w = max(64, round(frame_width * scale))
        detect_h = max(64, round(frame_height * scale))
        detector.setInputSize((detect_w, detect_h))

        source_fps = float(capture.get(cv2.CAP_PROP_FPS)) or 30.0
        # Decode sequentially and process every Nth frame. Seeking per sample
        # instead costs a keyframe seek plus decode each time -- measured at
        # 15.4 s for 20 s of video, slower than realtime. Linear decode of the
        # same span is a fraction of that, because skipped frames are still
        # decoded but never converted, detected on, or histogrammed.
        stride = max(1, round(source_fps / max(0.1, sample_fps)))

        # One seek to the start of the span is unavoidable and cheap.
        capture.set(cv2.CAP_PROP_POS_MSEC, start * 1000.0)

        per_sample: list[list[FaceObservation]] = []
        sample_times: list[float] = []
        scene_cuts: list[float] = []
        previous_hist: np.ndarray | None = None
        activity = ActivityAccumulator(frame_width, frame_height)

        index = 0
        total_frames = int(duration * source_fps)
        while index <= total_frames:
            ok, frame = capture.read()
            if not ok or frame is None:
                break

            if index % stride == 0:
                offset = index / source_fps
                small = (cv2.resize(frame, (detect_w, detect_h),
                                    interpolation=cv2.INTER_AREA)
                         if scale < 1.0 else frame)
                faces = _detect(detector, small, min_confidence=min_confidence,
                                frame_height=detect_h, t=offset,
                                upscale=1.0 / scale if scale else 1.0)
                per_sample.append(faces)
                sample_times.append(offset)

                hist = _histogram(small)
                if previous_hist is not None and _is_cut(previous_hist, hist):
                    scene_cuts.append(offset)
                previous_hist = hist

                # Built from the frames already decoded, so this costs one pass
                # over small arrays rather than another decode.
                activity.add(small)

            index += 1
    finally:
        capture.release()

    log.debug(
        "face scan: %d samples, %.0f%% with a face, %d scene cut(s)",
        len(per_sample),
        100 * (sum(1 for s in per_sample if s) / len(per_sample)) if per_sample else 0,
        len(scene_cuts),
    )
    return FaceScan(
        per_sample=per_sample,
        scene_cuts=scene_cuts,
        sample_times=sample_times,
        frame_width=frame_width,
        frame_height=frame_height,
        activity=activity.build(),
    )


def _detect(detector, frame, *, min_confidence: float, frame_height: int,
            t: float, upscale: float = 1.0) -> list[FaceObservation]:
    """Run YuNet on one frame and convert its output to source-pixel observations."""
    import cv2

    try:
        _, raw = detector.detect(frame)
    except cv2.error:  # pragma: no cover - malformed frame
        return []
    if raw is None or len(raw) == 0:
        return []

    minimum_height = frame_height * MIN_FACE_HEIGHT_RATIO
    faces: list[FaceObservation] = []
    for row in raw:
        # YuNet rows are [x, y, w, h, 5 landmark x/y pairs..., score].
        x, y, w, h = (float(v) for v in row[:4])
        score = float(row[-1])
        if score < min_confidence or h < minimum_height:
            continue
        faces.append(FaceObservation(
            t=t,
            x=(x + w / 2) * upscale,
            y=(y + h / 2) * upscale,
            width=w * upscale,
            height=h * upscale,
            confidence=score,
        ))
    return faces


def _histogram(frame) -> np.ndarray:
    """Normalised hue/saturation histogram, for scene-cut comparison."""
    import cv2

    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    hist = cv2.calcHist([hsv], [0, 1], None, [50, 60], [0, 180, 0, 256])
    cv2.normalize(hist, hist, 0, 1, cv2.NORM_MINMAX)
    return hist


def _is_cut(previous: np.ndarray, current: np.ndarray) -> bool:
    import cv2

    correlation = cv2.compareHist(previous, current, cv2.HISTCMP_CORREL)
    return correlation < SCENE_CUT_CORRELATION


def plan_layout_for(
    video: Path | str,
    *,
    start: float,
    duration: float,
    out_width: int,
    out_height: int,
    sample_fps: float,
    pan_smoothing: float,
    max_pan_speed: float,
    min_face_ratio: float,
    deadzone_ratio: float = 0.18,
    content_pane_share: float = 0.58,
    detect_screen_share: bool = True,
):
    """Scan a clip and return the `LayoutPlan` for it.

    Any detection failure degrades to the blurred-background layout rather than
    failing the render -- a clip with a slightly wrong frame is worth more than
    no clip at all.
    """
    from ..ingest.probe import probe
    from .layouts import choose_layout, plan_blurred_fit

    media = probe(video)
    try:
        result = scan(video, start=start, duration=duration, sample_fps=sample_fps)
    except FaceDetectionUnavailable as exc:
        log.warning("face detection unavailable (%s); using the blurred fit", exc)
        return plan_blurred_fit(
            src_w=media.width, src_h=media.height,
            out_w=out_width, out_h=out_height,
            reason=f"face detection unavailable: {exc}",
        )

    if not result.per_sample:
        return plan_blurred_fit(
            src_w=media.width, src_h=media.height,
            out_w=out_width, out_h=out_height,
            reason="no frames could be sampled from this span",
        )

    # Before cropping to the face, check whether the face is merely an inset
    # webcam over screen-share content. Cropping to it would discard the actual
    # subject of the video.
    if detect_screen_share:
        stacked = _try_content_stack(
            result, out_width=out_width, out_height=out_height,
            content_pane_share=content_pane_share,
        )
        if stacked is not None:
            return stacked

    plan = choose_layout(
        result.per_sample,
        src_w=result.frame_width or media.width,
        src_h=result.frame_height or media.height,
        out_w=out_width,
        out_h=out_height,
        duration=duration,
        pan_smoothing=pan_smoothing,
        max_pan_speed=max_pan_speed,
        min_face_ratio=min_face_ratio,
        scene_cuts=result.scene_cuts,
        deadzone_ratio=deadzone_ratio,
    )
    # `choose_layout` estimates the ratio from observation spacing; the scan
    # knows it exactly, so prefer the measured value for the QA check.
    return plan.model_copy(update={"face_ratio": result.face_ratio})


def _try_content_stack(result: FaceScan, *, out_width: int, out_height: int,
                       content_pane_share: float):
    """Plan a content stack if this looks like a screen-share source, else None."""
    from .layouts import plan_content_stack
    from .regions import detect_layout_regions

    observed = [s[0] for s in result.per_sample if s]
    if len(observed) < 3:
        return None

    def median(values: list[float]) -> float:
        ordered = sorted(values)
        return ordered[len(ordered) // 2]

    face_x = median([o.x for o in observed])
    face_y = median([o.y for o in observed])
    face_w = median([o.width for o in observed])
    face_h = median([o.height for o in observed])

    found = detect_layout_regions(result.activity, face_x, face_y, face_w, face_h)
    if found is None:
        return None

    webcam, content = found
    return plan_content_stack(
        webcam, content,
        src_w=result.frame_width, src_h=result.frame_height,
        out_w=out_width, out_h=out_height,
        content_share=content_pane_share,
        face_ratio=result.face_ratio,
    )
