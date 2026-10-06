"""The automated QA gate (docs/BUILD_BRIEF.md section 12).

Nobody watches these clips before they ship, so this is the only thing standing
between a broken render and the user posting it. Every check returns pass, warn
or fail with a machine-readable reason, and a failing clip is moved to
`rejected/` and replaced by the next-best candidate.

The checks measure the **rendered file**, not the plan that produced it. A crop
that ran off the edge, an encoder that silently dropped audio, or captions that
drifted are all things the plan looks fine about.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from ..config import QAConfig, RenderConfig
from ..ingest.probe import DEVNULL, measure_loudness, probe
from ..models import ClipPlan, QACheck, QAReport, Word
from ..render.ffmpeg import FFmpegError, run
from ..utils.logging import get_logger

log = get_logger(__name__)

# ffmpeg's blackdetect/freezedetect/silencedetect report through stderr lines.
_BLACK_RE = re.compile(r"black_start:(?P<start>[\d.]+)\s+black_end:(?P<end>[\d.]+)")
_FREEZE_START_RE = re.compile(r"freeze_start:\s*(?P<t>[\d.]+)")
_FREEZE_END_RE = re.compile(r"freeze_end:\s*(?P<t>[\d.]+)")
_SILENCE_START_RE = re.compile(r"silence_start:\s*(?P<t>-?[\d.]+)")
_SILENCE_END_RE = re.compile(r"silence_end:\s*(?P<t>[\d.]+)")

SILENCE_THRESHOLD_DB = -35.0
"""Below this is treated as silence. Speech noise floors sit well above it."""

DEFAULT_SILENCE_MIN_GAP = 0.5
"""Shorter gaps are ordinary speech rhythm, not dead air."""


@dataclass
class QAContext:
    """Everything the gate needs beyond the file itself."""

    plan: ClipPlan
    words: list[Word]
    ass_text: str = ""
    duration_bounds: tuple[float, float] = (0.0, 1e9)
    expected_width: int = 1080
    expected_height: int = 1920
    expected_fps: int = 30
    #: The pre-roll refinement deliberately added, in seconds. The lead-silence
    #: limit is measured on top of this -- penalising padding we asked for would
    #: reject every clip.
    pre_roll: float = 0.0
    #: The campaign kept the source audio as delivered, so there is no
    #: loudness target to hold it to.
    audio_untouched: bool = False
    #: False when the campaign forbids burned-in captions (campaign/edits.py).
    captions_expected: bool = True


def check_clip(path: Path, context: QAContext, qa: QAConfig,
               render: RenderConfig) -> QAReport:
    """Run every check against one rendered clip."""
    checks: list[QACheck] = []

    try:
        media = probe(path)
    except (FFmpegError, FileNotFoundError, OSError) as exc:
        return QAReport(
            clip_id=context.plan.clip_id, file=str(path),
            checks=[QACheck(name="probe", status="fail",
                            detail=f"the rendered file could not be probed: {exc}")],
        )

    checks.append(_check_duration(media.duration, context))
    checks.append(_check_resolution(media, context))
    checks.append(_check_fps(media, context))
    checks.append(_check_audio_present(media))

    detections = detect_all(path, duration=media.duration, has_audio=media.has_audio,
                            silence_min_gap=qa.silence_min_gap,
                            freeze_min_duration=qa.freeze_min_duration)

    if media.has_audio and context.audio_untouched:
        checks.append(QACheck(name="loudness", status="pass",
                              detail="original audio kept as the campaign requires; "
                                     "loudness not normalised"))
    elif media.has_audio:
        checks.append(_check_loudness(path, qa))
        checks.append(_check_silence_ratio(detections.silence, media.duration, qa))
        checks.append(_check_lead_silence(detections.silence, qa, context.pre_roll))
        checks.append(_check_trail_silence(detections.silence, media.duration, qa))

    checks.append(_check_black_frames(detections.black, qa))
    checks.append(_check_frozen_frames(detections.frozen_seconds, media.duration, qa))

    layout = context.plan.layout
    if layout is not None and layout.is_face_centric:
        checks.append(_check_face_ratio(layout.face_ratio, qa))

    if context.ass_text:
        if context.captions_expected:
            checks.extend(_check_captions(context, qa))
        else:
            checks.append(QACheck(name="caption_sync", status="pass",
                                  detail="no burned-in captions: the brief forbids them"))

    return QAReport(clip_id=context.plan.clip_id, file=str(path), checks=checks)


# --------------------------------------------------------------------------
# individual checks
# --------------------------------------------------------------------------


def _check_duration(duration: float, context: QAContext) -> QACheck:
    low, high = context.duration_bounds
    if duration < low:
        return QACheck(name="duration", status="fail", measured=duration, limit=low,
                       detail=f"{duration:.1f}s is under the campaign minimum of {low:.0f}s")
    if duration > high:
        return QACheck(name="duration", status="fail", measured=duration, limit=high,
                       detail=f"{duration:.1f}s is over the campaign maximum of {high:.0f}s")
    return QACheck(name="duration", status="pass", measured=duration,
                   detail=f"{duration:.1f}s, inside [{low:.0f}, {high:.0f}]")


def _check_resolution(media, context: QAContext) -> QACheck:
    expected = (context.expected_width, context.expected_height)
    actual = (media.width, media.height)
    if actual != expected:
        return QACheck(name="resolution", status="fail",
                       detail=f"{actual[0]}x{actual[1]}, expected {expected[0]}x{expected[1]}")
    return QACheck(name="resolution", status="pass", detail=f"{actual[0]}x{actual[1]}")


def _check_fps(media, context: QAContext) -> QACheck:
    if abs(media.fps - context.expected_fps) > 0.5:
        return QACheck(name="fps", status="warn", measured=media.fps,
                       limit=float(context.expected_fps),
                       detail=f"{media.fps:.2f} fps, expected {context.expected_fps}")
    return QACheck(name="fps", status="pass", measured=media.fps,
                   detail=f"{media.fps:.2f} fps")


def _check_audio_present(media) -> QACheck:
    if not media.has_audio:
        return QACheck(name="audio_present", status="fail",
                       detail="the rendered clip has no audio track")
    return QACheck(name="audio_present", status="pass",
                   detail=f"{media.audio_codec}, {media.audio_channels}ch "
                          f"@ {media.audio_sample_rate} Hz")


def _check_loudness(path: Path, qa: QAConfig) -> QACheck:
    try:
        loudness = measure_loudness(path)
    except FFmpegError as exc:
        return QACheck(name="loudness", status="warn",
                       detail=f"could not measure loudness: {exc}")

    integrated = loudness.get("i")
    true_peak = loudness.get("tp")
    if integrated is None:
        return QACheck(name="loudness", status="warn",
                       detail="loudnorm reported no integrated loudness")

    if not (qa.min_lufs <= integrated <= qa.max_lufs):
        return QACheck(
            name="loudness", status="fail", measured=integrated,
            detail=f"{integrated:.1f} LUFS is outside "
                   f"[{qa.min_lufs:.0f}, {qa.max_lufs:.0f}]",
        )
    if true_peak is not None and true_peak > qa.max_true_peak_dbtp:
        return QACheck(
            name="loudness", status="warn", measured=true_peak,
            limit=qa.max_true_peak_dbtp,
            detail=f"true peak {true_peak:.1f} dBTP is above "
                   f"{qa.max_true_peak_dbtp:.1f}; clipping is possible",
        )
    return QACheck(name="loudness", status="pass", measured=integrated,
                   detail=f"{integrated:.1f} LUFS, true peak {true_peak:.1f} dBTP"
                          if true_peak is not None else f"{integrated:.1f} LUFS")


def _check_silence_ratio(spans: list[tuple[float, float]], duration: float,
                         qa: QAConfig) -> QACheck:
    if duration <= 0:
        return QACheck(name="silence_ratio", status="warn", detail="zero-length clip")
    total = sum(end - start for start, end in spans)
    ratio = min(1.0, total / duration)
    if ratio > qa.max_silence_ratio:
        return QACheck(name="silence_ratio", status="fail", measured=ratio,
                       limit=qa.max_silence_ratio,
                       detail=f"{ratio:.0%} silent, over the {qa.max_silence_ratio:.0%} limit")
    return QACheck(name="silence_ratio", status="pass", measured=ratio,
                   detail=f"{ratio:.0%} silent")


def _check_lead_silence(spans: list[tuple[float, float]], qa: QAConfig,
                        pre_roll: float = 0.0) -> QACheck:
    """A clip that opens on dead air has already lost the scroll.

    This is a **warn**, not a fail. Whisper's word timestamps routinely lead the
    actual audio onset by a few hundred milliseconds -- measured at ~0.4 s on the
    speech fixtures -- so clips snapped to a word boundary often open with a
    little silence. That makes the clip slightly weaker, not broken, and failing
    it would replace it with a lower-scoring clip: a bad trade. The measurement
    is still reported so a systematic offset is visible.
    """
    lead = next((end for start, end in spans if start <= 0.05), 0.0)
    limit = qa.max_lead_silence + pre_roll
    if lead > limit:
        return QACheck(name="lead_silence", status="warn", measured=lead,
                       limit=limit,
                       detail=f"{lead:.2f}s of silence before speech starts, "
                              f"over the {limit:.2f}s limit "
                              f"({qa.max_lead_silence:.2f}s + {pre_roll:.2f}s pre-roll)")
    return QACheck(name="lead_silence", status="pass", measured=lead,
                   detail=f"speech starts {lead:.2f}s in")


def _check_trail_silence(spans: list[tuple[float, float]], duration: float,
                         qa: QAConfig) -> QACheck:
    trailing = next((duration - start for start, end in spans
                     if end >= duration - 0.05), 0.0)
    if trailing > qa.max_trail_silence:
        return QACheck(name="trail_silence", status="warn", measured=trailing,
                       limit=qa.max_trail_silence,
                       detail=f"{trailing:.2f}s of dead air at the end")
    return QACheck(name="trail_silence", status="pass", measured=trailing,
                   detail=f"{trailing:.2f}s of tail")


def _check_black_frames(spans: list[tuple[float, float]], qa: QAConfig) -> QACheck:
    total = sum(end - start for start, end in spans)
    if total > qa.max_black_seconds:
        return QACheck(name="black_frames", status="fail", measured=total,
                       limit=qa.max_black_seconds,
                       detail=f"{total:.2f}s of black frames, over the "
                              f"{qa.max_black_seconds:.2f}s limit")
    return QACheck(name="black_frames", status="pass", measured=total,
                   detail=f"{total:.2f}s of black")


def _check_frozen_frames(total: float, duration: float, qa: QAConfig) -> QACheck:
    """Distinguish a stalled render from a merely still passage."""
    ratio = total / duration if duration > 0 else 0.0
    if ratio > qa.max_freeze_ratio:
        return QACheck(name="frozen_frames", status="fail", measured=total,
                       limit=qa.max_freeze_ratio * duration,
                       detail=f"{total:.2f}s of {duration:.0f}s is frozen ({ratio:.0%}), "
                              f"over the {qa.max_freeze_ratio:.0%} limit -- "
                              "the render may have stalled")
    if total > qa.max_freeze_seconds:
        return QACheck(name="frozen_frames", status="warn", measured=total,
                       limit=qa.max_freeze_seconds,
                       detail=f"{total:.2f}s of still video ({ratio:.0%} of the clip)")
    return QACheck(name="frozen_frames", status="pass", measured=total,
                   detail=f"{total:.2f}s frozen ({ratio:.0%})")


def _check_face_ratio(ratio: float, qa: QAConfig) -> QACheck:
    """Only meaningful for face-centric layouts (section 12)."""
    if ratio < qa.min_face_ratio:
        return QACheck(name="face_ratio", status="fail", measured=ratio,
                       limit=qa.min_face_ratio,
                       detail=f"a face was visible in only {ratio:.0%} of sampled "
                              f"frames; a follow-crop needs {qa.min_face_ratio:.0%}")
    return QACheck(name="face_ratio", status="pass", measured=ratio,
                   detail=f"face present in {ratio:.0%} of sampled frames")


def _check_captions(context: QAContext, qa: QAConfig) -> list[QACheck]:
    """Every transcript word inside the clip must appear in the ASS, and no
    caption event may fall outside it.

    Word *text* is not compared, because profanity masking legitimately rewrites
    it (section 7.2 versus section 12 -- noted in docs/PLAN.md). Timing coverage is
    what actually catches drift.
    """
    from ..render.captions import parse_event_times

    events = parse_event_times(context.ass_text)
    checks: list[QACheck] = []

    if not events:
        return [QACheck(name="caption_sync", status="fail",
                        detail="the ASS file contains no caption events")]

    duration = context.plan.duration
    outside = [
        (start, end) for start, end, _ in events
        if start < -1e-6 or end > duration + qa.caption_sync_tolerance
    ]
    if outside:
        checks.append(QACheck(
            name="caption_bounds", status="fail", measured=float(len(outside)),
            detail=f"{len(outside)} caption event(s) fall outside the clip, "
                   f"first at {outside[0][0]:.2f}-{outside[0][1]:.2f}s",
        ))
    else:
        checks.append(QACheck(name="caption_bounds", status="pass",
                              detail=f"{len(events)} events, all inside the clip"))

    # Coverage: every spoken word should be inside some caption event's span.
    clip_words = [
        w for w in context.words
        if context.plan.start <= (w.start + w.end) / 2 < context.plan.end
    ]
    if clip_words:
        covered = 0
        for word in clip_words:
            # Word times are source-absolute; caption events are clip-relative.
            centre = (word.start + word.end) / 2 - context.plan.start
            if any(s - qa.caption_sync_tolerance <= centre <= e + qa.caption_sync_tolerance
                   for s, e, _ in events):
                covered += 1
        ratio = covered / len(clip_words)
        if ratio < 0.95:
            checks.append(QACheck(
                name="caption_coverage", status="fail", measured=ratio, limit=0.95,
                detail=f"only {ratio:.0%} of spoken words fall inside a caption event",
            ))
        else:
            checks.append(QACheck(name="caption_coverage", status="pass", measured=ratio,
                                  detail=f"{ratio:.0%} of spoken words covered"))
    return checks


# --------------------------------------------------------------------------
# ffmpeg detectors
# --------------------------------------------------------------------------


@dataclass
class Detections:
    """Black, freeze and silence spans, all from one decode of the file."""

    black: list[tuple[float, float]] = field(default_factory=list)
    frozen_seconds: float = 0.0
    silence: list[tuple[float, float]] = field(default_factory=list)


def detect_all(path: Path, *, duration: float, has_audio: bool,
               silence_min_gap: float = DEFAULT_SILENCE_MIN_GAP,
               freeze_min_duration: float = 1.0) -> Detections:
    """Run every detector in a single FFmpeg pass.

    Three separate passes cost three full decodes of the clip -- measured at
    1.10 s + 1.37 s + 0.92 s on a 20-second clip. They are independent filters
    over the same frames, so one invocation with both a video and an audio
    filter chain does the same work for one decode.
    """
    args = [
        "-hide_banner", "-nostdin", "-loglevel", "info",
        "-i", str(path),
        # pix_th 0.05, not ffmpeg's 0.10: at 0.10 a night scene with its picture
        # intact counts as black (Chad Powers Ep 6: an embrace in a dark crowd
        # and a kiss, 3.6s and 7.9s flagged); at 0.05 neither is, while a true
        # black frame or fade (luma 16) still is.
        "-vf", ("blackdetect=d=0.05:pic_th=0.98:pix_th=0.05,"
                f"freezedetect=n=-60dB:d={freeze_min_duration}"),
    ]
    if has_audio:
        args += ["-af", f"silencedetect=n={SILENCE_THRESHOLD_DB}dB:d={silence_min_gap}"]
    args += ["-f", "null", DEVNULL]

    try:
        stderr = run(args, check=False).stderr
    except FFmpegError as exc:  # pragma: no cover - defensive
        log.debug("QA detection pass failed: %s", exc)
        return Detections()

    return Detections(
        black=_parse_black(stderr),
        frozen_seconds=_parse_freeze(stderr),
        silence=_parse_silence(stderr, duration) if has_audio else [],
    )


def _parse_black(stderr: str) -> list[tuple[float, float]]:
    return [(float(m.group("start")), float(m.group("end")))
            for m in _BLACK_RE.finditer(stderr)]


def _parse_freeze(stderr: str) -> float:
    """Total frozen seconds. freezedetect reports starts and ends separately."""
    starts = [float(m.group("t")) for m in _FREEZE_START_RE.finditer(stderr)]
    ends = [float(m.group("t")) for m in _FREEZE_END_RE.finditer(stderr)]
    total = 0.0
    for i, start in enumerate(starts):
        end = ends[i] if i < len(ends) else None
        if end is not None and end > start:
            total += end - start
    return total


def _parse_silence(stderr: str, duration: float) -> list[tuple[float, float]]:
    """Silent spans, as (start, end). An unclosed span runs to the end."""
    starts = [max(0.0, float(m.group("t"))) for m in _SILENCE_START_RE.finditer(stderr)]
    ends = [float(m.group("t")) for m in _SILENCE_END_RE.finditer(stderr)]

    spans: list[tuple[float, float]] = []
    for i, start in enumerate(starts):
        spans.append((start, ends[i] if i < len(ends) else duration))
    return spans


def summarize(report: QAReport) -> str:
    """One line for logs and the run report."""
    failures = report.failures
    if failures:
        return f"FAIL ({len(failures)}): " + "; ".join(c.detail for c in failures[:3])
    warns = [c for c in report.checks if c.status == "warn"]
    if warns:
        return f"pass with {len(warns)} warning(s): " + "; ".join(c.detail for c in warns[:2])
    return f"pass ({len(report.checks)} checks)"
