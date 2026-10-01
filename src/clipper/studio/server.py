"""The Control Center's web server: `clipper studio` (http://127.0.0.1:8765).

Local only -- it binds to 127.0.0.1 and serves one user's library. A typed JSON
API under /api (its OpenAPI schema generates the front end's types), live
updates over Server-Sent Events at /api/events, and the built single-page app
(studio/web -> studio/static) for every other path.

While it runs it syncs TikTok and Instagram every `sync_minutes` (15 by
default), snapshots every post's numbers, and tells open pages to refresh.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
import statistics
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import yaml
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ..campaign import editor
from ..campaign.editor import CampaignError, CampaignForm
from ..config import CampaignConfig
from ..learn import log as perf
from ..paths import REPO_ROOT
from ..utils.logging import get_logger
from . import alerts, db, evidence, library, setup, stats
from .events import Broker

log = get_logger(__name__)

STATIC = Path(__file__).parent / "static"
HOST, PORT = "127.0.0.1", 8765
HEARTBEAT_SECONDS = 15
SESSION_GAP_MINUTES = 30
#: After "Mark posted", sync every FAST_SYNC_SECONDS for this long, so the post's
#: link is ready while campaigns still accept it (a 30-minute window is reported).
WATCH_FOR = timedelta(minutes=30)
FAST_SYNC_SECONDS = 120


# --------------------------------------------------------------------------
# Response models (the front end's types are generated from these)
# --------------------------------------------------------------------------

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


PLATFORM_NAMES = {"tiktok": "TikTok", "instagram": "Instagram", "youtube": "YouTube"}


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
    taste: str
    weights_n: int
    min_for_weights: int
    min_for_agreement: int
    reason_labels: dict[str, str]


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


# --------------------------------------------------------------------------
# Reading the library
# --------------------------------------------------------------------------

def campaigns_dir() -> Path:
    return REPO_ROOT / "campaigns"


def load_campaigns() -> dict[str, CampaignConfig]:
    out = {}
    for path in sorted(campaigns_dir().glob("*.yaml")):
        if path.name == "example.yaml":  # the template, not a campaign
            continue
        try:
            campaign = CampaignConfig.load(path)
        except (ValueError, OSError, yaml.YAMLError) as exc:  # one bad file mustn't hide the rest
            log.warning("skipping %s: %s", path.name, exc)
            continue
        out[campaign.name] = campaign
    return out


_TECH = re.compile(r"^(pix|rec\d+|hd|uhd|sdr|hdr|prores|h26[45]|\d+fps|\d{3,4}p|\d{8})$",
                   re.IGNORECASE)


def display_source(title: str) -> str:
    """A source's name without the delivery-file suffix: "ChadPowers_206_pix_rec709_..."
    reads "ChadPowers 206"."""
    tokens = re.split(r"[_\s]+", (title or "").strip())
    kept = []
    for token in tokens:
        if _TECH.match(token):
            break
        kept.append(token)
    return " ".join(kept) or title


def effective_status(clip: dict, posts: list) -> str:
    """A clip with a live post is posted, whatever it was marked; later states stay."""
    if clip["status"] == "ready" and posts:
        return "posted"
    return clip["status"]


class Snapshot:
    """Everything the API reads, gathered once per request."""

    def __init__(self) -> None:
        self.campaigns = load_campaigns()
        self.rows = perf.read()
        found = stats.posts_by_clip(self.rows)
        with db.connect() as con:
            self.raw_clips = db.clips(con)
            self.state = db.campaign_state(con)
            self.settings = db.settings(con)
            self.submitted = db.submitted(con)
        now = datetime.now()
        self.clips: list[Clip] = []
        all_posts: list[dict] = []
        pending: list[tuple[dict, list[dict]]] = []
        for c in self.raw_clips:
            posts = [{**p, "campaign": c["campaign"], "clip": c["id"], "clip_title": c["title"]}
                     for p in found.get((c["campaign"], c["source_id"], c["clip_id"]), [])]
            all_posts += posts
            pending.append((c, posts))
        medians = stats.medians(all_posts)
        for c, posts in pending:
            brief = self.campaigns.get(c["campaign"])
            models = []
            for p in posts:
                age = stats.age_hours(p.get("posted_at"), now)
                settling = age is not None and age < stats.SETTLING_HOURS.get(p["platform"], 0)
                if settling:
                    # Instagram reports 0 for watch time and skip rate until it has
                    # computed them; a fresh reel's "0s" is "not yet", not zero.
                    for name in ("avg_watch_s", "skip_rate_pct"):
                        if not p.get(name):
                            p[name] = None
                median = medians.get((p["platform"], p["campaign"]))
                views = p.get("views_latest")
                models.append(Post(
                    platform=p["platform"], account=p["account"], url=p["url"],
                    campaign=p["campaign"], clip=p["clip"], clip_title=p["clip_title"],
                    posted_at=p.get("posted_at"), age_hours=age, settling=settling,
                    posted_caption=p.get("posted_caption"),
                    views=views, likes=p.get("likes"), comments=p.get("comments"),
                    shares=p.get("shares"), saves=p.get("saves"),
                    avg_watch_s=p.get("avg_watch_s"), watched_full_pct=p.get("watched_full_pct"),
                    skip_rate_pct=p.get("skip_rate_pct"),
                    x_median=round(views / median, 1) if median and views is not None else None,
                    est_earnings=stats.estimate_earnings(
                        views, brief.reward_per_1k_usd if brief else None,
                        brief.min_payout_usd if brief else None,
                        brief.max_payout_usd if brief else None),
                    submitted_at=self.submitted.get(p["url"])))
            status = effective_status(c, models)
            if models and status in ("posted", "submitted"):
                # A post found after the clip was marked submitted still needs submitting.
                status = "submitted" if all(m.submitted_at for m in models) else "posted"
            scores = json.loads(c.get("scores") or "{}")
            self.clips.append(Clip(
                score=scores.get("score"), rubric=scores.get("rubric") or {},
                pool=scores.get("pool"), pool_rank=scores.get("pool_rank"),
                picked_by=scores.get("picked_by") or "unknown", rating=c.get("rating"),
                read=scores.get("read"), watched=scores.get("watched"),
                visual_payoff=scores.get("visual_payoff"), sees=scores.get("sees") or "",
                reasons=json.loads(c.get("reasons") or "[]"),
                proof=Proof(**evidence.summary(json.loads(c.get("evidence") or "null"),
                                               [m.model_dump() for m in models])),
                watching=bool(c.get("watch_until") and c["watch_until"] > now.strftime("%Y-%m-%d %H:%M")),
                id=c["id"], campaign=c["campaign"], title=c["title"] or c["clip_id"],
                hook=c["hook"], caption=c["caption"], duration_s=c["duration_s"],
                source_title=display_source(c["source_title"]), status=status,
                marked=c["status"], notes=c["notes"], created_at=c["created_at"],
                file_exists=library.clip_path(c["file"]).exists(),
                video=f"/media/{c['id']}", thumb=f"/thumb/{c['id']}", posts=models))
        # Before posting: which already-posted clips each one repeats (studio/duplicates.py).
        from . import duplicates

        posted = {c.id: [f"{PLATFORM_NAMES.get(p.platform, p.platform)}"
                         + (f" @{p.account.lstrip('@')}" if p.account else "") for p in c.posts]
                  or (["posted"] if c.status in ("posted", "submitted") else [])
                  for c in self.clips}
        found = duplicates.find(self.raw_clips, posted)
        for clip in self.clips:
            clip.duplicates = [Duplicate(**d) for d in found.get(clip.id, [])]

    @property
    def posts(self) -> list[Post]:
        return [p for c in self.clips for p in c.posts]

    def campaign(self, name: str) -> Campaign:
        brief = self.campaigns.get(name)
        mine = [c for c in self.clips if c.campaign == name]
        posts = [p for c in mine for p in c.posts]
        counts = CampaignCounts(**{s: sum(1 for c in mine if c.status == s) for s in db.STATUSES})
        earnings = [p.est_earnings for p in posts if p.est_earnings is not None]
        st = self.state.get(name, {})
        auto = st.get("auto_post")
        return Campaign(
            name=name, title=editor.title_of(brief) if brief else editor.pretty(name),
            marketplace=brief.marketplace if brief else "", has_brief=brief is not None, archived=bool(st.get("archived")),
            auto_post=None if auto is None else bool(auto),
            platforms=list(brief.platform_targets) if brief else [],
            reward_per_1k_usd=brief.reward_per_1k_usd if brief else None,
            max_clips=brief.max_clips_per_source if brief else None,
            campaign_url=brief.campaign_url if brief else "",
            clips=len(mine), counts=counts,
            views=sum(p.views or 0 for p in posts),
            est_earnings=round(sum(earnings), 2) if earnings else None,
            to_submit=sum(1 for p in posts if not p.submitted_at),
            last_post=max((p.posted_at for p in posts if p.posted_at), default=None))

    def campaign_names(self) -> list[str]:
        return sorted(set(self.campaigns) | {c.campaign for c in self.clips})


def brief_of(campaign: CampaignConfig) -> Brief:
    return Brief(
        min_seconds=campaign.duration.min_seconds, max_seconds=campaign.duration.max_seconds,
        required_text=campaign.required_caption_text, hashtags=list(campaign.required_hashtags),
        credit=campaign.required_credit_text, captions=list(campaign.fallback_captions),
        hook_texts=list(campaign.hook_texts), original_audio=campaign.keep_original_audio,
        long_description=campaign.long_description, min_payout_usd=campaign.min_payout_usd,
        max_payout_usd=campaign.max_payout_usd, campaign_url=campaign.campaign_url,
        deadline=campaign.deadline, authorization=campaign.source_authorization,
        notes=campaign.notes)


def accounts() -> list[Account]:
    """Every connected account's health, from its stored token."""
    out = []
    from ..instagram import api as ig_api
    from ..tiktok import api as tt_api

    tiktoks = tt_api.token_files()
    for path in tiktoks:
        token = tt_api.read_token(path)
        if not token.get("handle") and len(tiktoks) == 1:
            # Learnt on the next sync; until then, the handle in its posts' links.
            token["handle"] = next((stats.handle_from_url(r.get("url", "")) for r in perf.read()
                                    if "tiktok.com/@" in (r.get("url") or "")), "")
        left = (float(token.get("obtained_at", 0)) + float(token.get("refresh_expires_in", 0))
                - time.time()) / 86400
        out.append(Account(
            id=path.stem, platform="tiktok", connected=left > 0,
            handle=token.get("handle") or token.get("display_name") or "",
            health="ok" if left > 7 else "warn" if left > 0 else "error",
            detail=("Stats: views, likes, comments, shares. TikTok's API gives no watch time."
                    if left > 0 else "Login expired: connect it again with Add a TikTok account"),
            expires_in_days=round(left, 1)))
    for path in ig_api.token_files():
        token = ig_api.read_token(path)
        left = (float(token.get("obtained_at", 0)) + ig_api.TOKEN_LIFETIME - time.time()) / 86400
        out.append(Account(
            id=path.stem, platform="instagram", connected=left > 0,
            handle=token.get("username", ""),
            health="ok" if left > 7 else "warn" if left > 0 else "error",
            detail=("Stats: views, watch time, 3-second skip rate, saves. Renews itself."
                    if left > 0 else "Token expired: paste a new one with Add an Instagram account"),
            expires_in_days=round(left, 1)))
    from ..youtube import api as yt_api

    for path in yt_api.token_files():
        # Google's refresh tokens don't expire on a clock (in production); a
        # revoked or lapsed one shows up as a sync problem instead.
        out.append(Account(
            id=path.stem, platform="youtube", connected=True, handle=yt_api.name(path), health="ok",
            detail="Stats: views, likes, comments, and a day or two later watch time and shares.",
            expires_in_days=None))
    return out


# --------------------------------------------------------------------------
# The app
# --------------------------------------------------------------------------

VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".m4v", ".webm", ".avi"}


def source_folders() -> list[Path]:
    """Where footage for new clips is looked for: Clipper's downloads, then yours."""
    from ..paths import downloads_dir

    return [downloads_dir(), Path.home() / "Downloads"]


def list_sources() -> list[dict]:
    seen, out = set(), []
    for folder in source_folders():
        if not folder.is_dir():
            continue
        for path in folder.iterdir():
            if path.suffix.lower() in VIDEO_EXTENSIONS and path.is_file():
                key = path.resolve()
                if key in seen:
                    continue
                seen.add(key)
                stat = path.stat()
                out.append({"name": path.name, "path": str(key),
                            "size_mb": round(stat.st_size / 1_048_576, 1),
                            "modified": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M"),
                            "folder": "Clipper uploads" if folder == source_folders()[0] else "Downloads"})
    return sorted(out, key=lambda s: s["modified"], reverse=True)


def allowed_source(path: str) -> Path:
    """A source path the page may clip: an existing video in one of the source folders."""
    target = Path(path).resolve()
    if (target.suffix.lower() not in VIDEO_EXTENSIONS or not target.is_file()
            or not any(folder.resolve() in target.parents for folder in source_folders()
                       if folder.is_dir())):
        raise HTTPException(400, "pick a video from the list, or upload one")
    return target


def purge_trash() -> int:
    """Clips in the trash for 30 days go to the Windows Recycle Bin."""
    from ..utils.recycle import recycle

    purged = 0
    with db.connect() as con:
        for clip in db.expired_trash(con):
            if recycle(library.clip_path(clip["file"])):
                db.forget(con, clip["id"])
                purged += 1
    return purged


def create_app(*, auto_sync: bool = False) -> FastAPI:
    from .jobs import JobRunner

    broker = Broker()
    last_problems: list[str] = []
    jobs = JobRunner(broker.publish)
    tiktok = setup.tiktok_connect(broker.publish)
    youtube = setup.youtube_connect(broker.publish)
    whop_login = setup.whop_connect(lambda *a: broker.publish("alerts.changed"))
    from .imports import ImportRunner

    importer = ImportRunner(broker.publish)

    async def sync_now() -> dict:
        broker.publish("sync.started")
        result = await asyncio.to_thread(stats.run_sync)
        if not result.get("skipped"):
            last_problems[:] = result["problems"]
        broker.publish("stats.synced", {"at": result.get("last_synced", ""),
                                        "problems": result.get("problems", [])})
        return result

    async def sync_loop() -> None:
        while True:
            with db.connect() as con:
                minutes = max(5, int(db.settings(con)["sync_minutes"] or 15))
                watching = con.execute("SELECT 1 FROM clips WHERE watch_until > ? LIMIT 1",
                                       (datetime.now().strftime("%Y-%m-%d %H:%M"),)).fetchone()
            interval = FAST_SYNC_SECONDS if watching else minutes * 60
            synced = stats.last_synced(perf.read())
            due = True
            if synced:
                with contextlib.suppress(ValueError):
                    age = (datetime.now() - datetime.strptime(synced, "%Y-%m-%d %H:%M"))
                    due = age.total_seconds() >= interval
            if due:
                try:
                    await sync_now()
                    if watching:
                        await asyncio.to_thread(stop_watching_found)
                except Exception:
                    log.exception("automatic sync failed")
            await asyncio.sleep(60)

    def stop_watching_found() -> None:
        """Clips whose post has turned up need no more fast syncs."""
        found = [c.id for c in Snapshot().clips if c.watching and c.posts]
        with db.connect() as con:
            for clip_id in found:
                db.update_clip(con, clip_id, watch_until=None)

    def backfill_words() -> None:
        try:
            if library.backfill_text():
                broker.publish("clips.changed")
        except Exception as exc:  # optional; the duplicate check just sees less
            log.info("couldn't transcribe clips for the duplicate check: %s", exc)

    async def alerts_loop() -> None:
        """Campaign alerts from Discord, every few minutes (studio/alerts.py)."""
        while True:
            if alerts.token() and alerts.watched():
                try:
                    await asyncio.to_thread(alerts.check, publish=broker.publish)
                except Exception:
                    log.exception("campaign alert check failed")
            await asyncio.sleep(alerts.CHECK_MINUTES * 60)

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        broker.bind(asyncio.get_running_loop())
        with contextlib.suppress(Exception):  # scores for clips filed before they were kept
            await asyncio.to_thread(library.backfill_scores)
        with contextlib.suppress(Exception):  # and a (late) evidence snapshot
            await asyncio.to_thread(library.backfill_evidence, load_campaigns())
        # Words for clips without them (imported ones), for the duplicate check.
        # In the background: it loads a small Whisper model.
        words = asyncio.create_task(asyncio.to_thread(backfill_words))
        task = asyncio.create_task(sync_loop()) if auto_sync else None
        watcher = asyncio.create_task(alerts_loop()) if auto_sync else None
        if auto_sync:
            with contextlib.suppress(Exception):
                purged = await asyncio.to_thread(purge_trash)
                if purged:
                    log.info("moved %d clip(s) from the 30-day trash to the Recycle Bin", purged)
        yield
        words.cancel()
        for t in (task, watcher):
            if t:
                t.cancel()

    app = FastAPI(title="Clipper Control Center", lifespan=lifespan,
                  docs_url=None, redoc_url=None)

    @app.middleware("http")
    async def same_origin_writes(request: Request, call_next):
        """Only the Control Center's own pages may change things.

        It listens on 127.0.0.1, but any website open in the browser could still
        send it a request; browsers label those with the site's Origin.
        """
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            origin = request.headers.get("origin")
            if origin and origin.split("://", 1)[-1] != request.headers.get("host", ""):
                from fastapi.responses import JSONResponse

                return JSONResponse({"detail": "cross-site request refused"}, status_code=403)
        return await call_next(request)

    @app.get("/api/status")
    def status() -> Status:
        rows = perf.read()
        with db.connect() as con:
            settings = db.settings(con)
        return Status(last_synced=stats.last_synced(rows), syncing=stats.syncing(),
                      sync_minutes=int(settings["sync_minutes"] or 15),
                      problems=list(last_problems), auto_post=settings["auto_post"] == "1",
                      accounts=accounts())

    @app.get("/api/home")
    def home() -> Home:
        snap = Snapshot()
        active = {n for n in snap.campaign_names()
                  if not snap.state.get(n, {}).get("archived")}
        clips = [c for c in snap.clips if c.campaign in active]
        posts = [p for c in clips for p in c.posts]
        earnings = [p.est_earnings for p in posts if p.est_earnings is not None]
        views = [p.views for p in posts if p.views is not None]
        since = None
        last = snap.settings.get("last_visit") or ""
        if last:
            with db.connect() as con:
                before = db.views_at(con, last.replace("T", " "))
            gains = [(p, (p.views or 0) - before.get(p.url, 0)) for p in posts]
            top = max(gains, key=lambda g: g[1], default=None)
            since = SinceLastVisit(
                since=last, views_gained=sum(max(0, g) for _, g in gains),
                new_posts=sum(1 for p in posts if (p.posted_at or "") > last[:16].replace("T", " ")),
                top_mover=top[0] if top and top[1] > 0 else None,
                top_mover_gain=top[1] if top and top[1] > 0 else 0)
        pipeline = CampaignCounts(**{s: sum(1 for c in clips if c.status == s)
                                     for s in db.STATUSES})
        return Home(
            metrics=Metrics(
                est_earnings=round(sum(earnings), 2) if earnings else None,
                views=sum(views), posts=len(posts),
                median_views=statistics.median(views) if views else None,
                to_submit=sum(1 for p in posts if not p.submitted_at),
                ready=pipeline.ready),
            since=since, pipeline=pipeline,
            # The getting-started checklist, in the order a new user does it.
            first_run={"ai": setup.ai_status()["ready"], "campaign": bool(snap.campaigns),
                       "clips": bool(snap.clips),
                       "accounts": any(a.connected for a in accounts())})

    @app.post("/api/visit")
    def visit() -> dict:
        """The page is open and in view (sent on load, every few minutes, and on return).

        A new session starts after SESSION_GAP_MINUTES without one; the previous
        session's last activity becomes "last visit", so "since you were last here"
        covers the time away -- not the seconds since the last page load.
        """
        now = datetime.now()
        with db.connect() as con:
            seen = db.settings(con).get("seen_at") or ""
            if seen:
                with contextlib.suppress(ValueError):
                    gap = now - datetime.strptime(seen, "%Y-%m-%d %H:%M:%S")
                    if gap.total_seconds() >= SESSION_GAP_MINUTES * 60:
                        db.set_setting(con, "last_visit", seen)
            db.set_setting(con, "seen_at", now.strftime("%Y-%m-%d %H:%M:%S"))
        return {"ok": True}

    @app.get("/api/campaigns")
    def campaigns() -> list[Campaign]:
        snap = Snapshot()
        return [snap.campaign(n) for n in snap.campaign_names()]

    @app.get("/api/campaigns/{name}")
    def campaign(name: str) -> CampaignDetail:
        snap = Snapshot()
        if name not in snap.campaign_names():
            raise HTTPException(404, f"no campaign {name!r}")
        brief = snap.campaigns.get(name)
        return CampaignDetail(campaign=snap.campaign(name),
                              brief=brief_of(brief) if brief else None,
                              clips=[c for c in snap.clips if c.campaign == name])

    @app.patch("/api/campaigns/{name}")
    def update_campaign(name: str, changes: dict) -> dict:
        with db.connect() as con:
            db.set_campaign_state(con, name, **changes)
        broker.publish("campaigns.changed")
        return {"ok": True}

    @app.get("/api/campaigns/{name}/form")
    def campaign_form(name: str) -> CampaignForm:
        campaign = load_campaigns().get(name)
        if campaign is None:
            raise HTTPException(404, f"no campaign {name!r}")
        return editor.to_form(campaign)

    @app.post("/api/campaigns")
    def create_campaign(form: CampaignForm) -> dict:
        try:
            campaign = editor.create(campaigns_dir(), form)
        except CampaignError as exc:
            raise HTTPException(400, str(exc)) from exc
        broker.publish("campaigns.changed")
        return {"name": campaign.name}

    @app.put("/api/campaigns/{name}")
    def edit_campaign(name: str, form: CampaignForm) -> dict:
        try:
            campaign = editor.update(campaigns_dir(), name, form)
        except CampaignError as exc:
            raise HTTPException(400, str(exc)) from exc
        broker.publish("campaigns.changed")
        return {"name": campaign.name}

    @app.delete("/api/campaigns/{name}")
    def delete_campaign(name: str) -> dict:
        """Only a campaign with no clips; one with clips is archived instead."""
        from ..utils.recycle import recycle

        with db.connect() as con:
            if con.execute("SELECT 1 FROM clips WHERE campaign=? LIMIT 1", (name,)).fetchone():
                raise HTTPException(400, "this campaign has clips; archive it instead")
        path = editor.path_for(campaigns_dir(), name)
        if path is None:
            raise HTTPException(404, f"no campaign {name!r}")
        if not recycle(path):
            raise HTTPException(500, "could not move the file to the Recycle Bin")
        broker.publish("campaigns.changed")
        return {"ok": True}

    @app.post("/api/campaigns/read-brief")
    def read_brief(body: dict) -> CampaignForm:
        """Fill the New campaign form from a pasted brief (the configured AI model)."""
        from ..config import Config
        from ..pipeline import build_backend

        try:
            backend = build_backend(Config.load())
        except Exception as exc:  # no key, backend not installed
            raise HTTPException(400, f"AI isn't set up ({str(exc).splitlines()[0]}). "
                                     "Add your AI key in Settings, or fill the form in "
                                     "yourself.") from exc
        try:
            return editor.read_brief(str(body.get("text") or ""), backend)
        except CampaignError as exc:
            raise HTTPException(400, str(exc)) from exc
        except Exception as exc:  # the model is busy / refused
            log.warning("reading a brief failed: %s", exc)
            raise HTTPException(503, "the AI model didn't answer (it may be busy). Try again "
                                     "in a minute, or fill the form in yourself.") from exc

    def check_brief(text: str) -> CampaignCheck:
        from ..config import Config
        from ..pipeline import build_backend
        from . import finder

        try:
            backend = build_backend(Config.load())
        except Exception as exc:
            raise HTTPException(400, "Checking a campaign needs your AI key: add it in Settings.") from exc
        try:
            form = editor.read_brief(text, backend)
        except CampaignError as exc:
            raise HTTPException(400, str(exc)) from exc
        except Exception as exc:
            log.warning("campaign check failed: %s", exc)
            raise HTTPException(503, "The AI didn't answer (it may be busy). Try again in a minute.") from exc
        return CampaignCheck(form=form, fit=Fit(**finder.fit(form, Snapshot(), accounts())))

    @app.post("/api/campaigns/check")
    async def check_campaign(body: dict) -> CampaignCheck:
        """Read a pasted campaign brief and say how well it fits this user."""
        return await asyncio.to_thread(check_brief, str(body.get("text") or ""))

    @app.get("/api/found")
    def found_campaigns() -> list[FoundCampaign]:
        from . import finder

        return [FoundCampaign(**f) for f in finder.found()]

    @app.post("/api/found/{key}/check")
    async def check_found(key: str) -> CampaignCheck:
        from . import finder

        brief = finder.brief_of(key)
        if brief is None:
            raise HTTPException(404, "no such campaign")
        return await asyncio.to_thread(check_brief, brief)

    @app.get("/api/alerts")
    def get_alerts() -> Alerts:
        return Alerts(**alerts.status())

    @app.get("/api/alerts/discord")
    async def discord_bot() -> DiscordBot:
        """The bot behind the token, and every channel it can read (asks Discord)."""
        from ..watch import discord

        if not alerts.token():
            raise HTTPException(400, "Add your bot's token first")
        try:
            bot = await asyncio.to_thread(discord.bot, alerts.token())
            channels = await asyncio.to_thread(discord.channels, alerts.token())
        except discord.DiscordError as exc:
            raise HTTPException(400, str(exc)) from exc
        return DiscordBot(**bot, channels=[AlertChannel(**c) for c in channels])

    @app.put("/api/alerts/channels")
    async def watch_channels(body: dict) -> Alerts:
        from ..watch import discord

        ids = [str(i) for i in body.get("ids") or []]
        try:
            available = await asyncio.to_thread(discord.channels, alerts.token()) if ids else []
            alerts.watch(ids, available)
        except (discord.DiscordError, ValueError) as exc:
            raise HTTPException(400, str(exc)) from exc
        broker.publish("alerts.changed")
        return get_alerts()

    @app.put("/api/alerts/prefs")
    def alert_prefs(body: dict) -> Alerts:
        try:
            alerts.set_prefs(str(body.get("profile") or ""), float(body.get("min_rate") or 0))
        except (TypeError, ValueError) as exc:
            raise HTTPException(400, str(exc)) from exc
        broker.publish("alerts.changed")
        return get_alerts()

    @app.post("/api/alerts/check")
    async def check_alerts() -> AlertCheck:
        """Check the watched channels now, rather than at the next scheduled check."""
        result = await asyncio.to_thread(alerts.check, publish=broker.publish)
        broker.publish("alerts.changed")
        return AlertCheck(**result)

    @app.get("/api/alerts/whop")
    async def whop_available() -> list[WhopFeed]:
        """Forum feeds in the Whop communities the user belongs to (asks Whop)."""
        from ..watch import whop

        try:
            return [WhopFeed(**f) for f in await asyncio.to_thread(whop.feeds)]
        except whop.WhopError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.put("/api/alerts/whop/feeds")
    async def watch_whop(body: dict) -> Alerts:
        from ..watch import whop

        ids = [str(i) for i in body.get("ids") or []]
        try:
            available = await asyncio.to_thread(whop.feeds) if ids else []
            alerts.whop_watch(ids, available)
        except (whop.WhopError, ValueError) as exc:
            raise HTTPException(400, str(exc)) from exc
        broker.publish("alerts.changed")
        return get_alerts()

    @app.post("/api/alerts/whop/connect")
    def whop_connect() -> dict[str, str]:
        if not setup.key_set("WHOP_CLIENT_ID"):
            raise HTTPException(400, "Save your Whop app's ID first")
        return whop_login.start()

    @app.get("/api/alerts/whop/connect")
    def whop_connect_state() -> dict[str, str]:
        return whop_login.view()

    @app.post("/api/alerts/whop/disconnect")
    def whop_disconnect() -> Alerts:
        from ..watch import whop

        whop.sign_out()
        alerts.whop_watch([], [])
        broker.publish("alerts.changed")
        return get_alerts()

    @app.post("/api/alerts/test-push")
    async def alert_test_push() -> dict:
        from ..watch import notify

        try:
            await asyncio.to_thread(alerts.test_push)
        except (ValueError, notify.PushError) as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"ok": True}

    @app.post("/api/found/{key}/dismiss")
    def dismiss_found(key: str) -> dict:
        from . import finder

        finder.dismiss(key)
        return {"ok": True}

    @app.get("/api/clips")
    def clips(campaign: str | None = None) -> list[Clip]:
        return [c for c in Snapshot().clips if campaign is None or c.campaign == campaign]

    @app.get("/api/clips/{clip_id}")
    def clip(clip_id: int) -> Clip:
        found = next((c for c in Snapshot().clips if c.id == clip_id), None)
        if found is None:
            raise HTTPException(404, "no such clip")
        return found

    @app.patch("/api/clips/{clip_id}")
    def update_clip(clip_id: int, changes: dict) -> dict:
        with db.connect() as con:
            if db.clip(con, clip_id) is None:
                raise HTTPException(404, "no such clip")
            try:
                # The page edits what the user owns; captions and ranges come from runs.
                allowed = {k: v for k, v in changes.items() if k in ("status", "notes", "title")}
                if allowed.get("status") == "posted":
                    allowed["watch_until"] = (datetime.now() + WATCH_FOR).strftime("%Y-%m-%d %H:%M")
                elif "status" in allowed:
                    allowed["watch_until"] = None
                db.update_clip(con, clip_id, **allowed)
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from exc
        broker.publish("clips.changed", {"id": clip_id})
        return {"ok": True}

    @app.get("/api/posts")
    def posts(campaign: str | None = None) -> list[Post]:
        return [p for p in Snapshot().posts if campaign is None or p.campaign == campaign]

    @app.put("/api/clips/{clip_id}/submitted")
    def clip_submitted(clip_id: int, changes: dict) -> dict:
        """Mark a clip submitted (or not): its status, and every post link it has."""
        submitted = bool(changes.get("submitted", True))
        found = next((c for c in Snapshot().clips if c.id == clip_id), None)
        if found is None:
            raise HTTPException(404, "no such clip")
        with db.connect() as con:
            for post in found.posts:
                db.set_submitted(con, post.url.split("?", 1)[0], submitted)
            if submitted:
                db.update_clip(con, clip_id, status="submitted")
            elif found.marked == "submitted":
                db.update_clip(con, clip_id, status="posted" if found.posts else "ready")
        broker.publish("clips.changed", {"id": clip_id})
        return {"ok": True}

    @app.put("/api/clips/{clip_id}/rating")
    def rate_clip(clip_id: int, body: dict) -> dict:
        """The user's 1-5 verdict on a clip, and why; what the learner learns from."""
        rating = body.get("rating")
        with db.connect() as con:
            if db.clip(con, clip_id) is None:
                raise HTTPException(404, "no such clip")
            try:
                db.set_rating(con, clip_id, None if rating is None else int(rating),
                              [str(r) for r in body.get("reasons") or []])
            except (ValueError, TypeError) as exc:
                raise HTTPException(400, str(exc)) from exc
        broker.publish("clips.changed", {"id": clip_id})
        return {"ok": True}

    @app.get("/api/learning")
    def learning() -> Learning:
        from ..config import Config
        from ..learn import feedback

        snap = Snapshot()
        views = {c.id: sum(p.views or 0 for p in c.posts) for c in snap.clips if c.posts}
        with db.connect() as con:
            active = db.settings(con).get("learn_from_feedback", "1") == "1"
        clips = feedback.from_rows(snap.raw_clips, views)
        result = feedback.report(clips, Config.load().llm.rubric_weights.as_dict(), active=active)
        return Learning(active=active, min_for_weights=feedback.MIN_FOR_WEIGHTS,
                        min_for_agreement=feedback.MIN_FOR_AGREEMENT,
                        reason_labels=db.REASONS, **result.__dict__)

    @app.get("/api/posts/history")
    def post_history(url: str) -> list[dict]:
        with db.connect() as con:
            return db.history(con, url.split("?", 1)[0])

    @app.post("/api/sync")
    async def sync() -> dict:
        return await sync_now()

    @app.get("/api/accounts")
    def get_accounts() -> list[Account]:
        return accounts()

    @app.delete("/api/accounts/{platform}/{account}")
    def disconnect(platform: str, account: str) -> dict:
        from ..instagram import api as ig_api
        from ..tiktok import api as tt_api
        from ..youtube import api as yt_api

        module = {"tiktok": tt_api, "instagram": ig_api, "youtube": yt_api}.get(platform)
        if module is None or not module.remove(account):
            raise HTTPException(404, "no such account")
        broker.publish("accounts.changed")
        return {"ok": True}

    @app.post("/api/accounts/tiktok/connect")
    def tiktok_connect() -> dict[str, str]:
        """Start TikTok's login; the page opens the returned consent link."""
        return tiktok.start()

    @app.get("/api/accounts/tiktok/connect")
    def tiktok_connect_state() -> dict[str, str]:
        return tiktok.view()

    @app.post("/api/accounts/youtube/connect")
    def youtube_connect() -> dict[str, str]:
        """Start Google's sign-in; the page opens the returned consent link."""
        if not (setup.key_set("YOUTUBE_CLIENT_ID") and setup.key_set("YOUTUBE_CLIENT_SECRET")):
            raise HTTPException(400, "Save your Google app's client ID and secret first")
        return youtube.start()

    @app.get("/api/accounts/youtube/connect")
    def youtube_connect_state() -> dict[str, str]:
        return youtube.view()

    @app.post("/api/accounts/instagram")
    def instagram_connect(body: dict) -> dict:
        from ..instagram import api as ig_api

        try:
            username = ig_api.login(str(body.get("token") or ""))
        except ig_api.InstagramError as exc:
            raise HTTPException(400, f"Instagram refused the token: {exc}") from exc
        broker.publish("accounts.changed")
        return {"username": username}

    @app.get("/api/setup")
    def get_setup() -> Setup:
        ai = setup.ai_status()
        return Setup(ai_ready=ai["ready"], ai_backend=ai["backend"], ai_detail=ai["detail"],
                     keys={k: setup.key_set(k) for k in setup.KEYS},
                     tiktok_app=setup.key_set("TIKTOK_CLIENT_KEY")
                     and setup.key_set("TIKTOK_CLIENT_SECRET"),
                     tiktok_connect=tiktok.view(),
                     youtube_app=setup.key_set("YOUTUBE_CLIENT_ID") and setup.key_set("YOUTUBE_CLIENT_SECRET"))

    @app.put("/api/setup/keys")
    def put_keys(values: dict[str, str]) -> Setup:
        try:
            setup.set_keys(values)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        broker.publish("settings.changed")
        return get_setup()

    @app.post("/api/setup/test-ai")
    async def test_ai() -> dict:
        ok, detail = await asyncio.to_thread(setup.test_ai)
        return {"ok": ok, "detail": detail}

    @app.post("/api/setup/check")
    async def system_check() -> list[dict]:
        return await asyncio.to_thread(setup.system_check)

    @app.get("/api/settings")
    def get_settings() -> dict[str, str]:
        with db.connect() as con:
            return db.settings(con)

    @app.put("/api/settings")
    def update_settings(changes: dict) -> dict[str, str]:
        with db.connect() as con:
            try:
                for key, value in changes.items():
                    db.set_setting(con, key, value)
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from exc
            result = db.settings(con)
        broker.publish("settings.changed")
        return result

    @app.get("/api/events")
    async def events(request: Request) -> StreamingResponse:
        queue = broker.subscribe()

        async def stream():
            try:
                yield "retry: 3000\n\n"
                while not await request.is_disconnected():
                    try:
                        event_id, event, data = await asyncio.wait_for(
                            queue.get(), timeout=HEARTBEAT_SECONDS)
                    except TimeoutError:
                        yield ": keep-alive\n\n"
                        continue
                    yield f"id: {event_id}\nevent: {event}\ndata: {data}\n\n"
            finally:
                broker.unsubscribe(queue)

        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.post("/api/clips/{clip_id}/reveal")
    def reveal(clip_id: int) -> dict:
        """Show the clip's file in Explorer, selected, e.g. to drag it into an upload page."""
        with db.connect() as con:
            found = db.clip(con, clip_id)
        if found is None:
            raise HTTPException(404, "no such clip")
        path = library.clip_path(found["file"]).resolve()
        if not path.exists():
            raise HTTPException(404, "the file is missing from the library")
        if sys.platform == "win32":
            # One string, path in quotes: passed as a list, Python quotes the
            # whole "/select,..." argument, which Explorer can't parse when the
            # name has spaces or brackets -- it opened Documents instead.
            subprocess.Popen(f'explorer /select,"{path}"')
        return {"path": str(path)}

    @app.delete("/api/clips/{clip_id}")
    def delete_clip(clip_id: int) -> dict:
        """To the trash: hidden at once, the file kept 30 days, then recycled."""
        with db.connect() as con:
            if db.clip(con, clip_id) is None:
                raise HTTPException(404, "no such clip")
            db.trash(con, clip_id, True)
        broker.publish("clips.changed", {"id": clip_id})
        return {"ok": True}

    @app.post("/api/clips/{clip_id}/restore")
    def restore_clip(clip_id: int) -> dict:
        with db.connect() as con:
            if db.clip(con, clip_id) is None:
                raise HTTPException(404, "no such clip")
            db.trash(con, clip_id, False)
        broker.publish("clips.changed", {"id": clip_id})
        return {"ok": True}

    @app.get("/api/sources")
    def sources() -> list[dict]:
        return list_sources()

    @app.put("/api/uploads/{filename}")
    async def upload(filename: str, request: Request) -> dict:
        """Stream an uploaded video into Clipper's downloads folder (no size limit)."""
        from ..paths import downloads_dir, ensure

        name = re.sub(r"[^\w .()\-]+", "_", Path(filename).name).strip() or "upload.mp4"
        if Path(name).suffix.lower() not in VIDEO_EXTENSIONS:
            raise HTTPException(400, "only video files (mp4, mov, mkv, m4v, webm, avi)")
        target = ensure(downloads_dir()) / name
        partial = target.with_name(target.name + ".part")
        with partial.open("wb") as out:
            async for chunk in request.stream():
                out.write(chunk)
        partial.replace(target)
        return next(s for s in list_sources() if Path(s["path"]) == target.resolve())

    @app.post("/api/imports/inspect")
    async def inspect_link(body: dict) -> dict:
        """What a shared footage link holds, before downloading it."""
        from . import imports

        try:
            found = await asyncio.to_thread(imports.inspect, str(body.get("url") or ""))
        except imports.ImportError_ as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"kind": found.kind, "zipped": found.zipped,
                "files": [{"name": f.name, "size": f.size} for f in found.files]}

    @app.post("/api/imports")
    async def start_import(body: dict) -> dict:
        """Download a shared link's videos into Clipper's uploads, in the background."""
        from . import imports

        url = str(body.get("url") or "")
        try:
            found = await asyncio.to_thread(imports.inspect, url)
        except imports.ImportError_ as exc:
            raise HTTPException(400, str(exc)) from exc
        pick = [str(n) for n in body.get("files") or []]
        return importer.start(url, found, pick or None).view()

    @app.get("/api/imports")
    def list_imports() -> list[dict]:
        return importer.list()

    @app.get("/api/jobs")
    def list_jobs() -> list[dict]:
        return jobs.list()

    @app.post("/api/jobs")
    def start_job(body: dict) -> dict:
        campaign = load_campaigns().get(str(body.get("campaign") or ""))
        if campaign is None:
            raise HTTPException(400, "pick a campaign")
        source = allowed_source(str(body.get("source") or ""))
        mode = str(body.get("mode") or "auto")
        if mode == "manual":
            from ..runner import parse_range

            try:
                ranges = [parse_range(f"{a}-{b}") for a, b in body.get("ranges") or []]
            except (ValueError, TypeError) as exc:
                raise HTTPException(400, str(exc)) from exc
            if not ranges:
                raise HTTPException(400, "add at least one start and end time")
            short = [r for r in ranges if r[1] - r[0] < 3]
            if short:
                raise HTTPException(400, "each moment needs to be at least 3 seconds long")
            return jobs.submit(campaign, str(source), None, ranges).view()
        from . import plans

        top = max(1, min(500, int(body.get("top") or 4))) if mode == "top" else None
        return jobs.submit(campaign, str(source),
                           plans.clip_count(top, campaign.max_clips_per_source)).view()

    @app.get("/api/sources/video")
    def source_video(path: str) -> FileResponse:
        """A source video for the page's player (manual mode), with seeking."""
        target = allowed_source(path)
        kind = {".mp4": "video/mp4", ".m4v": "video/mp4", ".webm": "video/webm",
                ".mov": "video/quicktime"}.get(target.suffix.lower(), "application/octet-stream")
        return FileResponse(target, media_type=kind)

    @app.get("/thumb/{clip_id}")
    def thumb(clip_id: int) -> FileResponse:
        """A still of the clip at 1.5s (hook on screen), made once and kept."""
        with db.connect() as con:
            found = db.clip(con, clip_id)
        path = library.clip_path(found["file"]) if found else None
        if path is None or not path.exists():
            raise HTTPException(404, "no such clip file")
        still = library.thumbnail(path)
        if still is None:
            raise HTTPException(404, "could not make a still")
        return FileResponse(still, media_type="image/jpeg",
                            headers={"Cache-Control": "max-age=86400"})

    @app.post("/api/clips/{clip_id}/posts")
    def add_post(clip_id: int, body: dict) -> dict:
        """A post link pasted by hand, for when the sync hasn't found the post yet."""
        from .posts import PostLinkError, add_link

        with db.connect() as con:
            found = db.clip(con, clip_id)
        if found is None:
            raise HTTPException(404, "no such clip")
        try:
            platform = add_link(found, str(body.get("url") or ""))
        except PostLinkError as exc:
            raise HTTPException(400, str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(409, f"{perf.log_path().name} is open in Excel; close it and "
                                     "try again") from exc
        with db.connect() as con:
            if found["status"] == "ready":
                db.update_clip(con, clip_id, status="posted")
            db.update_clip(con, clip_id, watch_until=None)
        broker.publish("clips.changed", {"id": clip_id})
        return {"platform": platform}

    @app.get("/media/{clip_id}/proof")
    def proof_pack(clip_id: int):
        """A zip to send a campaign if it rejects the clip: brief, checks, posts, views, clip."""
        from fastapi.responses import Response

        with db.connect() as con:
            found = db.clip(con, clip_id)
            if found is None:
                raise HTTPException(404, "no such clip")
            snap = next((c for c in Snapshot().clips if c.id == clip_id), None)
            posts = [p.model_dump() for p in (snap.posts if snap else [])]
            history = {p["url"]: db.history_full(con, p["url"]) for p in posts}
        saved = json.loads(found.get("evidence") or "null") or evidence.late_snapshot(
            found, load_campaigns().get(found["campaign"]))
        data = evidence.build_pack(found, library.clip_path(found["file"]), saved, posts, history)
        name = re.sub(r'[\\/:*?"<>|]+', "", found["title"] or found["clip_id"]).strip()[:60] or "clip"
        from urllib.parse import quote

        return Response(data, media_type="application/zip", headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(name + ' - proof.zip')}"})

    @app.get("/media/{clip_id}")
    def media(clip_id: int, download: bool = False) -> FileResponse:
        """The clip's video: streamed for playing, or as a named file to save."""
        with db.connect() as con:
            found = db.clip(con, clip_id)
        path = library.clip_path(found["file"]) if found else None
        if path is None or not path.exists():
            raise HTTPException(404, "no such clip file")
        if download:
            name = re.sub(r'[\\/:*?"<>|]+', "", found["title"] or found["clip_id"]).strip()[:80]
            return FileResponse(path, media_type="video/mp4",
                                filename=f"{name or 'clip'} - {found['campaign']}.mp4")
        return FileResponse(path, media_type="video/mp4")

    from .research_api import build_router

    app.include_router(build_router(broker))

    # The single-page app: real files from the build, index.html for its routes.
    if (STATIC / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=STATIC / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str) -> FileResponse:
        if path.startswith("api/"):
            raise HTTPException(404, "no such endpoint")
        target = (STATIC / path).resolve()
        if path and target.is_file() and STATIC.resolve() in target.parents:
            return FileResponse(target)
        index = STATIC / "index.html"
        if not index.exists():
            raise HTTPException(503, "the Control Center has not been built: "
                                     "cd src/clipper/studio/web && npm run build")
        return FileResponse(index, headers={"Cache-Control": "no-cache"})

    return app


def serve(*, open_browser: bool = True, port: int = PORT) -> None:
    import threading
    import webbrowser

    import uvicorn

    url = f"http://{HOST}:{port}/"
    if _running(port):
        # Opened twice (e.g. the desktop shortcut clicked again): show the one running.
        if open_browser:
            webbrowser.open(url)
        return
    if open_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    uvicorn.run(create_app(auto_sync=True), host=HOST, port=port, log_level="warning")


def _running(port: int) -> bool:
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex((HOST, port)) == 0
