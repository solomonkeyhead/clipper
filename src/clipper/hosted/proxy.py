"""Pass a signed-in user's request through to their Clipper and its reply back (D228).

Both ways stream: a multi-GB upload is never held in memory, the live event stream (/api/events) arrives
event by event, and a video's Range requests and 206 replies pass through as they are. The browser's Host
and Origin go through unchanged, since Clipper refuses a write whose Origin is not its Host. The gateway's
own session cookie stays here; the gateway signs in to Clipper with that process's bearer token instead.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable

import httpx
from fastapi import Request
from fastapi.responses import Response, StreamingResponse

#: Headers that belong to one connection, not the request (RFC 9110 7.6.1), plus what the gateway replaces.
HOP_BY_HOP = frozenset({"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te",
                        "trailer", "transfer-encoding", "upgrade", "proxy-connection"})
SESSION_COOKIE = "clipper_session"


def client() -> httpx.AsyncClient:
    """No read or write timeout: an event stream stays open for hours, an upload can take as long."""
    return httpx.AsyncClient(timeout=httpx.Timeout(None, connect=5.0),
                             limits=httpx.Limits(max_connections=None, max_keepalive_connections=50))


def _without_session(cookie: str) -> str:
    parts = [p for p in cookie.split(";") if p.strip() and p.split("=", 1)[0].strip() != SESSION_COOKIE]
    return ";".join(parts).strip()


def request_headers(request: Request, token: str) -> list[tuple[str, str]]:
    named = {h.strip().lower() for h in request.headers.get("connection", "").split(",")}
    out = []
    for key, value in request.headers.items():
        k = key.lower()
        if k in HOP_BY_HOP or k in named or k == "authorization":
            continue
        if k == "cookie":
            value = _without_session(value)
            if not value:
                continue
        out.append((key, value))
    out.append(("Authorization", f"Bearer {token}"))
    return out


async def forward(request: Request, http: httpx.AsyncClient, base: str, token: str, *,
                  done: Callable[[], None] = lambda: None) -> Response:
    """Send the request to `base` (http://127.0.0.1:<port>) and stream the reply. `done` runs once the
    reply has finished streaming (or failed), so the process manager knows a long transfer ended.
    Raises httpx.ConnectError when nothing listens there (the caller shows "starting your studio")."""
    has_body = "content-length" in request.headers or "transfer-encoding" in request.headers
    upstream_request = http.build_request(
        request.method, httpx.URL(base + request.url.path, query=request.url.query.encode("latin-1")),
        headers=request_headers(request, token), content=request.stream() if has_body else None)
    try:
        upstream = await http.send(upstream_request, stream=True)
    except BaseException:
        done()
        raise

    async def body() -> AsyncIterator[bytes]:
        try:
            async for chunk in upstream.aiter_raw():
                yield chunk
        finally:
            await upstream.aclose()
            done()

    reply = StreamingResponse(body(), status_code=upstream.status_code)
    reply.raw_headers = [(k, v) for k, v in upstream.headers.raw if k.lower().decode("latin-1") not in HOP_BY_HOP]
    return reply
