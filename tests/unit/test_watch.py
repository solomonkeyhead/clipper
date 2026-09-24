"""Campaign watcher: email parsing, judging, push decisions and idempotence.

Nothing here touches the network: the mailbox and the push are replaced, and
the judge answers through the mock backend.
"""

from __future__ import annotations

import json
from email.message import EmailMessage

import pytest

from clipper.config import WatchConfig
from clipper.llm.base import MockBackend
from clipper.watch import mailbox, notify, watcher
from clipper.watch.judge import Verdict, judge, safe_link
from clipper.watch.mailbox import Mail, parse


def raw_email(*, subject="New campaign: FX Adults S2", html=None, plain=None,
              msg_id="<a1@vyro.com>") -> bytes:
    msg = EmailMessage()
    msg["From"] = "Vyro <campaigns@vyro.com>"
    msg["To"] = "alerts@example.com"
    msg["Subject"] = subject
    msg["Date"] = "Tue, 22 Sep 2026 10:00:00 +0000"
    if msg_id:
        msg["Message-ID"] = msg_id
    msg.set_content(plain or "See the campaign.")
    if html:
        msg.add_alternative(html, subtype="html")
    return msg.as_bytes()


class TestParse:
    def test_html_links_survive_as_text(self):
        mail = parse(raw_email(html=(
            "<html><head><style>p{}</style></head><body><p>FX: Adults | S2 is live.</p>"
            '<p><a href="https://vyro.com/campaigns/fx-adults-s2-lgHLBft0">Join now</a></p>'
            "</body></html>")))
        assert "FX: Adults | S2 is live." in mail.text
        assert "(https://vyro.com/campaigns/fx-adults-s2-lgHLBft0)" in mail.text
        assert "p{}" not in mail.text, "style blocks are not text"
        assert mail.key == "<a1@vyro.com>"

    def test_plain_only_message(self):
        mail = parse(raw_email(plain="A new campaign pays $2 per 1,000 views."))
        assert "$2 per 1,000 views" in mail.text

    def test_a_message_without_id_gets_a_stable_key(self):
        a = parse(raw_email(msg_id=None))
        b = parse(raw_email(msg_id=None))
        assert a.key == b.key and len(a.key) == 64


class TestSafeLink:
    @pytest.mark.parametrize("url", [
        "https://vyro.com/campaigns/x", "https://app.vyro.com/c/1",
        "https://whop.com/discover/x", "https://contentrewards.com/discover"])
    def test_campaign_sites_are_kept(self, url):
        assert safe_link(url) == url

    @pytest.mark.parametrize("url", [
        "http://vyro.com/campaigns/x", "https://vyro.com.evil.example/x",
        "https://notvyro.com/x", "javascript:alert(1)", ""])
    def test_anything_else_is_dropped(self, url):
        assert safe_link(url) == ""


def verdict(**kw) -> Verdict:
    base = dict(is_new_campaign=True, source="vyro", name="FX: Adults | S2", owner="FX",
                rate="$2,000 / 1M views", rate_per_1k_usd=2.0, platforms=["tiktok"],
                fit="yes", why="TV comedy, your niche", rights="licensed")
    return Verdict(**{**base, **kw})


class TestShouldPush:
    cfg = WatchConfig()

    def test_a_fitting_campaign_is_pushed(self):
        assert watcher.should_push(verdict(), self.cfg) == (True, "")

    @pytest.mark.parametrize("change, reason", [
        ({"is_new_campaign": False}, "not a new campaign"),
        ({"fit": "no", "why": "supplement ad"}, "not a fit"),
        ({"rate_per_1k_usd": 0.5}, "under"),
        ({"platforms": ["youtube"]}, "only on youtube"),
    ])
    def test_reasons_not_to_push(self, change, reason):
        ok, why = watcher.should_push(verdict(**change), self.cfg)
        assert not ok and reason in why

    def test_an_unstated_rate_is_not_a_reason_to_skip(self):
        assert watcher.should_push(verdict(rate_per_1k_usd=0.0), self.cfg)[0]

    def test_maybes_can_be_turned_off(self):
        cfg = WatchConfig(notify_maybe=False)
        assert not watcher.should_push(verdict(fit="maybe"), cfg)[0]


class TestMessage:
    def test_unclear_rights_are_flagged(self):
        body = watcher.message_for(verdict(rights="unclear"))
        assert "rights" in body["message"]

    def test_title_names_platform_campaign_and_rate(self):
        body = watcher.message_for(verdict())
        assert body["title"] == "Vyro: FX: Adults | S2 ($2,000 / 1M views)"
        assert body["priority"] == 4


class TestJudge:
    def test_link_outside_campaign_sites_is_removed(self, data_root):
        answer = verdict(link="https://phish.example/login").model_dump()
        mail = Mail(key="k", sender="x", subject="s", sent="d", text="t")
        v = judge(mail, "profile", MockBackend(responses=[json.dumps(answer)]))
        assert v is not None and v.link == ""

    def test_unusable_answer_is_none(self, data_root):
        mail = Mail(key="k", sender="x", subject="s", sent="d", text="t")
        assert judge(mail, "profile", MockBackend(responses=["not json"])) is None


@pytest.fixture
def env(monkeypatch, data_root):
    monkeypatch.setenv("WATCH_EMAIL", "alerts@example.com")
    monkeypatch.setenv("WATCH_EMAIL_APP_PASSWORD", "abcd efgh ijkl mnop")
    monkeypatch.setenv("NTFY_TOPIC", "test-topic")


def fake_mailbox(monkeypatch, mails):
    def fetch(host, user, password, *, folders, days, skip):
        assert password == "abcdefghijklmnop", "app password spaces are removed"
        return [m for m in mails if m.key not in skip]
    monkeypatch.setattr(mailbox, "fetch_recent", fetch)


def capture_pushes(monkeypatch, fail=False):
    sent = []

    def push(server, body, timeout=15.0):
        if fail:
            raise notify.PushError("offline")
        sent.append(body)
    monkeypatch.setattr(notify, "push", push)
    return sent


MAILS = [Mail(key="m1", sender="vyro", subject="New", sent="d", text="FX campaign"),
         Mail(key="m2", sender="vyro", subject="Reminder", sent="d", text="FX campaign")]


class TestRunPass:
    def test_one_campaign_is_pushed_once_across_emails_and_passes(self, env, monkeypatch, tmp_path):
        fake_mailbox(monkeypatch, MAILS)
        sent = capture_pushes(monkeypatch)
        answer = json.dumps(verdict().model_dump())
        state = tmp_path / "state.json"
        backend = MockBackend(responses=[answer, answer])

        first = watcher.run_pass(WatchConfig(), backend, path=state)
        second = watcher.run_pass(WatchConfig(), MockBackend(), path=state)

        assert len(sent) == 1 and sent[0]["topic"] == "test-topic"
        assert len(first.pushed) == 1 and first.skipped[0][1] == "already pushed"
        assert second.read == 0, "handled messages are not read again"

    def test_a_failed_push_is_retried_then_given_up(self, env, monkeypatch, tmp_path):
        fake_mailbox(monkeypatch, MAILS[:1])
        capture_pushes(monkeypatch, fail=True)
        state = tmp_path / "state.json"
        answer = json.dumps(verdict().model_dump())
        for _ in range(watcher.MAX_ATTEMPTS):
            result = watcher.run_pass(WatchConfig(), MockBackend(responses=[answer]), path=state)
            assert result.failed == 1
        assert watcher.run_pass(WatchConfig(), MockBackend(), path=state).read == 0

    def test_dry_run_pushes_and_remembers_nothing(self, env, monkeypatch, tmp_path):
        fake_mailbox(monkeypatch, MAILS[:1])
        sent = capture_pushes(monkeypatch)
        state = tmp_path / "state.json"
        answer = json.dumps(verdict().model_dump())
        result = watcher.run_pass(WatchConfig(), MockBackend(responses=[answer]),
                                  dry_run=True, path=state)
        assert len(result.pushed) == 1 and sent == [] and not state.exists()

    def test_missing_secrets_are_named(self, monkeypatch):
        for k in ("WATCH_EMAIL", "WATCH_EMAIL_APP_PASSWORD", "NTFY_TOPIC"):
            monkeypatch.delenv(k, raising=False)
        with pytest.raises(watcher.WatchConfigError, match="WATCH_EMAIL_APP_PASSWORD"):
            watcher.secrets()
