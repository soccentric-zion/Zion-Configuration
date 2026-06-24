"""Retention job: trim metric history and telemetry queue past the window.

audit.md §6: "disk usage + retention/vacuum, persistent vs volatile
state." The retention job prunes ``metric_history`` and
``telemetry_queue`` past the configured window. Operators can
override the static config via
``PUT /api/files/logs/retention`` (#125) — the override is
persisted in ``system_settings_kv`` and consulted here before
falling back to the static ``SamplingConfig``.
"""

from __future__ import annotations

import asyncio
import contextlib

from app.config import get_settings
from app.db.database import get_db

# KV keys for the operator-controllable overrides (per #42, #125).
# Live values win over the static config so an audit row matches
# what the operator sees in the dashboard.
KV_HISTORY_RETENTION_DAYS = "retention.history_retention_days"
KV_VACUUM_INTERVAL_HOURS = "retention.vacuum_interval_hours"

# Defaults match the static config floor / ceiling. The static
# config still provides a sane fallback for a fresh device.
_DEFAULT_HISTORY_DAYS = 30
_DEFAULT_VACUUM_HOURS = 24


async def _kv_get_int(key: str, default: int) -> int:
    """Read an int override from ``system_settings_kv``; fall back
    to ``default`` on miss or parse error."""
    row = await get_db().fetch_one("SELECT value FROM system_settings_kv WHERE key = ?", (key,))
    if row is None:
        return default
    raw = row["value"]
    try:
        return int(raw) if raw is not None else default
    except (TypeError, ValueError):
        return default


async def effective_history_days() -> int:
    """Operator override (if any) wins over the static config.

    Both are clamped to [1, 3650] so an out-of-band typo (e.g. a
    ``DELETE`` that leaves junk behind) cannot blow up the
    retention job.
    """
    override = await _kv_get_int(KV_HISTORY_RETENTION_DAYS, _DEFAULT_HISTORY_DAYS)
    if override < 1:
        return 1
    if override > 3650:
        return 3650
    static = get_settings().sampling.history_retention_days
    return override if override != _DEFAULT_HISTORY_DAYS else static


async def effective_vacuum_hours() -> int:
    """Operator override (if any) wins over the static config."""
    override = await _kv_get_int(KV_VACUUM_INTERVAL_HOURS, _DEFAULT_VACUUM_HOURS)
    if override < 1:
        return 1
    if override > 168:
        return 168
    return override


async def vacuum_now() -> dict[str, int]:
    """Run the retention prune + SQLite ``VACUUM`` immediately (#125).

    Returns the number of rows removed from each table so the SPA
    can render a "freed N rows" toast. ``VACUUM`` is heavy on a
    large DB — the operator's ``require_confirm`` gate is the
    signal that this is acceptable.

    ``Database.execute`` returns ``lastrowid`` (master CLAUDE.md
    Don't: don't branch on the return value to detect "did this
    match?"). For DELETE we want the rowcount, so we go through
    ``conn.execute`` directly and read ``cur.rowcount``.
    """
    db = get_db()
    days = await effective_history_days()
    cutoff = f"-{days} days"
    cur_mh = await db.conn.execute("DELETE FROM metric_history WHERE ts < datetime('now', ?)", (cutoff,))
    mh_count = cur_mh.rowcount or 0
    cur_tq = await db.conn.execute("DELETE FROM telemetry_queue WHERE sent = 1 AND ts < datetime('now', ?)", (cutoff,))
    tq_count = cur_tq.rowcount or 0
    await db.conn.commit()
    # VACUUM reclaims space inside the SQLite file (the DELETE
    # above only marks pages free, it does not shrink the file).
    await db.conn.execute("VACUUM")
    await db.conn.commit()
    return {
        "metric_history": int(mh_count),
        "telemetry_queue": int(tq_count),
        "retention_days": days,
    }


class Retention:
    def __init__(self) -> None:
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()

    def start(self) -> None:
        self._stop.clear()
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            if self._task:
                await self._task

    async def _run(self) -> None:
        while not self._stop.is_set():
            with contextlib.suppress(Exception):
                await self.enforce()
            # The vacuum interval is operator-overridable (#125).
            interval_s = float(await effective_vacuum_hours()) * 3600.0
            await asyncio.sleep(interval_s)

    async def enforce(self) -> None:
        """Prune ``metric_history`` and ``telemetry_queue`` past the
        current retention window. The window is the operator-set
        override (if any), else the static config — see
        ``effective_history_days()``.
        """
        days = await effective_history_days()
        cutoff = f"-{days} days"
        db = get_db()
        await db.execute("DELETE FROM metric_history WHERE ts < datetime('now', ?)", (cutoff,))
        await db.execute("DELETE FROM telemetry_queue WHERE sent = 1 AND ts < datetime('now', ?)", (cutoff,))


retention = Retention()
