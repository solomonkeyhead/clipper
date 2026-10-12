"""Topics and videos for Create, in the Control Center's database (studio/db.py)."""

from __future__ import annotations

import json

from ..studio import db
from . import channel as channels

TOPIC_STATUSES = ("new", "used", "skipped")
VIDEO_STATUSES = ("writing", "draft", "approved", "voiced", "building", "built", "failed")


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


def send_back(topic_id: int) -> None:
    """The idea on the board again, by the owner's hand (D190): shown even if a Short of it is in Clips."""
    with db.connect() as con:
        con.execute("UPDATE create_topics SET status='new', sent_back=1 WHERE id=?", (topic_id,))


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
    out["history"] = json.loads(out.get("history") or "[]")
    out["future"] = json.loads(out.get("future") or "[]")
    out["built"] = json.loads(out.get("built") or "null")
    return out


#: What a change to each script field is called on the page (D199). Words, title, description and hashtags aren't
#: here: a change of words sends the video back to its voice, and the rest isn't in the video.
SHOWN = {"music": "music", "sfx": "sound effects", "zooms": "zoom-ins", "cut_ins": "cut-ins", "loop": "the loop",
         "transitions": "transitions", "cover": "the cover", "captions": "captions", "caption_size": "caption size",
         "caption_shift": "caption place", "caption_colour": "caption colour", "hook": "the on-screen hook"}


def unbuilt(script: dict, built: dict | None) -> list[str] | None:
    """What has changed since the last build, in words for the page (D199); None when it wasn't kept (built before
    D199). The parts list counted only new footage and drawings, and said "No changes yet" after music or a cover."""
    if not built:
        return None
    out = [label for key, label in SHOWN.items() if script.get(key) != built.get(key)]
    now, then = script.get("beats") or [], built.get("beats") or []
    out += [f"sentence {i}" for i, (a, b) in enumerate(zip(now, then, strict=False), 1) if a != b]
    return out


#: How many earlier versions of a script are kept for Undo (D193).
HISTORY = 20


def remember(row: dict, why: str) -> None:
    """The script as it is now, kept before a change (`why`: "Another take", "your edit"...) so Undo can bring it
    back (D193). The same script twice in a row is kept once. A change clears what Redo would bring back (D196)."""
    if not (row["script"].get("beats") or []):
        return
    past = list(row.get("history") or [])
    if past and past[-1]["script"] == row["script"]:
        return
    past.append({"script": row["script"], "check_notes": row.get("check_notes") or "", "why": why, "at": db.now()})
    with db.connect() as con:
        con.execute("UPDATE create_videos SET history=?, future='[]' WHERE id=?", (json.dumps(past[-HISTORY:]), row["id"]))


def undo(video_id: int, forward: bool = False) -> dict | None:
    """The script before the last change, back in place (and taken off the history); None if there's none.
    The script it replaces goes on the other list, so `forward` (Redo, D196) brings it back again."""
    row = video(video_id)
    take, give = ("future", "history") if forward else ("history", "future")
    if not row or not row[take]:
        return None
    last = row[take].pop()
    row[give].append({"script": row["script"], "check_notes": row.get("check_notes") or "", "why": last["why"],
                      "at": db.now()})
    texts = lambda s: [b.get("text") for b in s.get("beats") or []]  # noqa: E731
    changes = {"script": last["script"], "check_notes": last["check_notes"]}
    if texts(last["script"]) != texts(row["script"]):   # other words: a voice made for these no longer fits
        changes.update(voice="", timings="", status="draft")
    update_video(video_id, **changes)
    with db.connect() as con:
        con.execute(f"UPDATE create_videos SET {take}=?, {give}=? WHERE id=?",
                    (json.dumps(row[take]), json.dumps(row[give][-HISTORY:]), video_id))
    return last


def _texts(script: dict) -> list[str]:
    return [b.get("text") for b in script.get("beats") or []]


def use_take(video_id: int, k: int) -> dict | None:
    """An earlier take (`k` in the history, oldest first) back in place, the script it replaces kept in the history
    (D199): Undo only stepped back one at a time, blind. None if there's no such take."""
    row = video(video_id)
    past = row["history"] if row else []
    if not 0 <= k < len(past):
        return None
    pick = past[k]
    remember(row, "before an earlier take")
    changes = {"script": pick["script"], "check_notes": pick.get("check_notes", "")}
    if _texts(pick["script"]) != _texts(row["script"]):   # other words: a voice made for these no longer fits
        changes.update(voice="", timings="", status="draft")
    update_video(video_id, **changes)
    return pick


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
               "archived_at", "archive_hold", "ready", "built"}
    sets = {k: (json.dumps(v) if k in ("script", "timings", "built") and not isinstance(v, str) else v)
            for k, v in changes.items() if k in allowed}
    if "status" in sets and sets["status"] not in VIDEO_STATUSES:
        raise ValueError(f"status must be one of {', '.join(VIDEO_STATUSES)}")
    if not sets:
        return
    with db.connect() as con:
        con.execute(f"UPDATE create_videos SET {', '.join(f'{k}=?' for k in sets)}, updated_at=? WHERE id=?",
                    [*sets.values(), db.now(), video_id])
