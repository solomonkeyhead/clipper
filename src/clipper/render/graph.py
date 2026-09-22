"""Building the FFmpeg filter graph and command for one clip.

The whole render is a single FFmpeg invocation: cut, reframe, burn captions,
normalise loudness, encode. Nothing is piped through Python.

That is a deliberate departure from BUILD_BRIEF.md section 11.1, which suggests
decoding with OpenCV and piping raw frames out. Raw 1080x1920 at 30 fps is about
93 MB/s through a pipe, plus a Python loop in the hot path. The time-varying
follow-crop it was needed for can instead be driven by FFmpeg's own ``sendcmd``
filter, since ``crop``'s ``x`` and ``y`` options are marked as command-capable
(``T`` in ``ffmpeg -h filter=crop``). Verified working; see docs/DECISIONS.md D11.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..config import RenderConfig
from ..models import LayoutPlan
from .ffmpeg import escape_filter_path

# Blur strength for the blurred_fit background, at the reference 1080 width.
BACKGROUND_BLUR_SIGMA = 40.0
# Enough to separate the foreground strip from the fill without crushing a dark
# source to solid black -- which also risks tripping the QA blackdetect check.
BACKGROUND_DARKEN = -0.15


@dataclass(frozen=True)
class RenderSpec:
    """Everything one clip render needs. Built by the caller, consumed here."""

    source: Path
    output: Path
    start: float
    duration: float
    layout: LayoutPlan
    ass_path: Path | None
    fonts_dir: Path | None
    width: int
    height: int
    fps: int
    encoder: str
    loudness_lufs: float
    true_peak_dbtp: float
    crf: int
    nvenc_cq: int
    x264_preset: str
    audio_bitrate: str
    audio_rate: int
    has_audio: bool = True
    sendcmd_path: Path | None = None


def build_sendcmd_script(layout: LayoutPlan) -> str:
    """Render a follow-crop trajectory as a ``sendcmd`` command script.

    Each line is ``<time> crop x <px>;`` -- the format sendcmd expects. Times are
    relative to the clip, which is what the filter graph sees because the input
    is seeked with ``-ss`` before ``-i`` and timestamps restart at zero.
    """
    lines: list[str] = []
    for kf in layout.keyframes:
        lines.append(f"{kf.t:.3f} crop x {kf.x};")
        lines.append(f"{kf.t:.3f} crop y {kf.y};")
    return "\n".join(lines) + ("\n" if lines else "")


def build_video_filter(spec: RenderSpec) -> str:
    """The full video filter graph, ending in a stream labelled ``[v]``."""
    layout = spec.layout
    if layout.kind == "follow_crop":
        chain = _follow_crop_chain(spec)
    elif layout.kind == "two_speaker_stack":
        chain = _two_speaker_chain(spec)
    elif layout.kind == "content_stack":
        chain = _content_stack_chain(spec)
    elif layout.kind == "blurred_fit":
        chain = _blurred_fit_chain(spec)
    else:  # pragma: no cover - LayoutKind is a closed Literal
        raise ValueError(f"unknown layout {layout.kind!r}")

    # Normalise frame rate and pixel aspect before captions, so caption
    # positioning is computed against the final geometry.
    tail = [f"fps={spec.fps}", "setsar=1", "format=yuv420p"]
    if spec.ass_path is not None:
        tail.append(_ass_filter(spec))

    return f"{chain},{','.join(tail)}[v]"


def _ass_filter(spec: RenderSpec) -> str:
    """The ``ass`` filter with an explicitly escaped Windows path.

    ``fontsdir`` is set so libass finds the bundled OFL fonts without them being
    installed system-wide -- otherwise output depends on whatever fonts the
    machine happens to have.
    """
    parts = [f"f='{escape_filter_path(spec.ass_path)}'"]
    if spec.fonts_dir is not None:
        parts.append(f"fontsdir='{escape_filter_path(spec.fonts_dir)}'")
    # original_size tells libass what PlayRes maps onto; our ASS header already
    # uses the output size, so they agree.
    parts.append(f"original_size={spec.width}x{spec.height}")
    return "ass=" + ":".join(parts)


def _follow_crop_chain(spec: RenderSpec) -> str:
    """Crop tracking the speaker, driven by sendcmd, then scaled to output."""
    layout = spec.layout
    cw, ch = layout.crop_width, layout.crop_height
    first = layout.keyframes[0] if layout.keyframes else None
    x0, y0 = (first.x, first.y) if first else (0, 0)

    steps = []
    # sendcmd must sit upstream of the filter it drives.
    if spec.sendcmd_path is not None and len(layout.keyframes) > 1:
        steps.append(f"sendcmd=f='{escape_filter_path(spec.sendcmd_path)}'")
    steps.append(f"crop={cw}:{ch}:{x0}:{y0}")
    steps.append(f"scale={spec.width}:{spec.height}:flags=lanczos")
    return "[0:v]" + ",".join(steps)


def _two_speaker_chain(spec: RenderSpec) -> str:
    """Two static crops stacked vertically, each filling half the output."""
    layout = spec.layout
    if len(layout.panes) != 2:
        raise ValueError("two_speaker_stack needs exactly two panes")

    pane_h = spec.height // 2
    top, bottom = layout.panes
    return (
        f"[0:v]split=2[tsrc][bsrc];"
        f"[tsrc]crop={top.width}:{top.height}:{top.x}:{top.y},"
        f"scale={spec.width}:{pane_h}:flags=lanczos[ttop];"
        f"[bsrc]crop={bottom.width}:{bottom.height}:{bottom.x}:{bottom.y},"
        f"scale={spec.width}:{spec.height - pane_h}:flags=lanczos[tbot];"
        f"[ttop][tbot]vstack=inputs=2"
    )


def _content_stack_chain(spec: RenderSpec) -> str:
    """Screen-share content above the speaker's webcam, each filling its pane.

    Each pane is scaled to *cover* its slot and then centre-cropped, so neither
    is letterboxed. The pane heights come from the plan, which sized them from
    the content's own aspect ratio -- see `layouts.plan_content_stack`.
    """
    layout = spec.layout
    if len(layout.panes) != 2 or len(layout.pane_heights) != 2:
        raise ValueError("content_stack needs two panes and two pane heights")

    top, bottom = layout.panes
    top_h, bottom_h = layout.pane_heights
    if top_h + bottom_h != spec.height:
        raise ValueError(
            f"content_stack pane heights {top_h}+{bottom_h} do not sum to "
            f"the output height {spec.height}"
        )

    return (
        f"[0:v]split=2[csrc][wsrc];"
        f"[csrc]crop={top.width}:{top.height}:{top.x}:{top.y},"
        f"scale={spec.width}:{top_h}:force_original_aspect_ratio=increase:flags=lanczos,"
        f"crop={spec.width}:{top_h}[ctop];"
        f"[wsrc]crop={bottom.width}:{bottom.height}:{bottom.x}:{bottom.y},"
        f"scale={spec.width}:{bottom_h}:force_original_aspect_ratio=increase:flags=lanczos,"
        f"crop={spec.width}:{bottom_h}[cbot];"
        f"[ctop][cbot]vstack=inputs=2"
    )


def _blurred_fit_chain(spec: RenderSpec) -> str:
    """Source at full width, centred over a blurred, darkened copy of itself."""
    sigma = round(BACKGROUND_BLUR_SIGMA * spec.width / 1080, 1)
    return (
        f"[0:v]split=2[bg][fg];"
        f"[bg]scale={spec.width}:{spec.height}:force_original_aspect_ratio=increase,"
        f"crop={spec.width}:{spec.height},gblur=sigma={sigma},"
        f"eq=brightness={BACKGROUND_DARKEN}[bgb];"
        f"[fg]scale={spec.width}:-2:flags=lanczos[fgs];"
        f"[bgb][fgs]overlay=(W-w)/2:(H-h)/2:shortest=1"
    )


def build_audio_filter(spec: RenderSpec) -> str:
    """Loudness normalisation to the configured target, ending in ``[a]``."""
    return (
        f"[0:a]loudnorm=I={spec.loudness_lufs}:TP={spec.true_peak_dbtp}:LRA=11,"
        f"aresample={spec.audio_rate}:resampler=soxr,"
        f"aformat=sample_fmts=fltp:channel_layouts=stereo[a]"
    )


def build_command(spec: RenderSpec) -> list[str]:
    """The complete FFmpeg argument list for one clip render."""
    filters = [build_video_filter(spec)]
    if spec.has_audio:
        filters.append(build_audio_filter(spec))

    args: list[str] = [
        "-hide_banner",
        "-nostdin",
        "-loglevel", "error",
        # -ss before -i is the fast seek; modern FFmpeg still decodes from the
        # preceding keyframe, so it is accurate as well as quick.
        "-ss", f"{spec.start:.3f}",
        "-t", f"{spec.duration:.3f}",
        "-i", str(spec.source),
        "-filter_complex", ";".join(filters),
        "-map", "[v]",
    ]

    if spec.has_audio:
        args += ["-map", "[a]", "-c:a", "aac", "-b:a", spec.audio_bitrate,
                 "-ar", str(spec.audio_rate), "-ac", "2"]
    else:
        args += ["-an"]

    args += _video_encoder_args(spec)
    args += [
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        # FFmpeg 9 removed -vsync; -fps_mode is the replacement.
        "-fps_mode", "cfr",
        "-y", str(spec.output),
    ]
    return args


def _video_encoder_args(spec: RenderSpec) -> list[str]:
    if spec.encoder == "h264_nvenc":
        return [
            "-c:v", "h264_nvenc",
            "-preset", "p5",
            "-rc", "vbr",
            "-cq", str(spec.nvenc_cq),
            "-b:v", "0",
            "-profile:v", "high",
        ]
    return [
        "-c:v", "libx264",
        "-preset", spec.x264_preset,
        "-crf", str(spec.crf),
        "-profile:v", "high",
        "-level", "4.2",
    ]


def output_size(cfg: RenderConfig, *, draft: bool) -> tuple[int, int]:
    """Output dimensions, honouring ``--draft``."""
    if draft:
        return cfg.draft_width, cfg.draft_height
    return cfg.width, cfg.height
