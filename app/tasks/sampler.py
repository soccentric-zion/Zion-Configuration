"""Periodic metric sampler.

Two cadences:
* fast (config ``metrics_interval_seconds``): broadcast live system health on
  the ``metrics`` WebSocket channel.
* slow (config ``history_interval_seconds``): persist a handful of key metrics
  to ``metric_history`` (batched insert) for sparklines/heatmaps and alerts,
  and to ``telemetry_queue`` so the cloud-telemetry queue depth reflects
  real state (#44).
"""

from __future__ import annotations

import asyncio
import contextlib
import time

from app.config import get_settings
from app.db.database import get_db
from app.hal.registry import get_hal
from app.ws.manager import hub


class Sampler:
    def __init__(self) -> None:
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()

    def start(self) -> None:
        self._stop.clear()
        # Prime psutil.cpu_percent so the first health() broadcast after
        # process start isn't 0.0 for every core (#46). psutil uses the
        # first call as the baseline and reports usage as the *delta*
        # since that call — a second call ~1s later returns real values.
        try:
            import psutil

            psutil.cpu_percent(percpu=True)
        except Exception:  # pragma: no cover  - psutil is always present
            pass
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    async def _run(self) -> None:
        cfg = get_settings().sampling
        last_history = 0.0
        while not self._stop.is_set():
            health = get_hal("system").health()
            await hub.broadcast("metrics", {"type": "health", "data": health, "ts": time.time()})

            now = time.time()
            if now - last_history >= cfg.history_interval_seconds:
                last_history = now
                await self._persist(health)

            await asyncio.sleep(cfg.metrics_interval_seconds)

    async def _persist(self, health: dict[str, object]) -> None:
        thermal = get_hal("thermal").throttling()
        rows = [
            ("cpu_total", float(health.get("cpu_total", 0.0))),  # type: ignore[arg-type]
            ("mem_percent", float(health.get("mem_percent", 0.0))),  # type: ignore[arg-type]
            ("cpu_temp_c", float(health.get("cpu_temp_c", 0.0))),  # type: ignore[arg-type]
            ("cpu_freq_mhz", float(thermal.get("cpu_freq_mhz", 0.0))),
        ]
        db = get_db()
        await db.conn.executemany("INSERT INTO metric_history (metric, value) VALUES (?, ?)", rows)
        # Same rows also queue for cloud telemetry. Each is unsent (sent=0)
        # until the flush endpoint / task ships it. The cloud telemetry
        # queue depth is then real, not a permanent zero (#44).
        await db.conn.executemany(
            "INSERT INTO telemetry_queue (metric, value) VALUES (?, ?)",
            rows,
        )
        await db.conn.commit()


sampler = Sampler()
