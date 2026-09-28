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
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ..config import CampaignConfig
from ..learn import log as perf
from ..paths import REPO_ROOT
from ..utils.logging import get_logger
from . import db, library, stats
from .events import Broker

log = get_logger(__name__)

STATIC = Path(__file__).parent / "static"
HOST, PORT = "127.0.0.1", 8765
HEARTBEAT_SECONDS = 15
SESSION_GAP_MINUTES = 30


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


class CampaignCounts(BaseModel):
    ready: int = 0
    posted: int = 0
    submitted: int = 0
    skipped: int = 0


class Campaign(BaseModel):
    name: str
    has_brief: bool
    archived: bool
    auto_post: bool | None
    platforms: list[str]
    reward_per_1k_usd: float | None = None
    clips: int
    counts: CampaignCounts
    views: float
    est_earnings: float | None = None
    to_submit: int
    last_post: str | None = None


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
    platform: str
    connected: bool
    handle: str
    health: str            # ok | warn | error
    detail: str
    expires_in_days: float | None = None


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
        try:
            campaign = CampaignConfig.load(path)
        except (ValueError, OSError) as exc:
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
            if models and status == "posted" and all(m.submitted_at for m in models):
                status = "submitted"
            self.clips.append(Clip(
                id=c["id"], campaign=c["campaign"], title=c["title"] or c["clip_id"],
                hook=c["hook"], caption=c["caption"], duration_s=c["duration_s"],
                source_title=display_source(c["source_title"]), status=status,
                marked=c["status"], notes=c["notes"], created_at=c["created_at"],
                file_exists=library.clip_path(c["file"]).exists(),
                video=f"/media/{c['id']}", thumb=f"/thumb/{c['id']}", posts=models))

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
            name=name, has_brief=brief is not None, archived=bool(st.get("archived")),
            auto_post=None if auto is None else bool(auto),
            platforms=list(brief.platform_targets) if brief else [],
            reward_per_1k_usd=brief.reward_per_1k_usd if brief else None,
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
    """Connection health for each platform, from the stored tokens."""
    out = []
    from ..tiktok import api as tt_api

    tiktok = tt_api.token_path()
    if tiktok.exists():
        token = json.loads(tiktok.read_text(encoding="utf-8"))
        left = (float(token.get("obtained_at", 0)) + float(token.get("refresh_expires_in", 0))
                - time.time()) / 86400
        handle = next((stats.handle_from_url(r.get("url", "")) for r in perf.read()
                       if "tiktok.com/@" in (r.get("url") or "")), "")
        out.append(Account(
            platform="tiktok", connected=left > 0, handle=handle,
            health="ok" if left > 7 else "warn" if left > 0 else "error",
            detail=("Stats: views, likes, comments, shares. TikTok's API gives no watch time."
                    if left > 0 else "Login expired: run clipper tiktok login"),
            expires_in_days=round(left, 1)))
    else:
        out.append(Account(platform="tiktok", connected=False, handle="", health="error",
                           detail="Not connected: run clipper tiktok login"))
    from ..instagram import api as ig_api

    if ig_api.token_path().exists():
        token = json.loads(ig_api.token_path().read_text(encoding="utf-8"))
        left = (float(token.get("obtained_at", 0)) + ig_api.TOKEN_LIFETIME - time.time()) / 86400
        out.append(Account(
            platform="instagram", connected=left > 0, handle=token.get("username", ""),
            health="ok" if left > 7 else "warn" if left > 0 else "error",
            detail=("Stats: views, watch time, 3-second skip rate, saves. Renews itself."
                    if left > 0 else "Token expired: generate a new one and run "
                                     "clipper instagram login"),
            expires_in_days=round(left, 1)))
    else:
        out.append(Account(platform="instagram", connected=False, handle="", health="error",
                           detail="Not connected: run clipper instagram login"))
    return out


# --------------------------------------------------------------------------
# The app
# --------------------------------------------------------------------------

def create_app(*, auto_sync: bool = False) -> FastAPI:
    broker = Broker()
    last_problems: list[str] = []

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
            synced = stats.last_synced(perf.read())
            due = True
            if synced:
                with contextlib.suppress(ValueError):
                    age = (datetime.now() - datetime.strptime(synced, "%Y-%m-%d %H:%M"))
                    due = age.total_seconds() >= minutes * 60
            if due:
                try:
                    await sync_now()
                except Exception:
                    log.exception("automatic sync failed")
            await asyncio.sleep(60)

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        broker.bind(asyncio.get_running_loop())
        task = asyncio.create_task(sync_loop()) if auto_sync else None
        yield
        if task:
            task.cancel()

    app = FastAPI(title="Clipper Control Center", lifespan=lifespan,
                  docs_url=None, redoc_url=None)

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
            first_run={"accounts": any(a.connected for a in accounts()),
                       "campaign": bool(snap.campaigns), "clips": bool(snap.clips)})

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
                db.update_clip(con, clip_id, **allowed)
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from exc
        broker.publish("clips.changed", {"id": clip_id})
        return {"ok": True}

    @app.get("/api/posts")
    def posts(campaign: str | None = None) -> list[Post]:
        return [p for p in Snapshot().posts if campaign is None or p.campaign == campaign]

    @app.put("/api/posts/submitted")
    def mark_submitted(changes: dict) -> dict:
        url = str(changes.get("url") or "").split("?", 1)[0]
        if not url:
            raise HTTPException(400, "url is required")
        with db.connect() as con:
            db.set_submitted(con, url, bool(changes.get("submitted", True)))
        broker.publish("clips.changed")
        return {"ok": True}

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
        path = library.clip_path(found["file"])
        if not path.exists():
            raise HTTPException(404, "the file is missing from the library")
        if sys.platform == "win32":
            subprocess.Popen(["explorer", f"/select,{path}"])
        return {"path": str(path)}

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

    @app.get("/media/{clip_id}")
    def media(clip_id: int) -> FileResponse:
        with db.connect() as con:
            found = db.clip(con, clip_id)
        path = library.clip_path(found["file"]) if found else None
        if path is None or not path.exists():
            raise HTTPException(404, "no such clip file")
        return FileResponse(path, media_type="video/mp4")

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
