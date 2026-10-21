"""The hosted plan's meter (D237): what the account's plan allows, and what this Clipper has used of it.

Hosted, the gateway writes `<data>/plan.json` (hosted/catalog.plan_json): the month's footage minutes and Create
Shorts, the credits ever bought, the watermark, retention, accounts and queue. What Clipper charged is kept in
`<data>/meter.sqlite`; after each charge it writes `<data>/meter.json`, which the gateway reads to show what is left.

* Minutes: a source is charged once ever, by its id (a re-run or re-cut of it is free), in whole minutes rounded
  up, at least MIN_MINUTES a video; checked when the length is known, before transcription or any AI call.
* Shorts: a video's first build is one Short, counted once by its id. A Short includes `redraws_per_short`
  redraws; each further batch of them uses one more Short.
* The month's allowance goes first, then credits. Free's Short is once ever (`shorts_once`), not a month's.
* Not enough left: PlanLimit, in plain words, before anything is spent; an endpoint answers 402 (studio/server.py).

The studio's older feature tiers (studio/plans.py) take their tier from here when hosted.

On one's own computer there is no plan.json: nothing here limits, counts or deletes anything. Hosted with plan.json
missing or unreadable, Free's limits apply (fail closed), but retention deletes nothing on them.
"""

from __future__ import annotations

import contextlib
import json
import math
import os
import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path

from .utils.logging import get_logger

log = get_logger(__name__)

#: A video is billed at least this many minutes (the pricing).
MIN_MINUTES = 10
#: Clips rendered at once by the plan's queue (runner's render pool).
QUEUE_WORKERS = {"normal": 1, "fast": 2, "fastest": 3}
#: What applies when hosted without a readable plan.json: Free's limits (hosted/catalog.py), no credits.
FREE = {"plan": "free", "status": "active", "period_start": "", "period_end": "",
        "limits": {"minutes": 60, "shorts": 1, "shorts_once": True, "redraws_per_short": 3, "retention_days": 7,
                   "accounts_per_platform": 1, "seats": 1, "watermark_after": 3, "queue": "normal"},
        "credits": {"minutes": 0, "shorts": 0}, "ai_budget_usd": None, "billing_url": "/billing"}
DAY = 86400.0

_lock = threading.Lock()


class PlanLimit(RuntimeError):
    """The plan hasn't enough left for this: nothing was started or charged. The message says what gives more."""


def plan() -> dict | None:
    """plan.json with every field there, plus `fallback` (True: Free's stand-in). None on one's own computer."""
    from .hosting import hosted
    from .paths import data_root

    path = data_root() / "plan.json"
    if not path.is_file():
        return {**FREE, "fallback": True} if hosted() else None
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
        limits = {**FREE["limits"], **doc["limits"]}
        credits = {k: int((doc.get("credits") or {}).get(k) or 0) for k in ("minutes", "shorts")}
        _since(doc["period_start"])
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        log.error("meter: plan.json can't be read (%s); Free's limits apply", exc)
        return {**FREE, "fallback": True}
    return {**FREE, **doc, "limits": limits, "credits": credits, "fallback": False}


def _since(stamp: str) -> float:
    """The period's start; none known (the fail-closed stand-in): everything ever counts."""
    return datetime.fromisoformat(stamp).timestamp() if stamp else 0.0


def _day(stamp: str) -> str:
    try:
        dt = datetime.fromisoformat(stamp)
    except (TypeError, ValueError):
        return ""
    return f"{dt.day} {dt:%B}"


# ---------- the store

def _db() -> sqlite3.Connection:
    from .paths import data_root

    path = data_root() / "meter.sqlite"
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, timeout=10, isolation_level=None)
    # kind: minutes | shorts | redraws | clips; what: "source:<id>", "video:<id>", "clip:<source>:<clip>";
    # paid: allowance | credits (minutes, shorts), "" (redraws), clean | marked (clips, the watermark).
    con.execute("CREATE TABLE IF NOT EXISTS charges (at REAL, kind TEXT, amount INT, what TEXT, paid TEXT)")
    return con


@contextlib.contextmanager
def _tx():
    """One charge at a time: the lock against the server's other threads, BEGIN IMMEDIATE against a second
    process (the CLI), so two jobs can't both spend the last minutes."""
    with _lock, contextlib.closing(_db()) as con:
        con.execute("BEGIN IMMEDIATE")
        try:
            yield con
        except BaseException:
            con.execute("ROLLBACK")
            raise
        con.execute("COMMIT")


def _sum(con: sqlite3.Connection, kind: str, *, paid: str = "", since: float = 0.0, what: str = "") -> int:
    query, args = "SELECT COALESCE(SUM(amount), 0) FROM charges WHERE kind = ? AND at >= ?", [kind, since]
    if paid:
        query, args = query + " AND paid = ?", [*args, paid]
    if what:
        query, args = query + " AND what = ?", [*args, what]
    return int(con.execute(query, args).fetchone()[0])


def _used(con: sqlite3.Connection, p: dict, kind: str) -> int:
    """The allowance used this period (Free's once-ever Short: ever)."""
    once = kind == "shorts" and p["limits"]["shorts_once"]
    return _sum(con, kind, paid="allowance", since=0.0 if once else _since(p["period_start"]))


def _left(con: sqlite3.Connection, p: dict, kind: str) -> tuple[int, int]:
    """(allowance left, credits left). Credits left are those ever bought less those ever used."""
    return (max(0, int(p["limits"][kind]) - _used(con, p, kind)),
            max(0, p["credits"][kind] - _sum(con, kind, paid="credits")))


def _charge(con: sqlite3.Connection, p: dict, kind: str, amount: int, what: str, *, dry: bool = False,
            redraw: bool = False) -> None:
    """`amount` from the allowance first, then credits; PlanLimit, charging nothing, when both together fall short."""
    allowance, credits = _left(con, p, kind)
    if amount > allowance + credits:
        raise PlanLimit(_refusal(p, kind, amount, allowance + credits, redraw=redraw))
    if dry:
        return
    now, first = time.time(), min(amount, allowance)
    for paid, n in (("allowance", first), ("credits", amount - first)):
        if n:
            con.execute("INSERT INTO charges VALUES (?, ?, ?, ?, ?)", (now, kind, n, what, paid))
    _write_meter(con, p)


def _refusal(p: dict, kind: str, need: int, left: int, *, redraw: bool) -> str:
    until = f" until {_day(p['period_end'])}" if _day(p["period_end"]) and not (
        kind == "shorts" and p["limits"]["shorts_once"]) else ""
    if kind == "minutes":
        return (f"This video counts as {need} footage minutes, and your plan has {left} left{until}. "
                "Buy more minutes or change plan on the Billing page.")
    what = (f"These drawings need {need} more Short{'s' if need != 1 else ''} (each Short includes "
            f"{p['limits']['redraws_per_short']} redraws), and your plan has {left or 'none'} left{until}."
            if redraw else f"Your plan has no Create Shorts left{until}.")
    return f"{what} Buy 5 Shorts or change plan on the Billing page."


def _write_meter(con: sqlite3.Connection, p: dict) -> None:
    """`<data>/meter.json` for the gateway, replaced in one step (the gateway never reads half a file). Written
    inside the charge's transaction, so two charges can't write their figures in the wrong order."""
    from .paths import data_root

    doc = {"period_start": p["period_start"], "minutes_used": _used(con, p, "minutes"),
           "shorts_used": _used(con, p, "shorts"),
           "credits_used": {k: _sum(con, k, paid="credits") for k in ("minutes", "shorts")}}
    path = data_root() / "meter.json"
    tmp = path.with_name(f"meter.json.{os.getpid()}.tmp")
    try:
        tmp.write_text(json.dumps(doc), encoding="utf-8")
        tmp.replace(path)
    except OSError as exc:   # the charge stands; the gateway shows the last figures until the next one
        log.warning("meter: couldn't write %s (%s)", path, exc)


# ---------- footage minutes

def billable(seconds: float) -> int:
    return max(MIN_MINUTES, math.ceil(seconds / 60))


def _charged(con: sqlite3.Connection, source_id: str) -> bool:
    return con.execute("SELECT 1 FROM charges WHERE kind = 'minutes' AND what = ?",
                       (f"source:{source_id}",)).fetchone() is not None


def charge_source(source_id: str, seconds: float) -> None:
    """A source's minutes, once ever: called by ingest when its length is known, before transcription or any AI
    call. PlanLimit when not enough are left (the job fails with that message)."""
    p = plan()
    if p is None:
        return
    with _tx() as con:
        if _charged(con, source_id):
            return
        _charge(con, p, "minutes", billable(seconds), f"source:{source_id}")
    log.info("meter: source %s charged %d minute(s)", source_id, billable(seconds))


def check_sources(paths: list[str]) -> None:
    """At New clips' submit, when the files' lengths are known: PlanLimit (a 402) when those not charged yet need
    more minutes than are left. A file that can't be read is left to ingest, which says what's wrong with it."""
    p = plan()
    if p is None:
        return
    from .ingest.probe import probe
    from .utils.cache import source_id_for_file

    need = 0
    with contextlib.closing(_db()) as con:
        for path in paths:
            try:
                if not _charged(con, source_id_for_file(path)):
                    need += billable(probe(path).duration)
            except (OSError, RuntimeError, ValueError) as exc:
                log.debug("meter: %s not measured (%s)", path, exc)
        if need:
            _charge(con, p, "minutes", need, "", dry=True)


# ---------- Create Shorts (create/meterhooks.py calls these)

def charge_first_build(video_id: int, *, dry: bool = False) -> None:
    """One Short for a video's first build, counted once by its id (a failed first build is tried again)."""
    p = plan()
    if p is None:
        return
    with _tx() as con:
        if not _sum(con, "shorts", what=f"video:{video_id}"):
            _charge(con, p, "shorts", 1, f"video:{video_id}", dry=dry)


def charge_redraws(video_id: int, count: int, *, dry: bool = False) -> None:
    """`count` redraws of a video: free while its Shorts' redraws last, then one more Short per batch of them.
    ponytail: counted when the build starts, so a rebuild cancelled before drawing counts them again on its
    retry; keep which drawings were counted if that turns out to matter."""
    p = plan()
    if p is None or count <= 0:
        return
    what, per = f"video:{video_id}", max(1, int(p["limits"]["redraws_per_short"]))
    with _tx() as con:
        covered = _sum(con, "shorts", what=what) * per
        extra = math.ceil(max(0, _sum(con, "redraws", what=what) + count - covered) / per)
        if extra:
            _charge(con, p, "shorts", extra, what, dry=dry, redraw=True)
        if not dry:
            con.execute("INSERT INTO charges VALUES (?, ?, ?, ?, '')", (time.time(), "redraws", count, what))


# ---------- the rest of the plan

def watermark(what: str) -> bool:
    """Whether a clip's render carries the watermark: past this period's first `watermark_after` clips. A clip
    keeps what it was first given (`what` names it: re-rendered with a new hook, a clean one stays clean), so the
    count is of clips, not renders. ponytail: a clip that fails its checks uses a number too."""
    p = plan()
    if p is None:
        return False
    after = p["limits"].get("watermark_after")
    with _tx() as con:
        row = con.execute("SELECT paid FROM charges WHERE kind = 'clips' AND what = ?", (what,)).fetchone()
        if row:
            return row[0] == "marked"
        marked = after is not None and _clips(con, p) >= int(after)
        con.execute("INSERT INTO charges VALUES (?, 'clips', 1, ?, ?)", (time.time(), what, "marked" if marked else "clean"))
    return marked


def _clips(con: sqlite3.Connection, p: dict) -> int:
    return _sum(con, "clips", since=_since(p["period_start"]))


def render_workers(default: int) -> int:
    """Clips rendered at once: the plan's queue hosted, `default` on one's own computer."""
    p = plan()
    return default if p is None else QUEUE_WORKERS.get(str(p["limits"]["queue"]), 1)


def accounts_per_platform() -> int | None:
    p = plan()
    return None if p is None else int(p["limits"]["accounts_per_platform"])


def billing_url() -> str:
    p = plan()
    return (p or {}).get("billing_url") or "/billing"


def usage() -> dict:
    """GET /api/usage (wave 3 contract): the plan and what is used of it; {"hosted": false} on one's own computer."""
    p = plan()
    if p is None:
        return {"hosted": False}
    from .llm import usage as ai

    lim = p["limits"]
    with contextlib.closing(_db()) as con:
        minutes, shorts = _left(con, p, "minutes"), _left(con, p, "shorts")
        used = {k: _used(con, p, k) for k in ("minutes", "shorts")}
        clips = _clips(con, p)
    return {"hosted": True, "plan": p["plan"], "status": p["status"], "period_end": p["period_end"],
            "minutes": {"used": used["minutes"], "limit": int(lim["minutes"]), "credits_left": minutes[1]},
            "shorts": {"used": used["shorts"], "limit": int(lim["shorts"]), "credits_left": shorts[1],
                       "once": bool(lim["shorts_once"])},
            "redraws_per_short": int(lim["redraws_per_short"]), "retention_days": int(lim["retention_days"]),
            "watermark_after": lim["watermark_after"], "clips_this_period": clips,
            "ai": {"spent_usd": round(ai.spent_usd(_since(p["period_start"])), 2), "budget_usd": p.get("ai_budget_usd")},
            "billing_url": p.get("billing_url") or "/billing", "logout_url": "/logout"}


# ---------- retention

def _age(path: Path) -> float:
    """When a file was last written: hosted, sources are uploads and imported links, written here as they arrive."""
    return path.stat().st_mtime


def _newest(folder: Path) -> float:
    """A work folder's last use: the folder or anything directly in it."""
    return max([_age(folder), *(_age(p) for p in folder.iterdir())])


def _inside(path: Path, folder: Path) -> bool:
    try:
        return folder.resolve() in path.resolve().parents
    except OSError:
        return False


def retention(now: float | None = None) -> dict[str, int]:
    """Hosted, delete what is older than the plan keeps: clips' files (their library rows stay, with no file, so
    the page shows no player for them), sources uploaded or downloaded, and the work folders. Only inside this
    data folder, and never on Free's stand-in for an unreadable plan.json: a paying user's clips would go.
    Hosted deletes are real (utils/recycle, D229). Returns how many of each went."""
    from .hosting import hosted
    from .paths import data_root, downloads_dir
    from .studio import db, library
    from .utils.recycle import recycle

    p = plan()
    if p is None or p["fallback"] or not hosted():
        return {}
    now = time.time() if now is None else now
    cutoff = now - int(p["limits"]["retention_days"]) * DAY
    gone = {"clips": 0, "sources": 0, "work": 0}
    stamp = datetime.fromtimestamp(cutoff).strftime("%Y-%m-%d %H:%M")   # the library's clock (db.now)
    with db.connect() as con:
        for row in con.execute("SELECT id, file FROM clips WHERE file != '' AND created_at < ?", (stamp,)).fetchall():
            path = library.clip_path(row["file"])
            if not _inside(path, library.library_dir()) or (path.exists() and _age(path) >= cutoff):
                continue   # outside the library, or made again since (a new hook)
            if recycle(path):
                if library.captions_path(path).is_file():   # its .srt goes with it (D240)
                    recycle(library.captions_path(path))
                con.execute("UPDATE clips SET file = '' WHERE id = ?", (row["id"],))
                gone["clips"] += 1
    root = data_root()
    work = root / "work"
    folders = [d for d in work.iterdir() if d.name != "runs"] if work.is_dir() else []
    folders += list((work / "runs").iterdir()) if (work / "runs").is_dir() else []
    for kind, paths in (("sources", list(downloads_dir().iterdir()) if downloads_dir().is_dir() else []),
                        ("work", folders)):
        for path in paths:
            if path.is_symlink() or not _inside(path, root):
                continue
            if (_newest(path) if path.is_dir() else _age(path)) < cutoff and recycle(path):
                gone[kind] += 1
    if any(gone.values()):
        log.info("retention (%d days): deleted %d clip file(s), %d source(s), %d work folder(s)",
                 int(p["limits"]["retention_days"]), gone["clips"], gone["sources"], gone["work"])
    return gone


if __name__ == "__main__":      # the gateway, daily, for a user whose Clipper isn't running: python -m clipper.meter
    print(retention())
