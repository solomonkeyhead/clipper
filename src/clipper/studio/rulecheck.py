"""Keeping every clip not yet posted on its brief's rules (D81).

Whenever a campaign's rules may have changed -- the campaign saved, its brief
pasted, a rule added from a finding, a run finished, the Control Center
started -- its unposted clips are brought up to date:

1. each stored caption gets whatever the every-platform rules now require
   (campaign/rules.py `enforce`), in the library and the performance log, so
   what the syncs match on is what gets posted;
2. each clip's post texts are read against the brief by the AI check
   (campaign/audit.py), unless it already read exactly these texts against
   exactly this brief.

Posted clips are left alone: their caption is what's live. One check per
campaign runs at a time, in the background; the clips page updates when done.
"""

from __future__ import annotations

import hashlib
import json
import threading
from datetime import datetime

from ..campaign import audit, rules
from ..config import CampaignConfig
from ..utils.logging import get_logger
from . import db

log = get_logger(__name__)

_running: set[str] = set()
_again: set[str] = set()
_lock = threading.Lock()


def _unposted(con, campaign: str, live: dict) -> list[dict]:
    return [c for c in db.clips(con, campaign) if c["status"] in ("ready", "skipped")
            and not live.get((c["campaign"], c["source_id"], c["clip_id"]))]


def reapply(campaign: CampaignConfig) -> int:
    """Rewrite unposted clips' captions to the current rules. Returns how many changed."""
    from ..learn import log as perf
    from . import stats

    changed = 0
    with stats.log_lock:
        rows = perf.read()
        live = stats.posts_by_clip(rows)
        with db.connect() as con:
            for clip in _unposted(con, campaign.name, live):
                caption = rules.enforce(clip["caption"] or "", campaign)
                if caption == (clip["caption"] or ""):
                    continue
                db.update_clip(con, clip["id"], caption=caption)
                for row in rows:
                    if (not row.get("url") and (row.get("campaign"), row.get("source_id"), row.get("clip_id"))
                            == (clip["campaign"], clip["source_id"], clip["clip_id"])):
                        row["caption"] = caption
                changed += 1
        if changed:
            try:
                perf.write(rows)
            except PermissionError:
                log.warning("rule check: %s is open in Excel; the log's captions update next time",
                            perf.log_path().name)
    if changed:
        log.info("%s: %d caption(s) brought up to the brief's rules", campaign.name, changed)
    return changed


#: (campaign fingerprint, title, caption, hook) -> its texts: every page load builds
#: them for every unposted clip, and they only change when one of those does.
_texts: dict[tuple, list[rules.PostText]] = {}


def texts(clip: dict, campaign: CampaignConfig, *, fingerprint: str | None = None) -> list[rules.PostText]:
    """Each platform's texts for `clip`. With `fingerprint` (the campaign as it is
    now, see `fingerprint`), remembered until the clip's or the campaign's text changes."""
    # On YouTube the searchable title, when one is written (D96); else the clip's name.
    args = (extras(clip).get("youtube_title") or clip["title"] or "", clip["caption"] or "", clip["hook"] or "")
    duration = clip.get("duration_s")
    if fingerprint is None:
        return rules.post_texts(*args, campaign, duration)
    key = (fingerprint, *args, duration)
    found = _texts.get(key)
    if found is None:
        if len(_texts) > 5000:
            _texts.clear()
        found = _texts[key] = rules.post_texts(*args, campaign, duration)
    return found


def fingerprint(campaign: CampaignConfig) -> str:
    return hashlib.sha256(campaign.model_dump_json().encode()).hexdigest()[:16]


def audit_key(clip: dict, campaign: CampaignConfig, brief: str | None,
              posts: list[rules.PostText] | None = None) -> tuple[str, str]:
    """(the AI check's prompt for this clip, its key). `posts`: its texts, if already worked out."""
    posts = texts(clip, campaign) if posts is None else posts
    user = audit.build_user(audit.brief_text(campaign, brief), posts, clip["hook"] or "",
                            audit.code_checked(campaign))
    return user, audit.key(user)


def stored(clip: dict) -> dict | None:
    try:
        return json.loads(clip.get("audit") or "null")
    except ValueError:
        return None


def check_clips(campaign: CampaignConfig, *, backends=None, cache=None, publish=None) -> int:
    """The AI check on each unposted clip whose texts or brief changed. Returns how many it read."""
    from ..learn import log as perf
    from . import stats

    live = stats.posts_by_clip(perf.read())
    with db.connect() as con:
        brief = (db.brief(con, campaign.name) or {}).get("text")
        clips = _unposted(con, campaign.name, live)
    todo = []
    for clip in clips:
        user, key = audit_key(clip, campaign, brief)
        if (stored(clip) or {}).get("key") != key:
            todo.append((clip, user, key))
    if not todo:
        return 0
    if backends is None:
        backends = _backends()
    if cache is None:
        from ..llm.cache import LLMCache

        cache = LLMCache()
    done = 0
    for i in range(0, len(todo), audit.AUDIT_BATCH):   # stored as each batch is read, so the page fills in
        chunk = todo[i:i + audit.AUDIT_BATCH]
        found = audit.audit([(texts(clip, campaign), clip["hook"] or "") for clip, _, _ in chunk],
                            audit.brief_text(campaign, brief), audit.code_checked(campaign), backends, cache=cache)
        with db.connect() as con:
            for (clip, _, key), (problems, refused) in zip(chunk, found, strict=True):
                if problems is None:
                    continue  # unchecked: tried again next time; refused: said on the clip, check it by hand
                db.set_audit(con, clip["id"], {"key": key, "problems": [p.model_dump() for p in problems],
                                               "refused": refused, "at": datetime.now().strftime("%Y-%m-%d %H:%M")})
                done += 1
        if publish:
            publish("clips.changed")
    log.info("%s: the AI rule check read %d clip(s)", campaign.name, done)
    return done


#: A clip whose title and comment couldn't be written is tried again after this long.
EXTRAS_RETRY_HOURS = 24


def extras(clip: dict) -> dict:
    """The clip's stored YouTube title and pinned comment (campaign/extras.py), if any."""
    try:
        found = json.loads(clip.get("extras") or "null") or {}
    except ValueError:
        return {}
    return found if found.get("youtube_title") else {}


def _extras_due(clip: dict, now: datetime) -> bool:
    from ..campaign.extras import EXTRAS_VERSION

    try:
        found = json.loads(clip.get("extras") or "null") or {}
    except ValueError:
        found = {}
    if found.get("v") != EXTRAS_VERSION:
        return True
    if found.get("youtube_title"):
        return False
    failed = found.get("failed_at")
    return not failed or (now - datetime.strptime(failed, "%Y-%m-%d %H:%M")).total_seconds() > EXTRAS_RETRY_HOURS * 3600


def fill_extras(campaign: CampaignConfig, *, backends=None, cache=None) -> int:
    """Write the YouTube title and pinned comment for each unposted clip without
    current ones (D96), several clips a call (D185). Returns how many were written."""
    from ..campaign import extras as extras_mod
    from ..campaign.description import pasted_brief
    from ..learn import log as perf
    from . import library, stats

    now = datetime.now()
    live = stats.posts_by_clip(perf.read())
    with db.connect() as con:
        # Ready clips only: a skipped one gets its own if it's put back.
        clips = [c for c in _unposted(con, campaign.name, live) if c["status"] == "ready" and _extras_due(c, now)]
    if not clips:
        return 0
    spoken = library.clip_texts(clips)
    if backends is None:
        backends = _backends()
    if cache is None:
        from ..llm.cache import LLMCache

        cache = LLMCache()
    clips = [c for c in clips if c["id"] in spoken]
    written = extras_mod.write([(spoken[c["id"]], c["hook"] or "", c.get("source_title") or "") for c in clips],
                               campaign, backends, brief=pasted_brief(campaign.name), cache=cache)
    with db.connect() as con:
        for clip, found in zip(clips, written, strict=True):
            value = ({"v": extras_mod.EXTRAS_VERSION, **found.model_dump()} if found
                     else {"v": extras_mod.EXTRAS_VERSION, "failed_at": now.strftime("%Y-%m-%d %H:%M")})
            db.update_clip(con, clip["id"], extras=json.dumps(value))
    done = sum(1 for found in written if found)
    log.info("%s: YouTube titles and pinned comments written for %d clip(s)", campaign.name, done)
    return done


def _backends():
    from ..config import Config
    from ..runner import _correction_backends

    return _correction_backends(Config.load(), None)


def recheck(name: str, *, publish=None, backends=None, cache=None) -> None:
    """Both steps for campaign `name`, now."""
    from .server import load_campaigns

    campaign = load_campaigns().get(name)
    if campaign is None:
        return
    if reapply(campaign) and publish:
        publish("clips.changed")
    # Titles first: the AI check then reads the texts as they'll be posted.
    if fill_extras(campaign, backends=backends, cache=cache) and publish:
        publish("clips.changed")
    check_clips(campaign, backends=backends, cache=cache, publish=publish)
    if publish:
        publish("clips.changed")


def start(names: list[str] | str, publish=None) -> None:
    """`recheck` in the background, one campaign at a time; a campaign asked for
    while it is being checked is checked again after."""
    for name in [names] if isinstance(names, str) else names:
        with _lock:
            if name in _running:
                _again.add(name)
                continue
            _running.add(name)
        threading.Thread(target=_run, args=(name, publish), name=f"rulecheck-{name}", daemon=True).start()


def checking(name: str) -> bool:
    return name in _running


def _run(name: str, publish) -> None:
    while True:
        try:
            recheck(name, publish=publish)
        except Exception:  # a failed check leaves clips unchecked, and says so in the log
            log.exception("rule check for %s failed", name)
        with _lock:
            if name in _again:
                _again.discard(name)
                continue
            _running.discard(name)
            return
