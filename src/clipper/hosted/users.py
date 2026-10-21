"""The gateway's accounts (D228): users, sessions, password resets, credits, and Paddle's webhook ids.

One SQLite file, `<root>/gateway.sqlite`. Tokens (sessions, reset links) are stored only as their sha256,
so a copy of the database signs nobody in. Passwords are scrypt hashes with their parameters, so the cost
can go up later without breaking old ones.
"""

from __future__ import annotations

import contextlib
import hashlib
import hmac
import re
import secrets
import sqlite3
import time
from collections.abc import Iterator
from pathlib import Path

from . import root

MIN_PASSWORD = 10
SESSION_DAYS = 30
RESET_MINUTES = 60
#: last_seen is written at most this often, not on every proxied request (a video plays as many requests).
SEEN_EVERY = 60

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT UNIQUE NOT NULL, password TEXT NOT NULL,
  created REAL NOT NULL, plan TEXT NOT NULL DEFAULT 'free', status TEXT NOT NULL DEFAULT 'active',
  age_confirmed INTEGER NOT NULL DEFAULT 0, terms_version TEXT NOT NULL DEFAULT '', deleted_at REAL,
  paddle_customer_id TEXT, paddle_subscription_id TEXT, period_start TEXT, period_end TEXT);
CREATE TABLE IF NOT EXISTS sessions (
  token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
  created REAL NOT NULL, expires REAL NOT NULL, last_seen REAL NOT NULL);
CREATE TABLE IF NOT EXISTS password_resets (
  token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
  expires REAL NOT NULL, used INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS credits (
  id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL REFERENCES users(id),
  minutes INTEGER NOT NULL DEFAULT 0, shorts INTEGER NOT NULL DEFAULT 0, source TEXT NOT NULL, at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS webhook_events (event_id TEXT PRIMARY KEY, at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS members (
  id INTEGER PRIMARY KEY AUTOINCREMENT, owner_id INTEGER NOT NULL REFERENCES users(id),
  user_id INTEGER REFERENCES users(id), invited_email TEXT NOT NULL, token_hash TEXT UNIQUE,
  created REAL NOT NULL, accepted_at REAL);
"""
#: Columns added after D228's first schema (billing, D234), added to an older database on open.
LATER_COLUMNS = (("grace_until", "TEXT"), ("refunded_at", "REAL"), ("paddle_event_at", "REAL"))

_made: set[Path] = set()
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
#: Fields E2 (billing) and the admin page may change with update().
FIELDS = ("plan", "status", "paddle_customer_id", "paddle_subscription_id", "period_start", "period_end",
          "grace_until", "refunded_at", "paddle_event_at")
INVITE_DAYS = 7


class AccountError(ValueError):
    """Said to the person as it is (bad email, short password, address taken)."""


@contextlib.contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    path = root() / "gateway.sqlite"
    if path not in _made:
        path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, timeout=10)
    con.row_factory = sqlite3.Row
    try:
        if path not in _made:
            con.execute("PRAGMA journal_mode=WAL")
            con.executescript(SCHEMA)
            have = {r["name"] for r in con.execute("PRAGMA table_info(users)")}
            for name, kind in LATER_COLUMNS:
                if name not in have:
                    con.execute(f"ALTER TABLE users ADD COLUMN {name} {kind}")
            _made.add(path)
        with con:
            yield con
    finally:
        con.close()


def _sha(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# ---------- passwords

def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    n, r, p = 2**14, 8, 1
    digest = hashlib.scrypt(password.encode(), salt=salt, n=n, r=r, p=p, dklen=32)
    return f"scrypt${n}${r}${p}${salt.hex()}${digest.hex()}"


def check_password(password: str, stored: str) -> bool:
    try:
        kind, n, r, p, salt, digest = stored.split("$")
        if kind != "scrypt":
            return False
        mine = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p),
                              dklen=len(digest) // 2)
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(mine.hex(), digest)


#: Checked against when the email is unknown, so a wrong address takes as long as a wrong password.
_DUMMY = hash_password("not-a-real-password")


def _valid_password(password: str) -> None:
    if len(password) < MIN_PASSWORD:
        raise AccountError(f"Use a password of at least {MIN_PASSWORD} characters.")


# ---------- users

def normal_email(email: str) -> str:
    return (email or "").strip().lower()


def create_user(email: str, password: str, *, age_confirmed: bool, terms_version: str,
                period: tuple[str, str], now: float | None = None) -> dict:
    """A new Free account. `period` is its first (start, end), from catalog.first_period()."""
    email = normal_email(email)
    if len(email) > 254 or not EMAIL.match(email):
        raise AccountError("That doesn't look like an email address.")
    _valid_password(password)
    with connect() as con:
        try:
            cur = con.execute(
                "INSERT INTO users (email, password, created, age_confirmed, terms_version, period_start, period_end)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (email, hash_password(password), now or time.time(), int(age_confirmed), terms_version, *period))
        except sqlite3.IntegrityError:
            raise AccountError("There is already an account with that email. Log in instead.") from None
    return get_user(cur.lastrowid)


def get_user(user_id: int) -> dict | None:
    with connect() as con:
        row = con.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return dict(row) if row else None


def by_email(email: str) -> dict | None:
    with connect() as con:
        row = con.execute("SELECT * FROM users WHERE email = ? AND deleted_at IS NULL",
                          (normal_email(email),)).fetchone()
    return dict(row) if row else None


def all_users() -> list[dict]:
    """Every live account, with when it was last seen (for /admin)."""
    with connect() as con:
        rows = con.execute(
            "SELECT u.*, (SELECT MAX(last_seen) FROM sessions s WHERE s.user_id = u.id) AS last_seen"
            " FROM users u WHERE deleted_at IS NULL ORDER BY u.id").fetchall()
    return [dict(r) for r in rows]


def authenticate(email: str, password: str) -> dict | None:
    user = by_email(email)
    if user is None:
        check_password(password, _DUMMY)
        return None
    return user if check_password(password, user["password"]) else None


def update(user_id: int, **fields) -> dict:
    """Change plan, status, Paddle ids or the period (E2's webhooks, the admin page). Callers then
    rewrite plan.json with catalog.refresh()."""
    bad = set(fields) - set(FIELDS)
    if bad:
        raise ValueError(f"not a user field: {sorted(bad)}")
    if fields:
        with connect() as con:
            con.execute(f"UPDATE users SET {', '.join(f'{k} = ?' for k in fields)} WHERE id = ?",
                        (*fields.values(), user_id))
    return get_user(user_id)


def set_password(user_id: int, password: str) -> None:
    _valid_password(password)
    with connect() as con:
        con.execute("UPDATE users SET password = ? WHERE id = ?", (hash_password(password), user_id))
        con.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))


def mark_deleted(user_id: int, now: float | None = None) -> None:
    """Keep the row (its id and billing ids, for refunds and the books) but nothing that names the person:
    the email is replaced so the address can sign up again, the password can't match anything."""
    with connect() as con:
        con.execute("UPDATE users SET email = ?, password = '', deleted_at = ? WHERE id = ?",
                    (f"deleted-{user_id}", now or time.time(), user_id))
        con.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
        con.execute("DELETE FROM password_resets WHERE user_id = ?", (user_id,))
        con.execute("DELETE FROM members WHERE owner_id = ? OR user_id = ?", (user_id, user_id))


# ---------- sessions

def new_session(user_id: int, now: float | None = None) -> str:
    token = secrets.token_urlsafe(32)
    now = now or time.time()
    with connect() as con:
        con.execute("INSERT INTO sessions VALUES (?, ?, ?, ?, ?)",
                    (_sha(token), user_id, now, now + SESSION_DAYS * 86400, now))
    return token


def session_user(token: str, now: float | None = None) -> dict | None:
    if not token:
        return None
    now = now or time.time()
    with connect() as con:
        row = con.execute(
            "SELECT u.*, s.last_seen AS seen, s.token_hash AS th FROM sessions s JOIN users u ON u.id = s.user_id"
            " WHERE s.token_hash = ? AND s.expires > ? AND u.deleted_at IS NULL", (_sha(token), now)).fetchone()
        if row and now - row["seen"] > SEEN_EVERY:
            con.execute("UPDATE sessions SET last_seen = ? WHERE token_hash = ?", (now, row["th"]))
    if row is None:
        return None
    user = dict(row)
    del user["seen"], user["th"]
    return user


def end_session(token: str) -> None:
    with connect() as con:
        con.execute("DELETE FROM sessions WHERE token_hash = ?", (_sha(token),))


# ---------- password resets

def new_reset(user_id: int, now: float | None = None) -> str:
    token = secrets.token_urlsafe(32)
    with connect() as con:
        con.execute("INSERT INTO password_resets VALUES (?, ?, ?, 0)",
                    (_sha(token), user_id, (now or time.time()) + RESET_MINUTES * 60))
    return token


def reset_user(token: str, now: float | None = None) -> dict | None:
    """Whose link this is, while it is unused and in time."""
    with connect() as con:
        row = con.execute("SELECT user_id FROM password_resets WHERE token_hash = ? AND used = 0 AND expires > ?",
                          (_sha(token), now or time.time())).fetchone()
    return get_user(row["user_id"]) if row else None


def use_reset(token: str, password: str, now: float | None = None) -> dict | None:
    """Set the new password with a reset link, once: the link is used up and every session ends."""
    user = reset_user(token, now)
    if user is None or user["deleted_at"]:
        return None
    set_password(user["id"], password)   # raises AccountError before the link is spent
    with connect() as con:
        con.execute("UPDATE password_resets SET used = 1 WHERE token_hash = ?", (_sha(token),))
    return user


# ---------- credits (packs) and Paddle's events

def add_credits(user_id: int, *, minutes: int = 0, shorts: int = 0, source: str, now: float | None = None) -> None:
    """A pack bought (source "pack:<name>") or given by hand ("comp"). Callers then run catalog.refresh()."""
    with connect() as con:
        con.execute("INSERT INTO credits (user_id, minutes, shorts, source, at) VALUES (?, ?, ?, ?, ?)",
                    (user_id, int(minutes), int(shorts), source, now or time.time()))


def credits(user_id: int) -> list[dict]:
    with connect() as con:
        return [dict(r) for r in con.execute("SELECT * FROM credits WHERE user_id = ? ORDER BY id", (user_id,))]


def first_seen_event(event_id: str, now: float | None = None) -> bool:
    """True the first time a webhook event id arrives, False for Paddle's retries of it (E2)."""
    with connect() as con:
        cur = con.execute("INSERT OR IGNORE INTO webhook_events VALUES (?, ?)", (event_id, now or time.time()))
    return cur.rowcount == 1


def forget_event(event_id: str) -> None:
    """An event whose handling failed: Paddle's retry of it is let through again."""
    with connect() as con:
        con.execute("DELETE FROM webhook_events WHERE event_id = ?", (event_id,))


def by_paddle(*, subscription_id: str | None = None, customer_id: str | None = None) -> dict | None:
    """The live account with this Paddle subscription, or else this Paddle customer."""
    column, value = ("paddle_subscription_id", subscription_id) if subscription_id else (
        "paddle_customer_id", customer_id)
    if not value:
        return None
    with connect() as con:
        row = con.execute(f"SELECT * FROM users WHERE {column} = ? AND deleted_at IS NULL ORDER BY id DESC",
                          (value,)).fetchone()
    return dict(row) if row else None


# ---------- team seats (Agency, D234)

def invite(owner_id: int, email: str, now: float | None = None) -> str:
    """A seat held for `email` until they open the link (token stored only as its sha256)."""
    email = normal_email(email)
    if len(email) > 254 or not EMAIL.match(email):
        raise AccountError("That doesn't look like an email address.")
    with connect() as con:
        owner = con.execute("SELECT email FROM users WHERE id = ?", (owner_id,)).fetchone()
        if owner and owner["email"] == email:
            raise AccountError("That is your own address.")
        if con.execute("SELECT 1 FROM members WHERE owner_id = ? AND invited_email = ?", (owner_id, email)).fetchone():
            raise AccountError("That address is already on your team or invited.")
        token = secrets.token_urlsafe(32)
        con.execute("INSERT INTO members (owner_id, invited_email, token_hash, created) VALUES (?, ?, ?, ?)",
                    (owner_id, email, _sha(token), now or time.time()))
    return token


def members(owner_id: int, now: float | None = None) -> list[dict]:
    """Joined members and invites still in time (an expired invite frees its seat)."""
    cutoff = (now or time.time()) - INVITE_DAYS * 86400
    with connect() as con:
        con.execute("DELETE FROM members WHERE owner_id = ? AND accepted_at IS NULL AND created < ?",
                    (owner_id, cutoff))
        return [dict(r) for r in con.execute("SELECT * FROM members WHERE owner_id = ? ORDER BY id", (owner_id,))]


def invite_owner(token: str, now: float | None = None) -> dict | None:
    """The invite behind a link, while unused and in time, with the owner's id."""
    with connect() as con:
        row = con.execute("SELECT * FROM members WHERE token_hash = ? AND accepted_at IS NULL AND created > ?",
                          (_sha(token), (now or time.time()) - INVITE_DAYS * 86400)).fetchone()
    return dict(row) if row else None


def accept_invite(token: str, user: dict, now: float | None = None) -> dict:
    """Join the owner's team. The invite is for one address: a forwarded link doesn't let someone else in."""
    row = invite_owner(token, now)
    if row is None:
        raise AccountError("This invite has expired or was already used. Ask for a new one.")
    if row["invited_email"] != normal_email(user["email"]):
        raise AccountError("This invite is for another email address. Log in with that one.")
    if row["owner_id"] == user["id"]:
        raise AccountError("That is your own team.")
    with connect() as con:
        if con.execute("SELECT 1 FROM members WHERE owner_id = ? AND accepted_at IS NOT NULL", (user["id"],)).fetchone():
            raise AccountError("You have a team of your own. Remove its members first.")
        con.execute("DELETE FROM members WHERE user_id = ?", (user["id"],))   # one team at a time
        con.execute("UPDATE members SET user_id = ?, accepted_at = ?, token_hash = NULL WHERE id = ?",
                    (user["id"], now or time.time(), row["id"]))
    return get_user(row["owner_id"])


def remove_member(owner_id: int, member_id: int) -> None:
    with connect() as con:
        con.execute("DELETE FROM members WHERE id = ? AND owner_id = ?", (member_id, owner_id))


def leave_team(user_id: int) -> None:
    with connect() as con:
        con.execute("DELETE FROM members WHERE user_id = ?", (user_id,))


def team_owner(user_id: int) -> dict | None:
    """The live account whose team this user has joined, if any."""
    with connect() as con:
        row = con.execute("SELECT u.* FROM members m JOIN users u ON u.id = m.owner_id WHERE m.user_id = ?"
                          " AND m.accepted_at IS NOT NULL AND u.deleted_at IS NULL", (user_id,)).fetchone()
    return dict(row) if row else None
