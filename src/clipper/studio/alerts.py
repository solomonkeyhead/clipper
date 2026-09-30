"""Campaign alerts from Discord (D65): new campaigns, judged, listed and pushed.

The user follows campaign announcement channels into a small server of their
own and adds a bot they made to it (watch/discord.py). Every few minutes while
the Control Center is open, Clipper reads what's new in the channels they chose,
asks the campaign judge (watch/judge.py) whether each post announces a campaign
and how well it fits them, keeps each campaign for the Campaigns page, and
pushes the fitting ones to their phone through ntfy when they've set a topic.

Discord keeps the messages, so nothing is missed while the PC is off: the next
check reads everything since the last message it saw.
"""

from __future__ import annotations

import os
import threading
from datetime import UTC, datetime, timedelta

from ..utils.logging import get_logger
from ..watch import discord
from . import db

log = get_logger(__name__)

TOKEN = "DISCORD_BOT_TOKEN"
TOPIC = "NTFY_TOPIC"
CHECK_MINUTES = 5
FIRST_LOOK = 25    # messages read from a channel when it's first watched...
FIRST_DAYS = 14    # ...of which only posts this recent are judged
MIN_CHARS = 40     # shorter posts ("gm", a role ping) aren't campaigns
MAX_ATTEMPTS = 3   # a post the AI can't judge is retried, then passed over

_attempts: dict[str, int] = {}
_checking = threading.Lock()


def token() -> str:
    return os.environ.get(TOKEN, "").strip()


def watched() -> list[dict]:
    with db.connect() as con:
        return [dict(r) for r in con.execute(
            "SELECT id, guild_id, guild, name, last_id FROM discord_channels ORDER BY guild, name")]


def watch(ids: list[str], available: list[dict]) -> list[dict]:
    """Read exactly these channels from now on (each one of `available`)."""
    by_id = {c["id"]: c for c in available}
    unknown = [i for i in ids if i not in by_id]
    if unknown:
        raise ValueError("the bot can't see one of those channels; refresh the list")
    with db.connect() as con:
        con.execute(f"DELETE FROM discord_channels WHERE id NOT IN ({','.join('?' * len(ids))})", ids)
        for i in ids:
            c = by_id[i]
            con.execute("INSERT INTO discord_channels (id, guild_id, guild, name, added_at) "
                        "VALUES (?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET guild=excluded.guild, "
                        "name=excluded.name", (i, c["guild_id"], c["guild"], c["name"], db.now()))
    return watched()


def prefs() -> dict:
    """What the judge is told about the user, and the lowest pay worth an alert."""
    from ..config import Config

    base = Config.load().watch
    with db.connect() as con:
        s = db.settings(con)
    rate = s["alert_min_rate"]
    return {"profile": s["alert_profile"].strip() or base.profile.strip(),
            "min_rate": float(rate) if rate else base.min_rate_per_1k}


def set_prefs(profile: str, min_rate: float) -> dict:
    if not 0 <= min_rate <= 1000:
        raise ValueError("the minimum rate is dollars per 1,000 views")
    with db.connect() as con:
        db.set_setting(con, "alert_profile", profile.strip()[:2000])
        db.set_setting(con, "alert_min_rate", f"{min_rate:g}")
    return prefs()


def status() -> dict:
    with db.connect() as con:
        s = db.settings(con)
    return {"token_set": bool(token()), "watched": watched(), "checked_at": s["alerts_checked"],
            "error": s["alerts_error"], "push_set": bool(os.environ.get(TOPIC, "").strip()),
            "every_minutes": CHECK_MINUTES, **prefs()}


def _config():
    from ..config import Config

    p = prefs()
    return Config.load().watch.model_copy(update={"profile": p["profile"], "min_rate_per_1k": p["min_rate"]})


def _recent(message: dict) -> bool:
    try:
        sent = datetime.fromisoformat(message["timestamp"])
    except (KeyError, ValueError):
        return True
    return sent >= datetime.now(UTC) - timedelta(days=FIRST_DAYS)


def check(backend=None, *, publish=None) -> dict:
    """Read, judge, keep and push what's new in every watched channel, once."""
    if not _checking.acquire(blocking=False):
        return {"busy": True}
    try:
        return _check(backend, publish)
    finally:
        _checking.release()


def _check(backend, publish) -> dict:
    from ..llm.cache import LLMCache
    from ..watch import notify
    from ..watch.judge import judge_text
    from ..watch.watcher import message_for, should_push
    from .finder import record_found

    result = {"read": 0, "campaigns": 0, "new": [], "pushed": 0, "errors": []}
    tok, channels = token(), watched()
    if not tok or not channels:
        return result
    cfg, cache, topic = _config(), LLMCache(), os.environ.get(TOPIC, "").strip()
    for ch in channels:
        first = ch["last_id"] is None
        try:
            posts = discord.messages(tok, ch["id"], after=ch["last_id"], latest=FIRST_LOOK)
        except discord.DiscordError as exc:
            result["errors"].append(f"#{ch['name']}: {exc}")
            continue
        for m in posts:
            text = discord.text_of(m)
            if len(text) >= MIN_CHARS and (not first or _recent(m)):
                if backend is None:
                    from ..config import Config
                    from ..pipeline import build_backend

                    backend = build_backend(Config.load())
                result["read"] += 1
                author = (m.get("author") or {}).get("username") or "someone"
                verdict = judge_text(
                    f"Discord post in #{ch['name']} ({ch['guild']}), from {author}\n"
                    f"Date: {m.get('timestamp', '')}", text, cfg.profile, backend, cache=cache,
                    label=f"#{ch['name']} {m['id']}")
                if verdict is None:
                    _attempts[m["id"]] = _attempts.get(m["id"], 0) + 1
                    if _attempts[m["id"]] < MAX_ATTEMPTS:
                        result["errors"].append("The AI didn't answer; those posts are read again next check")
                        break  # keep last_id before this post
                    _attempts.pop(m["id"], None)
                elif verdict.is_new_campaign:
                    result["campaigns"] += 1
                    link = verdict.link or discord.link_to(ch["guild_id"], ch["id"], m["id"])
                    verdict = verdict.model_copy(update={"link": link})
                    if record_found(verdict, text, via="discord"):
                        result["new"].append(verdict.name or "a campaign")
                        ok, _ = should_push(verdict, cfg)
                        if ok and topic:
                            try:
                                notify.push(cfg.ntfy_server, notify.payload(topic, **message_for(verdict)))
                                result["pushed"] += 1
                            except notify.PushError as exc:
                                log.warning("alert push failed: %s", exc)
            with db.connect() as con:
                con.execute("UPDATE discord_channels SET last_id=? WHERE id=?", (m["id"], ch["id"]))
        if first and not posts:  # an empty channel: read from now on
            with db.connect() as con:
                con.execute("UPDATE discord_channels SET last_id=? WHERE id=? AND last_id IS NULL",
                            (discord.snowflake_now(), ch["id"]))
    with db.connect() as con:
        db.set_setting(con, "alerts_checked", db.now())
        db.set_setting(con, "alerts_error", result["errors"][0] if result["errors"] else "")
    if publish and (result["new"] or result["errors"] or result["read"]):
        publish("found.changed")
    return result


def test_push() -> None:
    from ..watch import notify

    topic = os.environ.get(TOPIC, "").strip()
    if not topic:
        raise ValueError("add your ntfy topic first")
    notify.push(_config().ntfy_server, notify.payload(
        topic, title="Clipper campaign alerts",
        message="Test alert. New campaigns that fit you will arrive like this.",
        tags=["white_check_mark"]))
