"""Topics and videos for Create, in the Control Center's database (studio/db.py)."""

from __future__ import annotations

import json

from ..studio import db
from . import channel as channels

TOPIC_STATUSES = ("new", "used", "skipped")
VIDEO_STATUSES = ("draft", "approved", "voiced", "building", "built", "failed")


def topics(status: str | None = "new") -> list[dict]:
    """The ideas of the channel being worked on (create/channel.py)."""
    with db.connect() as con:
        sql = "SELECT * FROM create_topics WHERE channel=?" + (" AND status=?" if status else "") + " ORDER BY COALESCE(score, -1) DESC, felt DESC, id"
        return [dict(r) for r in con.execute(sql, (channels.active_slug(), *((status,) if status else ())))]


def add_topics(found: list[dict]) -> int:
    """Add new ideas, skipping any already on the list (same question, ignoring case)."""
    channel = channels.active_slug()
    with db.connect() as con:
        seen = {r["question"].strip().lower() for r in con.execute(
            "SELECT question FROM create_topics WHERE channel=?", (channel,))}
        added = 0
        for t in found:
            q = " ".join(str(t.get("question", "")).split())
            if not q or q.lower() in seen:
                continue
            con.execute("INSERT INTO create_topics (question, angle, felt, created_at, channel, score, series) "
                        "VALUES (?,?,?,?,?,?,?)",
                        (q, str(t.get("angle", "")).strip(), int(bool(t.get("felt"))), db.now(), channel,
                         t.get("score"), str(t.get("series") or "").strip()))
            seen.add(q.lower())
            added += 1
        return added


def set_topic(topic_id: int, status: str) -> None:
    if status not in TOPIC_STATUSES:
        raise ValueError(f"status must be one of {', '.join(TOPIC_STATUSES)}")
    with db.connect() as con:
        con.execute("UPDATE create_topics SET status=? WHERE id=?", (status, topic_id))


def topic(topic_id: int) -> dict | None:
    with db.connect() as con:
        row = con.execute("SELECT * FROM create_topics WHERE id=?", (topic_id,)).fetchone()
    return dict(row) if row else None


def videos(every: bool = False) -> list[dict]:
    """The videos of the channel being worked on, or (`every`) of all of them."""
    with db.connect() as con:
        if every:
            return [_video(r) for r in con.execute("SELECT * FROM create_videos ORDER BY id DESC")]
        return [_video(r) for r in con.execute("SELECT * FROM create_videos WHERE channel=? ORDER BY id DESC",
                                               (channels.active_slug(),))]


def video(video_id: int) -> dict | None:
    with db.connect() as con:
        row = con.execute("SELECT * FROM create_videos WHERE id=?", (video_id,)).fetchone()
    return _video(row) if row else None


def _video(row) -> dict:
    out = dict(row)
    out["script"] = json.loads(out["script"] or "{}")
    out["timings"] = json.loads(out["timings"]) if out["timings"] else None
    return out


def add_video(topic_id: int | None, script: dict, check_notes: str = "", ready: str = "") -> int:
    """`ready` names the ready-made script it was made from, if any (D131)."""
    with db.connect() as con:
        cur = con.execute(
            "INSERT INTO create_videos (topic_id, script, check_notes, ready, created_at, updated_at, channel) "
            "VALUES (?,?,?,?,?,?,?)",
            (topic_id, json.dumps(script), check_notes, ready, db.now(), db.now(), channels.active_slug()))
        return int(cur.lastrowid)


def update_video(video_id: int, **changes) -> None:
    allowed = {"status", "script", "check_notes", "voice", "timings", "clip_id", "error",
               "archived_at", "archive_hold", "ready"}
    sets = {k: (json.dumps(v) if k in ("script", "timings") and not isinstance(v, str) else v)
            for k, v in changes.items() if k in allowed}
    if "status" in sets and sets["status"] not in VIDEO_STATUSES:
        raise ValueError(f"status must be one of {', '.join(VIDEO_STATUSES)}")
    if not sets:
        return
    with db.connect() as con:
        con.execute(f"UPDATE create_videos SET {', '.join(f'{k}=?' for k in sets)}, updated_at=? WHERE id=?",
                    [*sets.values(), db.now(), video_id])
