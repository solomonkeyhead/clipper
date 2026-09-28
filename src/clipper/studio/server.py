"""The Control Center's web server: `clipper studio` (http://127.0.0.1:8765).

Local only -- it binds to 127.0.0.1 and serves one user's library. A JSON API
under /api feeds one static page (studio/static/). Campaign briefs are read
from campaigns/*.yaml, clips and their states from the database, and post
links and stats from the performance log.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from ..config import CampaignConfig
from ..learn import log as perf
from ..paths import REPO_ROOT
from ..utils.logging import get_logger
from . import db, library, stats

log = get_logger(__name__)

STATIC = Path(__file__).parent / "static"
HOST, PORT = "127.0.0.1", 8765


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


def campaign_summary(campaign: CampaignConfig) -> dict:
    """The rules worth seeing at a glance while posting."""
    return {
        "name": campaign.name,
        "platforms": list(campaign.platform_targets),
        "min_seconds": campaign.duration.min_seconds,
        "max_seconds": campaign.duration.max_seconds,
        "required_text": campaign.required_caption_text,
        "hashtags": list(campaign.required_hashtags),
        "credit": campaign.required_credit_text,
        "hook_overlay": campaign.hook_overlay,
        "original_audio": campaign.keep_original_audio,
        "authorization": campaign.source_authorization,
        "notes": campaign.notes,
    }


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


def effective_status(clip: dict, posts: list[dict]) -> str:
    """A clip with a live post is posted, whatever it was marked; later states stay."""
    if clip["status"] == "ready" and posts:
        return "posted"
    return clip["status"]


def create_app() -> FastAPI:
    app = FastAPI(title="Clipper Control Center", docs_url=None, redoc_url=None)

    def clip_view(clip: dict, posts: dict) -> dict:
        found = posts.get((clip["campaign"], clip["source_id"], clip["clip_id"]), [])
        return {**clip, "source_title": display_source(clip["source_title"]),
                "status": effective_status(clip, found), "marked": clip["status"],
                "posts": found, "video": f"/media/{clip['id']}",
                "exists": library.clip_path(clip["file"]).exists()}

    @app.get("/api/overview")
    def overview() -> dict:
        campaigns = load_campaigns()
        rows = perf.read()
        posts = stats.posts_by_clip(rows)
        with db.connect() as con:
            clips = [clip_view(c, posts) for c in db.clips(con)]
            state = db.campaign_state(con)
            settings = db.settings(con)
        names = sorted(set(campaigns) | {c["campaign"] for c in clips})
        out = []
        for name in names:
            mine = [c for c in clips if c["campaign"] == name]
            counts = {s: sum(1 for c in mine if c["status"] == s) for s in db.STATUSES}
            views = sum((p.get("views_latest") or 0) for c in mine for p in c["posts"])
            st = state.get(name, {})
            out.append({"name": name, "has_brief": name in campaigns,
                        "archived": bool(st.get("archived")), "auto_post": st.get("auto_post"),
                        "clips": len(mine), "counts": counts, "views": views,
                        "platforms": list(campaigns[name].platform_targets)
                        if name in campaigns else []})
        return {"campaigns": out, "settings": settings,
                "last_synced": stats.last_synced(rows)}

    @app.get("/api/campaigns/{name}")
    def campaign(name: str) -> dict:
        campaigns = load_campaigns()
        posts = stats.posts_by_clip(perf.read())
        with db.connect() as con:
            clips = [clip_view(c, posts) for c in db.clips(con, name)]
            st = db.campaign_state(con).get(name, {})
        if name not in campaigns and not clips:
            raise HTTPException(404, f"no campaign {name!r}")
        return {"campaign": campaign_summary(campaigns[name]) if name in campaigns
                else {"name": name},
                "archived": bool(st.get("archived")), "auto_post": st.get("auto_post"),
                "clips": clips}

    @app.patch("/api/clips/{clip_id}")
    def update_clip(clip_id: int, changes: dict = Body(...)) -> dict:
        with db.connect() as con:
            if db.clip(con, clip_id) is None:
                raise HTTPException(404, "no such clip")
            try:
                # The page edits what the user owns; captions and ranges come from runs.
                changes = {k: v for k, v in changes.items() if k in ("status", "notes", "title")}
                db.update_clip(con, clip_id, **changes)
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from exc
            return db.clip(con, clip_id)

    @app.patch("/api/campaigns/{name}")
    def update_campaign(name: str, changes: dict = Body(...)) -> dict:
        with db.connect() as con:
            db.set_campaign_state(con, name, **changes)
            return db.campaign_state(con).get(name, {})

    @app.put("/api/settings")
    def update_settings(changes: dict = Body(...)) -> dict:
        with db.connect() as con:
            try:
                for key, value in changes.items():
                    db.set_setting(con, key, value)
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from exc
            return db.settings(con)

    @app.post("/api/sync")
    def sync() -> dict:
        rows = perf.read()
        problems = stats.sync_all(rows)
        try:
            perf.write(rows)
        except PermissionError:
            problems.append(f"{perf.log_path().name} is open in Excel; close it and sync again")
        return {"problems": problems, "last_synced": stats.last_synced(rows)}

    @app.post("/api/clips/{clip_id}/reveal")
    def reveal(clip_id: int) -> dict:
        """Show the clip's file in Explorer, selected, e.g. to drag it into an upload page."""
        with db.connect() as con:
            clip = db.clip(con, clip_id)
        if clip is None:
            raise HTTPException(404, "no such clip")
        path = library.clip_path(clip["file"])
        if not path.exists():
            raise HTTPException(404, "the file is missing from the library")
        if sys.platform == "win32":
            subprocess.Popen(["explorer", f"/select,{path}"])
        return {"path": str(path)}

    @app.get("/thumb/{clip_id}")
    def thumb(clip_id: int) -> FileResponse:
        """A still of the clip at 1.5s (hook on screen), made once and kept."""
        with db.connect() as con:
            clip = db.clip(con, clip_id)
        path = library.clip_path(clip["file"]) if clip else None
        if path is None or not path.exists():
            raise HTTPException(404, "no such clip file")
        still = library.thumbnail(path)
        if still is None:
            raise HTTPException(404, "could not make a still")
        return FileResponse(still, media_type="image/jpeg")

    @app.get("/media/{clip_id}")
    def media(clip_id: int) -> FileResponse:
        with db.connect() as con:
            clip = db.clip(con, clip_id)
        path = library.clip_path(clip["file"]) if clip else None
        if path is None or not path.exists():
            raise HTTPException(404, "no such clip file")
        return FileResponse(path, media_type="video/mp4")

    app.mount("/", StaticFiles(directory=STATIC, html=True), name="static")
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
    uvicorn.run(create_app(), host=HOST, port=port, log_level="warning")


def _running(port: int) -> bool:
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex((HOST, port)) == 0
