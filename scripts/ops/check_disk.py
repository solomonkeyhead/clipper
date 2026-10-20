"""Exit 1 (and email, if SMTP_URL and --to are set) when the data volume is under 15% free (D-NEW).

    python scripts/ops/check_disk.py --path /srv/clipper --to you@example.com
Stdlib only.
"""

from __future__ import annotations

import argparse
import os
import shutil
import smtplib
import sys
from email.message import EmailMessage
from urllib.parse import unquote, urlparse

MIN_FREE = 0.15


def free_fraction(path: str) -> float:
    u = shutil.disk_usage(path)
    return u.free / u.total


def send(url: str, to: str, text: str) -> None:
    u = urlparse(url)
    msg = EmailMessage()
    msg["Subject"] = "Clipper: disk space low"
    msg["From"] = unquote(u.username or "clipper@localhost")
    msg["To"] = to
    msg.set_content(text)
    cls = smtplib.SMTP_SSL if u.scheme == "smtps" else smtplib.SMTP
    with cls(u.hostname, u.port or (465 if u.scheme == "smtps" else 587), timeout=30) as s:
        if u.scheme == "smtp":
            s.starttls()
        if u.username:
            s.login(unquote(u.username), unquote(u.password or ""))
        s.send_message(msg)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--path", default=os.environ.get("CLIPPER_HOSTED_ROOT", "/srv/clipper"))
    ap.add_argument("--min-free", type=float, default=MIN_FREE, help="fraction, default 0.15")
    ap.add_argument("--to", help="email address to warn")
    a = ap.parse_args(argv)
    frac = free_fraction(a.path)
    if frac >= a.min_free:
        print(f"ok: {frac:.0%} free on {a.path}")
        return 0
    text = f"Only {frac:.0%} free on {a.path} (limit {a.min_free:.0%})."
    print(text, file=sys.stderr)
    url = os.environ.get("SMTP_URL", "")
    if a.to and url:
        try:
            send(url, a.to, text)
        except Exception as exc:
            print(f"email failed: {exc}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
