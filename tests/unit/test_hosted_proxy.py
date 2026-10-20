"""The gateway's proxy and process manager (D228): streams both ways, bearer token, one Clipper per user.

A small FastAPI app served by uvicorn in a thread stands in for a user's Clipper, and a few lines of
http.server stand in for `clipper studio` in the process manager: no real Clipper starts here.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import sys
import threading
import time
from pathlib import Path

import httpx
import pytest
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

from clipper.hosted import proxy
from clipper.hosted.procs import Procs, StartFailed, free_port

#: Stands in for `clipper studio --port N`: answers with what it was started with, if the bearer is right.
FAKE_CLIPPER = r'''
import json, os, sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
data = os.environ["CLIPPER_DATA_DIR"]
class H(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.headers.get("Authorization") != "Bearer " + os.environ["CLIPPER_TOKEN"]:
            self.send_response(401); self.end_headers(); return
        job = os.path.join(data, "job")
        body = {"/api/jobs": [{"status": open(job).read() if os.path.exists(job) else "done"}],
                "/api/create": {"videos": []}}.get(self.path.split("?")[0])
        if body is None:
            body = {"path": self.path, "host": self.headers.get("Host"), "data": data,
                    "hosted": os.environ.get("CLIPPER_HOSTED"), "no_update": os.environ.get("CLIPPER_NO_UPDATE"),
                    "smtp": os.environ.get("SMTP_URL"), "paddle": os.environ.get("PADDLE_API_KEY")}
        raw = json.dumps(body).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw))); self.end_headers(); self.wfile.write(raw)
    def log_message(self, *a): pass
ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
'''


def fake_command(tmp: Path):
    script = tmp / "fake_clipper.py"
    script.write_text(FAKE_CLIPPER)
    return lambda port: [sys.executable, str(script), str(port)]


@contextlib.contextmanager
def serve(app):
    port = free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="off"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(200):
        if server.started:
            break
        time.sleep(0.02)
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(5)


# ---------- the proxy

@pytest.fixture
def chain(tmp_path):
    """A browser -> gateway (proxy.forward only) -> upstream, both on real sockets."""
    release = threading.Event()
    video = tmp_path / "v.mp4"
    video.write_bytes(bytes(range(256)) * 40)
    up = FastAPI()

    @up.api_route("/echo", methods=["GET", "POST", "PUT", "DELETE"])
    async def echo(request: Request):
        body = await request.body()
        reply = JSONResponse({"method": request.method, "query": request.url.query, "len": len(body),
                              "sha": hashlib.sha256(body).hexdigest(), "headers": dict(request.headers)},
                             status_code=201, headers={"X-Up": "yes"})
        reply.set_cookie("a", "1")
        reply.set_cookie("b", "2")
        return reply

    @up.get("/api/events")
    async def events():
        async def stream():
            yield "data: one\n\n"
            await asyncio.to_thread(release.wait, 10)
            yield "data: two\n\n"
        return StreamingResponse(stream(), media_type="text/event-stream")

    @up.api_route("/video", methods=["GET", "HEAD"])
    def media():
        return FileResponse(video, media_type="video/mp4")

    with serve(up) as upstream:
        gw = FastAPI()
        holder = {}

        @gw.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "HEAD"])
        async def everything(request: Request, path: str):
            http = holder.setdefault("http", proxy.client())
            return await proxy.forward(request, http, upstream, "process-token")

        with serve(gw) as gateway:
            yield gateway, release, video


def test_proxy_passes_method_body_status_and_headers_and_adds_the_token(chain):
    gateway, _, _ = chain
    blob = b"x" * (3 * 1024 * 1024 + 7)

    def chunks():
        for i in range(0, len(blob), 65536):
            yield blob[i:i + 65536]

    host = gateway.split("//")[1]
    r = httpx.post(f"{gateway}/echo?a=1&b=two", content=chunks(),
                   headers={"Origin": f"http://{host}", "Authorization": "Bearer from-browser",
                            "Cookie": "clipper_session=secret; theme=dark", "Content-Length": str(len(blob))})
    assert r.status_code == 201
    got = r.json()
    assert got["method"] == "POST" and got["query"] == "a=1&b=two"
    assert got["len"] == len(blob) and got["sha"] == hashlib.sha256(blob).hexdigest()
    h = got["headers"]
    assert h["authorization"] == "Bearer process-token"
    assert h["host"] == host and h["origin"] == f"http://{host}"   # Clipper's same-origin check compares these
    assert "clipper_session" not in h["cookie"] and "theme=dark" in h["cookie"]
    assert r.headers["x-up"] == "yes"
    assert r.headers.get_list("set-cookie") == ["a=1; Path=/; SameSite=lax", "b=2; Path=/; SameSite=lax"]
    assert httpx.delete(f"{gateway}/echo").json()["method"] == "DELETE"


def test_proxy_drops_a_session_only_cookie_header(chain):
    gateway, _, _ = chain
    h = httpx.get(f"{gateway}/echo", headers={"Cookie": "clipper_session=secret"}).json()["headers"]
    assert "cookie" not in h


def test_proxy_streams_events_one_by_one(chain):
    gateway, release, _ = chain
    with httpx.stream("GET", f"{gateway}/api/events", timeout=10) as r:
        lines = r.iter_lines()
        assert next(lines) == "data: one"   # arrives while the upstream still holds the second back
        release.set()
        rest = [line for line in lines if line]
    assert rest == ["data: two"]


def test_proxy_passes_range_requests(chain):
    gateway, _, video = chain
    r = httpx.get(f"{gateway}/video", headers={"Range": "bytes=10-19"})
    assert r.status_code == 206
    assert r.content == video.read_bytes()[10:20]
    assert r.headers["content-range"] == f"bytes 10-19/{video.stat().st_size}"
    assert httpx.head(f"{gateway}/video").headers["content-length"] == str(video.stat().st_size)


# ---------- the process manager

@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setenv("CLIPPER_HOSTED_ROOT", str(tmp_path / "root"))
    return tmp_path / "root"


def test_procs_start_with_their_own_folder_and_token_and_restart_when_dead(tmp_path, root, monkeypatch):
    monkeypatch.setenv("SMTP_URL", "smtp://secret@mail")
    monkeypatch.setenv("PADDLE_API_KEY", "secret")
    prepared = []
    procs = Procs(fake_command(tmp_path), prepare=prepared.append, ready_timeout=15)

    async def run():
        p = await procs.get(7)
        try:
            assert prepared == [7] and procs.state(7) == "running"
            async with httpx.AsyncClient(headers=p.headers()) as c:
                got = (await c.get(f"{p.base}/anything")).json()
                assert (await httpx.AsyncClient().get(f"{p.base}/anything")).status_code == 401
            assert Path(got["data"]) == root / "users" / "7" / "data"
            assert got["hosted"] == "1" and got["no_update"] == "1"
            assert got["smtp"] is None and got["paddle"] is None   # gateway secrets stay in the gateway
            assert (await procs.get(7)).popen.pid == p.popen.pid
            p.popen.kill()
            p.popen.wait()
            again = await procs.get(7)
            assert again.popen.pid != p.popen.pid and again.alive()
        finally:
            procs.stop_all()
        assert not procs.procs and not again.alive()

    asyncio.run(run())


def test_procs_stop_when_idle_but_not_while_a_job_runs(tmp_path, root):
    procs = Procs(fake_command(tmp_path), idle=0, ready_timeout=15)

    async def run():
        p = await procs.get(1)
        try:
            (root / "users" / "1" / "data" / "job").write_text("running")
            await procs.reap(now=time.time() + 5)
            assert procs.state(1) == "running"
            (root / "users" / "1" / "data" / "job").write_text("done")
            p.inflight = 1   # a long upload still going
            await procs.reap(now=time.time() + 5)
            assert procs.state(1) == "running"
            p.inflight = 0
            await procs.reap(now=time.time() + 5)
            assert procs.state(1) == "stopped" and not p.alive()
        finally:
            procs.stop_all()

    asyncio.run(run())


def test_procs_report_a_start_that_fails(tmp_path, root):
    procs = Procs(lambda port: [sys.executable, "-c", "raise SystemExit(3)"], ready_timeout=10)
    with pytest.raises(StartFailed):
        asyncio.run(procs.get(2))
    assert procs.state(2) == "stopped"
