"""One Clipper process per signed-in user (D228): started on their first request, stopped when idle.

Each runs `clipper studio` on a free port of 127.0.0.1 with its own data folder and a random token that
only the gateway knows (the proxy sends it as a bearer token). A process is stopped after IDLE_SECONDS
without a request, unless it is clipping or building a Short, or a request (a long upload) is still going.
A process that died is started again on the next request.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import secrets
import signal
import socket
import subprocess
import sys
import time
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass

import httpx

from ..utils.logging import get_logger
from . import data_dir, user_dir

log = get_logger(__name__)

IDLE_SECONDS = 20 * 60
READY_SECONDS = 30.0
MAX_TRANSFER = 6 * 3600
#: Gateway secrets a user's Clipper never needs (billing, mail, who the admins are).
GATEWAY_ONLY = ("SMTP_URL", "CLIPPER_ADMINS", "CLIPPER_HOSTED_ROOT")


class StartFailed(RuntimeError):
    pass


def clipper_command(port: int) -> list[str]:
    return [sys.executable, "-m", "clipper.cli", "studio", "--no-browser", "--port", str(port)]


def free_port() -> int:
    # ponytail: the port could be taken between here and the child binding it; the start then fails and
    # the next request tries another.
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@dataclass
class Proc:
    user_id: int
    port: int
    token: str
    popen: subprocess.Popen
    last_used: float
    ready: bool = False
    inflight: int = 0   # proxied requests still streaming (uploads, downloads)

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def alive(self) -> bool:
        return self.popen.poll() is None

    def headers(self) -> dict:
        return {"Authorization": f"Bearer {self.token}"}


def _kill(popen: subprocess.Popen) -> None:
    """The process and what it started (ffmpeg, a browser filming a drawing)."""
    if popen.poll() is None:
        if os.name == "nt":
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(popen.pid)], capture_output=True, check=False)
        else:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(popen.pid, signal.SIGTERM)
            try:
                popen.wait(10)
            except subprocess.TimeoutExpired:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(popen.pid, signal.SIGKILL)
    with contextlib.suppress(subprocess.TimeoutExpired):
        popen.wait(5)


def user_env(user_id: int) -> dict[str, str]:
    """What a user's Clipper runs with: the gateway's environment less its own secrets, hosted, on their folder."""
    env = {k: v for k, v in os.environ.items() if k not in GATEWAY_ONLY and not k.startswith("PADDLE_")}
    return env | {"CLIPPER_HOSTED": "1", "CLIPPER_HOST": "127.0.0.1", "CLIPPER_DATA_DIR": str(data_dir(user_id)),
                  "CLIPPER_NO_UPDATE": "1", "PYTHONUNBUFFERED": "1"}


#: What a stopped user's Clipper would do each day if it ran (D237): delete stored platform data not refreshed
#: for 30 days or left by a disconnected account (D231; a running one does it in its syncs), and what is older
#: than the plan keeps (clipper/meter.py retention; a running one does it in its own daily task).
DAILY = (["-m", "clipper.studio.platform_data"], ["-m", "clipper.meter"])
DAILY_SECONDS = 600


class Procs:
    def __init__(self, command: Callable[[int], list[str]] = clipper_command, *,
                 prepare: Callable[[int], None] | None = None, idle: float = IDLE_SECONDS,
                 ready_timeout: float = READY_SECONDS) -> None:
        """`prepare(user_id)` runs before each start (catalog.refresh: the period and plan.json)."""
        self.command, self.prepare, self.idle, self.ready_timeout = command, prepare, idle, ready_timeout
        self.procs: dict[int, Proc] = {}
        self._locks: defaultdict[int, asyncio.Lock] = defaultdict(asyncio.Lock)

    def running(self, user_id: int) -> Proc | None:
        p = self.procs.get(user_id)
        return p if p and p.ready and p.alive() else None

    def state(self, user_id: int) -> str:
        p = self.procs.get(user_id)
        if p is None:
            return "stopped"
        if not p.alive():
            return "died"
        return "running" if p.ready else "starting"

    async def get(self, user_id: int) -> Proc:
        """The user's Clipper, started (and waited for) when it isn't running."""
        p = self.running(user_id)
        if p is None:
            async with self._locks[user_id]:
                p = self.running(user_id)
                if p is None:
                    p = await self._start(user_id)
        p.last_used = time.time()
        return p

    async def _start(self, user_id: int) -> Proc:
        old = self.procs.pop(user_id, None)
        if old is not None:
            log.warning("user %d: Clipper had stopped (exit %s), starting it again", user_id, old.popen.poll())
            await asyncio.to_thread(_kill, old.popen)
        if self.prepare:
            self.prepare(user_id)
        data = data_dir(user_id)
        data.mkdir(parents=True, exist_ok=True)
        port, token = free_port(), secrets.token_urlsafe(32)
        env = user_env(user_id) | {"CLIPPER_TOKEN": token}
        with (user_dir(user_id) / "clipper.log").open("ab") as out:
            popen = subprocess.Popen(self.command(port), env=env, stdout=out, stderr=subprocess.STDOUT,
                                     stdin=subprocess.DEVNULL, start_new_session=os.name != "nt")
        p = Proc(user_id, port, token, popen, time.time())
        self.procs[user_id] = p
        try:
            await self._wait_ready(p)
        except StartFailed:
            self.procs.pop(user_id, None)
            await asyncio.to_thread(_kill, popen)
            raise
        log.info("user %d: Clipper started on port %d", user_id, port)
        return p

    async def _wait_ready(self, p: Proc) -> None:
        deadline = time.monotonic() + self.ready_timeout
        async with httpx.AsyncClient(timeout=3) as client:
            while time.monotonic() < deadline:
                if not p.alive():
                    raise StartFailed(f"Clipper exited with {p.popen.poll()}")
                try:
                    await client.get(f"{p.base}/api/status", headers=p.headers())
                    p.ready = True
                    return
                except httpx.HTTPError:
                    await asyncio.sleep(0.25)
        raise StartFailed("Clipper did not answer in time")

    async def busy(self, p: Proc) -> bool:
        """Clipping (a job queued or running) or building a Short: stopping now would cut it off."""
        # A reply the browser dropped before its first byte never says it ended, so a count left up for
        # MAX_TRANSFER no longer holds the process.
        if p.inflight > 0 and time.time() - p.last_used < MAX_TRANSFER:
            return True
        try:
            async with httpx.AsyncClient(timeout=10, headers=p.headers()) as client:
                jobs = (await client.get(f"{p.base}/api/jobs")).json()
                if any(j.get("status") in ("queued", "running") for j in jobs):
                    return True
                videos = (await client.get(f"{p.base}/api/create")).json().get("videos") or []
                return any(v.get("status") in ("voiced", "building") for v in videos)
        except (httpx.HTTPError, ValueError, AttributeError):
            return False   # not answering: nothing to protect

    async def reap(self, now: float | None = None) -> None:
        """Stop what has been idle long enough, and forget what died (it starts again when asked for)."""
        now = now or time.time()
        for user_id, p in list(self.procs.items()):
            if not p.alive():
                self.procs.pop(user_id, None)
            elif p.ready and now - p.last_used > self.idle and not await self.busy(p):
                log.info("user %d: idle, stopping Clipper", user_id)
                await self.stop(user_id)

    async def daily(self, user_ids: list[int]) -> int:
        """DAILY for each user whose Clipper isn't running, one after another; returns how many. The user's start
        lock is held meanwhile, so their studio can't start and write the same files half way through."""
        done = 0
        for user_id in user_ids:
            if not data_dir(user_id).is_dir():
                continue
            async with self._locks[user_id]:
                p = self.procs.get(user_id)
                if p is not None and p.alive():
                    continue
                if self.prepare:   # retention reads plan.json: the current plan, not last month's
                    self.prepare(user_id)
                for args in DAILY:
                    try:
                        result = await asyncio.to_thread(
                            subprocess.run, [sys.executable, *args], env=user_env(user_id), capture_output=True,
                            stdin=subprocess.DEVNULL, timeout=DAILY_SECONDS, check=False)
                        if result.returncode:
                            log.warning("user %d: %s failed (exit %d)", user_id, args[-1], result.returncode)
                    except (OSError, subprocess.SubprocessError) as exc:
                        log.warning("user %d: %s didn't run (%s)", user_id, args[-1], type(exc).__name__)
            done += 1
        return done

    async def stop(self, user_id: int) -> None:
        p = self.procs.pop(user_id, None)
        if p is not None:
            await asyncio.to_thread(_kill, p.popen)

    def stop_all(self) -> None:
        for user_id in list(self.procs):
            _kill(self.procs.pop(user_id).popen)
