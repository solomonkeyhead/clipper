"""The data that moves between pipeline stages.

Every stage reads and writes these as JSON under ``data/work/<source_id>/`` so
any stage can resume from the previous one's output (BUILD_BRIEF.md section 6).
They are pydantic models rather than dataclasses so that a half-written artifact
from a crashed run fails loudly on load instead of propagating nonsense.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

LayoutKind = Literal[
    "follow_crop", "fit_crop", "two_speaker_stack", "content_stack", "blurred_fit",
    "per_shot",
]
PolicyRisk = Literal["none", "low", "high"]
QAStatus = Literal["pass", "warn", "fail"]


class Artifact(BaseModel):
    """Base for anything persisted to ``data/work/``."""

    model_config = ConfigDict(extra="forbid")

    def save(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        # Write to a temp sibling and replace, so an interrupted run cannot leave
        # a truncated JSON file that later looks like valid cached state.
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(
            json.dumps(self.model_dump(mode="json"), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        tmp.replace(path)
        return path

    @classmethod
    def load(cls, path: Path) -> Self:
        return cls.model_validate_json(Path(path).read_text(encoding="utf-8"))


# --------------------------------------------------------------------------
# media
# --------------------------------------------------------------------------


class MediaInfo(Artifact):
    """What ffprobe says about a source file."""

    path: str
    duration: float
    width: int
    height: int
    fps: float
    has_audio: bool
    video_codec: str = ""
    audio_codec: str = ""
    audio_channels: int = 0
    audio_sample_rate: int = 0
    size_bytes: int = 0


class SourceInfo(Artifact):
    """``info.json``: everything known about the source before transcription."""

    source_id: str
    media: MediaInfo
    title: str = ""
    url: str = ""
    uploader: str = ""
    upload_date: str = ""
    original_duration: float = 0.0
    audio_path: str = ""
    # YouTube "Most replayed", when yt-dlp provides it: segments with
    # start_time / end_time / value. Absent for local files and most footage.
    heatmap: list[dict[str, float]] | None = None

    @property
    def has_heatmap(self) -> bool:
        return bool(self.heatmap)


# --------------------------------------------------------------------------
# transcript
# --------------------------------------------------------------------------


class Word(Artifact):
    """One word with its timing. The atom everything downstream is built from."""

    start: float
    end: float
    text: str
    probability: float = 1.0

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)

    @property
    def normalized(self) -> str:
        """Lowercased and stripped of punctuation, for filler/keyword matching."""
        return "".join(c for c in self.text.lower() if c.isalnum() or c == "'")


class Transcript(Artifact):
    """``transcript.json``."""

    source_id: str
    language: str
    language_probability: float = 1.0
    model: str = ""
    duration: float = 0.0
    words: list[Word] = Field(default_factory=list)

    @property
    def text(self) -> str:
        return " ".join(w.text.strip() for w in self.words).strip()

    def words_between(self, start: float, end: float) -> list[Word]:
        """Words whose midpoint falls inside [start, end).

        Midpoint rather than full containment: a word straddling the boundary
        belongs to whichever side holds most of it, which keeps caption text and
        clip text consistent.
        """
        return [w for w in self.words if start <= (w.start + w.end) / 2 < end]


class Sentence(Artifact):
    """A sentence, from punctuation or a long pause."""

    index: int
    start: float
    end: float
    text: str
    word_indices: tuple[int, int]  # [lo, hi) into Transcript.words
    # Silence before this sentence starts; used for boundary snapping.
    gap_before: float = 0.0
    ends_with_terminal_punctuation: bool = False
    paragraph: int = 0

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


class Sentences(Artifact):
    """``sentences.json``."""

    source_id: str
    sentences: list[Sentence] = Field(default_factory=list)


# --------------------------------------------------------------------------
# candidates and scoring
# --------------------------------------------------------------------------


class Candidate(Artifact):
    """A window of the source that might become a clip."""

    candidate_id: str
    start: float
    end: float
    sentence_indices: tuple[int, int]  # [lo, hi) into Sentences.sentences
    text: str
    word_count: int = 0
    silence_ratio: float = 0.0
    pre_score: float = 0.0  # cheap ranking used to cap the LLM batch
    # Scripted TV: the scene this window lies in. A clip is never extended
    # past it, so it cannot pick up a moment of the neighbouring scene.
    scene_start: float | None = None
    scene_end: float | None = None

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)

    def overlaps(self, other: Candidate) -> bool:
        return self.start < other.end and other.start < self.end

    def iou(self, other: Candidate) -> float:
        """Intersection over union, for deduping near-identical windows."""
        inter = max(0.0, min(self.end, other.end) - max(self.start, other.start))
        union = (self.duration + other.duration) - inter
        return inter / union if union > 0 else 0.0


class Scene(Artifact):
    """One scene of scripted TV: from the camera cut that opens it to the next."""

    index: int
    start: float
    end: float
    first_sentence: int
    last_sentence: int  # inclusive
    summary: str = ""

    @property
    def duration(self) -> float:
        return self.end - self.start


class Scenes(Artifact):
    """``scenes.json``."""

    source_id: str
    scenes: list[Scene] = Field(default_factory=list)


class Candidates(Artifact):
    """``candidates.json``."""

    source_id: str
    candidates: list[Candidate] = Field(default_factory=list)


class RubricScores(Artifact):
    """One LLM's verdict on one candidate (BUILD_BRIEF.md section 9.1)."""

    hook_strength: int = Field(ge=0, le=10)
    standalone_clarity: int = Field(ge=0, le=10)
    payoff: int = Field(ge=0, le=10)
    emotional_intensity: int = Field(ge=0, le=10)
    quotability: int = Field(ge=0, le=10)
    ending_completeness: int = Field(ge=0, le=10)

    needs_prior_context: bool = False
    is_sponsor_or_ad: bool = False
    policy_risk: PolicyRisk = "none"

    hook_text: str = ""
    suggested_caption: str = ""
    hashtags: list[str] = Field(default_factory=list)

    def total(self, weights: dict[str, float]) -> float:
        """Weighted rubric sum on the original 0-10 scale."""
        dumped = self.model_dump()
        num = sum(weights[k] * dumped[k] for k in weights)
        den = sum(weights.values())
        return num / den if den else 0.0


class SignalValues(Artifact):
    """Raw, un-normalized signal values for one candidate."""

    candidate_id: str
    llm_a: RubricScores | None = None
    llm_b: RubricScores | None = None
    llm_total: float | None = None  # absolute 0-10, after the disagreement penalty
    audio: float | None = None
    heatmap: float | None = None
    text: float | None = None
    # Per-feature detail, kept so `clipper explain` can show its working.
    audio_features: dict[str, float] = Field(default_factory=dict)
    text_features: dict[str, float] = Field(default_factory=dict)
    heatmap_features: dict[str, float] = Field(default_factory=dict)
    dropped: bool = False
    drop_reason: str = ""


class Signals(Artifact):
    """``signals.json``."""

    source_id: str
    available: list[str] = Field(default_factory=list)
    values: list[SignalValues] = Field(default_factory=list)


class ScoredCandidate(Artifact):
    """A candidate with its normalized components and final composite."""

    candidate_id: str
    composite: float
    components: dict[str, float] = Field(default_factory=dict)  # percentile ranks
    raw: dict[str, float] = Field(default_factory=dict)
    penalty: float = 1.0
    penalty_reasons: list[str] = Field(default_factory=list)
    dropped: bool = False
    drop_reason: str = ""


class Scored(Artifact):
    """``scored.json``."""

    source_id: str
    weights_used: dict[str, float] = Field(default_factory=dict)
    scored: list[ScoredCandidate] = Field(default_factory=list)


# --------------------------------------------------------------------------
# selection and rendering
# --------------------------------------------------------------------------


class CropRect(Artifact):
    """An integer-pixel crop rectangle in source coordinates."""

    x: int
    y: int
    width: int
    height: int

    @model_validator(mode="after")
    def _positive(self) -> CropRect:
        if self.width <= 0 or self.height <= 0:
            raise ValueError("crop width and height must be positive")
        if self.x < 0 or self.y < 0:
            raise ValueError("crop origin must be non-negative")
        return self

    @property
    def center(self) -> tuple[float, float]:
        return self.x + self.width / 2, self.y + self.height / 2


class CropKeyframe(Artifact):
    """The crop origin at one instant, relative to the clip's own start."""

    t: float
    x: int
    y: int


class LayoutSegment(Artifact):
    """One shot of a `per_shot` layout, with times relative to the clip."""

    start: float
    end: float
    layout: LayoutPlan

    @property
    def duration(self) -> float:
        return self.end - self.start


class LayoutPlan(Artifact):
    """How one clip gets from 16:9 to 9:16."""

    kind: LayoutKind
    crop_width: int = 0
    crop_height: int = 0
    # Crop origin for follow_crop and fit_crop. Framing is static, so there is
    # one keyframe, at t=0; the list shape is kept so plans saved by earlier
    # versions still load.
    keyframes: list[CropKeyframe] = Field(default_factory=list)
    # two_speaker_stack / content_stack: one crop per pane, top then bottom.
    panes: list[CropRect] = Field(default_factory=list)
    # Output height of each pane, in the same order. Computed at planning time
    # so the filter-graph builder needs no layout-specific knowledge.
    pane_heights: list[int] = Field(default_factory=list)
    # per_shot: one framing per shot, in time order, tiling the clip exactly.
    segments: list[LayoutSegment] = Field(default_factory=list)
    face_ratio: float = 0.0  # fraction of sampled frames with a usable face
    reason: str = ""

    @property
    def is_face_centric(self) -> bool:
        """QA only enforces the face-presence check for these layouts.

        `content_stack` is included: it still shows the speaker, in its own
        pane, so losing the face mid-clip is just as wrong there.

        A `per_shot` clip qualifies only if *every* shot does. A clip that is
        half close-up and half wide has no face for half its length by design,
        and failing it for that would reject the framing that fixed it.
        """
        if self.kind == "per_shot":
            return bool(self.segments) and all(
                s.layout.is_face_centric for s in self.segments)
        return self.kind in ("follow_crop", "fit_crop", "two_speaker_stack",
                             "content_stack")

    @property
    def describe(self) -> str:
        """The layout name for a manifest, naming the shots when segmented."""
        if self.kind != "per_shot" or not self.segments:
            return self.kind
        # Run-length encoded: a clip cut into seven shots that mostly agree
        # should read as "5x follow_crop", not as the same word five times.
        parts: list[tuple[str, int]] = []
        for segment in self.segments:
            if parts and parts[-1][0] == segment.layout.kind:
                parts[-1] = (parts[-1][0], parts[-1][1] + 1)
            else:
                parts.append((segment.layout.kind, 1))
        return "per_shot[" + "+".join(
            kind if n == 1 else f"{n}x {kind}" for kind, n in parts) + "]"


class ClipPlan(Artifact):
    """One selected clip, refined and ready to render."""

    clip_id: str
    candidate_id: str
    rank: int
    start: float
    end: float
    text: str
    composite: float = 0.0
    hook_text: str = ""
    suggested_caption: str = ""
    # A campaign's `long_description`: sentences between the caption line and
    # the hashtags (campaign/description.py).
    description: str = ""
    hashtags: list[str] = Field(default_factory=list)
    caption_style: str = "bold_pop"
    layout: LayoutPlan | None = None
    # How refinement moved the boundaries, for `explain` and debugging.
    refine_notes: list[str] = Field(default_factory=list)
    # What the opening looked like, so the performance log can compare posts
    # that did and did not have each retention change (see learn/log.py).
    lead_in: float | None = None  # seconds before the first word
    hook_shown: bool = False

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


# --------------------------------------------------------------------------
# QA
# --------------------------------------------------------------------------


class QACheck(Artifact):
    """One automated check on one rendered clip."""

    name: str
    status: QAStatus
    detail: str = ""
    measured: float | None = None
    limit: float | None = None


class QAReport(Artifact):
    """All checks for one rendered clip; written beside a rejected clip."""

    clip_id: str
    file: str
    checks: list[QACheck] = Field(default_factory=list)

    @property
    def status(self) -> QAStatus:
        if any(c.status == "fail" for c in self.checks):
            return "fail"
        if any(c.status == "warn" for c in self.checks):
            return "warn"
        return "pass"

    @property
    def failures(self) -> list[QACheck]:
        return [c for c in self.checks if c.status == "fail"]


LayoutSegment.model_rebuild()
