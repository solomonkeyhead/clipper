"""Campaigns: their pages, payouts, the campaign editor and reading a pasted brief. Moved out of server.py
when it passed 2,200 lines. Wired in by `routes`, like create_api."""

from __future__ import annotations

import asyncio
from datetime import datetime

from fastapi import FastAPI, HTTPException

from ..campaign import editor
from ..campaign.editor import CampaignError, CampaignForm
from ..utils.logging import get_logger
from . import db, rulecheck, server
from .api_models import Campaign, CampaignCheck, CampaignDetail, Fit, Payout

log = get_logger(__name__)


def check_brief(text: str) -> CampaignCheck:
    """Read a pasted brief with the AI and say how well the campaign fits this user."""
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
    return CampaignCheck(form=form, fit=Fit(**finder.fit(form, server.Snapshot(), server.accounts())))


def routes(app: FastAPI, publish) -> None:
    @app.get("/api/campaigns")
    def campaigns() -> list[Campaign]:
        snap = server.Snapshot()
        return [snap.campaign(n) for n in snap.campaign_names()]

    @app.get("/api/campaigns/{name}")
    def campaign(name: str) -> CampaignDetail:
        snap = server.Snapshot()
        if name not in snap.campaign_names():
            raise HTTPException(404, f"no campaign {name!r}")
        brief = snap.campaigns.get(name)
        return CampaignDetail(campaign=snap.campaign(name),
                              brief=server.brief_of(brief) if brief else None,
                              clips=[c for c in snap.clips if c.campaign == name])

    @app.patch("/api/campaigns/{name}")
    def update_campaign(name: str, changes: dict) -> dict:
        with db.connect() as con:
            try:
                db.set_campaign_state(con, name, **changes)
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from exc
        publish("campaigns.changed")
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
        publish("campaigns.changed")
        return Payout(**found)

    @app.delete("/api/payouts/{payout_id}")
    def delete_payout(payout_id: int) -> dict:
        with db.connect() as con:
            db.delete_payout(con, payout_id)
        publish("campaigns.changed")
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
        publish("clips.changed", {})
        return {"ok": True}

    @app.get("/api/campaigns/{name}/form")
    def campaign_form(name: str) -> CampaignForm:
        campaign = server.load_campaigns().get(name)
        if campaign is None:
            raise HTTPException(404, f"no campaign {name!r}")
        return editor.to_form(campaign)

    @app.post("/api/campaigns")
    def create_campaign(form: CampaignForm) -> dict:
        try:
            campaign = editor.create(server.campaigns_dir(), form)
        except CampaignError as exc:
            raise HTTPException(400, str(exc)) from exc
        publish("campaigns.changed")
        return {"name": campaign.name}

    @app.put("/api/campaigns/{name}")
    def edit_campaign(name: str, form: CampaignForm) -> dict:
        try:
            campaign = editor.update(server.campaigns_dir(), name, form)
        except CampaignError as exc:
            raise HTTPException(400, str(exc)) from exc
        publish("campaigns.changed")
        rulecheck.start(name, publish)  # the rules may have changed (D81)
        return {"name": campaign.name}

    @app.post("/api/campaigns/{name}/recheck")
    def recheck_campaign(name: str) -> dict:
        if name not in server.load_campaigns():
            raise HTTPException(404, f"no campaign {name!r}")
        rulecheck.start(name, publish)
        publish("clips.changed")
        return {"ok": True}

    @app.put("/api/campaigns/{name}/brief")
    def save_campaign_brief(name: str, body: dict) -> dict:
        """Keep the brief as pasted, whole, for Ask (D76)."""
        if name not in server.load_campaigns():
            raise HTTPException(404, f"no campaign {name!r}")
        text = str(body.get("text") or "").strip()
        if len(text) < 40:
            raise HTTPException(400, "that's too short to be a brief")
        with db.connect() as con:
            db.save_brief(con, name, text)
        rulecheck.start(name, publish)  # the AI check reads the brief itself
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
        path = editor.path_for(server.campaigns_dir(), name)
        if path is None:
            raise HTTPException(404, f"no campaign {name!r}")
        if not recycle(path):
            raise HTTPException(500, "could not move the file to the Recycle Bin")
        publish("campaigns.changed")
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

    @app.post("/api/campaigns/check")
    async def check_campaign(body: dict) -> CampaignCheck:
        """Read a pasted campaign brief and say how well it fits this user."""
        return await asyncio.to_thread(check_brief, str(body.get("text") or ""))
