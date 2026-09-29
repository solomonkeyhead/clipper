"""The Research section's API: chat threads, niches and their briefs, saved items.

Mounted by server.create_app. Everything needs the Research plan; running a
proposed action (research/agent.py) needs Pro (studio/plans.py).
"""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..research import agent, radar, sources
from ..utils.logging import get_logger
from . import db, plans, setup

log = get_logger(__name__)


class ResearchStatus(BaseModel):
    plan: str
    can_research: bool
    can_act: bool
    ai: bool
    web: bool
    youtube: bool


class Action(BaseModel):
    type: str                  # hook_lines | clip_job | campaign
    label: str
    params: dict
    done: bool = False


class Source(BaseModel):
    title: str
    url: str


class Message(BaseModel):
    id: int
    role: str
    content: str
    sources: list[Source]
    actions: list[Action]
    created_at: str


class Thread(BaseModel):
    id: int
    title: str
    niche_id: int | None = None
    updated_at: str


class ThreadDetail(Thread):
    messages: list[Message]


class Niche(BaseModel):
    id: int
    name: str
    description: str
    keywords: list[str]
    brief: dict | None = None
    brief_at: str | None = None
    stale: bool


class SavedItem(BaseModel):
    id: int
    kind: str
    text: str
    url: str
    niche_id: int | None = None
    created_at: str


def _message(row) -> Message:
    return Message(id=row["id"], role=row["role"], content=row["content"],
                   sources=json.loads(row["sources"]), actions=json.loads(row["actions"]),
                   created_at=row["created_at"])


def _niche(row) -> Niche:
    niche = dict(row)
    return Niche(id=row["id"], name=row["name"], description=row["description"],
                 keywords=json.loads(row["keywords"]),
                 brief=json.loads(row["brief"]) if row["brief"] else None,
                 brief_at=row["brief_at"], stale=radar.is_stale(niche))


def niche_rows() -> list[dict]:
    with db.connect() as con:
        rows = [dict(r) for r in con.execute("SELECT * FROM niches ORDER BY name COLLATE NOCASE")]
    for r in rows:
        r["keywords"] = json.loads(r["keywords"])
    return rows


def toolbox() -> agent.Toolbox:
    """The user's own data, in the shape the chat's tools return."""
    from . import server

    def clips(campaign: str | None) -> list[dict]:
        return [{
            "title": c.title, "campaign": c.campaign, "status": c.status, "score": c.score,
            "rating": c.rating, "made": c.created_at, "seconds": c.duration_s,
            "posts": [{"platform": p.platform, "views": p.views, "likes": p.likes,
                       "avg_watch_s": p.avg_watch_s, "skip_rate_pct": p.skip_rate_pct,
                       "x_median": p.x_median, "posted": p.posted_at} for p in c.posts],
        } for c in server.Snapshot().clips if campaign is None or c.campaign == campaign]

    def campaigns() -> list[dict]:
        snap = server.Snapshot()
        out = []
        for name in snap.campaign_names():
            c, brief = snap.campaign(name), snap.campaigns.get(name)
            out.append({"id": name, "title": c.title, "archived": c.archived,
                        "pay_per_1k_usd": c.reward_per_1k_usd, "platforms": c.platforms,
                        "clips": c.clips, "views": c.views, "est_earnings_usd": c.est_earnings,
                        **({"seconds": [brief.duration.min_seconds, brief.duration.max_seconds],
                            "focus": brief.selection_focus, "hook_lines": list(brief.hook_texts)}
                           if brief else {})})
        return out

    def footage() -> list[dict]:
        return [{"name": s["name"], "path": s["path"], "size_mb": s["size_mb"]}
                for s in server.list_sources()]

    def niches() -> list[dict]:
        return [{"name": n["name"], "description": n["description"], "keywords": n["keywords"],
                 "brief": json.loads(n["brief"]) if n.get("brief") else None}
                for n in niche_rows()]

    return agent.Toolbox(clips=clips, campaigns=campaigns, footage=footage, niches=niches)


def build_router(broker, jobs) -> APIRouter:
    r = APIRouter(prefix="/api/research")

    @r.get("/status")
    def status() -> ResearchStatus:
        plan = plans.current()
        return ResearchStatus(plan=plan, can_research=plans.has("research", plan),
                              can_act=plans.has("research_actions", plan),
                              ai=setup.key_set("GEMINI_API_KEY"), web=sources.has_web(),
                              youtube=sources.has_youtube())

    # ---------- chat ----------

    @r.get("/threads")
    def threads() -> list[Thread]:
        plans.require("research")
        with db.connect() as con:
            return [Thread(**dict(t)) for t in con.execute(
                "SELECT id, title, niche_id, updated_at FROM research_threads ORDER BY updated_at DESC")]

    @r.get("/threads/{thread_id}")
    def thread(thread_id: int) -> ThreadDetail:
        plans.require("research")
        with db.connect() as con:
            row = con.execute("SELECT id, title, niche_id, updated_at FROM research_threads WHERE id=?",
                              (thread_id,)).fetchone()
            if row is None:
                raise HTTPException(404, "no such conversation")
            messages = [_message(m) for m in con.execute(
                "SELECT * FROM research_messages WHERE thread_id=? ORDER BY id", (thread_id,))]
        return ThreadDetail(**dict(row), messages=messages)

    @r.delete("/threads/{thread_id}")
    def delete_thread(thread_id: int) -> dict:
        plans.require("research")
        with db.connect() as con:
            con.execute("DELETE FROM research_messages WHERE thread_id=?", (thread_id,))
            con.execute("DELETE FROM research_threads WHERE id=?", (thread_id,))
        return {"ok": True}

    @r.post("/ask")
    async def ask(body: dict) -> ThreadDetail:
        """Ask in a conversation (a new one without `thread_id`); returns it with the answer."""
        plans.require("research")
        text = " ".join(str(body.get("text") or "").split())
        if not text:
            raise HTTPException(400, "type a question")
        niche_id = body.get("niche_id")
        with db.connect() as con:
            thread_id = body.get("thread_id")
            if thread_id is None:
                cur = con.execute("INSERT INTO research_threads (title, niche_id, created_at, updated_at) "
                                  "VALUES (?, ?, ?, ?)", (agent.title_for(text), niche_id, db.now(), db.now()))
                thread_id = cur.lastrowid
            else:
                found = con.execute("SELECT niche_id FROM research_threads WHERE id=?", (thread_id,)).fetchone()
                if found is None:
                    raise HTTPException(404, "no such conversation")
                niche_id = niche_id or found["niche_id"]
            history = [(m["role"], m["content"]) for m in con.execute(
                "SELECT role, content FROM research_messages WHERE thread_id=? ORDER BY id", (thread_id,))]
            con.execute("INSERT INTO research_messages (thread_id, role, content, created_at) "
                        "VALUES (?, 'user', ?, ?)", (thread_id, text, db.now()))
        niche = next((n for n in niche_rows() if n["id"] == niche_id), None) if niche_id else None
        can_act = plans.has("research_actions")

        def progress(step: str) -> None:
            broker.publish("research.progress", {"thread_id": thread_id, "step": step})

        broker.publish("research.progress", {"thread_id": thread_id, "step": "Thinking"})
        try:
            answer = await asyncio.to_thread(agent.ask, text, history, toolbox(), niche=niche,
                                             can_act=can_act, progress=progress)
        except agent.ResearchError as exc:
            answer = agent.Answer(text=f"⚠️ {exc}")
        except Exception as exc:  # shown in the chat, logged for debugging
            log.exception("research question failed")
            answer = agent.Answer(text=f"⚠️ Something went wrong: {str(exc)[:200]}")
        with db.connect() as con:
            con.execute("INSERT INTO research_messages (thread_id, role, content, sources, actions, "
                        "created_at) VALUES (?, 'assistant', ?, ?, ?, ?)",
                        (thread_id, answer.text, agent.dumps(answer.sources),
                         agent.dumps(answer.actions), db.now()))
            con.execute("UPDATE research_threads SET updated_at=? WHERE id=?", (db.now(), thread_id))
        broker.publish("research.progress", {"thread_id": thread_id, "step": ""})
        return thread(thread_id)

    @r.post("/messages/{message_id}/actions/{index}")
    def run_action(message_id: int, index: int) -> dict:
        """Do what the chat proposed, now that the user confirmed it (Pro)."""
        plans.require("research_actions")
        with db.connect() as con:
            row = con.execute("SELECT actions FROM research_messages WHERE id=?", (message_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "no such message")
        actions = json.loads(row["actions"])
        if not 0 <= index < len(actions):
            raise HTTPException(404, "no such action")
        action = actions[index]
        if action.get("done"):
            raise HTTPException(400, "already done")
        result = _perform(action, jobs)
        action["done"] = True
        with db.connect() as con:
            con.execute("UPDATE research_messages SET actions=? WHERE id=?",
                        (agent.dumps(actions), message_id))
        broker.publish("campaigns.changed")
        return result

    # ---------- niches ----------

    @r.get("/niches")
    def niches() -> list[Niche]:
        plans.require("research")
        with db.connect() as con:
            return [_niche(n) for n in con.execute("SELECT * FROM niches ORDER BY name COLLATE NOCASE")]

    def _clean(body: dict) -> tuple[str, str, list[str]]:
        name = " ".join(str(body.get("name") or "").split())[:80]
        if not name:
            raise HTTPException(400, "give the niche a name")
        keywords = [" ".join(str(k).split()) for k in body.get("keywords") or [] if str(k).strip()][:10]
        return name, str(body.get("description") or "").strip()[:500], keywords

    @r.post("/niches")
    def add_niche(body: dict) -> Niche:
        plans.require("research")
        name, description, keywords = _clean(body)
        with db.connect() as con:
            cur = con.execute("INSERT INTO niches (name, description, keywords, created_at) VALUES (?, ?, ?, ?)",
                              (name, description, json.dumps(keywords), db.now()))
            return _niche(con.execute("SELECT * FROM niches WHERE id=?", (cur.lastrowid,)).fetchone())

    @r.put("/niches/{niche_id}")
    def edit_niche(niche_id: int, body: dict) -> Niche:
        plans.require("research")
        name, description, keywords = _clean(body)
        with db.connect() as con:
            con.execute("UPDATE niches SET name=?, description=?, keywords=? WHERE id=?",
                        (name, description, json.dumps(keywords), niche_id))
            row = con.execute("SELECT * FROM niches WHERE id=?", (niche_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "no such niche")
        return _niche(row)

    @r.delete("/niches/{niche_id}")
    def delete_niche(niche_id: int) -> dict:
        plans.require("research")
        with db.connect() as con:
            con.execute("DELETE FROM niches WHERE id=?", (niche_id,))
        return {"ok": True}

    @r.post("/niches/{niche_id}/refresh")
    async def refresh_niche(niche_id: int) -> Niche:
        plans.require("research")
        return await asyncio.to_thread(refresh_one, niche_id, broker)

    # ---------- saved ----------

    @r.get("/saved")
    def saved() -> list[SavedItem]:
        plans.require("research")
        with db.connect() as con:
            return [SavedItem(**dict(s)) for s in con.execute("SELECT * FROM saved_items ORDER BY id DESC")]

    @r.post("/saved")
    def save(body: dict) -> SavedItem:
        plans.require("research")
        kind = str(body.get("kind") or "idea")
        if kind not in ("hook", "idea", "answer", "link"):
            raise HTTPException(400, "kind must be hook, idea, answer or link")
        text = str(body.get("text") or "").strip()[:8000]
        if not text:
            raise HTTPException(400, "nothing to save")
        with db.connect() as con:
            cur = con.execute("INSERT INTO saved_items (kind, text, url, niche_id, created_at) VALUES (?, ?, ?, ?, ?)",
                              (kind, text, str(body.get("url") or "")[:500], body.get("niche_id"), db.now()))
            return SavedItem(**dict(con.execute("SELECT * FROM saved_items WHERE id=?",
                                                (cur.lastrowid,)).fetchone()))

    @r.delete("/saved/{item_id}")
    def unsave(item_id: int) -> dict:
        plans.require("research")
        with db.connect() as con:
            con.execute("DELETE FROM saved_items WHERE id=?", (item_id,))
        return {"ok": True}

    return r


def refresh_one(niche_id: int, broker) -> Niche:
    """Rebuild one niche's brief (used by the page and the daily refresh)."""
    from ..config import Config
    from ..pipeline import build_backend

    niche = next((n for n in niche_rows() if n["id"] == niche_id), None)
    if niche is None:
        raise HTTPException(404, "no such niche")
    try:
        backend = build_backend(Config.load())
    except Exception as exc:
        raise HTTPException(400, "Briefs need your AI key: add it in Settings.") from exc

    def progress(step: str) -> None:
        broker.publish("research.progress", {"niche_id": niche_id, "step": step})

    try:
        brief = radar.refresh(niche, backend, progress=progress)
    except Exception as exc:
        log.warning("niche brief failed: %s", exc)
        raise HTTPException(503, "The AI didn't answer (it may be busy); try again in a minute.") from exc
    finally:
        progress("")
    with db.connect() as con:
        con.execute("UPDATE niches SET brief=?, brief_at=? WHERE id=?",
                    (json.dumps(brief, ensure_ascii=False), db.now(), niche_id))
        return _niche(con.execute("SELECT * FROM niches WHERE id=?", (niche_id,)).fetchone())


def _perform(action: dict, jobs) -> dict:
    from ..campaign import editor
    from . import server

    params = action.get("params") or {}
    kind = action.get("type")
    if kind == "hook_lines":
        campaign = server.load_campaigns().get(params.get("campaign"))
        if campaign is None:
            raise HTTPException(400, "that campaign no longer exists")
        form = editor.to_form(campaign)
        lines = [line for line in params.get("lines") or [] if line not in form.hook_texts]
        form.hook_texts = [*form.hook_texts, *lines]
        try:
            editor.update(server.campaigns_dir(), campaign.name, form)
        except editor.CampaignError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"done": f"Added {len(lines)} hook line(s) to {editor.title_of(campaign)}"}
    if kind == "clip_job":
        campaign = server.load_campaigns().get(params.get("campaign"))
        if campaign is None:
            raise HTTPException(400, "that campaign no longer exists")
        source = server.allowed_source(str(params.get("source") or ""))
        count = params.get("count")
        job = jobs.submit(campaign, str(source), int(count) if count else None)
        return {"done": "Clipping started; progress is on the New clips page", "job": job.id,
                "navigate": "/new"}
    if kind == "campaign":
        return {"done": "Opening the new campaign form", "navigate": "/campaigns/new",
                "title": params.get("title", ""), "brief": params.get("brief", "")}
    raise HTTPException(400, f"unknown action {kind!r}")
