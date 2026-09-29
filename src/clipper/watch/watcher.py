"""One pass of the watcher: read new mail, judge it, push what fits.

Meant to run on a schedule (Task Scheduler). A pass is idempotent: every email
is judged once, and each campaign is pushed once, however many emails mention
it. A message whose judgement or push fails is retried on the next pass, up to
`MAX_ATTEMPTS`, so a dropped connection costs a delay, not a missed campaign.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from ..config import WatchConfig
from ..llm.base import LLMBackend
from ..llm.cache import LLMCache
from ..paths import data_root, ensure
from ..utils.logging import get_logger
from . import mailbox, notify
from .judge import Verdict, judge

log = get_logger(__name__)

MAX_ATTEMPTS = 3
ENV_USER = "WATCH_EMAIL"
ENV_PASSWORD = "WATCH_EMAIL_APP_PASSWORD"
ENV_TOPIC = "NTFY_TOPIC"


class WatchConfigError(RuntimeError):
    """A required setting is missing from .env."""


@dataclass
class State:
    """What earlier passes have done, kept in data/watch/state.json."""

    done: list[str] = field(default_factory=list)  # message keys fully handled
    attempts: dict[str, int] = field(default_factory=dict)
    pushed: list[str] = field(default_factory=list)  # campaign keys already pushed

    @classmethod
    def load(cls, path: Path) -> State:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return cls()
        return cls(done=list(data.get("done", [])), attempts=dict(data.get("attempts", {})),
                   pushed=list(data.get("pushed", [])))

    def save(self, path: Path) -> None:
        ensure(path.parent)
        # Bounded: a few thousand keys covers years of campaign mail.
        data = {"done": self.done[-5000:], "attempts": self.attempts,
                "pushed": self.pushed[-2000:]}
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
        tmp.replace(path)


@dataclass
class PassResult:
    read: int = 0
    campaigns: int = 0
    pushed: list[Verdict] = field(default_factory=list)
    skipped: list[tuple[Verdict, str]] = field(default_factory=list)
    failed: int = 0


def state_path() -> Path:
    return data_root() / "watch" / "state.json"


def secrets() -> tuple[str, str, str]:
    """(mailbox user, app password, ntfy topic) from the environment / .env."""
    values = [os.environ.get(k, "").strip() for k in (ENV_USER, ENV_PASSWORD, ENV_TOPIC)]
    missing = [k for k, v in zip((ENV_USER, ENV_PASSWORD, ENV_TOPIC), values, strict=True)
               if not v]
    if missing:
        raise WatchConfigError(
            f"missing in .env: {', '.join(missing)}. See .env.example (campaign watcher).")
    user, password, topic = values
    return user, password.replace(" ", ""), topic


def campaign_key(v: Verdict) -> str:
    return f"{v.source}|{v.name.strip().lower()}|{v.owner.strip().lower()}"


def should_push(v: Verdict, cfg: WatchConfig) -> tuple[bool, str]:
    """Whether a verdict is worth a push, and if not, why not."""
    if not v.is_new_campaign:
        return False, "not a new campaign"
    if v.fit == "no":
        return False, f"not a fit: {v.why}"
    if v.fit == "maybe" and not cfg.notify_maybe:
        return False, "only a maybe"
    if 0 < v.rate_per_1k_usd < cfg.min_rate_per_1k:
        return False, f"pays ${v.rate_per_1k_usd:.2f}/1K, under ${cfg.min_rate_per_1k:.2f}/1K"
    wanted = {p.lower() for p in cfg.platforms}
    offered = {p.lower() for p in v.platforms}
    if wanted and offered and not wanted & offered:
        return False, f"only on {', '.join(sorted(offered))}"
    return True, ""


def message_for(v: Verdict) -> dict:
    """Title and body of the push for one campaign."""
    where = {"vyro": "Vyro", "whop": "Whop"}.get(v.source, "Campaign")
    title = f"{where}: {v.name or 'new campaign'}"
    if v.rate:
        title += f" ({v.rate})"
    lines = [v.why] if v.why else []
    details = [x for x in (v.owner and f"by {v.owner}", v.content,
                           v.platforms and "on " + ", ".join(v.platforms),
                           v.budget and f"budget {v.budget}",
                           v.deadline and f"ends {v.deadline}") if x]
    if details:
        lines.append(" · ".join(details))
    if v.locked == "yes":
        lines.append("Locked / application only.")
    if v.rights == "unclear":
        lines.append("Check the campaign owner has rights to the content before clipping.")
    return {"title": title, "message": "\n".join(lines) or "New campaign",
            "click": v.link, "priority": 4 if v.fit == "yes" else 3,
            "tags": ["movie_camera"] if v.fit == "yes" else ["grey_question"]}


def run_pass(cfg: WatchConfig, backend: LLMBackend | list[LLMBackend], *,
             dry_run: bool = False, path: Path | None = None) -> PassResult:
    """Read, judge and push once. `dry_run` judges without pushing or saving."""
    user, password, topic = secrets()
    path = path or state_path()
    state = State.load(path)
    mails = mailbox.fetch_recent(cfg.imap_host, user, password, folders=cfg.folders,
                                 days=cfg.lookback_days, skip=set(state.done))
    result = PassResult(read=len(mails))
    cache = LLMCache()
    for mail in mails:
        verdict = judge(mail, cfg.profile, backend, cache=cache)
        if verdict is None:
            _failed(state, mail.key, result)
            continue
        if verdict.is_new_campaign:
            result.campaigns += 1
            if not dry_run:
                _remember(verdict, mail)
        ok, reason = should_push(verdict, cfg)
        key = campaign_key(verdict)
        if ok and key in state.pushed:
            ok, reason = False, "already pushed"
        if not ok:
            result.skipped.append((verdict, reason))
            log.info("not pushed: %r (%s)", mail.subject, reason)
            state.done.append(mail.key)
            continue
        if not dry_run:
            try:
                notify.push(cfg.ntfy_server, notify.payload(topic, **message_for(verdict)))
            except notify.PushError as exc:
                log.warning("push failed, will retry next pass: %s", exc)
                _failed(state, mail.key, result)
                continue
            state.pushed.append(key)
        result.pushed.append(verdict)
        state.done.append(mail.key)
    if not dry_run:
        state.save(path)
    return result


def _remember(verdict: Verdict, mail) -> None:
    """Keep the campaign for the Control Center's Campaigns page, pushed or not."""
    try:
        from ..studio.finder import record_found

        record_found(verdict, f"{mail.subject}\n\n{mail.text}")
    except Exception as exc:  # the page is a convenience; the push still matters
        log.warning("could not keep %r for the Control Center: %s", verdict.name, exc)


def _failed(state: State, key: str, result: PassResult) -> None:
    result.failed += 1
    state.attempts[key] = state.attempts.get(key, 0) + 1
    if state.attempts[key] >= MAX_ATTEMPTS:
        log.warning("giving up on a message after %d attempts", MAX_ATTEMPTS)
        state.done.append(key)
        state.attempts.pop(key, None)


def test_push(cfg: WatchConfig) -> None:
    """Send one push so the user can see the phone side works."""
    _, _, topic = secrets()
    notify.push(cfg.ntfy_server, notify.payload(
        topic, title="Clipper campaign watcher",
        message="Test push. New campaigns that fit you will arrive like this.",
        tags=["white_check_mark"]))
