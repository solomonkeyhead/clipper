"""The Control Center's database: data/clipper.db (SQLite, standard library).

It holds what used to live in folder names and file names -- which clips a
campaign has, where each file is, and how far along it is (ready, posted,
submitted, skipped) -- plus settings such as the auto-post toggle, which post
links have been submitted to their campaign, and a snapshot of every post's
numbers at each sync (for growth charts and "since you were last here").
Post links and current stats stay in the performance log
(data/performance.xlsx), which the TikTok and Instagram syncs fill; the Control
Center joins the two on (campaign, source_id, clip_id).
"""

from __future__ import annotations

import json
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
CREATE TABLE IF NOT EXISTS post_state (
    url         TEXT PRIMARY KEY,     -- the post's link, without query string
    submitted_at TEXT                 -- NULL: not yet submitted to its campaign
);
CREATE TABLE IF NOT EXISTS research_threads (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    title       TEXT NOT NULL DEFAULT '',
    niche_id    INTEGER,                  -- unused since D63
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS research_messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id   INTEGER NOT NULL,
    role        TEXT NOT NULL,            -- user | assistant
    content     TEXT NOT NULL,
    sources     TEXT NOT NULL DEFAULT '[]',   -- [{title, url}]
    actions     TEXT NOT NULL DEFAULT '[]',   -- unused since D63 (chat actions removed)
    created_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS jobs (
    id          INTEGER PRIMARY KEY,      -- studio/jobs.py Job.id
    data        TEXT NOT NULL,            -- the finished job, with its results report
    finished    TEXT NOT NULL
);
-- Discord channels the campaign alerts read (studio/alerts.py), in the user's own server.
CREATE TABLE IF NOT EXISTS discord_channels (
    id          TEXT PRIMARY KEY,
    guild_id    TEXT NOT NULL,
    guild       TEXT NOT NULL DEFAULT '',
    name        TEXT NOT NULL DEFAULT '',
    last_id     TEXT,                     -- newest message already read
    added_at    TEXT NOT NULL
);
-- Whop forum feeds the campaign alerts read (studio/alerts.py, watch/whop.py).
CREATE TABLE IF NOT EXISTS whop_feeds (
    id          TEXT PRIMARY KEY,         -- experience id, exp_...
    company     TEXT NOT NULL DEFAULT '',
    name        TEXT NOT NULL DEFAULT '',
    last_seen   TEXT,                     -- created_at of the newest post read
    added_at    TEXT NOT NULL
);
-- Which campaign a video belongs to (studio/footage.py); files are never moved.
CREATE TABLE IF NOT EXISTS footage (
    path        TEXT PRIMARY KEY,         -- resolved file path
    campaign    TEXT NOT NULL,
    how         TEXT NOT NULL,            -- clipped | added
    at          TEXT NOT NULL
);
-- The brief as the user pasted it, whole (D76): the form keeps what Clipper
-- acts on; Ask reads this for everything else (eligibility, payout terms...).
CREATE TABLE IF NOT EXISTS campaign_briefs (
    campaign    TEXT PRIMARY KEY,
    text        TEXT NOT NULL,
    saved_at    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS found_campaigns (
    key         TEXT PRIMARY KEY,         -- watch.watcher.campaign_key
    source      TEXT NOT NULL DEFAULT '',
    name        TEXT NOT NULL DEFAULT '',
    owner       TEXT NOT NULL DEFAULT '',
    rate        TEXT NOT NULL DEFAULT '',
    rate_per_1k_usd REAL,
    platforms   TEXT NOT NULL DEFAULT '[]',
    budget      TEXT NOT NULL DEFAULT '',
    deadline    TEXT NOT NULL DEFAULT '',
    link        TEXT NOT NULL DEFAULT '',
    fit         TEXT NOT NULL DEFAULT '',
    why         TEXT NOT NULL DEFAULT '',
    brief       TEXT NOT NULL DEFAULT '',  -- the email's text, for "Add campaign"
    found_at    TEXT NOT NULL,
    dismissed   INTEGER NOT NULL DEFAULT 0
);
-- Niches and saved items (D62) were removed in D63.
DROP TABLE IF EXISTS niches;
DROP TABLE IF EXISTS saved_items;
CREATE TABLE IF NOT EXISTS snapshots (
    url         TEXT NOT NULL,
    at          TEXT NOT NULL,        -- ISO time of the sync
    views       INTEGER,
    likes       INTEGER,
    comments    INTEGER,
    shares      INTEGER,
    saves       INTEGER,
    avg_watch_s REAL,
    skip_rate_pct REAL,
    PRIMARY KEY (url, at)
);
"""

#: Defaults for `settings`; stored values win.
DEFAULT_SETTINGS = {
    # Posting goes through each platform's official API. With auto-post off,
    # every post waits in the review queue for a click; on, an approved clip
    # posts itself. Off until the user turns it on.
    "auto_post": "0",
    # "Since you were last here": the end of the previous session (last activity
    # before a 30-minute gap), and the latest activity in this one.
    "last_visit": "",
    "seen_at": "",
    # Minutes between automatic syncs while the Control Center is open.
    "sync_minutes": "15",
    # Use the user's clip ratings when scoring (learn/feedback.py).
    "learn_from_feedback": "1",
    # Which plan's features are on (studio/plans.py). A local install is the
    # owner's, so everything; a hosted version would set this per account.
    "plan": "pro",
    # Campaign alerts (studio/alerts.py): what the user clips, for the fit judge
    # ("" = config's watch.profile), the lowest pay worth an alert, and the last check.
    "alert_profile": "",
    "alert_min_rate": "",
    "alerts_checked": "",
    "alerts_error": "",
}


#: Columns added after the first release, created on older databases at connect.
MIGRATIONS = [
    ("clips", "deleted_at", "ALTER TABLE clips ADD COLUMN deleted_at TEXT"),
    # How the scorer rated the clip (JSON: score, rubric, composite, pool, pool_rank,
    # picked_by), and how the user did: 1-5, with reasons (JSON list).
    ("clips", "scores", "ALTER TABLE clips ADD COLUMN scores TEXT"),
    ("clips", "rating", "ALTER TABLE clips ADD COLUMN rating INTEGER"),
    ("clips", "reasons", "ALTER TABLE clips ADD COLUMN reasons TEXT NOT NULL DEFAULT '[]'"),
    ("clips", "rated_at", "ALTER TABLE clips ADD COLUMN rated_at TEXT"),
    # The campaign's rules and the clip's checks when it was made (studio/evidence.py).
    ("clips", "evidence", "ALTER TABLE clips ADD COLUMN evidence TEXT"),
    # Marked posted at: syncs run every 2 minutes until this passes or the post is found.
    ("clips", "watch_until", "ALTER TABLE clips ADD COLUMN watch_until TEXT"),
    # The AI check of a clip's post texts against its brief (studio/rulecheck.py):
    # JSON {key, problems, at}; `key` says which texts and brief it read.
    ("clips", "audit", "ALTER TABLE clips ADD COLUMN audit TEXT"),
    # Where a found campaign came from: "email", "discord" or "whop".
    ("found_campaigns", "via", "ALTER TABLE found_campaigns ADD COLUMN via TEXT NOT NULL DEFAULT 'email'"),
]
TRASH_DAYS = 30


def db_path() -> Path:
    return data_root() / "clipper.db"


#: Database files whose schema this process has brought up to date.
_ready: set[Path] = set()


@contextmanager
def connect(path: Path | None = None):
    path = path or db_path()
    fresh = path not in _ready or not path.exists()  # a file replaced since gets its schema again
    con = sqlite3.connect(ensure(path.parent) / path.name, timeout=10)
    con.row_factory = sqlite3.Row
    try:
        if fresh:
            con.execute("PRAGMA journal_mode=WAL")
            con.executescript(SCHEMA)
            for table, column, sql in MIGRATIONS:
                if column not in {r["name"] for r in con.execute(f"PRAGMA table_info({table})")}:
                    con.execute(sql)
            con.commit()
            _ready.add(path)
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
    for optional in ("scores", "evidence"):
        if clip.get(optional) is not None:
            fields.append(optional)
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
    """The library's clips, leaving out any in the trash."""
    query, args = "SELECT * FROM clips WHERE deleted_at IS NULL", ()
    if campaign is not None:
        query, args = query + " AND campaign=?", (campaign,)
    return [dict(r) for r in con.execute(query + " ORDER BY created_at, id", args)]


def trash(con: sqlite3.Connection, clip_id: int, deleted: bool = True) -> None:
    """Move a clip to the trash (hidden, file kept) or back out of it."""
    con.execute("UPDATE clips SET deleted_at=? WHERE id=?", (now() if deleted else None, clip_id))


def expired_trash(con: sqlite3.Connection, days: int = TRASH_DAYS) -> list[dict]:
    from datetime import timedelta

    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M")
    return [dict(r) for r in con.execute(
        "SELECT * FROM clips WHERE deleted_at IS NOT NULL AND deleted_at < ?", (cutoff,))]


def forget(con: sqlite3.Connection, clip_id: int) -> None:
    con.execute("DELETE FROM clips WHERE id=?", (clip_id,))


def clip(con: sqlite3.Connection, clip_id: int) -> dict | None:
    row = con.execute("SELECT * FROM clips WHERE id=?", (clip_id,)).fetchone()
    return dict(row) if row else None


def update_clip(con: sqlite3.Connection, clip_id: int, **changes) -> None:
    allowed = {k: v for k, v in changes.items()
               if k in ("status", "notes", "title", "caption", "start_s", "end_s", "watch_until")}
    if "status" in allowed and allowed["status"] not in STATUSES:
        raise ValueError(f"status must be one of {', '.join(STATUSES)}")
    if allowed:
        sets = ", ".join(f"{k}=?" for k in allowed)
        con.execute(f"UPDATE clips SET {sets} WHERE id=?", [*allowed.values(), clip_id])


REASONS = {
    # what the user liked
    "great_hook": "Great hook", "funny": "Funny", "emotional": "Emotional",
    "good_ending": "Good ending", "on_brief": "Right for the campaign",
    # what they didn't
    "weak_hook": "Weak hook", "boring": "Boring / slow", "bad_ending": "Cut off / bad ending",
    "needs_context": "Needs context", "off_brief": "Wrong for the campaign",
    "bad_framing": "Bad framing", "caption_errors": "Caption mistakes",
    "wrong_text": "On-screen text doesn't fit",
}


def set_rating(con: sqlite3.Connection, clip_id: int, rating: int | None,
               reasons: list[str]) -> None:
    """The user's verdict on a clip: 1 (bad) to 5 (great), None to clear."""
    if rating is not None and not 1 <= int(rating) <= 5:
        raise ValueError("rating must be 1 to 5")
    unknown = set(reasons) - set(REASONS)
    if unknown:
        raise ValueError(f"unknown reason: {', '.join(sorted(unknown))}")
    import json

    con.execute("UPDATE clips SET rating=?, reasons=?, rated_at=? WHERE id=?",
                (rating, json.dumps(list(dict.fromkeys(reasons))),
                 now() if rating is not None else None, clip_id))


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
    if key == "plan" and value not in ("free", "research", "pro"):
        raise ValueError("plan must be free, research or pro")
    con.execute("INSERT INTO settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))


def submitted(con: sqlite3.Connection) -> dict[str, str]:
    """Post url -> when it was submitted to its campaign."""
    return {r["url"]: r["submitted_at"] for r in
            con.execute("SELECT url, submitted_at FROM post_state WHERE submitted_at IS NOT NULL")}


def set_submitted(con: sqlite3.Connection, url: str, done: bool) -> None:
    con.execute("INSERT INTO post_state (url, submitted_at) VALUES (?, ?) "
                "ON CONFLICT(url) DO UPDATE SET submitted_at=excluded.submitted_at",
                (url, now() if done else None))


def add_snapshots(con: sqlite3.Connection, at: str, posts: list[dict]) -> None:
    con.executemany(
        "INSERT OR REPLACE INTO snapshots (url, at, views, likes, comments, shares, saves, "
        "avg_watch_s, skip_rate_pct) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [(p["url"], at, p.get("views_latest"), p.get("likes"), p.get("comments"),
          p.get("shares"), p.get("saves"), p.get("avg_watch_s"), p.get("skip_rate_pct"))
         for p in posts])


def views_at(con: sqlite3.Connection, when: str) -> dict[str, int]:
    """Each post's views at the last snapshot on or before `when`."""
    rows = con.execute(
        "SELECT url, views FROM snapshots s WHERE at = (SELECT MAX(at) FROM snapshots t "
        "WHERE t.url = s.url AND t.at <= ?)", (when,))
    return {r["url"]: int(r["views"] or 0) for r in rows}


def history_full(con: sqlite3.Connection, url: str) -> list[dict]:
    """Every snapshot of one post, for its proof pack."""
    return [dict(r) for r in con.execute(
        "SELECT at, views, likes, comments, shares FROM snapshots WHERE url=? ORDER BY at", (url,))]


def history(con: sqlite3.Connection, url: str) -> list[dict]:
    return [dict(r) for r in con.execute(
        "SELECT at, views, avg_watch_s, skip_rate_pct FROM snapshots WHERE url=? ORDER BY at",
        (url,))]


#: A pasted brief is kept up to this many characters (a long one is ~8,000).
BRIEF_CHARS = 30_000


def save_brief(con: sqlite3.Connection, campaign: str, text: str) -> None:
    con.execute("INSERT INTO campaign_briefs (campaign, text, saved_at) VALUES (?,?,?) "
                "ON CONFLICT(campaign) DO UPDATE SET text=excluded.text, saved_at=excluded.saved_at",
                (campaign, text.strip()[:BRIEF_CHARS], now()))


def briefs(con: sqlite3.Connection) -> dict[str, str]:
    """Every pasted brief's text, by campaign."""
    return {r["campaign"]: r["text"] for r in con.execute("SELECT campaign, text FROM campaign_briefs")}


def set_audit(con: sqlite3.Connection, clip_id: int, audit: dict | None) -> None:
    con.execute("UPDATE clips SET audit=? WHERE id=?",
                (json.dumps(audit, ensure_ascii=False) if audit is not None else None, clip_id))


def brief(con: sqlite3.Connection, campaign: str) -> dict | None:
    row = con.execute("SELECT text, saved_at FROM campaign_briefs WHERE campaign=?", (campaign,)).fetchone()
    return dict(row) if row else None
