"""Campaigns found for the user and the alerts that find them (Discord, Whop, push): the server's endpoints
for them, moved out of server.py when it passed 2,200 lines. Wired in by `routes`, like create_api."""

from __future__ import annotations

import asyncio

from fastapi import FastAPI, HTTPException

from . import alerts, setup
from .api_models import (
    AlertChannel,
    AlertCheck,
    Alerts,
    CampaignCheck,
    DiscordBot,
    FoundCampaign,
    WhopFeed,
)
from .campaigns_api import check_brief


def routes(app: FastAPI, publish, *, whop_login) -> None:
    """`whop_login`: the Whop sign-in under way."""

    @app.get("/api/found")
    def found_campaigns() -> list[FoundCampaign]:
        import threading

        from . import finder

        found = [FoundCampaign(**f) for f in finder.found()]
        # Campaigns found before niches existed (or when no AI answered) are sorted in the background.
        if any(not f.niche for f in found):
            threading.Thread(target=finder.sort_niches, args=(publish,), name="niches", daemon=True).start()
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
        publish("alerts.changed")
        return get_alerts()

    @app.put("/api/alerts/prefs")
    def alert_prefs(body: dict) -> Alerts:
        try:
            alerts.set_prefs(str(body.get("profile") or ""), float(body.get("min_rate") or 0))
        except (TypeError, ValueError) as exc:
            raise HTTPException(400, str(exc)) from exc
        publish("alerts.changed")
        return get_alerts()

    @app.post("/api/alerts/check")
    async def check_alerts() -> AlertCheck:
        """Check the watched channels now, rather than at the next scheduled check."""
        result = await asyncio.to_thread(alerts.check, publish=publish)
        publish("alerts.changed")
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
        publish("alerts.changed")
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
        publish("alerts.changed")
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
