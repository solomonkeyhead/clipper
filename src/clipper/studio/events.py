"""Live updates for open Control Center pages (Server-Sent Events).

One queue per connected page: a shared queue would hand each event to only one
of them. `publish` is safe to call from worker threads (the sync runs in one).
"""

from __future__ import annotations

import asyncio
import itertools
import json


class Broker:
    def __init__(self) -> None:
        self._clients: set[asyncio.Queue] = set()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._ids = itertools.count(1)

    def bind(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=100)
        self._clients.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._clients.discard(queue)

    def publish(self, event: str, data: dict | None = None) -> None:
        message = (next(self._ids), event, json.dumps(data or {}))
        if self._loop is None:
            return

        def deliver() -> None:
            for queue in list(self._clients):
                if queue.full():  # a stalled page: drop its oldest update
                    queue.get_nowait()
                queue.put_nowait(message)

        try:
            if asyncio.get_running_loop() is self._loop:
                deliver()
                return
        except RuntimeError:
            pass
        self._loop.call_soon_threadsafe(deliver)

    @property
    def clients(self) -> int:
        return len(self._clients)
