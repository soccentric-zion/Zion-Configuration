"""Generic WebSocket broadcast hub keyed by channel name.

Background tasks (sampler, alert engine, OTA) publish JSON payloads to named
channels; connected clients subscribed to a channel receive every message.
Dead sockets are pruned on send.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import WebSocket


class Hub:
    def __init__(self) -> None:
        self._channels: dict[str, set[WebSocket]] = {}
        self._lock = asyncio.Lock()

    async def join(self, channel: str, ws: WebSocket) -> None:
        async with self._lock:
            self._channels.setdefault(channel, set()).add(ws)

    async def leave(self, channel: str, ws: WebSocket) -> None:
        async with self._lock:
            conns = self._channels.get(channel)
            if conns:
                conns.discard(ws)

    async def broadcast(self, channel: str, payload: dict[str, Any]) -> None:
        async with self._lock:
            conns = list(self._channels.get(channel, set()))
        dead: list[WebSocket] = []
        for ws in conns:
            try:
                await ws.send_json(payload)
            except Exception:  # client gone / send failed
                dead.append(ws)
        if dead:
            async with self._lock:
                conns_set = self._channels.get(channel)
                if conns_set:
                    for ws in dead:
                        conns_set.discard(ws)

    def count(self, channel: str) -> int:
        return len(self._channels.get(channel, set()))


# Shared singleton hub used across the app.
hub = Hub()
