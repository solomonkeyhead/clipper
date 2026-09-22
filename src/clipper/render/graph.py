"""Building the FFmpeg filter graph and command for one clip.

The whole render is a single FFmpeg invocation: cut, reframe, burn captions,
normalise loudness, encode. Nothing is piped through Python.

That is a deliberate departure from BUILD_BRIEF.md section 11.1, which suggests
decoding with OpenCV and piping raw frames out: raw 1080x1920 at 30 fps is about
93 MB/s through a pipe, plus a Python loop in the hot path. It is also no longer
needed for the reason it was suggested -- a moving crop. Framing is static per
shot (docs/DECISIONS.md D40), so every layout is a fixed filter chain.
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


def build_video_filter(spec: RenderSpec) -> str:
    """The full video filter graph, ending in a stream labelled ``[v]``."""
    if spec.layout.kind == "per_shot":
        chain = _per_shot_chain(spec)
    else:
        chain = _layout_chain(spec, spec.layout, "[0:v]", "")

    # Normalise frame rate and pixel aspect before captions, so caption
    # positioning is computed against the final geometry.
    tail = [f"fps={spec.fps}", "setsar=1", "format=yuv420p"]
    if spec.ass_path is not None:
        tail.append(_ass_filter(spec))

    return f"{chain},{','.join(tail)}[v]"


def _layout_chain(spec: RenderSpec, layout: LayoutPlan, src: str, tag: str) -> str:
    """One layout's chain, reading from `src` and using `tag`-suffixed labels.

    `src` and `tag` exist so the same builders serve both a whole clip (reading
    ``[0:v]``, no suffix) and one shot of a segmented render (reading its own
    trimmed branch, with a suffix that keeps intermediate labels unique).
    """
    if layout.kind == "follow_crop":
        return _follow_crop_chain(spec, layout, src)
    if layout.kind == "fit_crop":
        return _blurred_fit_chain(spec, src, tag, layout)
    if layout.kind == "two_speaker_stack":
        return _two_speaker_chain(spec, layout, src, tag)
    if layout.kind == "content_stack":
        return _content_stack_chain(spec, layout, src, tag)
    if layout.kind == "blurred_fit":
        return _blurred_fit_chain(spec, src, tag)
    raise ValueError(f"unknown layout {layout.kind!r}")


def _per_shot_chain(spec: RenderSpec) -> str:
    """Each shot framed by its own chain, then concatenated back together.

    One FFmpeg pass, not one render per shot: the source is split, each branch
    is trimmed to its shot and given its own layout, and `concat` rejoins them.
    Every branch produces the same output size, which is what `concat` requires.

    Captions are burned after the concat, on the reassembled timeline, so their
    timings need no adjustment.
    """
    segments = spec.layout.segments
    if not segments:
        raise ValueError("per_shot layout has no segments")

    count = len(segments)
    parts = ["[0:v]split=" + str(count)
             + "".join(f"[shot{i}]" for i in range(count))]
    for i, segment in enumerate(segments):
        parts.append(
            f"[shot{i}]trim=start={segment.start:.3f}:end={segment.end:.3f},"
            f"setpts=PTS-STARTPTS[cut{i}]"
        )
        body = _layout_chain(spec, segment.layout, f"[cut{i}]", f"s{i}")
        # `concat` refuses inputs whose sample aspect ratios differ, and the
        # layouts round theirs differently: a blurred_fit branch came out at
        # SAR 1216:1215 next to a follow_crop branch at 10240:10239, which
        # failed the render outright. Forcing square pixels per branch, rather
        # than once after the concat, is what makes the branches joinable.
        parts.append(f"{body},setsar=1,format=yuv420p[seg{i}]")
    parts.append("".join(f"[seg{i}]" for i in range(count))
                 + f"concat=n={count}:v=1:a=0")
    return ";".join(parts)


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


def _follow_crop_chain(spec: RenderSpec, layout: LayoutPlan, src: str) -> str:
    """A static 9:16 crop, scaled to fill the output."""
    first = layout.keyframes[0] if layout.keyframes else None
    x, y = (first.x, first.y) if first else (0, 0)
    return (f"{src}crop={layout.crop_width}:{layout.crop_height}:{x}:{y},"
            f"scale={spec.width}:{spec.height}:flags=lanczos")


def _two_speaker_chain(spec: RenderSpec, layout: LayoutPlan, src: str,
                       tag: str) -> str:
    """Two static crops stacked vertically, each filling half the output."""
    if len(layout.panes) != 2:
        raise ValueError("two_speaker_stack needs exactly two panes")

    pane_h = spec.height // 2
    top, bottom = layout.panes
    return (
        f"{src}split=2[tsrc{tag}][bsrc{tag}];"
        f"[tsrc{tag}]crop={top.width}:{top.height}:{top.x}:{top.y},"
        f"scale={spec.width}:{pane_h}:flags=lanczos[ttop{tag}];"
        f"[bsrc{tag}]crop={bottom.width}:{bottom.height}:{bottom.x}:{bottom.y},"
        f"scale={spec.width}:{spec.height - pane_h}:flags=lanczos[tbot{tag}];"
        f"[ttop{tag}][tbot{tag}]vstack=inputs=2"
    )


def _content_stack_chain(spec: RenderSpec, layout: LayoutPlan, src: str,
                         tag: str) -> str:
    """Screen-share content above the speaker's webcam, each filling its pane.

    Each pane is scaled to *cover* its slot and then centre-cropped, so neither
    is letterboxed. The pane heights come from the plan, which sized them from
    the content's own aspect ratio -- see `layouts.plan_content_stack`.
    """
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
        f"{src}split=2[csrc{tag}][wsrc{tag}];"
        f"[csrc{tag}]crop={top.width}:{top.height}:{top.x}:{top.y},"
        f"scale={spec.width}:{top_h}:force_original_aspect_ratio=increase:flags=lanczos,"
        f"crop={spec.width}:{top_h}[ctop{tag}];"
        f"[wsrc{tag}]crop={bottom.width}:{bottom.height}:{bottom.x}:{bottom.y},"
        f"scale={spec.width}:{bottom_h}:force_original_aspect_ratio=increase:flags=lanczos,"
        f"crop={spec.width}:{bottom_h}[cbot{tag}];"
        f"[ctop{tag}][cbot{tag}]vstack=inputs=2"
    )


def _blurred_fit_chain(spec: RenderSpec, src: str, tag: str,
                       layout: LayoutPlan | None = None) -> str:
    """A region at full output width, centred over a blurred copy of itself.

    With no `layout` the region is the whole frame (`blurred_fit`). A `fit_crop`
    layout passes a narrower region -- wide enough to hold the subject and any
    graphic beside them -- which is cropped first, so the background is a blur
    of that same region rather than of content the viewer cannot see.
    """
    sigma = round(BACKGROUND_BLUR_SIGMA * spec.width / 1080, 1)
    region = ""
    if layout is not None and layout.keyframes:
        kf = layout.keyframes[0]
        region = f"crop={layout.crop_width}:{layout.crop_height}:{kf.x}:{kf.y},"
    return (
        f"{src}{region}split=2[bg{tag}][fg{tag}];"
        f"[bg{tag}]scale={spec.width}:{spec.height}:force_original_aspect_ratio=increase,"
        f"crop={spec.width}:{spec.height},gblur=sigma={sigma},"
        f"eq=brightness={BACKGROUND_DARKEN}[bgb{tag}];"
        f"[fg{tag}]scale={spec.width}:-2:flags=lanczos[fgs{tag}];"
        f"[bgb{tag}][fgs{tag}]overlay=(W-w)/2:(H-h)/2:shortest=1"
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
