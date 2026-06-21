"""
Fan-out WebSocket messages to all connected ``/ws/recording`` clients.

Concurrency:
- ``add`` / ``remove`` / ``broadcast`` are async and must run on the FastAPI event loop.
- ``schedule_broadcast`` is safe from worker threads (uses ``run_coroutine_threadsafe``).

Security: callers must supply JSON-serializable payloads; do not forward untrusted binary.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Optional, Set

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class RecordingHub:
    def __init__(self) -> None:
        self._clients: Set[WebSocket] = set()
        self._lock = asyncio.Lock()
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def attach_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    async def add(self, ws: WebSocket) -> None:
        async with self._lock:
            self._clients.add(ws)

    async def remove(self, ws: WebSocket) -> None:
        async with self._lock:
            self._clients.discard(ws)

    async def broadcast(self, payload: Dict[str, Any]) -> None:
        async with self._lock:
            targets = list(self._clients)
        dead: list[WebSocket] = []
        for ws in targets:
            try:
                await ws.send_json(payload)
            except Exception as e:
                logger.debug("recording ws send failed: %s", e)
                dead.append(ws)
        if dead:
            async with self._lock:
                for ws in dead:
                    self._clients.discard(ws)

    def schedule_broadcast(self, payload: Dict[str, Any]) -> None:
        """Fire-and-forget from a non-async thread."""
        loop = self._loop
        if loop is None or not loop.is_running():
            return

        async def _run() -> None:
            await self.broadcast(payload)

        try:
            asyncio.run_coroutine_threadsafe(_run(), loop)
        except RuntimeError:
            pass


_hub: Optional[RecordingHub] = None


def get_recording_hub() -> RecordingHub:
    global _hub
    if _hub is None:
        _hub = RecordingHub()
    return _hub
