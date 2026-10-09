"""Accounts, connecting them, setup keys and settings: the server's endpoints for them, moved out of
server.py when it passed 2,200 lines. Wired in by `routes`, like create_api."""

from __future__ import annotations

import asyncio
import json
import urllib.parse

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse

from .. import connect
from ..paths import REPO_ROOT
from . import accounts as account_groups
from . import db, server, setup
from .api_models import Account, AccountGroup, Setup


def routes(app: FastAPI, publish, *, tiktok, youtube, instagram, last_problems: list[str]) -> None:
    """`tiktok`, `youtube`, `instagram`: the logins under way; `last_problems`: the last sync's, kept by the server."""

    @app.get("/api/accounts")
    def get_accounts() -> list[Account]:
        found = server.accounts()
        posts = server.Snapshot().posts
        groups = account_groups.groups()
        for a in found:
            a.key = account_groups.key(a.platform, a.handle)
            mine = [p for p in posts if account_groups.key(p.platform, p.account) == a.key]
            a.posts, a.views = len(mine), sum(p.views or 0 for p in mine)
            a.groups = [g["name"] for g in groups if a.key in g["members"]]
            # The last sync's problem with this account, so a failing one doesn't read "ok" here.
            label = {"tiktok": "TikTok", "instagram": "Instagram", "youtube": "YouTube", "x": "X"}[a.platform]
            failed = next((p for p in last_problems if p.startswith(f"{label} ")
                           and p.split(":", 1)[0].removeprefix(f"{label} ").lstrip("@") == a.handle), "")
            if failed:
                a.health, a.detail = "error", f"Last sync failed: {failed.split(':', 1)[1].strip()}"
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
        publish("accounts.changed")
        return group

    @app.delete("/api/account-groups/{group_id}")
    def delete_group(group_id: int) -> dict:
        if not account_groups.delete_group(group_id):
            raise HTTPException(404, "no such group")
        publish("accounts.changed")
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
        publish("accounts.changed")
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
        if not server.yt_has_app() and not connect.enabled():
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
        publish("accounts.changed")
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
        publish("accounts.changed")
        return {"username": username}

    @app.get("/api/setup")
    def get_setup() -> Setup:
        ai = setup.ai_status()
        return Setup(ai_ready=ai["ready"], ai_backend=ai["backend"], ai_detail=ai["detail"],
                     keys={k: setup.key_set(k) for k in setup.KEYS},
                     tiktok_app=connect.enabled() or (setup.key_set("TIKTOK_CLIENT_KEY")
                                                     and setup.key_set("TIKTOK_CLIENT_SECRET")),
                     tiktok_connect=tiktok.view(),
                     youtube_app=server.yt_has_app(),
                     instagram_app=connect.enabled() or (setup.key_set("INSTAGRAM_APP_ID") and setup.key_set("INSTAGRAM_APP_SECRET")))

    @app.put("/api/setup/keys")
    def put_keys(values: dict[str, str]) -> Setup:
        try:
            setup.set_keys(values)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        publish("settings.changed")
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
        publish("settings.changed")
        return result
