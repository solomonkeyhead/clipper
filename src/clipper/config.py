"""Typed config: the global pipeline settings and the per-campaign rules.

Two separate files with different lifetimes. `config/default.yaml` is tuning --
weights, thresholds, encoder settings -- and is rewritten by `clipper learn`.
`campaigns/<name>.yaml` is contractual: what a specific payout campaign requires
and forbids. Keeping them apart means retuning weights can never quietly relax a
campaign's compliance rules.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .paths import REPO_ROOT

DEFAULT_CONFIG_PATH = REPO_ROOT / "config" / "default.yaml"

CaptionStyle = Literal["bold_pop", "clean_white", "yellow_highlight"]
LLMBackend = Literal["gemini", "ollama", "anthropic", "mock"]
Encoder = Literal["auto", "h264_nvenc", "libx264"]
CreditPosition = Literal["top_left", "top_right", "bottom_left", "bottom_right"]

Unit = Annotated[float, Field(ge=0.0, le=1.0)]
"""A weight or ratio in [0, 1]."""


class StrictModel(BaseModel):
    """Reject unknown keys so a typo in YAML is an error, not a silent default."""

    model_config = ConfigDict(extra="forbid", frozen=True)


# --------------------------------------------------------------------------
# global config
# --------------------------------------------------------------------------


class TranscriptionConfig(StrictModel):
    model: str = "large-v3"
    compute_type: Literal["float16", "int8_float16", "int8", "float32"] = "float16"
    device: Literal["auto", "cuda", "cpu"] = "auto"
    language: str = "auto"  # "auto" or an ISO code like "en"
    vad_filter: bool = True
    beam_size: int = Field(default=5, ge=1, le=10)
    # Sources longer than this are transcribed in chunks to bound peak memory.
    chunk_seconds: int = Field(default=3600, ge=300)


class CandidatesConfig(StrictModel):
    min_seconds: float = Field(default=20.0, gt=0)
    max_seconds: float = Field(default=55.0, gt=0)
    target_seconds: tuple[float, float] = (25.0, 45.0)
    # Score candidates longer than target_seconds[1] slightly down (see
    # signals/combine.py `length_penalty`). Scripted campaigns turn it on.
    prefer_target_length: bool = False
    max_candidates: int = Field(default=60, ge=1)
    min_gap_seconds: float = Field(default=30.0, ge=0)
    # Hard filters (section 8).
    max_silence_ratio: Unit = 0.25
    edge_trim_seconds: float = Field(default=20.0, ge=0)
    # Sources shorter than this keep their head and tail as fair game.
    edge_trim_min_source_seconds: float = Field(default=300.0, ge=0)
    dedupe_iou: Unit = 0.8
    # Scripted TV: windows stay inside one scene (see candidates/scenes.py),
    # and start or end mid-scene only on a beat -- a pause at least `beat_gap`
    # long -- never in the middle of an exchange.
    scene_aware: bool = False
    beat_gap: float = Field(default=0.7, ge=0)
    # Ending needs a longer pause than starting. At 0.7s, clips ended on
    # "What is it doing?" with "No, don't eat that." 0.85s later, and on
    # "Thanks, guys." with "I love you." 0.75s later -- a reply, not a beat.
    # Across four episodes 1.2s still leaves 26-46 end points each.
    beat_end_gap: float = Field(default=1.2, ge=0)
    # Moments with no dialogue (a field play, a chase, a reaction held in
    # silence): windows around wordless stretches of at least `quiet_gap`
    # seconds, judged only by watching them (signals/visual.py, D68).
    quiet_moments: bool = True
    quiet_gap: float = Field(default=10.0, gt=0)
    max_quiet: int = Field(default=6, ge=0)

    @model_validator(mode="after")
    def _check_bounds(self) -> CandidatesConfig:
        if self.min_seconds >= self.max_seconds:
            raise ValueError("candidates.min_seconds must be < max_seconds")
        lo, hi = self.target_seconds
        if not (self.min_seconds <= lo <= hi <= self.max_seconds):
            raise ValueError(
                "candidates.target_seconds must sit inside [min_seconds, max_seconds]"
            )
        return self


class WeightsConfig(StrictModel):
    """Signal weights. Renormalized at runtime over whichever signals exist."""

    llm: Unit = 0.50
    heatmap: Unit = 0.20
    audio: Unit = 0.15
    text: Unit = 0.15

    @model_validator(mode="after")
    def _check_nonzero(self) -> WeightsConfig:
        if sum(self.as_dict().values()) <= 0:
            raise ValueError("weights must not all be zero")
        return self

    def as_dict(self) -> dict[str, float]:
        return {"llm": self.llm, "heatmap": self.heatmap, "audio": self.audio, "text": self.text}


class RubricWeights(StrictModel):
    """How the six 0-10 rubric scores roll up into one LLM total."""

    hook_strength: Unit = 0.30
    standalone_clarity: Unit = 0.20
    payoff: Unit = 0.20
    emotional_intensity: Unit = 0.15
    quotability: Unit = 0.10
    ending_completeness: Unit = 0.05

    def as_dict(self) -> dict[str, float]:
        return self.model_dump()


class SelectionConfig(StrictModel):
    """How many clips to take, and how good they have to be.

    Two thresholds, doing different jobs. `min_composite` is a *relative* guard
    on the percentile-ranked composite -- it can only say "this is weak for this
    video". `min_llm_total` is an *absolute* guard on the raw 0-10 rubric total,
    and is the only thing that can say "this video contains nothing worth
    clipping", which is what BUILD_BRIEF.md section 1 actually asks for. See
    PLAN.md P1 for why the brief's single percentile threshold cannot do it.
    """

    top_n: int = Field(default=5, ge=1)
    max_from_same_third: int = Field(default=3, ge=1)

    # Relative guard, deliberately low. With N candidates the percentile ranks
    # spread evenly over [0, 1] by construction, so a threshold of 0.35 rejects
    # the bottom third outright -- which is not a "tail", and starves the pool
    # of reserves used to replace a clip that fails QA. The absolute gate does
    # the quality work; this only catches a pathological bottom end.
    min_composite: Unit = 0.15

    # Absolute guard, on the same 0-10 scale the rubric uses. The prompts tell
    # the model most clips should score 3-6, so 5.5 asks for "above the middle
    # of what a harsh editor would accept". Tuned in Phase 5.
    min_llm_total: float = Field(default=5.5, ge=0.0, le=10.0)

    # Set false to reproduce the brief's literal percentile-only behaviour, so
    # the eval harness can measure whether the absolute gate actually helps.
    use_absolute_gate: bool = True


class LLMConfig(StrictModel):
    backend: LLMBackend = "gemini"
    model: str | None = None  # None => resolve the best free model at runtime
    batch_size: int = Field(default=15, ge=1, le=32)
    max_retries: int = Field(default=5, ge=0)
    temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    requests_per_minute: int = Field(default=10, ge=1)
    use_few_shot_examples: bool = False
    few_shot_count: int = Field(default=3, ge=0, le=10)
    # Prompt B costs a second pass over every candidate; disable to halve calls.
    use_second_opinion: bool = True
    # Penalty on |A - B| disagreement, in rubric-total points.
    disagreement_penalty: float = Field(default=0.25, ge=0.0, le=1.0)
    rubric_weights: RubricWeights = RubricWeights()
    # Ask the LLM which caption words speech recognition misheard ("picture"
    # for "pitcher") and fix them. Only sound-alike swaps are applied; see
    # clipper/transcribe/correct.py.
    correct_captions: bool = True
    # Model for caption correction, tried first; the scoring model is the
    # fallback when it is slow, overloaded or out of quota. On real clips the
    # default free model found "picture" -> "pitcher" but missed "clap" ->
    # "cramp", which gemini-3-flash-preview caught -- at ~90s per call on the
    # free tier. None uses the scoring model only.
    correction_model: str | None = None
    # Seconds to wait on `correction_model` before falling back. Gemini's API
    # refuses deadlines under 10s.
    correction_timeout: float = Field(default=30.0, ge=10)
    # Hard-drop candidates both prompts say need earlier context. Off for
    # scripted TV, where nearly every scene "needs context" by that standard
    # yet works as a clip: on a sitcom episode 26 of 48 were dropped for it.
    drop_needs_prior_context: bool = True
    # A campaign's `selection_focus`, set per run (runner.campaign_config).
    campaign_focus: str = ""
    # What the user's own ratings say they like and dislike (learn/feedback.py),
    # set per run; guidance for the scorer, weaker than the campaign focus.
    user_taste: str = ""
    # Caption fixes ruled wrong, as "heard -> replacement"; never applied.
    rejected_caption_fixes: list[str] = Field(default_factory=list)
    # The "watch it" pass (signals/visual.py): the model watches the best-scoring
    # moments -- picture and sound -- since a look or a reaction can make a joke
    # the words alone don't. Backends that can't take video skip it.
    watch_video: bool = True
    watch_shortlist: int = Field(default=12, ge=1, le=40)
    watch_model: str | None = None  # null = the scoring model

    @field_validator("rejected_caption_fixes")
    @classmethod
    def _check_rejected(cls, value: list[str]) -> list[str]:
        for entry in value:
            if entry.count("->") != 1 or not all(p.strip() for p in entry.split("->")):
                raise ValueError(
                    f"rejected_caption_fixes entry {entry!r} must look like "
                    "'heard -> replacement'")
        return value

    @property
    def rejected_fix_pairs(self) -> frozenset[tuple[str, str]]:
        """The rejected fixes, normalised the way caption words are compared."""
        import re

        def bare(text: str) -> str:
            return re.sub(r"[^\w']", "", text.lower())

        return frozenset(
            (bare(a), bare(b))
            for a, b in (entry.split("->") for entry in self.rejected_caption_fixes)
        )


class SafeArea(StrictModel):
    """Pixels of the 1080x1920 frame kept clear of platform UI."""

    # The research's universal box across TikTok, Reels and Shorts (compiled
    # from each platform's ad specs): x 120-888, y 288-1248, and left of x=780
    # below y=840 for the button rail. The old 400px bottom margin put captions
    # at y=1520 -- under TikTok's username and description.
    top: int = Field(default=300, ge=0)
    bottom: int = Field(default=680, ge=0)
    side: int = Field(default=120, ge=0)
    # The like/comment/share rail on the right, below y=840 (captions sit there).
    right: int = Field(default=300, ge=0)


class RenderConfig(StrictModel):
    width: int = Field(default=1080, gt=0)
    height: int = Field(default=1920, gt=0)
    fps: int = Field(default=30, gt=0)
    encoder: Encoder = "auto"
    x264_preset: str = "veryfast"
    crf: int = Field(default=20, ge=0, le=51)
    nvenc_cq: int = Field(default=23, ge=0, le=51)
    audio_bitrate: str = "192k"
    audio_rate: int = 48_000
    loudness_lufs: float = -14.0
    true_peak_dbtp: float = -1.5
    # Loudness range for dialogue clips (research R7.1: <= 8 LU).
    loudness_range: float = Field(default=8.0, gt=0)
    caption_style: CaptionStyle = "bold_pop"
    caption_font: str = "Inter"
    show_hook_text: bool = True
    hook_text_seconds: float = Field(default=2.0, ge=0)
    safe_area: SafeArea = SafeArea()
    # Face and graphic sampling rate for framing, in frames per second.
    face_sample_fps: float = Field(default=5.0, gt=0)
    # For screen-share sources, the share of output height given to the content
    # pane (the rest goes to the webcam). Only a target: the split also respects
    # the content's own aspect ratio so it is never squeezed.
    content_pane_share: Unit = 0.58
    # Set false to always crop to the face, even on screen-share footage.
    detect_screen_share: bool = True
    # A face narrower than this fraction of the frame is treated as an overlay
    # inset rather than the subject, and the whole frame is kept instead of
    # cropping to it. Lower it if genuine wide two-shots are being letterboxed
    # when you would rather they were stacked; raise it if reaction-cam overlays
    # are being blown up. See docs/DECISIONS.md D31 for why this is one number
    # rather than a cleverer test.
    min_subject_face_ratio: Unit = 0.13
    # Frame each shot of a clip separately, changing the framing only where the
    # source already cuts. Set false to pick one framing for the whole clip,
    # which is wrong for any clip whose composition changes part-way through.
    per_shot_framing: bool = True
    # Scripted TV: every person on screen stays in frame -- no stacks, no
    # picking the speaker. Set from a campaign's `scripted`.
    keep_everyone_in_frame: bool = False
    # With keep_everyone_in_frame: frame this many opening seconds full-screen
    # on the speaker (at most 4:5) instead of zooming out. 0 = off. Scripted
    # campaigns use 3s.
    opening_full_screen_seconds: float = Field(default=0.0, ge=0)
    # Shots shorter than this are merged into a neighbour rather than given
    # their own framing, and no clip gets more segments than `max_shots`.
    min_shot_seconds: float = Field(default=1.5, gt=0)
    max_shots: int = Field(default=8, ge=1)
    draft_width: int = Field(default=540, gt=0)
    draft_height: int = Field(default=960, gt=0)

    @model_validator(mode="after")
    def _check_safe_area(self) -> RenderConfig:
        sa = self.safe_area
        if sa.top + sa.bottom >= self.height:
            raise ValueError("render.safe_area top+bottom must be less than render.height")
        if 2 * sa.side >= self.width:
            raise ValueError("render.safe_area side*2 must be less than render.width")
        return self


class RefineConfig(StrictModel):
    """Boundary cleanup applied to each selected clip (section 10)."""

    pre_roll: float = Field(default=0.15, ge=0)
    post_roll: float = Field(default=0.35, ge=0)
    # Post-roll stops this far short of the next word, so its first sound is
    # never heard. Matters once post_roll is long (scripted: a reaction beat).
    tail_guard: float = Field(default=0.0, ge=0)
    # Scripted TV: the most silence allowed before a clip's first word and
    # after its last, even where a scene's own cut would allow more. None = off.
    max_lead_in: float | None = Field(default=None, ge=0)
    max_tail: float | None = Field(default=None, ge=0)
    filler_window: float = Field(default=1.5, ge=0)
    max_extend_seconds: float = Field(default=8.0, ge=0)
    filler_words: tuple[str, ...] = (
        "so", "um", "uh", "like", "you", "know", "and", "well", "okay", "right",
        "yeah", "i", "mean", "just", "basically", "actually",
    )


class QAConfig(StrictModel):
    """Thresholds for the gate on rendered clips.

    Note `max_silence_ratio` is deliberately looser than
    `candidates.max_silence_ratio` even though both are "silence". They measure
    different things: the candidate filter estimates silence from gaps between
    word timings (cheap, runs on every candidate before scoring), while this
    measures acoustic silence in the rendered audio. The acoustic measure reads
    systematically higher -- about 8 points on the speech fixtures -- so an
    equal threshold here would reject clips the filter had already passed,
    wasting a full render each time.
    """

    min_face_ratio: Unit = 0.5
    max_silence_ratio: Unit = 0.35
    max_black_seconds: float = Field(default=0.3, ge=0)

    # Freezes are judged two ways. A *stall* -- the render genuinely broke --
    # shows up as a large fraction of the clip being identical, and fails. A
    # merely still passage (a held shot, a slide, a speaker not moving much) is
    # normal content and only warns. Judging it on absolute seconds alone made
    # a 1.2s still moment in a 50s clip a failure, which is not a defect.
    max_freeze_ratio: Unit = 0.30
    max_freeze_seconds: float = Field(default=3.0, ge=0)
    # A run must last this long to count as frozen at all.
    freeze_min_duration: float = Field(default=1.0, ge=0.1)

    # A gap must last this long to count as dead air rather than a normal
    # inter-sentence pause.
    silence_min_gap: float = Field(default=0.5, ge=0.05)
    min_lufs: float = -18.0
    max_lufs: float = -11.0
    max_true_peak_dbtp: float = -0.5
    max_lead_silence: float = Field(default=0.3, ge=0)
    max_trail_silence: float = Field(default=1.5, ge=0)
    caption_sync_tolerance: float = Field(default=0.25, ge=0)

    @model_validator(mode="after")
    def _check_lufs(self) -> QAConfig:
        if self.min_lufs >= self.max_lufs:
            raise ValueError("qa.min_lufs must be < qa.max_lufs")
        return self


class WatchConfig(StrictModel):
    """The campaign watcher (`clipper watch`). Secrets live in .env."""

    imap_host: str = "imap.gmail.com"
    # Gmail can file forwarded mail as spam; reading it is harmless (read-only).
    folders: list[str] = ["INBOX", "[Gmail]/Spam"]
    lookback_days: int = Field(default=14, ge=1, le=90)
    # Who the campaigns are for; the judge reads this verbatim.
    profile: str = (
        "TikTok clips account (@solomonkeyclips) for TV and movie comedy: sitcom "
        "and film scenes. Also open to creators clipping their own long-form "
        "videos (podcasts, streams, YouTube) where the funny or dramatic moments "
        "stand alone. Not interested in product ads, supplements, AI tools, "
        "crypto or gambling.")
    platforms: list[str] = ["tiktok"]
    # Skip campaigns paying less than this per 1,000 views (when a rate is stated).
    min_rate_per_1k: float = Field(default=1.0, ge=0)
    notify_maybe: bool = True
    ntfy_server: str = "https://ntfy.sh"


class Config(StrictModel):
    """The whole of `config/default.yaml`."""

    transcription: TranscriptionConfig = TranscriptionConfig()
    candidates: CandidatesConfig = CandidatesConfig()
    weights: WeightsConfig = WeightsConfig()
    selection: SelectionConfig = SelectionConfig()
    llm: LLMConfig = LLMConfig()
    render: RenderConfig = RenderConfig()
    refine: RefineConfig = RefineConfig()
    qa: QAConfig = QAConfig()
    watch: WatchConfig = WatchConfig()

    @classmethod
    def load(cls, path: Path | None = None) -> Config:
        """Load from YAML, falling back to all-defaults if the file is absent."""
        path = path or DEFAULT_CONFIG_PATH
        if not path.exists():
            return cls()
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return cls.model_validate(data)


# --------------------------------------------------------------------------
# campaign config
# --------------------------------------------------------------------------


class DurationBounds(StrictModel):
    min_seconds: float = Field(default=15.0, gt=0)
    max_seconds: float = Field(default=60.0, gt=0)

    @model_validator(mode="after")
    def _check(self) -> DurationBounds:
        if self.min_seconds >= self.max_seconds:
            raise ValueError("duration.min_seconds must be < max_seconds")
        return self


class BrandMentions(StrictModel):
    required: bool = False
    terms: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _check(self) -> BrandMentions:
        if self.required and not self.terms:
            raise ValueError("brand_mentions.required is true but no terms were given")
        return self


class EditPermissions(StrictModel):
    """Per edit class: True allowed, False forbidden, None = the default (campaign/edits.py).

    Classes follow the retention research (D59): container edits (crop, head and
    tail trim, loudness) are always allowed and so are not listed.
    """

    captions: bool | None = None         # burned-in transcript captions
    added_text: bool | None = None       # hook line, labels
    internal_cuts: bool | None = None    # pauses, fillers, stutters inside a clip
    re_edit: bool | None = None          # removing or reordering lines, montage
    visual_effects: bool | None = None   # punch-in zooms, brightness correction
    audio_additions: bool | None = None  # music, sound effects
    overlays: bool | None = None         # b-roll, stickers, reaction cams


#: Where a campaign's clips can be posted (CampaignConfig.platform_targets).
PLATFORMS = ("tiktok", "instagram_reels", "youtube_shorts", "x")
#: Every platform's name as shown, under both its account key ("instagram") and
#: its campaign target ("instagram_reels"), plus the ones a post link can be from.
PLATFORM_NAMES = {"tiktok": "TikTok", "instagram": "Instagram", "instagram_reels": "Instagram",
                  "youtube": "YouTube", "youtube_shorts": "YouTube", "x": "X",
                  "facebook": "Facebook", "snapchat": "Snapchat", "threads": "Threads"}


class CaptionRule(StrictModel):
    """Text a brief says a post must carry, or must not (D81): "On YouTube, tag
    @JoshThomasChannel in the title" is include "@JoshThomasChannel" in the
    title on youtube_shorts. Enforced and checked per platform (campaign/rules.py)."""

    text: str
    must: Literal["include", "avoid"] = "include"
    # "title" is YouTube's title field; on a platform without one it means the caption.
    place: Literal["caption", "title"] = "caption"
    platforms: tuple[str, ...] = ()   # empty: every platform the campaign posts on
    quote: str = ""                   # the brief's own words, shown with the check

    @field_validator("text")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("a caption rule needs its text")
        return v.strip()

    @field_validator("platforms")
    @classmethod
    def _known(cls, v: tuple[str, ...]) -> tuple[str, ...]:
        unknown = [p for p in v if p not in PLATFORMS]
        if unknown:
            raise ValueError(f"unknown platform {unknown[0]!r}; use {', '.join(PLATFORMS)}")
        return v


class CampaignConfig(StrictModel):
    """Per-campaign rules. `source_authorization` is the gate on the whole tool."""

    name: str
    source_authorization: str
    # The name shown in the Control Center ("Chad Powers S2"); `name` stays the
    # id, the library folder and the file name, so a rename never moves clips.
    title: str = ""
    # Where the campaign runs: "Content Rewards", "Vyro", ...
    marketplace: str = ""
    # Open-licence campaigns (e.g. Creative Commons): refuse any video whose own
    # listing does not state this licence -- checked before downloading.
    require_license: str | None = None
    # ...or whose uploader is not one of these channels (name or channel id). A
    # CC label on someone else's re-upload grants nothing.
    allowed_channels: list[str] = Field(default_factory=list)
    # Short-form timing for non-scripted sources: prefer 20-45s, open on the
    # first word, end a beat after the last (scripted campaigns always get it).
    short_form_timing: bool = False
    platform_targets: tuple[str, ...] = ("tiktok", "youtube_shorts", "instagram_reels")
    duration: DurationBounds = DurationBounds()
    required_hashtags: tuple[str, ...] = ()
    required_credit_text: str = ""
    burn_credit_in_video: bool = False
    credit_position: CreditPosition = "top_left"
    forbidden_terms: tuple[str, ...] = ()
    mask_profanity_in_captions: bool = True
    # Mask the few words that get a post flagged (explicit sexual terms, slurs,
    # self-harm, hard drugs) in its caption, title and hook; swearing stays (D82).
    censor_flagged_words: bool = True
    brand_mentions: BrandMentions = BrandMentions()
    language: str = "en"
    # Most clips from one video when Clipper decides how many; None = every
    # moment that clears the quality bar (D71).
    max_clips_per_source: int | None = Field(default=None, ge=1)
    # Text the post caption must contain, e.g. a tune-in line a brief requires.
    required_caption_text: str = ""
    # Leave the audio exactly as delivered: no loudness normalisation. For
    # briefs that forbid changing a clip's audio.
    keep_original_audio: bool = False
    # The LLM-written title shown over a clip's first seconds. Off for briefs
    # that forbid edits which could misrepresent the source.
    hook_overlay: bool = True
    # Scripted TV: do not drop scenes for needing earlier context.
    scripted: bool = False
    # Use only `required_hashtags`, dropping the LLM's suggestions. For briefs
    # banning hashtags "not affiliated with this campaign".
    only_required_hashtags: bool = False
    # Captions supplied by the brief, used (in rotation) for a clip the LLM
    # gave no caption -- or for every clip, with `fixed_captions`.
    fallback_captions: tuple[str, ...] = ()
    fixed_captions: bool = False
    # On-screen hook lines supplied by the brief, used in rotation instead of
    # the LLM's. Empty: the LLM writes them.
    hook_texts: tuple[str, ...] = ()
    # What this campaign wants clipped, in the brief's own terms. Added to the
    # scoring prompt, overriding its general preferences: a romance campaign
    # rejects the comedy clips the default rubric favours.
    selection_focus: str = ""
    # A searchable description under the caption line: 2-3 plain sentences on
    # what happens in the clip, written from its transcript (TikTok's own tip:
    # longer descriptions get found more). `description_context` says what the
    # show is and who is in it; `description_keywords` are search terms worth
    # using where they fit. The caption line (with any #ad) stays first, so the
    # disclosure is visible before "more".
    long_description: bool = False
    description_context: str = ""
    description_keywords: tuple[str, ...] = ()
    # What kind of footage this is: sets the editing defaults (scripted scenes
    # keep their own edit; podcasts get pauses and fillers tightened). None:
    # "scripted" when `scripted` is set, else "podcast".
    content_type: Literal["scripted", "podcast", "other"] | None = None
    # Which kinds of edits the brief allows (campaign/edits.py). Unset fields
    # follow the content type's defaults and whatever `brief_rules` forbids.
    edits: EditPermissions = Field(default_factory=lambda: EditPermissions())
    # The brief's own wording about edits ("do not alter", "no jump cuts",
    # "keep the original audio", ...), read for anything it forbids.
    brief_rules: str = ""
    # Every other rule on a post's text -- mentions, per-platform tags, banned
    # words -- added and checked for each platform (campaign/rules.py, D81).
    caption_rules: tuple[CaptionRule, ...] = ()
    # Rules only the poster can follow ("keep likes and comments on"), shown as
    # a checklist when posting.
    posting_rules: tuple[str, ...] = ()
    # Preferred clip length, overriding the content type's default target.
    target_seconds: tuple[float, float] | None = None
    # What the campaign pays, for the Control Center's earnings estimates: per
    # 1,000 views, counted once a post passes the minimum, capped at the maximum.
    reward_per_1k_usd: float | None = None
    min_payout_usd: float | None = None
    max_payout_usd: float | None = None
    campaign_url: str = ""   # the campaign's page on Whop / Vyro
    deadline: str = ""       # YYYY-MM-DD, if the brief gives one
    notes: str = ""

    @field_validator("source_authorization")
    @classmethod
    def _authorization_is_meaningful(cls, v: str) -> str:
        """Refuse to run on a blank or placeholder authorization.

        This is the one hard gate in section 2: clipper will not process footage
        without a written note of what permits it.
        """
        cleaned = v.strip()
        if len(cleaned) < 10:
            raise ValueError(
                "source_authorization must name the campaign, URL, or explicit creator "
                "permission that covers this footage (at least 10 characters)"
            )
        placeholders = {"todo", "tbd", "none", "n/a", "na", "xxx", "test", "placeholder", "unknown"}
        if cleaned.lower().strip(" .!-") in placeholders:
            raise ValueError(
                f"source_authorization is a placeholder ({cleaned!r}). State the real "
                "campaign, URL, or permission covering this footage."
            )
        return cleaned

    @model_validator(mode="after")
    def _burned_credit_needs_text(self) -> CampaignConfig:
        if self.burn_credit_in_video and not self.required_credit_text.strip():
            raise ValueError(
                "burn_credit_in_video is true but required_credit_text is empty; "
                "there is nothing to burn in"
            )
        return self

    @classmethod
    def load(cls, path: Path) -> CampaignConfig:
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"campaign config not found: {path}")
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if "source_authorization" not in data:
            raise ValueError(
                f"{path.name} has no `source_authorization` field. clipper only processes "
                "footage you are authorized to clip; name the campaign, URL, or creator "
                "permission that covers it."
            )
        return cls.model_validate(data)


AUTHORIZATION_REMINDER = (
    "Reminder: clip only footage you are authorized to use. Unauthorized reposting "
    "risks copyright claims and account strikes."
)
