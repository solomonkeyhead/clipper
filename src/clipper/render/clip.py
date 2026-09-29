"""Rendering one clip: assemble the spec, write side files, run FFmpeg.

The ASS subtitle file is written into a per-clip working directory and kept,
not deleted: when a clip comes out wrong, the exact subtitle that produced it is
one of the first things worth looking at.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

from ..config import Config
from ..models import ClipPlan, LayoutPlan, MediaInfo, Word
from ..utils.logging import get_logger
from . import captions as cap
from .ffmpeg import bundled_fonts_dir, run, select_video_encoder
from .graph import RenderSpec, build_command, output_size

log = get_logger(__name__)


@dataclass(frozen=True)
class RenderResult:
    """What a render produced, plus the artifacts that explain it."""

    output: Path
    ass_path: Path | None
    encoder: str
    encoder_reason: str
    elapsed: float
    command: list[str]


def render_clip(
    *,
    source: Path,
    media: MediaInfo,
    plan: ClipPlan,
    words: list[Word],
    config: Config,
    work_dir: Path,
    output: Path,
    draft: bool = False,
    campaign_credit: str = "",
    credit_position: str = "top_left",
    mask_profanity: bool = False,
    normalize_audio: bool = True,
    lift_gamma: float | None = None,
    show_captions: bool = True,
    faces: list | None = None,
) -> RenderResult:
    """Render one `ClipPlan` to an MP4.

    `words` are source-absolute; `plan.start` rebases them. The layout must
    already be decided -- this function renders a plan, it does not make one.
    """
    if plan.layout is None:
        raise ValueError(f"{plan.clip_id} has no layout; run reframing before rendering")

    rc = config.render
    width, height = output_size(rc, draft=draft)
    work_dir.mkdir(parents=True, exist_ok=True)
    output.parent.mkdir(parents=True, exist_ok=True)

    style = cap.get_style(plan.caption_style or rc.caption_style)
    ass_path = work_dir / f"{plan.clip_id}.ass"
    cap.write_ass(
        cap.build_ass(
            words if show_captions else [],
            style=style,
            width=width,
            height=height,
            safe_area=rc.safe_area,
            clip_start=plan.start,
            duration=plan.duration,
            mask_profanity_words=mask_profanity,
            hook_text=plan.hook_text if rc.show_hook_text else "",
            hook_seconds=rc.hook_text_seconds if rc.show_hook_text else 0.0,
            credit_text=campaign_credit,
            credit_position=credit_position,
            faces=faces,
        ),
        ass_path,
    )

    layout = _scale_layout(plan.layout, media, width, height)

    encoder, reason = select_video_encoder(rc.encoder)

    spec = RenderSpec(
        source=source,
        output=output,
        start=plan.start,
        duration=plan.duration,
        layout=layout,
        ass_path=ass_path,
        fonts_dir=bundled_fonts_dir(),
        width=width,
        height=height,
        fps=rc.fps,
        encoder=encoder,
        loudness_lufs=rc.loudness_lufs,
        true_peak_dbtp=rc.true_peak_dbtp,
        crf=rc.crf + (6 if draft else 0),
        nvenc_cq=rc.nvenc_cq + (6 if draft else 0),
        x264_preset="ultrafast" if draft else rc.x264_preset,
        audio_bitrate=rc.audio_bitrate,
        audio_rate=rc.audio_rate,
        has_audio=media.has_audio,
        normalize_audio=normalize_audio,
        loudness_measured=(measure_loudness(source, plan.start, plan.duration, rc)
                           if normalize_audio and media.has_audio else None),
        loudness_range=rc.loudness_range,
        lift_gamma=lift_gamma,
    )

    command = build_command(spec)
    log.debug("rendering %s with %s (%s)", plan.clip_id, encoder, reason)
    started = time.perf_counter()
    run(command)
    elapsed = time.perf_counter() - started

    log.info(
        "%s rendered in %.1fs (%s, %s, %.1fs of video)",
        plan.clip_id, elapsed, layout.describe, encoder, plan.duration,
    )
    return RenderResult(
        output=output,
        ass_path=ass_path,
        encoder=encoder,
        encoder_reason=reason,
        elapsed=elapsed,
        command=command,
    )


def measure_loudness(source: Path, start: float, duration: float, rc) -> dict | None:
    """First loudnorm pass over the clip's span; None if it can't be read.

    One-pass loudnorm estimates as it goes and pumps on speech with pauses; with
    the clip measured first, the second pass applies one linear gain (research
    rule R7.1: -14 LUFS integrated, true peak -1.5 dBTP, range <= 8 LU).
    """
    import json
    import re

    try:
        proc = run(["-hide_banner", "-nostdin", "-ss", f"{start:.3f}", "-t", f"{duration:.3f}",
                    "-i", str(source), "-vn",
                    "-af", (f"loudnorm=I={rc.loudness_lufs}:TP={rc.true_peak_dbtp}:"
                            f"LRA={rc.loudness_range}:print_format=json"),
                    "-f", "null", "-"], check=False)
        found = re.findall(r"\{[^{}]*\"input_i\"[^{}]*\}", proc.stderr or "")
        data = json.loads(found[-1]) if found else None
    except (OSError, ValueError) as exc:
        log.debug("loudness measurement failed: %s", exc)
        return None
    if not data or any(str(data.get(k, "")).strip() in ("", "-inf", "inf")
                       for k in ("input_i", "input_tp", "input_lra", "input_thresh")):
        return None  # silence or a measurement loudnorm can't use: one pass instead
    return data


def _scale_layout(layout: LayoutPlan, media: MediaInfo, width: int, height: int) -> LayoutPlan:
    """Recompute crop geometry if the output size differs from the plan's.

    A layout planned for 1080x1920 has the right *aspect* for 540x960 too, since
    the crop is expressed in source pixels -- but the two_speaker pane split
    depends on the output height, so it is recomputed here rather than assumed.
    """
    from .layouts import crop_size_for_aspect

    if layout.kind == "per_shot":
        return layout.model_copy(update={"segments": [
            segment.model_copy(update={
                "layout": _scale_layout(segment.layout, media, width, height)})
            for segment in layout.segments
        ]})

    if layout.kind != "two_speaker_stack" or not layout.panes:
        return layout

    pane_h = height // 2
    crop_w, crop_h = crop_size_for_aspect(media.width, media.height, width, pane_h)
    if (crop_w, crop_h) == (layout.crop_width, layout.crop_height):
        return layout

    # Keep each pane's centre, resize the rectangle around it.
    from .layouts import clamp_crop_origin

    panes = []
    for pane in layout.panes:
        cx, cy = pane.center
        x, y = clamp_crop_origin(cx - crop_w / 2, cy - crop_h / 2,
                                 crop_w, crop_h, media.width, media.height)
        panes.append(pane.model_copy(update={"x": x, "y": y,
                                             "width": crop_w, "height": crop_h}))
    return layout.model_copy(update={"panes": panes,
                                     "crop_width": crop_w, "crop_height": crop_h})
