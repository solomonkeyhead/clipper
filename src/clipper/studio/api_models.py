"""The Control Center API's response models (studio/server.py).

The front end's types are generated from these (web/scripts/gen-api.mjs), so
a renamed or removed field breaks its build instead of a page.
"""

from __future__ import annotations

from pydantic import BaseModel

from ..campaign.editor import CampaignForm
from ..config import CaptionRule


class ReasonOption(BaseModel):
    key: str
    label: str


class ReasonGroup(BaseModel):
    """The reasons a rating can give, grouped as learning reads them (learn/feedback.py)."""

    label: str
    tone: str                      # good | bad | edit
    reasons: list[ReasonOption]


class PostPoint(BaseModel):
    """A post's numbers at one sync that changed them."""

    at: str
    views: float | None = None
    avg_watch_s: float | None = None
    skip_rate_pct: float | None = None


class Post(BaseModel):
    platform: str
    account: str
    url: str
    campaign: str
    clip: int
    clip_title: str
    posted_at: str | None = None
    age_hours: float | None = None
    settling: bool = False
    views: float | None = None
    likes: float | None = None
    comments: float | None = None
    shares: float | None = None
    saves: float | None = None
    avg_watch_s: float | None = None
    watched_full_pct: float | None = None
    skip_rate_pct: float | None = None
    x_median: float | None = None
    est_earnings: float | None = None
    submitted_at: str | None = None
    posted_caption: str | None = None   # as it is on the platform, for the proof pack


class Duplicate(BaseModel):
    id: int
    title: str
    how: str                       # "same moment" | "same lines"
    posted_on: list[str]


class RuleCheck(BaseModel):
    name: str
    passed: bool
    detail: str = ""


class PostCopy(BaseModel):
    """What to paste on one platform, with every rule checked (campaign/rules.py)."""

    platform: str          # tiktok | instagram_reels | youtube_shorts | x
    text_name: str = "Caption"  # what that platform's upload page calls `caption`
    title: str = ""        # YouTube's title
    caption: str
    checks: list[RuleCheck]


class BriefProblem(BaseModel):
    """Something the AI check found the post breaking (campaign/audit.py)."""

    rule: str
    platform: str = "all"
    where: str = "caption"
    problem: str = ""
    add: str = ""


class Rules(BaseModel):
    failed: list[str] = []         # the rule checks that fail, one line each
    checked: bool = False          # the AI check has read these exact texts against the brief
    checking: bool = False         # ...and is running for the campaign now
    refused: bool = False          # ...but the AI's content filter wouldn't read it
    brief: list[BriefProblem] = []  # what it found

    @property
    def ok(self) -> bool:
        return not self.failed and not self.brief


class Proof(BaseModel):
    saved_at: str | None = None
    late: bool = False
    passed: int = 0
    total: int = 0
    posted_ok: bool | None = None


class Clip(BaseModel):
    id: int
    campaign: str
    title: str
    hook: str
    caption: str
    duration_s: float | None = None
    source_title: str
    status: str            # effective: a clip with a live post is "posted"
    marked: str            # what the user set
    notes: str
    created_at: str
    file_exists: bool
    video: str
    thumb: str
    posts: list[Post]
    # What the scorer thought (0-10, the rubric's six parts, and its rank among
    # the video's moments), and what the user thought (1-5, with reasons).
    score: float | None = None
    rubric: dict[str, float] = {}
    pool: int | None = None
    pool_rank: int | None = None
    picked_by: str = "unknown"      # auto | hand | unknown
    # The watch pass (signals/visual.py): scores from reading and from watching,
    # how much the picture adds (0-10), and what it showed.
    read: float | None = None
    watched: float | None = None
    visual_payoff: int | None = None
    sees: str = ""
    rating: int | None = None
    reasons: list[str] = []
    # The dispute pack (studio/evidence.py): when the brief was saved, how many of
    # the clip's checks passed, and whether the posted captions meet the rules.
    proof: Proof | None = None
    watching: bool = False         # marked posted; looking for the post every 2 minutes
    duplicates: list[Duplicate] = []  # already-posted clips this one repeats
    # Not posted yet: each platform's text, and the brief's rules checked (D81).
    post_copy: list[PostCopy] = []
    pinned_comment: str = ""       # to pin under the post on every platform (campaign/extras.py)
    rules: Rules | None = None
    rerendering: str | None = None     # queued | rendering: a new hook on its way (D90)
    rerender_error: str | None = None  # why the last re-render failed


class CampaignCounts(BaseModel):
    ready: int = 0
    posted: int = 0
    submitted: int = 0
    skipped: int = 0


class Campaign(BaseModel):
    name: str
    title: str
    marketplace: str = ""
    has_brief: bool
    archived: bool
    auto_post: bool | None
    platforms: list[str]
    reward_per_1k_usd: float | None = None
    max_clips: int | None = None   # most clips per video when Clipper decides; None: no limit
    clips: int
    counts: CampaignCounts
    views: float
    est_earnings: float | None = None
    to_submit: int
    last_post: str | None = None
    campaign_url: str = ""         # where the user submits post links
    posting_rules: list[str] = []  # the brief's rules only the poster can follow


class Brief(BaseModel):
    min_seconds: float
    max_seconds: float
    required_text: str
    hashtags: list[str]
    credit: str
    captions: list[str]
    hook_texts: list[str]
    original_audio: bool
    long_description: bool
    min_payout_usd: float | None = None
    max_payout_usd: float | None = None
    campaign_url: str
    deadline: str
    authorization: str
    notes: str
    caption_rules: list[CaptionRule] = []
    posting_rules: list[str] = []


class CampaignDetail(BaseModel):
    campaign: Campaign
    brief: Brief | None
    clips: list[Clip]


class Metrics(BaseModel):
    est_earnings: float | None
    views: float
    posts: int
    median_views: float | None
    to_submit: int
    ready: int


class SinceLastVisit(BaseModel):
    since: str
    views_gained: float
    new_posts: int
    top_mover: Post | None = None
    top_mover_gain: float = 0


class Home(BaseModel):
    metrics: Metrics
    since: SinceLastVisit | None
    pipeline: CampaignCounts
    first_run: dict[str, bool]


class Account(BaseModel):
    id: str                # the account's token file, for disconnecting
    platform: str
    connected: bool
    handle: str
    health: str            # ok | warn | error
    detail: str
    expires_in_days: float | None = None
    key: str = ""          # "<platform>:<handle>", as its posts and groups know it (D89)
    posts: int = 0
    views: float = 0
    groups: list[str] = []


class AccountGroup(BaseModel):
    id: int | None = None
    name: str
    members: list[str]            # account keys
    campaigns: list[str] = []     # the campaigns it posts for; empty: any


class Band(BaseModel):
    label: str
    clips: int
    avg_rating: float | None = None
    rated: int
    median_views: float | None = None


class Dimension(BaseModel):
    key: str
    label: str
    default: float
    learned: float
    agreement: float | None = None


class ReasonCount(BaseModel):
    key: str
    label: str
    count: int


class Learning(BaseModel):
    active: bool
    rated: int
    unrated: int
    scored_and_rated: int
    agreement: float | None = None
    agreement_verdict: str
    with_views: int
    views_agreement: float | None = None
    views_verdict: str
    rating_vs_views: float | None = None
    bands: list[Band]
    dimensions: list[Dimension]
    reasons: list[ReasonCount]
    edit_problems: list[ReasonCount] = []  # framing, captions, on-screen text: fixed, not learnt (D86)
    outcomes: int = 0                      # posted clips whose views count toward learning
    taste: str
    weights_n: int
    min_for_weights: int
    min_for_agreement: int


class FoundCampaign(BaseModel):
    key: str
    source: str
    name: str
    owner: str
    rate: str
    rate_per_1k_usd: float | None = None
    platforms: list[str]
    budget: str
    deadline: str
    link: str
    fit: str
    why: str
    found_at: str
    dismissed: int
    via: str = "email"


class AlertChannel(BaseModel):
    id: str
    guild_id: str
    guild: str
    name: str
    kind: str = "text"


class WhopFeed(BaseModel):
    id: str
    company: str
    name: str


class Alerts(BaseModel):
    token_set: bool
    watched: list[AlertChannel]
    whop_app: bool = False          # the Whop app's ID is saved
    whop_signed_in: bool = False
    whop_feeds: list[WhopFeed] = []
    checked_at: str
    error: str
    push_set: bool
    every_minutes: int
    profile: str
    min_rate: float


class DiscordBot(BaseModel):
    id: str
    name: str
    content_intent: bool
    invite: str
    channels: list[AlertChannel]


class AlertCheck(BaseModel):
    busy: bool = False
    read: int = 0
    campaigns: int = 0
    new: list[str] = []
    pushed: int = 0
    errors: list[str] = []


class FitCheck(BaseModel):
    ok: bool | None = None
    text: str


class Fit(BaseModel):
    verdict: str                    # good | check | poor
    checks: list[FitCheck]
    per_post_usd: float | None = None
    checked_at: str


class CampaignCheck(BaseModel):
    form: CampaignForm
    fit: Fit


class Setup(BaseModel):
    ai_ready: bool
    ai_backend: str
    ai_detail: str
    keys: dict[str, bool]          # which keys are set (never their values)
    tiktok_app: bool               # TikTok developer app keys present
    tiktok_connect: dict[str, str]
    youtube_app: bool = False      # Google app client ID and secret present


class Status(BaseModel):
    last_synced: str
    syncing: bool
    sync_minutes: int
    problems: list[str]
    auto_post: bool
    accounts: list[Account]
