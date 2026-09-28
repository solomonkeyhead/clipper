"""The Control Center's database: data/clipper.db (SQLite, standard library).

It holds what used to live in folder names and file names -- which clips a
campaign has, where each file is, and how far along it is (ready, posted,
submitted, skipped) -- plus settings such as the auto-post toggle. Post links
and stats stay in the performance log (data/performance.xlsx), which the
TikTok and Instagram syncs fill; the Control Center joins the two on
(campaign, source_id, clip_id).
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from ..paths import data_root, ensure

STATUSES = ("ready", "posted", "submitted", "skipped")

SCHEMA = """
CREATE TABLE IF NOT EXISTS clips (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    campaign    TEXT NOT NULL,
    source_id   TEXT NOT NULL DEFAULT '',
    clip_id     TEXT NOT NULL DEFAULT '',
    source_title TEXT NOT NULL DEFAULT '',
    title       TEXT NOT NULL DEFAULT '',
    file        TEXT NOT NULL,            -- relative to data/library
    hook        TEXT NOT NULL DEFAULT '',
    caption     TEXT NOT NULL DEFAULT '',
    duration_s  REAL,
    start_s     REAL,
    end_s       REAL,
    status      TEXT NOT NULL DEFAULT 'ready',
    notes       TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL,
    UNIQUE (campaign, source_id, clip_id)
);
CREATE TABLE IF NOT EXISTS campaign_state (
    name        TEXT PRIMARY KEY,
    archived    INTEGER NOT NULL DEFAULT 0,
    auto_post   INTEGER               -- NULL: follow the global setting
);
CREATE TABLE IF NOT EXISTS settings (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL
);
"""

#: Defaults for `settings`; stored values win.
DEFAULT_SETTINGS = {
    # Posting goes through each platform's official API. With auto-post off,
    # every post waits in the review queue for a click; on, an approved clip
    # posts itself. Off until the user turns it on.
    "auto_post": "0",
}


def db_path() -> Path:
    return data_root() / "clipper.db"


@contextmanager
def connect(path: Path | None = None):
    path = path or db_path()
    ensure(path.parent)
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    try:
        con.executescript(SCHEMA)
        yield con
        con.commit()
    finally:
        con.close()


def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def upsert_clip(con: sqlite3.Connection, clip: dict) -> int:
    """Insert a clip, or refresh it when the same (campaign, source, clip) is re-rendered.

    A re-render keeps the clip's status and notes: they are the user's.
    """
    fields = ["campaign", "source_id", "clip_id", "source_title", "title", "file", "hook",
              "caption", "duration_s", "start_s", "end_s"]
    values = {f: clip.get(f) for f in fields}
    for text in ("source_id", "clip_id", "source_title", "title", "hook", "caption"):
        values[text] = values[text] or ""
    row = con.execute("SELECT id FROM clips WHERE campaign=? AND source_id=? AND clip_id=?",
                      (values["campaign"], values["source_id"], values["clip_id"])).fetchone()
    if row:
        sets = ", ".join(f"{f}=?" for f in fields)
        con.execute(f"UPDATE clips SET {sets} WHERE id=?", [*values.values(), row["id"]])
        return int(row["id"])
    cur = con.execute(
        f"INSERT INTO clips ({', '.join(fields)}, status, notes, created_at) "
        f"VALUES ({', '.join('?' * len(fields))}, ?, ?, ?)",
        [*values.values(), clip.get("status") or "ready", clip.get("notes") or "",
         clip.get("created_at") or now()])
    return int(cur.lastrowid)


def clips(con: sqlite3.Connection, campaign: str | None = None) -> list[dict]:
    query, args = "SELECT * FROM clips", ()
    if campaign is not None:
        query, args = query + " WHERE campaign=?", (campaign,)
    return [dict(r) for r in con.execute(query + " ORDER BY created_at, id", args)]


def clip(con: sqlite3.Connection, clip_id: int) -> dict | None:
    row = con.execute("SELECT * FROM clips WHERE id=?", (clip_id,)).fetchone()
    return dict(row) if row else None


def update_clip(con: sqlite3.Connection, clip_id: int, **changes) -> None:
    allowed = {k: v for k, v in changes.items()
               if k in ("status", "notes", "title", "caption", "start_s", "end_s")}
    if "status" in allowed and allowed["status"] not in STATUSES:
        raise ValueError(f"status must be one of {', '.join(STATUSES)}")
    if allowed:
        sets = ", ".join(f"{k}=?" for k in allowed)
        con.execute(f"UPDATE clips SET {sets} WHERE id=?", [*allowed.values(), clip_id])


def campaign_state(con: sqlite3.Connection) -> dict[str, dict]:
    return {r["name"]: dict(r) for r in con.execute("SELECT * FROM campaign_state")}


def set_campaign_state(con: sqlite3.Connection, name: str, **changes) -> None:
    con.execute("INSERT OR IGNORE INTO campaign_state (name) VALUES (?)", (name,))
    for key in ("archived", "auto_post"):
        if key in changes:
            value = changes[key]
            con.execute(f"UPDATE campaign_state SET {key}=? WHERE name=?",
                        (None if value is None else int(bool(value)), name))


def settings(con: sqlite3.Connection) -> dict[str, str]:
    stored = {r["key"]: r["value"] for r in con.execute("SELECT key, value FROM settings")}
    return {**DEFAULT_SETTINGS, **stored}


def set_setting(con: sqlite3.Connection, key: str, value: str) -> None:
    if key not in DEFAULT_SETTINGS:
        raise ValueError(f"unknown setting {key!r}")
    con.execute("INSERT INTO settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))
