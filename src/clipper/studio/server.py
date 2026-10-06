"""The Control Center's web server: `clipper studio` (http://127.0.0.1:8765).

Local only -- it binds to 127.0.0.1 and serves one user's library. A typed JSON
API under /api (its OpenAPI schema generates the front end's types), live
updates over Server-Sent Events at /api/events, and the built single-page app
(studio/web -> studio/static) for every other path.

While it runs it syncs every connected account every `sync_minutes` (15 by
default), snapshots every post's numbers, and tells open pages to refresh.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import statistics
import subprocess
import sys
import threading
import time
import urllib.parse
from datetime import datetime, timedelta
from pathlib import Path

import yaml
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .. import connect
from ..campaign import editor
from ..campaign.editor import CampaignError, CampaignForm
from ..config import PLATFORM_NAMES, CampaignConfig
from ..learn import log as perf
from ..paths import REPO_ROOT
from ..utils.logging import get_logger
from . import accounts as account_groups
from . import alerts, db, evidence, library, rerender, rulecheck, setup, stats
from . import editor as clip_editor
from .api_models import (
    Account,
    AccountGroup,
    AlertChannel,
    AlertCheck,
    Alerts,
    Brief,
    BriefProblem,
    Campaign,
    CampaignCheck,
    CampaignCounts,
    CampaignDetail,
    Clip,
    DiscordBot,
    Duplicate,
    EditorView,
    EditorWord,
    EditRule,
    Fit,
    FoundCampaign,
    Home,
    Learning,
    Metrics,
    Payout,
    Post,
    PostCopy,
    PostPoint,
    PostTask,
    Proof,
    ReasonGroup,
    ReasonOption,
    RuleCheck,
    Rules,
    Setup,
    SinceLastVisit,
    Status,
    WhatsWorking,
    WhopFeed,
)
from .events import Broker
from .imports import VIDEO_EXTENSIONS

log = get_logger(__name__)

#: Days in the dashboard's trend lines: from the first snapshot, at least this many, at most MAX.
TREND_DAYS = 14
MAX_TREND_DAYS = 400

STATIC = Path(__file__).parent / "static"
#: Where it listens. 127.0.0.1 is this computer only. CLIPPER_HOST=0.0.0.0 makes it reachable from other
#: machines (a server, D148), and then CLIPPER_TOKEN is required: without a token it refuses to start.
HOST, PORT = os.environ.get("CLIPPER_HOST", "").strip() or "127.0.0.1", 8765
LOOPBACK = ("127.0.0.1", "localhost", "::1")


def hosted() -> bool:
    """On a server others reach (CLIPPER_HOST set to something other than this computer)."""
    return HOST not in LOOPBACK or os.environ.get("CLIPPER_HOSTED", "") == "1"
HEARTBEAT_SECONDS = 15
SESSION_GAP_MINUTES = 30
#: After "Mark posted", sync every FAST_SYNC_SECONDS for this long, so the post's
#: link is ready while campaigns still accept it (a 30-minute window is reported).
WATCH_FOR = timedelta(minutes=30)
FAST_SYNC_SECONDS = 120


# --------------------------------------------------------------------------
# Reading the library
# --------------------------------------------------------------------------

def campaigns_dir() -> Path:
    from ..paths import campaigns_dir as user_campaigns

    return user_campaigns()


#: Each campaign file as last parsed, by its modification time (they're read on
#: every page load; campaigns are frozen, so sharing them is safe).
_campaign_cache: dict[Path, tuple[int, CampaignConfig | None]] = {}


def load_campaigns() -> dict[str, CampaignConfig]:
    out = {}
    for path in sorted(campaigns_dir().glob("*.yaml")):
        if path.name == "example.yaml":  # the template, not a campaign
            continue
        try:
            stamp = path.stat().st_mtime_ns
        except OSError:
            continue
        cached = _campaign_cache.get(path)
        if cached and cached[0] == stamp:
            campaign = cached[1]
        else:
            try:
                campaign = CampaignConfig.load(path)
            except (ValueError, OSError, yaml.YAMLError) as exc:  # one bad file mustn't hide the rest
                log.warning("skipping %s: %s", path.name, exc)
                campaign = None
            _campaign_cache[path] = (stamp, campaign)
        if campaign is not None:
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


#: A campaign ending within this many days is said so before clipping for it.
ENDING_SOON_DAYS = 3
#: Budget left that counts as running low: this much, or what this many views earn.
LOW_BUDGET_USD, LOW_BUDGET_VIEWS = 50.0, 20_000


def campaign_warning(deadline: str, budget_left: float | None, checked_at: str | None,
                     rate_per_1k: float | None, today: datetime | None = None) -> str:
    """Why clipping for a campaign may not pay much longer, or "" (D100): open
    marketplaces stop paying the moment a brand's budget runs out."""
    today = today or datetime.now()
    out = []
    try:
        days = (datetime.strptime(deadline, "%Y-%m-%d").date() - today.date()).days if deadline else None
    except ValueError:
        days = None
    if days is not None:
        if days < 0:
            out.append(f"Ended {deadline}")
        elif days == 0:
            out.append("Ends today")
        elif days <= ENDING_SOON_DAYS:
            out.append(f"Ends in {days} day{'s' if days > 1 else ''}")
    if budget_left is not None:
        low = max(LOW_BUDGET_USD, (rate_per_1k or 0) * LOW_BUDGET_VIEWS / 1000)
        if budget_left <= 0:
            out.append("Budget used up")
        elif budget_left < low:
            out.append(f"About ${budget_left:,.0f} of budget left")
        if out and out[-1].startswith(("Budget", "About")) and checked_at:
            try:
                age = (today - datetime.strptime(checked_at, "%Y-%m-%d %H:%M")).days
            except ValueError:
                age = 0
            if age >= 1:
                out[-1] += f" ({age} day{'s' if age > 1 else ''} ago)"
    return " · ".join(out)


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
            self.briefs = db.briefs(con)
            self.tasks_done = db.tasks_done(con)
            self.payouts = db.payouts(con)
        self._milestones: dict[str, list] = {}
        now = datetime.now()
        self._prints: dict[str, str] = {}
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
                    est_earnings=stats.post_earnings(views, brief),
                    submitted_at=self.submitted.get(p["url"]),
                    tasks=[PostTask(views=m.views, task=m.task, done=(p["url"], m.views) in self.tasks_done)
                           for m in self.milestones(brief) if (views or 0) >= m.views]))
            status = effective_status(c, models)
            if models and status in ("posted", "submitted"):
                # A post found after the clip was marked submitted still needs submitting.
                status = "submitted" if all(m.submitted_at for m in models) else "posted"
            scores = json.loads(c.get("scores") or "{}")
            # The file's change time, in its links: a re-rendered clip's new frame shows at once.
            try:
                version = int(library.clip_path(c["file"]).stat().st_mtime)
            except OSError:
                version = 0
            copy, rule_state = self._rules(c, brief) if brief and status in ("ready", "skipped") else ([], None)
            self.clips.append(Clip(
                post_copy=copy, rules=rule_state,
                pinned_comment=rulecheck.extras(c).get("pinned_comment", "") if status == "ready" else "",
                score=scores.get("score"), rubric=scores.get("rubric") or {},
                pool=scores.get("pool"), pool_rank=scores.get("pool_rank"),
                picked_by=scores.get("picked_by") or "unknown", rating=c.get("rating"),
                read=scores.get("read"), watched=scores.get("watched"),
                visual_payoff=scores.get("visual_payoff"), sees=scores.get("sees") or "",
                reasons=json.loads(c.get("reasons") or "[]"),
                proof=Proof(**evidence.summary(json.loads(c.get("evidence") or "null"),
                                               [m.model_dump() for m in models])),
                watching=bool(c.get("watch_until") and c["watch_until"] > now.strftime("%Y-%m-%d %H:%M")),
                id=c["id"], campaign=c["campaign"], submits=brief.submits if brief else True,
                title=c["title"] or c["clip_id"],
                hook=c["hook"], caption=c["caption"], duration_s=c["duration_s"],
                source_title=display_source(c["source_title"]), status=status,
                marked=c["status"], notes=c["notes"], created_at=c["created_at"],
                file_exists=bool(version), video=f"/media/{c['id']}?v={version}",
                thumb=f"/thumb/{c['id']}?v={version}", posts=models,
                rerendering=rerender.state_of(c["id"])[0], rerender_error=rerender.state_of(c["id"])[1]))
        # Before posting: which already-posted clips each one repeats (studio/duplicates.py).
        from . import duplicates

        posted = {c.id: [f"{PLATFORM_NAMES.get(p.platform, p.platform)}"
                         + (f" @{p.account.lstrip('@')}" if p.account else "") for p in c.posts]
                  or (["posted"] if c.status in ("posted", "submitted") else [])
                  for c in self.clips}
        found = duplicates.find(self.raw_clips, posted)
        for clip in self.clips:
            clip.duplicates = [Duplicate(**d) for d in found.get(clip.id, [])]

    def _rules(self, clip: dict, campaign: CampaignConfig) -> tuple[list[PostCopy], Rules]:
        from ..campaign import rules as caption_rules

        if campaign.name not in self._prints:
            self._prints[campaign.name] = rulecheck.fingerprint(campaign)
        texts = rulecheck.texts(clip, campaign, fingerprint=self._prints[campaign.name])
        copy = [PostCopy(platform=t.platform, title=t.title, caption=t.caption,
                         text_name=caption_rules.TEXT_NAMES.get(t.platform, "Caption"),
                         checks=[RuleCheck(name=r.name, passed=r.passed, detail=r.detail) for r in t.checks])
                for t in texts]
        _, key = rulecheck.audit_key(clip, campaign, self.briefs.get(campaign.name), texts)
        found = rulecheck.stored(clip) or {}
        current = found.get("key") == key
        return copy, Rules(failed=caption_rules.summary(texts), checked=current and not found.get("refused"),
                           refused=current and bool(found.get("refused")),
                           checking=rulecheck.checking(campaign.name),
                           brief=[BriefProblem(**p) for p in found.get("problems") or []] if current else [])

    def milestones(self, campaign: CampaignConfig | None) -> list:
        """The brief's view-milestone tasks (campaign/milestones.py), read once per campaign."""
        from ..campaign import milestones

        if campaign is None:
            return []
        if campaign.name not in self._milestones:
            self._milestones[campaign.name] = milestones.of(campaign, self.briefs.get(campaign.name))
        return self._milestones[campaign.name]

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
        locked = stats.locked_earnings(posts, lambda _: brief)
        return Campaign(
            name=name, title=editor.title_of(brief) if brief else editor.pretty(name),
            marketplace=brief.marketplace if brief else "", has_brief=brief is not None, archived=bool(st.get("archived")),
            auto_post=None if auto is None else bool(auto),
            platforms=list(brief.platform_targets) if brief else [],
            reward_per_1k_usd=brief.reward_per_1k_usd if brief else None,
            max_clips=brief.max_clips_per_source if brief else None,
            campaign_url=brief.campaign_url if brief else "",
            posting_rules=list(brief.posting_rules) if brief else [],
            clips=len(mine), counts=counts,
            views=sum(p.views or 0 for p in posts),
            est_earnings=round(sum(earnings), 2) if earnings else None,
            # Your own channel, or a campaign that takes no links, has nothing to submit (D144, D147).
            to_submit=0 if brief and not brief.submits else sum(1 for p in posts if not p.submitted_at),
            submits=brief.submits if brief else True, pay_model=brief.pay_model if brief else "per_view",
            last_post=max((p.posted_at for p in posts if p.posted_at), default=None),
            min_payout_usd=brief.min_payout_usd if brief else None,
            locked_usd=locked[0], locked_posts=locked[1], own_channel=bool(brief and brief.own_channel),
            **self.money(name, brief, posts))

    def money(self, name: str, brief: CampaignConfig | None, posts: list[Post]) -> dict:
        """What a campaign has paid, and whether it's about to stop paying (D99, D100)."""
        paid = [p["amount"] for p in self.payouts if p["campaign"] == name]
        views = sum(p.views or 0 for p in posts)
        st = self.state.get(name, {})
        deadline = brief.deadline if brief else ""
        return {"paid_usd": round(sum(paid), 2) if paid else None,
                "paid_per_1k": round(sum(paid) / views * 1000, 2) if paid and views else None,
                "deadline": deadline, "budget_left": st.get("budget_left"),
                "budget_checked_at": st.get("budget_checked_at"),
                "warning": campaign_warning(deadline, st.get("budget_left"), st.get("budget_checked_at"),
                                            brief.reward_per_1k_usd if brief else None)}

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
        notes=campaign.notes, caption_rules=list(campaign.caption_rules),
        posting_rules=list(campaign.posting_rules))


def yt_has_app() -> bool:
    """A Google app to sign in with: the user's own, or one built into Clipper (D84)."""
    from ..youtube import api as yt_api

    return yt_api.has_app() or connect.enabled()


def scoped(snap: Snapshot, scope: str | None) -> list[Clip]:
    """The clips the viewing switcher shows (studio/accounts.py): posted ones with
    their posts on its accounts, the rest when their campaign posts there."""
    view = account_groups.Scope(scope)
    if view.everything:
        return snap.clips
    out = []
    for clip in snap.clips:
        mine = [p for p in clip.posts if view.has_post(p.platform, p.account)]
        if mine:
            out.append(clip.model_copy(update={"posts": mine}))
        elif not clip.posts and clip.status in ("ready", "skipped"):
            campaign = snap.campaigns.get(clip.campaign)
            if view.wants_campaign(clip.campaign, campaign.platform_targets if campaign else ()):
                out.append(clip)
    return out


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
    from ..x import api as x_api

    for path in x_api.account_files():
        account = x_api.read(path)
        read_so_far = int(account.get("posts_read") or 0)
        out.append(Account(
            id=path.stem, platform="x", connected=True, handle=account.get("username", ""), health="ok",
            detail=(f"Stats: views, likes, replies, reposts, bookmarks, hourly, for posts up to "
                    f"{x_api.REFRESH_DAYS} days old. X charges per post read: {read_so_far} so far, "
                    f"at most ${read_so_far * x_api.PRICE_PER_POST:.2f}."),
            expires_in_days=None))
    return out


# --------------------------------------------------------------------------
# The app
# --------------------------------------------------------------------------



def _quiet_resets(loop: asyncio.AbstractEventLoop, context: dict) -> None:
    """Windows reports a browser closing its live-update connection as an error
    ("connection forcibly closed"); it's a page closed or reloaded, nothing more."""
    if isinstance(context.get("exception"), ConnectionResetError):
        return
    loop.default_exception_handler(context)


def source_folders() -> list[Path]:
    """Where footage for new clips is looked for: Clipper's downloads, then yours."""
    from ..paths import downloads_dir

    return [downloads_dir(), Path.home() / "Downloads"]


#: A clip downloaded to post: "<title> - <campaign>.mp4", "(1)" if downloaded twice.
_OWN_DOWNLOAD = re.compile(r" - ([a-z0-9][a-z0-9-]*)(?: \(\d+\))?$")


def own_clip(path: Path, campaigns: set[str]) -> bool:
    """A finished clip downloaded from the Control Center, not footage to clip."""
    found = _OWN_DOWNLOAD.search(path.stem)
    return bool(found and found.group(1) in campaigns)


def list_sources() -> list[dict]:
    seen, out = set(), []
    campaigns = set(load_campaigns())
    for folder in source_folders():
        if not folder.is_dir():
            continue
        for path in folder.iterdir():
            if path.suffix.lower() in VIDEO_EXTENSIONS and path.is_file() and not own_clip(path, campaigns):
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
    instagram = setup.instagram_connect(broker.publish)
    whop_login = setup.whop_connect(lambda *a: broker.publish("alerts.changed"))
    from .imports import ImportRunner

    importer = ImportRunner(broker.publish)
    rerenders = rerender.start(broker.publish)

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
        loop = asyncio.get_running_loop()
        broker.bind(loop)
        loop.set_exception_handler(_quiet_resets)
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
            # Clips made before a rule existed, or never read by the AI check (D81).
            with contextlib.suppress(Exception):
                with db.connect() as con:
                    state = db.campaign_state(con)
                rulecheck.start([n for n in load_campaigns() if not state.get(n, {}).get("archived")],
                                broker.publish)
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
    started_on = code_version()

    @app.get("/api/code-version")
    def running_code() -> dict:
        """The code this server started with, so a newer `clipper studio` can tell (D116)."""
        return {"version": started_on}

    @app.post("/api/quit")
    def quit_server(request: Request) -> dict:
        """Stop, for a `clipper studio` started on newer code (only from this computer)."""
        if request.client and request.client.host not in ("127.0.0.1", "::1", "localhost"):
            raise HTTPException(403, "only from this computer")
        from . import create_api

        # Never mid-work: a render or a Create build would be cut off (the old copy keeps running).
        if create_api._running or any(j["status"] in ("queued", "running") for j in jobs.list()):
            raise HTTPException(409, "busy: a job is running")
        threading.Timer(0.5, lambda: os._exit(0)).start()
        return {"stopping": True}
    # The clip list is ~200 KB of JSON; compressed it's a fraction. Video, images
    # and the live event stream are left as they are (Starlette's own exclusions).
    app.add_middleware(GZipMiddleware, minimum_size=2048)

    token = os.environ.get("CLIPPER_TOKEN", "").strip()

    @app.middleware("http")
    async def access_token(request: Request, call_next):
        """On a server (CLIPPER_TOKEN set): nothing without the token, given once as ?token=... and
        kept in a cookie. On this computer alone there is no token and nothing changes (D148)."""
        if not token:
            return await call_next(request)
        import hmac

        from fastapi.responses import JSONResponse, RedirectResponse

        given = request.query_params.get("token", "")
        if given and hmac.compare_digest(given, token):
            reply = RedirectResponse(request.url.path or "/", status_code=303)
            reply.set_cookie("clipper_token", token, httponly=True, samesite="strict", max_age=60 * 60 * 24 * 90)
            return reply
        have = request.cookies.get("clipper_token", "") or request.headers.get("authorization", "").removeprefix("Bearer ")
        if have and hmac.compare_digest(have, token):
            return await call_next(request)
        return JSONResponse({"detail": "this Clipper needs its access token: open it once with ?token=..."}, status_code=401)

    @app.middleware("http")
    async def same_origin_writes(request: Request, call_next):
        """Only the Control Center's own pages may change things.

        It listens on 127.0.0.1, but any website open in the browser could still
        send it a request; browsers label those with the site's Origin.
        """
        # The connect service's page posts a finished login here from its own site (D151); only a login
        # Clipper started is accepted (its nonce is checked), so this one address is let through.
        if request.method not in ("GET", "HEAD", "OPTIONS") and request.url.path != "/api/accounts/broker/return":
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
                      accounts=accounts(), hosted=hosted())

    #: The AI jobs a user can give to a provider (Settings > Who does what, D148): id, label, what it is.
    AI_JOBS = [
        ("judge", "Judge the moments", "Reads every moment of a video, scores it, and picks each clip's opening line."),
        ("script", "Write Create's scripts", "The script for a Short."),
        ("check", "Fact-check scripts", "Reads a script for mistakes."),
        ("sketch", "Draw the diagrams", "Chalkboard drawings. The one job the paid Claude key is meant for."),
        ("review", "Review the drawings", "Looks at a drawing and fixes it."),
        ("footage", "Pick stock footage", "Judges thumbnails and writes the searches."),
        ("place", "Place your own clips", "Matches your clips to the script's sentences."),
        ("topics", "Think up ideas", "The ideas list for a channel."),
    ]
    BACKENDS = {"claude_plan": "claude_code", "claude_api": "anthropic", "gemini": "gemini", "ollama": "ollama"}

    @app.get("/api/ai-jobs")
    def ai_jobs() -> dict:
        """Who does which AI job, and which providers this computer can use (D148)."""
        import shutil

        from ..config import Config
        from ..llm.claude_code import cli

        config = Config.load()
        mine = config.llm.backend
        current = next((k for k, v in BACKENDS.items() if v == mine), "")
        return {"jobs": [{"id": i, "label": label, "about": about, "choice": config.llm.job_providers.get(i, "")}
                         for i, label, about in AI_JOBS],
                "clipping": {"choice": current if mine != "gemini" else "", "now": mine},
                "available": {"claude_plan": bool(cli()), "claude_api": bool(os.environ.get("ANTHROPIC_API_KEY", "").strip()),
                              "gemini": bool(os.environ.get("GEMINI_API_KEY", "").strip()), "ollama": bool(shutil.which("ollama"))}}

    @app.put("/api/ai-jobs")
    def set_ai_job(body: dict) -> dict:
        from ..config import write_auto

        job, choice = str(body.get("job") or ""), str(body.get("choice") or "")
        if choice and choice not in BACKENDS:
            raise HTTPException(400, "unknown provider")
        if job == "clipping":   # captions, descriptions, rule checks and the rest of clipping
            write_auto({"llm": {"backend": BACKENDS[choice] if choice else None}})
        elif job in {j[0] for j in AI_JOBS}:
            write_auto({"llm": {"job_providers": {job: choice or None}}})
        else:
            raise HTTPException(404, "no such job")
        return {"job": job, "choice": choice}

    @app.get("/api/compute")
    def compute() -> dict:
        """This computer, and the speech-recognition settings that suit it (hardware.py, D148)."""
        from .. import hardware
        from ..config import Config

        hw = hardware.detect()
        plan = hardware.recommend(hw)
        mine = hardware.current(Config.load())
        return hardware.as_dict(hw, plan) | {
            "current": mine, "matches": mine["model"] == plan.model and mine["compute_type"] == plan.compute_type
            and mine["device"] in (plan.device, "auto")}

    @app.post("/api/compute/apply")
    def compute_apply() -> dict:
        from .. import hardware

        plan = hardware.recommend(hardware.detect())
        hardware.apply(plan)
        return {"applied": plan.label}

    @app.get("/api/home")
    def home(scope: str | None = None) -> Home:
        snap = Snapshot()
        active = {n for n in snap.campaign_names()
                  if not snap.state.get(n, {}).get("archived")}
        # Money and views cover every campaign, archived ones too: all-time totals (D138).
        # Work still to do (pipeline, to submit, tasks) is only the active campaigns'.
        every = scoped(snap, scope)
        clips = [c for c in every if c.campaign in active]
        posts = [p for c in every for p in c.posts]
        todo = [p for c in clips if not (snap.campaigns.get(c.campaign) and not snap.campaigns[c.campaign].submits)
                for p in c.posts]
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
        # The trend lines: views, and the money they'd earned, at the end of each day.
        # Each post is priced by its own campaign's rate, minimum and cap, as est_earnings is.
        today = datetime.now().date()
        by_day, earned_by_day = [], []
        with db.connect() as con:
            first = con.execute("SELECT MIN(at) FROM snapshots").fetchone()[0]
            span = (today - datetime.fromisoformat(first[:10]).date()).days + 1 if first else 0
            for d in range(min(MAX_TREND_DAYS, max(TREND_DAYS, span)) - 1, -1, -1):
                then = db.views_at(con, f"{today - timedelta(days=d)} 23:59:59")
                seen = [(p, then.get(p.url.split("?", 1)[0])) for p in posts]
                by_day.append(sum(v or 0 for _, v in seen))
                money = 0.0
                for p, v in seen:
                    brief = snap.campaigns.get(p.campaign)
                    if brief and v is not None:
                        money += stats.post_earnings(v, brief) or 0.0
                earned_by_day.append(round(money, 2))
        locked_usd, locked_posts = stats.locked_earnings(posts, lambda p: snap.campaigns.get(p.campaign))
        return Home(
            metrics=Metrics(
                locked_usd=locked_usd, locked_posts=locked_posts,
                est_earnings=round(sum(earnings), 2) if earnings else None,
                views=sum(views), posts=len(posts),
                median_views=statistics.median(views) if views else None,
                to_submit=sum(1 for p in todo if not p.submitted_at),
                ready=pipeline.ready,
                paid_usd=round(sum(p["amount"] for p in snap.payouts), 2) if snap.payouts else None,
                tasks_due=sum(1 for p in todo for t in p.tasks if not t.done),
                views_by_day=by_day, earned_by_day=earned_by_day),
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
            try:
                db.set_campaign_state(con, name, **changes)
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from exc
        broker.publish("campaigns.changed")
        return {"ok": True}

    @app.get("/api/payouts")
    def list_payouts(campaign: str | None = None) -> list[Payout]:
        with db.connect() as con:
            return [Payout(**p) for p in db.payouts(con, campaign)]

    @app.post("/api/payouts")
    def add_payout(body: dict) -> Payout:
        """A payout a campaign actually made, recorded by hand (D99)."""
        try:
            amount = float(body.get("amount"))
            with db.connect() as con:
                new = db.add_payout(con, str(body.get("campaign") or ""), amount,
                                    str(body.get("paid_on") or datetime.now().strftime("%Y-%m-%d")),
                                    str(body.get("note") or ""))
                found = next(p for p in db.payouts(con) if p["id"] == new)
        except (TypeError, ValueError) as exc:
            raise HTTPException(400, f"couldn't record that payout: {exc}") from exc
        broker.publish("campaigns.changed")
        return Payout(**found)

    @app.delete("/api/payouts/{payout_id}")
    def delete_payout(payout_id: int) -> dict:
        with db.connect() as con:
            db.delete_payout(con, payout_id)
        broker.publish("campaigns.changed")
        return {"ok": True}

    @app.post("/api/posts/task")
    def post_task(body: dict) -> dict:
        """A brief's view-milestone task marked done for a post, or undone (D98)."""
        try:
            url, views = str(body["url"]).split("?", 1)[0], int(body["views"])
        except (KeyError, TypeError, ValueError) as exc:
            raise HTTPException(400, "needs the post's url and the milestone's views") from exc
        with db.connect() as con:
            db.set_task_done(con, url, views, bool(body.get("done", True)))
        broker.publish("clips.changed", {})
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
        rulecheck.start(name, broker.publish)  # the rules may have changed (D81)
        return {"name": campaign.name}

    @app.post("/api/campaigns/{name}/recheck")
    def recheck_campaign(name: str) -> dict:
        if name not in load_campaigns():
            raise HTTPException(404, f"no campaign {name!r}")
        rulecheck.start(name, broker.publish)
        broker.publish("clips.changed")
        return {"ok": True}

    @app.put("/api/campaigns/{name}/brief")
    def save_campaign_brief(name: str, body: dict) -> dict:
        """Keep the brief as pasted, whole, for Ask (D76)."""
        if name not in load_campaigns():
            raise HTTPException(404, f"no campaign {name!r}")
        text = str(body.get("text") or "").strip()
        if len(text) < 40:
            raise HTTPException(400, "that's too short to be a brief")
        with db.connect() as con:
            db.save_brief(con, name, text)
        rulecheck.start(name, broker.publish)  # the AI check reads the brief itself
        return {"ok": True, "saved_at": db.now()}

    @app.get("/api/campaigns/{name}/brief")
    def get_campaign_brief(name: str) -> dict:
        with db.connect() as con:
            found = db.brief(con, name)
        return {"saved_at": found["saved_at"] if found else None,
                "chars": len(found["text"]) if found else 0}

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
        import threading

        from . import finder

        found = [FoundCampaign(**f) for f in finder.found()]
        # Campaigns found before niches existed (or when no AI answered) are sorted in the background.
        if any(not f.niche for f in found):
            threading.Thread(target=finder.sort_niches, args=(broker.publish,), name="niches", daemon=True).start()
        return found

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
    def clips(campaign: str | None = None, scope: str | None = None) -> list[Clip]:
        snap = Snapshot()
        return [c for c in scoped(snap, scope) if campaign is None or c.campaign == campaign]

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

    @app.post("/api/clips/{clip_id}/rerender")
    def rerender_clip(clip_id: int, body: dict | None = None) -> dict:
        """Make the clip again with a new on-screen hook (D90): the one given (a paid
        choice), or the brief's line its campaign has used least."""
        from ..campaign import rotation
        from . import plans

        hook = str((body or {}).get("hook") or "").strip()
        found = next((c for c in Snapshot().clips if c.id == clip_id), None)
        if found is None:
            raise HTTPException(404, "no such clip")
        if found.posts or found.status in ("posted", "submitted"):
            raise HTTPException(400, "it's posted: its video is what's live")
        if hook:
            # The hook asked for is the one used. Its own title or current hook is free
            # (showing the title fixes a mismatch); any other line is a paid choice.
            if hook not in (found.title, found.hook):
                plans.require("choose_lines")
        else:
            campaign = load_campaigns().get(found.campaign)
            lines = [h for h in (campaign.hook_texts if campaign else ()) if h != found.hook]
            if not lines:
                raise HTTPException(400, "the brief has no other on-screen lines; type one to use instead")
            hook = rotation.pick(campaign, "hook", lines)
        rerenders.submit(clip_id, hook)
        return {"queued": True, "hook": hook}

    @app.post("/api/campaigns/{name}/rerender-hooks")
    def rerender_hooks(name: str) -> dict:
        """Every unposted clip whose title is one of the brief's lines but whose
        video shows another: made again showing its title (D85, D90)."""
        campaign = load_campaigns().get(name)
        if campaign is None:
            raise HTTPException(404, f"no campaign {name!r}")
        todo = [c for c in Snapshot().clips if c.campaign == name and not c.posts
                and c.status in ("ready", "skipped") and c.title in campaign.hook_texts
                and c.hook and c.hook != c.title]
        for c in todo:
            rerenders.submit(c.id, c.title)
        return {"queued": len(todo)}

    # ---------- Create: the user's own channel (D108) ----------
    from . import create_api

    create_api.routes(app, broker.publish)

    # ---------- the editor (D103) ----------

    EDIT_LABELS = {"internal_cuts": "Cuts inside the clip", "visual_effects": "Zooms",
                   "added_text": "On-screen hook", "captions": "Captions",
                   "overlays": "B-roll and overlays", "audio_additions": "Music and sound effects",
                   "re_edit": "Reordering scenes"}

    def editor_campaign(name: str):
        campaign = load_campaigns().get(name)
        if campaign is None:
            raise HTTPException(400, "pick a campaign")
        return campaign

    def editor_clip(clip_id: int) -> dict:
        with db.connect() as con:
            row = db.clip(con, clip_id)
        if row is None:
            raise HTTPException(404, "no such clip")
        return row

    def editor_edit(body: dict, duration: float, campaign):
        from ..campaign.edits import permissions
        from ..editing import ClipEdit, problems

        try:
            edit = ClipEdit.model_validate(body.get("edit") or {}).tidy(duration)
        except ValueError as exc:
            raise HTTPException(400, f"that edit can't be read: {exc}") from exc
        wrong = problems(edit, permissions(campaign), campaign)
        if wrong:
            raise HTTPException(400, "; ".join(wrong).capitalize())
        return edit

    @app.get("/api/editor")
    def editor_view(campaign: str = "", source: str = "", clip: int | None = None) -> EditorView:
        """A video to edit: a library clip (`clip`), or a source video and campaign."""
        from ..campaign.edits import CLASSES, permissions
        from ..editing import blocked
        from ..ingest.download import load_info
        from ..utils.cache import source_id_for_file

        row = editor_clip(clip) if clip is not None else None
        if row is not None:
            campaign, sid, path = row["campaign"], row["source_id"], ""
        else:
            target = allowed_source(source)
            sid, path = source_id_for_file(target), str(target)
        found = editor_campaign(campaign)
        if not clip_editor.prepared(sid):
            if row is not None:
                raise HTTPException(409, "this clip's working files are gone; clip its video again instead")
            return EditorView(source_id=sid, prepared=False, source=path, name=Path(path).name,
                              campaign=found.name)
        info = load_info(sid)
        if not Path(info.media.path).exists():
            raise HTTPException(409, f"its video isn't on this PC any more ({Path(info.media.path).name})")
        clip_editor.make_proxy(sid, broker.publish)
        perms = permissions(found)
        no = blocked(perms)
        edit = None
        if row is not None:
            saved = (json.loads(row.get("scores") or "{}") or {}).get("edit")
            start, end = row.get("start_s"), row.get("end_s")
            if start is None or end is None:
                # Clips filed before their range was kept: the start is in the id, the length known.
                from ..utils.timecode import from_slug_timestamp

                start = from_slug_timestamp(row.get("clip_id") or "")
                end = start + float(row.get("duration_s") or 30) if start is not None else None
            pieces = [{"start": start, "end": end, "zoom": 1.0}] if start is not None and end is not None else []
            edit = saved or {"pieces": pieces, "hook": row.get("hook") or "", "fixes": []}
        return EditorView(
            source_id=sid, prepared=True, source=info.media.path, name=info.title or Path(info.media.path).name,
            campaign=found.name, duration=info.media.duration, fps=info.media.fps or 30.0,
            has_audio=info.media.has_audio, proxy_ready=clip_editor.proxy_ready(sid),
            words=[EditorWord(**w) for w in clip_editor.words(sid)],
            rules=[EditRule(key=c, label=EDIT_LABELS.get(c, c), allowed=perms.allowed[c], blocked=c in no,
                            why=perms.reasons[c]) for c in CLASSES],
            min_seconds=found.duration.min_seconds, max_seconds=found.duration.max_seconds,
            hooks=list(found.hook_texts), clip_id=clip, clip_status=row["status"] if row else None, edit=edit)

    @app.post("/api/editor/prepare")
    def editor_prepare(body: dict) -> dict:
        """Read and transcribe a video so the editor can open it: a job, with progress."""
        found = editor_campaign(str(body.get("campaign") or ""))
        target = allowed_source(str(body.get("source") or ""))
        return jobs.submit(found, str(target), None, prepare=True).view()

    @app.get("/api/editor/{source_id}/proxy")
    def editor_proxy(source_id: str) -> FileResponse:
        path = clip_editor.proxy_path(source_id)
        if not path.is_file():
            raise HTTPException(404, "the editor's copy isn't ready yet")
        return FileResponse(path, media_type="video/mp4")

    @app.get("/api/editor/{source_id}/peaks")
    def editor_peaks(source_id: str) -> list[int]:
        if not clip_editor.prepared(source_id):
            raise HTTPException(404, "no such video")
        return clip_editor.peaks(source_id)

    @app.post("/api/editor/tighten")
    def editor_tighten(body: dict) -> dict:
        """The edit with its pauses and fillers cut, as ordinary cuts to adjust or undo."""
        from ..campaign.edits import permissions
        from ..editing import ClipEdit, blocked, tighten
        from ..ingest.download import load_info
        from ..models import Transcript
        from ..paths import work_dir
        from ..runner import _loudness

        sid = str(body.get("source_id") or "")
        if not clip_editor.prepared(sid):
            raise HTTPException(404, "no such video")
        found = editor_campaign(str(body.get("campaign") or ""))
        no = blocked(permissions(found))
        if "internal_cuts" in no:
            raise HTTPException(400, f"No cuts inside the clip: {no['internal_cuts']}")
        info = load_info(sid)
        edit = ClipEdit.model_validate(body.get("edit") or {}).tidy(info.media.duration)
        words = Transcript.load(work_dir(sid) / "transcript.json").words
        tightened, cuts = tighten(edit, words, _loudness(info), min_length=found.duration.min_seconds or 0.0)
        return {"edit": tightened.model_dump(), "removed": round(edit.length - tightened.length, 2),
                "cuts": [{"start": c.start, "end": c.end, "why": c.reason} for c in cuts]}

    @app.post("/api/editor/preview")
    def editor_preview(body: dict) -> dict:
        """A quick draft render of the edit, through the real pipeline: exactly the
        framing, captions and hook the clip will have, smaller and faster."""
        from ..config import Config
        from ..ingest.download import load_info
        from ..paths import runs_dir
        from ..runner import RerenderError
        from ..runner import rerender as render_again

        sid = str(body.get("source_id") or "")
        if not clip_editor.prepared(sid):
            raise HTTPException(404, "no such video")
        found = editor_campaign(str(body.get("campaign") or ""))
        info = load_info(sid)
        edit = editor_edit(body, info.media.duration, found)
        base = editor_clip(int(body["clip"])) if body.get("clip") is not None else {"scores": "{}"}
        clip = {**base, "source_id": sid, "clip_id": "preview", "start_s": edit.start, "end_s": edit.end}
        clip_editor.clear_previews(sid)
        try:
            made = render_again(clip, edit.hook, config=Config.load(), campaign=found, out_root=runs_dir(),
                                edit=edit, draft=True)
        except RerenderError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"url": f"/api/editor/{sid}/preview/{made.name}", "length": round(edit.length, 2)}

    @app.get("/api/editor/{source_id}/preview/{name}")
    def editor_preview_file(source_id: str, name: str) -> FileResponse:
        path = clip_editor.preview_dir(source_id) / Path(name).name
        if path.suffix != ".mp4" or not path.is_file():
            raise HTTPException(404, "that preview is gone; make another")
        return FileResponse(path, media_type="video/mp4")

    @app.post("/api/editor/save")
    def editor_save(body: dict) -> dict:
        """Render the edit for real: over its clip (`clip`), or as a new clip."""
        from ..ingest.download import load_info
        from . import footage

        sid = str(body.get("source_id") or "")
        if not clip_editor.prepared(sid):
            raise HTTPException(404, "no such video")
        found = editor_campaign(str(body.get("campaign") or ""))
        info = load_info(sid)
        edit = editor_edit(body, info.media.duration, found)
        if body.get("clip") is not None:
            clip_id = int(body["clip"])
            row = editor_clip(clip_id)
            if row["status"] in ("posted", "submitted"):
                raise HTTPException(400, "it's posted: its video is what's live")
            rerenders.submit(clip_id, edit.hook, edit.model_dump())
            return {"queued": True, "clip": clip_id}
        source = str(allowed_source(info.media.path))
        footage.remember([source], found.name, "clipped")
        return {"queued": True, "job": jobs.submit(found, source, None, edits=[edit.model_dump()]).view()}

    @app.put("/api/clips/{clip_id}/caption")
    def edit_caption(clip_id: int, body: dict) -> dict:
        """The user's own caption for a clip not yet posted; the rules still apply (D81).
        Choosing or writing captions is part of the paid plans (D90)."""
        from . import plans

        plans.require("choose_lines")

        text = str(body.get("caption") or "").strip()
        if not text:
            raise HTTPException(400, "the caption is empty")
        return {"caption": save_caption(unposted(clip_id), text)}

    def unposted(clip_id: int) -> Clip:
        found = next((c for c in Snapshot().clips if c.id == clip_id), None)
        if found is None:
            raise HTTPException(404, "no such clip")
        if found.posts or found.status in ("posted", "submitted"):
            raise HTTPException(400, "it's posted: the caption is what's live")
        return found

    def save_caption(found: Clip, text: str) -> str:
        """A new caption for an unposted clip: the rules applied, in the library and
        the performance log, then read against the brief again."""
        from ..campaign import rules as caption_rules

        clip_id = found.id
        campaign = load_campaigns().get(found.campaign)
        caption = caption_rules.enforce(text, campaign) if campaign else text
        with stats.log_lock:
            rows = perf.read()
            with db.connect() as con:
                raw = db.clip(con, clip_id)
                db.update_clip(con, clip_id, caption=caption)
            for row in rows:  # what the syncs match the post by
                if (not row.get("url") and (row.get("campaign"), row.get("source_id"), row.get("clip_id"))
                        == (raw["campaign"], raw["source_id"], raw["clip_id"])):
                    row["caption"] = caption
            with contextlib.suppress(PermissionError):
                perf.write(rows)
        broker.publish("clips.changed", {"id": clip_id})
        rulecheck.start(found.campaign, broker.publish)
        return caption

    @app.post("/api/clips/{clip_id}/fix")
    def fix_clip(clip_id: int) -> dict:
        """One click for a flagged clip (D91): text the brief says is missing becomes
        a rule for every clip; anything else, the AI rewrites the caption to follow."""
        from ..campaign import fix
        from ..config import Config
        from ..runner import _correction_backends

        found = unposted(clip_id)
        rules_state = found.rules
        if rules_state is None or rules_state.ok:
            return {"fixed": [], "note": "nothing to fix"}
        fixed: list[str] = []
        rest = list(rules_state.failed)
        for problem in rules_state.brief:
            if problem.add.strip():
                platforms = [problem.platform] if problem.platform != "all" else []
                editor.add_caption_rule(campaigns_dir(), found.campaign, {
                    "text": problem.add.strip(), "must": "include",
                    "place": "title" if problem.where == "title" else "caption",
                    "platforms": platforms, "quote": problem.rule})
                fixed.append(f"“{problem.add.strip()}” added for every clip")
            else:
                rest.append(f"{problem.rule} ({problem.problem})" if problem.problem else problem.rule)
        if rest:
            campaign = load_campaigns().get(found.campaign)
            try:
                backends = _correction_backends(Config.load(), None)
            except Exception as exc:  # no AI key
                raise HTTPException(400, f"the AI isn't set up ({str(exc).splitlines()[0]}); edit the caption yourself") from exc
            new = fix.rewrite(found.caption, rest, campaign, backends) if campaign else None
            if new is None:
                raise HTTPException(502, "the AI couldn't rewrite it just now; try again, or edit the caption yourself")
            save_caption(found, new)
            fixed.append("caption rewritten")
        broker.publish("campaigns.changed")
        broker.publish("clips.changed", {"id": clip_id})
        rulecheck.start(found.campaign, broker.publish)
        return {"fixed": fixed}

    @app.get("/api/posts")
    def posts(campaign: str | None = None, scope: str | None = None) -> list[Post]:
        return [p for c in scoped(Snapshot(), scope) for p in c.posts
                if campaign is None or p.campaign == campaign]

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

    @app.post("/api/clips/{clip_id}/not-good")
    def not_good(clip_id: int, body: dict | None = None) -> dict:
        """One click on a ready clip: skip it, and learn from it (a 1/5). `undo`
        puts it back as it was (D74)."""
        body = body or {}
        with db.connect() as con:
            clip = db.clip(con, clip_id)
            if clip is None:
                raise HTTPException(404, "no such clip")
            if body.get("undo"):
                db.set_rating(con, clip_id, None, [])
                db.update_clip(con, clip_id, status=str(body.get("status") or "ready"))
            else:
                try:
                    db.set_rating(con, clip_id, 1, [str(r) for r in body.get("reasons") or []])
                except ValueError as exc:
                    raise HTTPException(400, str(exc)) from exc
                if clip["status"] == "ready":
                    db.update_clip(con, clip_id, status="skipped")
        broker.publish("clips.changed", {"id": clip_id})
        return {"ok": True}

    @app.get("/api/reasons")
    def reasons() -> list[ReasonGroup]:
        """The rating reasons the clip panel offers: one list, here (studio/db.py REASONS)."""
        from ..learn import feedback

        groups = (("What worked", "good", feedback.GOOD), ("What didn't", "bad", feedback.MOMENT_BAD),
                  ("In the edit", "edit", feedback.EDIT))
        return [ReasonGroup(label=label, tone=tone,
                            reasons=[ReasonOption(key=k, label=db.REASONS[k]) for k in keys])
                for label, tone, keys in groups]

    @app.get("/api/learning")
    def learning() -> Learning:
        from ..config import Config
        from ..learn import feedback

        snap = Snapshot()
        views = {c.id: sum(p.views or 0 for p in c.posts) for c in snap.clips if c.posts}
        with db.connect() as con:
            active = db.settings(con).get("learn_from_feedback", "1") == "1"
        clips = feedback.from_rows(snap.raw_clips, views, stats.clip_performance(snap.raw_clips, snap.rows))
        result = feedback.report(clips, Config.load().llm.rubric_weights.as_dict(), active=active)
        return Learning(active=active, min_for_weights=feedback.MIN_FOR_WEIGHTS,
                        min_for_agreement=feedback.MIN_FOR_AGREEMENT, **result.__dict__)

    @app.get("/api/learning/taste")
    def taste_export() -> dict:
        """What Clipper has learnt about this user's taste, as a file to keep or give to a new install:
        the six rubric weights and the reasons they give most. No clip text, no titles (D148)."""
        result = learning()
        return {"version": 1, "rated": result.rated,
                "weights": {d.key: d.learned for d in result.dimensions},
                "reasons": [{"key": r.key, "count": r.count} for r in result.reasons]}

    @app.put("/api/learning/taste")
    def taste_import(body: dict) -> dict:
        """Start from a taste file: its weights are the prior until this install's own ratings take over."""
        from ..learn import feedback

        weights = body.get("weights")
        valid = isinstance(weights, dict) and set(weights) == set(feedback.RUBRIC) \
            and all(isinstance(v, int | float) and 0 <= v <= 1 for v in weights.values())
        if not valid:
            raise HTTPException(400, "that isn't a taste file from Clipper")
        with db.connect() as con:
            db.set_setting(con, "taste_seed", json.dumps({"weights": {k: float(v) for k, v in weights.items()}}))
        return {"imported": True}

    @app.delete("/api/learning/taste")
    def taste_clear() -> dict:
        with db.connect() as con:
            db.set_setting(con, "taste_seed", "")
        return {"cleared": True}

    @app.get("/api/learning/compare")
    def whats_working() -> WhatsWorking:
        """Posts with each change against posts without it, fairly (D105)."""
        from ..learn import compare

        snap = Snapshot()
        since = snap.settings.get("tiktok_disclosed_since", "")
        disclosed_from = compare.parse(since)
        history: dict[str, list] = {}
        with db.connect() as con:
            for r in con.execute("SELECT url, at, views, skip_rate_pct FROM snapshots ORDER BY at"):
                history.setdefault(r["url"], []).append((compare.parse(r["at"]), r["views"], r["skip_rate_pct"]))
        raw = {c["id"]: c for c in snap.raw_clips}
        facts = []
        for clip in snap.clips:
            scores = json.loads(raw[clip.id].get("scores") or "{}") or {}
            for p in clip.posts:
                posted = compare.parse(p.posted_at)
                traits = {"cover": scores.get("cover") is not None, "edited": bool(scores.get("edit"))}
                if scores.get("picked_by") == "auto":  # only Clipper's picks can open on a payoff
                    traits["payoff"] = bool(scores.get("teaser"))
                if p.platform == "tiktok" and disclosed_from and posted:
                    traits["disclosed"] = posted >= disclosed_from
                facts.append(compare.PostFacts(
                    url=p.url, platform=p.platform, posted_at=posted,
                    history=tuple(h for h in history.get(p.url.split("?", 1)[0], []) if h[0]),
                    traits=traits, hook=clip.hook or "", campaign=clip.campaign))
        return WhatsWorking(age_hours=compare.AGE_HOURS, min_each=compare.MIN_EACH,
                            comparisons=[c.__dict__ | {"rows": [r.__dict__ | {"yes": r.yes.__dict__, "no": r.no.__dict__}
                                                                for r in c.rows]}
                                         for c in compare.compare(facts)],
                            hooks=[h.__dict__ for h in compare.hooks(facts)], disclosed_since=since)

    @app.get("/api/posts/history")
    def post_history(url: str) -> list[PostPoint]:
        with db.connect() as con:
            return [PostPoint(**p) for p in db.history(con, url.split("?", 1)[0])]

    @app.post("/api/sync")
    async def sync() -> dict:
        return await sync_now()

    @app.get("/api/accounts")
    def get_accounts() -> list[Account]:
        found = accounts()
        posts = Snapshot().posts
        groups = account_groups.groups()
        for a in found:
            a.key = account_groups.key(a.platform, a.handle)
            mine = [p for p in posts if account_groups.key(p.platform, p.account) == a.key]
            a.posts, a.views = len(mine), sum(p.views or 0 for p in mine)
            a.groups = [g["name"] for g in groups if a.key in g["members"]]
        return found

    @app.get("/api/account-groups")
    def list_groups() -> list[AccountGroup]:
        return [AccountGroup(**g) for g in account_groups.groups()]

    @app.post("/api/account-groups")
    def save_group(group: AccountGroup) -> AccountGroup:
        """Create a group, or rename/re-fill one (its id set). Part of Pro (D89)."""
        from . import plans

        plans.require("multi_account")
        try:
            group.id = account_groups.save_group(group.name, group.members, group.campaigns, group.id)
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        broker.publish("accounts.changed")
        return group

    @app.delete("/api/account-groups/{group_id}")
    def delete_group(group_id: int) -> dict:
        if not account_groups.delete_group(group_id):
            raise HTTPException(404, "no such group")
        broker.publish("accounts.changed")
        return {"ok": True}

    @app.delete("/api/accounts/{platform}/{account}")
    def disconnect(platform: str, account: str) -> dict:
        from ..instagram import api as ig_api
        from ..tiktok import api as tt_api
        from ..x import api as x_api
        from ..youtube import api as yt_api

        module = {"tiktok": tt_api, "instagram": ig_api, "youtube": yt_api, "x": x_api}.get(platform)
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
        if not yt_has_app() and not connect.enabled():
            raise HTTPException(400, "Save your Google app's client ID and secret first")
        return youtube.start()

    @app.get("/api/accounts/youtube/connect")
    def youtube_connect_state() -> dict[str, str]:
        return youtube.view()

    @app.post("/api/accounts/broker/return")
    async def broker_return(request: Request) -> HTMLResponse:
        """Where the connect service's page posts a finished login (D151). Only a login this Clipper started
        is accepted: its nonce is checked."""
        try:    # a plain form post; read by hand so Clipper needs no multipart package
            body = json.loads(urllib.parse.parse_qs((await request.body()).decode()).get("payload", ["{}"])[0])
        except ValueError:
            body = {}
        ok = connect.deliver(str(body.get("nonce", "")), {"error": body.get("error")} if body.get("error") else body.get("tokens") or {})
        return HTMLResponse("<p style='font-family:sans-serif;padding:2em'>"
                            + ("Connected. You can close this tab and go back to Clipper." if ok else "This login wasn't started here. Try again from Clipper.")
                            + "</p>")

    @app.post("/api/accounts/instagram/connect")
    def instagram_connect_start() -> dict[str, str]:
        """Start Instagram's login; the page opens the returned consent link (D150)."""
        from ..instagram import api as ig_api

        if not ig_api.has_app() and not connect.enabled():
            raise HTTPException(400, "Save your Meta app's ID and secret first")
        return instagram.start()

    @app.get("/api/accounts/instagram/connect")
    def instagram_connect_state() -> dict[str, str]:
        return instagram.view()

    @app.post("/api/accounts/x")
    def x_connect(body: dict) -> dict:
        """An X account by its username, read with the app's Bearer Token (D83)."""
        from ..x import api as x_api

        before = set(x_api.account_files())
        try:
            account = x_api.connect(str(body.get("username") or ""))
        except x_api.XError as exc:
            raise HTTPException(400, str(exc)) from exc
        refused = account_groups.undo_if_over("x", before)
        if refused:
            raise HTTPException(402, refused)
        broker.publish("accounts.changed")
        return {"username": account["username"]}

    @app.post("/api/accounts/instagram")
    def instagram_connect(body: dict) -> dict:
        from ..instagram import api as ig_api

        before = set(ig_api.token_files())
        try:
            username = ig_api.login(str(body.get("token") or ""))
        except ig_api.InstagramError as exc:
            raise HTTPException(400, f"Instagram refused the token: {exc}") from exc
        refused = account_groups.undo_if_over("instagram", before)
        if refused:
            raise HTTPException(402, refused)
        broker.publish("accounts.changed")
        return {"username": username}

    @app.get("/api/setup")
    def get_setup() -> Setup:
        ai = setup.ai_status()
        return Setup(ai_ready=ai["ready"], ai_backend=ai["backend"], ai_detail=ai["detail"],
                     keys={k: setup.key_set(k) for k in setup.KEYS},
                     tiktok_app=connect.enabled() or (setup.key_set("TIKTOK_CLIENT_KEY")
                                                     and setup.key_set("TIKTOK_CLIENT_SECRET")),
                     tiktok_connect=tiktok.view(),
                     youtube_app=yt_has_app(),
                     instagram_app=connect.enabled() or (setup.key_set("INSTAGRAM_APP_ID") and setup.key_set("INSTAGRAM_APP_SECRET")))

    @app.put("/api/setup/keys")
    def put_keys(values: dict[str, str]) -> Setup:
        try:
            setup.set_keys(values)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        broker.publish("settings.changed")
        return get_setup()

    @app.get("/api/setup/site/{page}")
    def site_page(page: str) -> PlainTextResponse:
        """The homepage and privacy policy a Google app needs to be published (docs/site, D79)."""
        if page not in ("index.html", "privacy.html", "terms.html"):
            raise HTTPException(404, "no such page")
        return PlainTextResponse((REPO_ROOT / "docs" / "site" / page).read_text(encoding="utf-8"))

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
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path.parent)])
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
        """Footage on this PC, each with the campaign it belongs to (studio/footage.py)."""
        from . import footage

        return footage.sort(list_sources(), load_campaigns())

    @app.put("/api/uploads/{filename}")
    async def upload(filename: str, request: Request, campaign: str = "") -> dict:
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
        if campaign:
            from . import footage

            footage.remember([str(target)], campaign, "added")
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
        return importer.start(url, found, pick or None, campaign=str(body.get("campaign") or "")).view()

    @app.get("/api/imports")
    def list_imports() -> list[dict]:
        return importer.list()

    @app.get("/api/jobs")
    def list_jobs() -> list[dict]:
        return jobs.list()

    @app.post("/api/jobs")
    def start_job(body: dict) -> list[dict]:
        """One job per video, queued and run in turn (D72)."""
        from . import footage, plans

        campaign = load_campaigns().get(str(body.get("campaign") or ""))
        if campaign is None:
            raise HTTPException(400, "pick a campaign")
        wanted = [str(s) for s in body.get("sources") or [body.get("source") or ""] if s]
        if not wanted:
            raise HTTPException(400, "pick a video")
        cap = plans.batch_limit()
        if cap is not None and len(wanted) > cap:
            raise HTTPException(402, f"Your plan clips up to {cap} videos at a time; Pro has no limit.")
        sources = list(dict.fromkeys(str(allowed_source(s)) for s in wanted))
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
            if len(sources) > 1:
                raise HTTPException(400, "hand-picked times belong to one video; pick just that one")
            footage.remember(sources, campaign.name, "clipped")
            return [jobs.submit(campaign, sources[0], None, ranges).view()]
        top = max(1, min(500, int(body.get("top") or 4))) if mode == "top" else None
        top = plans.clip_count(top, campaign.max_clips_per_source)
        footage.remember(sources, campaign.name, "clipped")
        return [jobs.submit(campaign, s, top).view() for s in sources]

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
        # A clip that opens on its chosen cover shows that, as the platforms will (D104).
        covered = (json.loads(found.get("scores") or "{}") or {}).get("cover") is not None
        still = library.thumbnail(path, at=0.0 if covered else 1.5)
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
    if _running(port) and not _replace_older(port):
        # Opened twice (e.g. the desktop shortcut clicked again): show the one running.
        if open_browser:
            webbrowser.open(url)
        return
    if open_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    if HOST not in LOOPBACK and not os.environ.get("CLIPPER_TOKEN", "").strip():
        raise SystemExit("CLIPPER_HOST makes Clipper reachable from other computers: set CLIPPER_TOKEN too "
                         "(any long secret), or it would be open to anyone who finds it. See docs/HOSTING.md.")
    uvicorn.run(create_app(auto_sync=True), host=HOST, port=port, log_level="warning")


def code_version() -> str:
    """The commit this copy of Clipper is on ("" when it isn't a git checkout)."""
    import subprocess

    from ..paths import REPO_ROOT

    try:
        return subprocess.run(["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"], capture_output=True,
                              text=True, timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def _replace_older(port: int) -> bool:
    """When the Clipper already running started on older code than what's on disk now, stop
    it, so this one starts on the new code (D116). The desktop icon only ever opened the
    running server, so a Clipper left running in the background kept old code for days:
    updates pulled, never used. True when the port is free again."""
    import time

    import httpx

    here = code_version()
    base = f"http://{HOST}:{port}"
    try:
        there = httpx.get(f"{base}/api/code-version", timeout=3).json().get("version", "")
    except (httpx.HTTPError, ValueError):
        there = ""  # a Clipper from before it could say: older by definition
    if not here or there == here:
        return False
    try:
        if httpx.post(f"{base}/api/quit", timeout=3).status_code != 200:
            return False  # busy (or too old to know /api/quit): leave it running
    except httpx.HTTPError:
        return False
    for _ in range(40):
        time.sleep(0.25)
        if not _running(port):
            return True
    return False


def _running(port: int) -> bool:
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex((HOST, port)) == 0
