"""Phone push through ntfy (https://ntfy.sh): free, no account, no SDK."""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from ..utils.logging import get_logger

log = get_logger(__name__)


class PushError(RuntimeError):
    """The push could not be delivered to the ntfy server."""


def payload(topic: str, *, title: str, message: str, click: str = "",
            priority: int = 3, tags: list[str] | None = None) -> dict:
    """The JSON body ntfy accepts; JSON rather than headers so text can be UTF-8."""
    body = {"topic": topic, "title": title[:250], "message": message[:3500],
            "priority": priority, "tags": tags or []}
    if click:
        body["click"] = click
    return body


def push(server: str, body: dict, *, timeout: float = 15.0) -> None:
    request = urllib.request.Request(
        server.rstrip("/"), data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            if response.status >= 300:
                raise PushError(f"ntfy answered {response.status}")
    except (urllib.error.URLError, OSError) as exc:
        raise PushError(f"cannot reach {server}: {exc}") from exc
