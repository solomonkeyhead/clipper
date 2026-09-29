"""The Ask chat's API: conversations with the research assistant (research/agent.py).

Mounted by server.create_app; needs the Research plan (studio/plans.py).
"""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..research import agent, sources
from ..utils.logging import get_logger
from . import db, plans, setup

log = get_logger(__name__)


class ResearchStatus(BaseModel):
    plan: str
    can_research: bool
    ai: bool
    web: bool


class Source(BaseModel):
    title: str
    url: str


class Message(BaseModel):
    id: int
    role: str
    content: str
    sources: list[Source]
    created_at: str


class Thread(BaseModel):
    id: int
    title: str
    updated_at: str


class ThreadDetail(Thread):
    messages: list[Message]


def _message(row) -> Message:
    return Message(id=row["id"], role=row["role"], content=row["content"],
                   sources=json.loads(row["sources"]), created_at=row["created_at"])


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

    return agent.Toolbox(clips=clips, campaigns=campaigns)


def build_router(broker) -> APIRouter:
    r = APIRouter(prefix="/api/research")

    @r.get("/status")
    def status() -> ResearchStatus:
        plan = plans.current()
        return ResearchStatus(plan=plan, can_research=plans.has("research", plan),
                              ai=setup.key_set("GEMINI_API_KEY"), web=sources.has_web())

    # ---------- chat ----------

    @r.get("/threads")
    def threads() -> list[Thread]:
        plans.require("research")
        with db.connect() as con:
            return [Thread(**dict(t)) for t in con.execute(
                "SELECT id, title, updated_at FROM research_threads ORDER BY updated_at DESC")]

    @r.get("/threads/{thread_id}")
    def thread(thread_id: int) -> ThreadDetail:
        plans.require("research")
        with db.connect() as con:
            row = con.execute("SELECT id, title, updated_at FROM research_threads WHERE id=?",
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
        with db.connect() as con:
            thread_id = body.get("thread_id")
            if thread_id is None:
                cur = con.execute("INSERT INTO research_threads (title, created_at, updated_at) "
                                  "VALUES (?, ?, ?)", (agent.title_for(text), db.now(), db.now()))
                thread_id = cur.lastrowid
            elif con.execute("SELECT 1 FROM research_threads WHERE id=?", (thread_id,)).fetchone() is None:
                raise HTTPException(404, "no such conversation")
            history = [(m["role"], m["content"]) for m in con.execute(
                "SELECT role, content FROM research_messages WHERE thread_id=? ORDER BY id", (thread_id,))]
            con.execute("INSERT INTO research_messages (thread_id, role, content, created_at) "
                        "VALUES (?, 'user', ?, ?)", (thread_id, text, db.now()))

        def progress(step: str) -> None:
            broker.publish("research.progress", {"thread_id": thread_id, "step": step})

        progress("Thinking")
        try:
            answer = await asyncio.to_thread(agent.ask, text, history, toolbox(), progress=progress)
        except agent.ResearchError as exc:
            answer = agent.Answer(text=f"⚠️ {exc}")
        except Exception as exc:  # shown in the chat, logged for debugging
            log.exception("research question failed")
            answer = agent.Answer(text=f"⚠️ Something went wrong: {str(exc)[:200]}")
        with db.connect() as con:
            con.execute("INSERT INTO research_messages (thread_id, role, content, sources, "
                        "created_at) VALUES (?, 'assistant', ?, ?, ?)",
                        (thread_id, answer.text, agent.dumps(answer.sources), db.now()))
            con.execute("UPDATE research_threads SET updated_at=? WHERE id=?", (db.now(), thread_id))
        progress("")
        return thread(thread_id)

    return r
