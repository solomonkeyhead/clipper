"""Campaign alerts (D65, D69): new campaigns, judged and listed.

The user signs in to their own Whop app and picks forum feeds in the communities
they've joined, where Content Rewards' campaign bot posts (watch/whop.py). Every
few minutes while the Control Center is open, Clipper reads what's new in the
feeds they chose, asks the campaign judge (watch/judge.py) whether each post
announces a campaign and how well it fits them, and keeps each campaign for the
Campaigns page. Discord channels and ntfy phone pushes were sources too, unused
and removed (D216).

Whop keeps its posts, so nothing is missed while the PC is off: the next check
reads everything since the last post it saw.
"""

from __future__ import annotations

import os
import threading
from datetime import UTC, datetime, timedelta

from ..config import PLATFORM_NAMES
from ..utils.logging import get_logger
from ..watch import whop
from . import db

log = get_logger(__name__)

CHECK_MINUTES = 5
FIRST_LOOK = 25    # posts read from a feed when it's first watched...
FIRST_DAYS = 14    # ...of which only posts this recent are judged
MIN_CHARS = 40     # shorter posts ("gm", a role ping) aren't campaigns
MAX_ATTEMPTS = 3   # a post the AI can't judge is retried, then passed over

_attempts: dict[str, int] = {}
_checking = threading.Lock()


def whop_watched() -> list[dict]:
    with db.connect() as con:
        return [dict(r) for r in con.execute(
            "SELECT id, company, name, last_seen FROM whop_feeds ORDER BY company, name")]


def whop_watch(ids: list[str], available: list[dict]) -> list[dict]:
    """Read exactly these Whop feeds from now on (each one of `available`)."""
    by_id = {f["id"]: f for f in available}
    if any(i not in by_id for i in ids):
        raise ValueError("one of those feeds isn't in your communities any more; refresh the list")
    with db.connect() as con:
        con.execute(f"DELETE FROM whop_feeds WHERE id NOT IN ({','.join('?' * len(ids))})", ids)
        for i in ids:
            f = by_id[i]
            con.execute("INSERT INTO whop_feeds (id, company, name, added_at) VALUES (?,?,?,?) "
                        "ON CONFLICT(id) DO UPDATE SET company=excluded.company, name=excluded.name",
                        (i, f["company"], f["name"], db.now()))
    return whop_watched()


def prefs() -> dict:
    """What the judge is told about the user."""
    from ..config import Config

    base = Config.load().watch
    with db.connect() as con:
        s = db.settings(con)
    return {"profile": s["alert_profile"].strip() or base.profile.strip()}


def set_prefs(profile: str) -> dict:
    with db.connect() as con:
        db.set_setting(con, "alert_profile", profile.strip()[:2000])
    return prefs()


def status() -> dict:
    with db.connect() as con:
        s = db.settings(con)
    return {"whop_signed_in": whop.signed_in(),
            "whop_app": bool(os.environ.get(whop.ENV_ID, "").strip()), "whop_feeds": whop_watched(),
            "checked_at": s["alerts_checked"], "error": s["alerts_error"],
            "every_minutes": CHECK_MINUTES, **prefs()}


def _config():
    from ..config import Config

    p = prefs()
    base = Config.load().watch
    # Where the user can post is whatever they've connected, not the config's
    # default: a TikTok-only list ruled out an Instagram campaign for a user
    # with Instagram connected.
    platforms = _connected() or base.platforms
    profile = f"{p['profile']}\nPosts on: {', '.join(PLATFORM_NAMES.get(x, x) for x in platforms)}."
    return base.model_copy(update={"profile": profile, "platforms": platforms})


def _connected() -> list[str]:
    """The platforms the user has an account connected on."""
    from ..instagram import api as ig_api
    from ..tiktok import api as tt_api
    from ..youtube import api as yt_api

    return [name for name, files in (("tiktok", tt_api.token_files), ("instagram", ig_api.token_files),
                                     ("youtube", yt_api.token_files)) if files()]


def _recent(stamp: str | None) -> bool:
    try:
        sent = datetime.fromisoformat((stamp or "").replace("Z", "+00:00"))
    except ValueError:
        return True
    return sent >= datetime.now(UTC) - timedelta(days=FIRST_DAYS)


def check(backend=None, *, publish=None) -> dict:
    """Read, judge, keep and push what's new in every watched source, once."""
    if not _checking.acquire(blocking=False):
        return {"busy": True}
    try:
        run = _Run(backend)
        _check_whop(run)
    finally:
        _checking.release()
    result = run.result
    if not run.checked:
        return result
    with db.connect() as con:
        db.set_setting(con, "alerts_checked", db.now())
        db.set_setting(con, "alerts_error", result["errors"][0] if result["errors"] else "")
    if publish and (result["new"] or result["errors"] or result["read"]):
        publish("found.changed")
    return result


class _Run:
    """One check: the judge, the cache, the tally."""

    def __init__(self, backend) -> None:
        from ..llm.cache import LLMCache

        self.backend = backend
        self.cfg, self.cache = _config(), LLMCache()
        self.result = {"read": 0, "campaigns": 0, "new": [], "errors": []}
        self.checked = False

    def judge(self, header: str, text: str, *, key: str, via: str, link: str) -> bool:
        """Judge one post and keep its campaign. False: the AI didn't answer
        and the post should be read again next check (up to MAX_ATTEMPTS)."""
        from ..watch.judge import judge_text
        from .finder import record_found

        if self.backend is None:
            from ..config import Config
            from ..pipeline import build_backend

            self.backend = build_backend(Config.load())
        self.result["read"] += 1
        verdict = judge_text(header, text, self.cfg.profile, self.backend, cache=self.cache, label=key)
        if verdict is None:
            _attempts[key] = _attempts.get(key, 0) + 1
            if _attempts[key] < MAX_ATTEMPTS:
                self.result["errors"].append("The AI didn't answer; those posts are read again next check")
                return False
            _attempts.pop(key, None)
            return True
        if not verdict.is_new_campaign:
            return True
        self.result["campaigns"] += 1
        verdict = verdict.model_copy(update={"link": verdict.link or link})
        if record_found(verdict, text, via=via):
            self.result["new"].append(verdict.name or "a campaign")
        return True


def _check_whop(run: _Run) -> None:
    feeds = whop_watched()
    if not feeds or not whop.signed_in():
        return
    run.checked = True
    for feed in feeds:
        first = feed["last_seen"] is None
        try:
            # Content Rewards' feed carries ~22 campaigns a day: a day with the
            # PC off is a backlog, so later checks read up to 100.
            posts = whop.new_posts(feed["id"], after=feed["last_seen"],
                                   first=FIRST_LOOK if first else 100)
        except whop.WhopError as exc:
            run.result["errors"].append(f"{feed['name']}: {exc}")
            continue
        for p in posts:
            text = whop.text_of(p)
            if len(text) >= MIN_CHARS and (not first or _recent(p.get("created_at"))):
                author = (p.get("user") or {}).get("username") or "someone"
                if not run.judge(f"Whop post in {feed['name']} ({feed['company']}), from {author}\n"
                                 f"Date: {p.get('created_at', '')}", text, key=p["id"], via="whop",
                                 link=whop.link_in(text)):
                    break
            with db.connect() as con:
                con.execute("UPDATE whop_feeds SET last_seen=? WHERE id=?", (p["created_at"], feed["id"]))
        if first and not posts:
            with db.connect() as con:
                con.execute("UPDATE whop_feeds SET last_seen=? WHERE id=? AND last_seen IS NULL",
                            (datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z"), feed["id"]))
