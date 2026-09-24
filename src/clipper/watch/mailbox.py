"""Read recent messages from the watcher's own mailbox, without changing it.

Folders are opened read-only and bodies fetched with BODY.PEEK, so nothing is
marked read, moved or deleted: the mailbox looks the same to the user after a
run as before it.
"""

from __future__ import annotations

import contextlib
import email
import email.policy
import hashlib
import imaplib
import re
from dataclasses import dataclass
from datetime import date, timedelta
from email.message import EmailMessage
from html.parser import HTMLParser
from typing import ClassVar

from ..utils.logging import get_logger

log = get_logger(__name__)

# Longer bodies are cut: campaign emails put what matters near the top, and
# the rest (footers, legal text) only costs tokens.
MAX_BODY_CHARS = 6000


@dataclass(frozen=True)
class Mail:
    """One message, reduced to what the judge needs."""

    key: str  # Message-ID, or a hash when a message has none
    sender: str
    subject: str
    sent: str
    text: str


class MailboxError(RuntimeError):
    """The mailbox could not be reached or logged into."""


def fetch_recent(host: str, user: str, password: str, *, folders: list[str],
                 days: int, skip: set[str]) -> list[Mail]:
    """Messages from the last `days` days in `folders`, minus keys in `skip`."""
    since = (date.today() - timedelta(days=days)).strftime("%d-%b-%Y")
    try:
        imap = imaplib.IMAP4_SSL(host)
    except OSError as exc:
        raise MailboxError(f"cannot reach {host}: {exc}") from exc
    try:
        try:
            imap.login(user, password)
        except imaplib.IMAP4.error as exc:
            raise MailboxError(
                f"login to {host} as {user} failed ({exc}). For Gmail, use an app "
                "password (myaccount.google.com/apppasswords), not the account password"
            ) from exc
        found: list[Mail] = []
        for folder in folders:
            status, _ = imap.select(_quote(folder), readonly=True)
            if status != "OK":
                log.debug("skipping folder %s (cannot open it)", folder)
                continue
            status, data = imap.search(None, "SINCE", since)
            if status != "OK" or not data or not data[0]:
                continue
            for num in data[0].split():
                status, parts = imap.fetch(num, "(BODY.PEEK[])")
                if status != "OK":
                    continue
                raw = next((p[1] for p in parts if isinstance(p, tuple)), None)
                if raw is None:
                    continue
                mail = parse(raw)
                if mail.key not in skip:
                    found.append(mail)
        return found
    finally:
        with contextlib.suppress(imaplib.IMAP4.error, OSError):
            imap.logout()


def _quote(folder: str) -> str:
    return f'"{folder}"' if " " in folder or "/" in folder else folder


def parse(raw: bytes) -> Mail:
    """Reduce a raw RFC 822 message to a `Mail`."""
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    assert isinstance(msg, EmailMessage)
    sender = str(msg.get("From", "")).strip()
    subject = str(msg.get("Subject", "")).strip()
    sent = str(msg.get("Date", "")).strip()
    key = str(msg.get("Message-ID", "")).strip() or hashlib.sha256(
        f"{sender}|{subject}|{sent}".encode()).hexdigest()
    return Mail(key=key, sender=sender, subject=subject, sent=sent, text=body_text(msg))


def body_text(msg: EmailMessage) -> str:
    """Plain text of a message, from its HTML part when it has one; links are kept."""
    plain = msg.get_body(preferencelist=("plain",))
    html = msg.get_body(preferencelist=("html",))
    text = ""
    if html is not None:
        # The HTML part carries the links; plain parts often drop them.
        text = html_to_text(html.get_content())
    if plain is not None and len(text) < 40:
        text = plain.get_content()
    text = re.sub(r"[ \t\u00a0]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text).strip()
    return text[:MAX_BODY_CHARS]


def html_to_text(html: str) -> str:
    parser = _Text()
    parser.feed(html)
    parser.close()
    return "".join(parser.out)


class _Text(HTMLParser):
    """HTML to text, writing each link's target after its label."""

    BLOCK: ClassVar[set[str]] = {"p", "div", "br", "tr", "li", "h1", "h2", "h3", "h4",
                                 "table", "section"}
    SKIP: ClassVar[set[str]] = {"style", "script", "head", "title"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.href: str | None = None
        self.skipping = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skipping += 1
        elif tag in self.BLOCK:
            self.out.append("\n")
        elif tag == "a":
            self.href = dict(attrs).get("href")

    def handle_endtag(self, tag):
        if tag in self.SKIP:
            self.skipping = max(0, self.skipping - 1)
        elif tag == "a" and self.href:
            if self.href.startswith("http"):
                self.out.append(f" ({self.href})")
            self.href = None
        elif tag in self.BLOCK:
            self.out.append("\n")

    def handle_data(self, data):
        if not self.skipping:
            self.out.append(data)
