"""Alert engine.

Pure evaluation helpers (unit-tested with a mocked clock) plus a background
loop that evaluates enabled rules against recent ``metric_history`` samples,
opens/resolves alerts, and broadcasts firing alerts on the ``alerts`` channel.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from dataclasses import dataclass

from app.db.database import get_db
from app.ws.manager import hub


@dataclass
class Sample:
    ts: float
    value: float


@dataclass
class Rule:
    id: int
    name: str
    metric: str
    condition: str  # '>', '<', '==', 'rate'
    threshold: float
    duration_s: int
    severity: str


def condition_met(condition: str, value: float, threshold: float) -> bool:
    if condition == ">":
        return value > threshold
    if condition == "<":
        return value < threshold
    if condition == "==":
        return abs(value - threshold) < 1e-9
    return False


def rate_of_change(samples: list[Sample]) -> float:
    """Per-second slope between the first and last sample."""
    if len(samples) < 2:
        return 0.0
    first, last = samples[0], samples[-1]
    dt = last.ts - first.ts
    if dt <= 0:
        return 0.0
    return (last.value - first.value) / dt


def evaluate(rule: Rule, samples: list[Sample], now: float) -> bool:
    """Return True if the rule should be *firing* given the samples and clock."""
    if not samples:
        return False
    if rule.condition == "rate":
        return abs(rate_of_change(samples)) > rule.threshold

    # For threshold conditions with a duration, the condition must hold for the
    # whole window [now-duration, now].
    if rule.duration_s <= 0:
        return condition_met(rule.condition, samples[-1].value, rule.threshold)

    window_start = now - rule.duration_s
    in_window = [s for s in samples if s.ts >= window_start]
    if not in_window:
        return False
    return all(condition_met(rule.condition, s.value, rule.threshold) for s in in_window)


def should_escalate(fired_at: float, acked: bool, now: float, escalate_after_s: float) -> bool:
    """Escalation timing helper (mock-clock testable)."""
    return (not acked) and (now - fired_at) >= escalate_after_s


class AlertEngine:
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
                await self._task

    async def _run(self) -> None:
        while not self._stop.is_set():
            with contextlib.suppress(Exception):
                await self.evaluate_once()
            await asyncio.sleep(5.0)

    async def evaluate_once(self) -> None:
        db = get_db()
        rules = await db.fetch_all("SELECT * FROM alert_rules WHERE enabled = 1")
        now = time.time()
        for r in rules:
            rule = Rule(
                id=r["id"],
                name=r["name"],
                metric=r["metric"],
                condition=r["condition"],
                threshold=r["threshold"],
                duration_s=r["duration_s"],
                severity=r["severity"],
            )
            samples = await self._recent_samples(rule.metric, max(rule.duration_s, 30))
            firing = evaluate(rule, samples, now)
            open_alert = await db.fetch_one("SELECT * FROM alerts WHERE rule_id = ? AND state = 'firing'", (rule.id,))
            if firing and not open_alert:
                msg = f"{rule.name}: {rule.metric} {rule.condition} {rule.threshold}"
                await db.execute(
                    """INSERT INTO alerts (rule_id, metric, severity, state, message)
                       VALUES (?, ?, ?, 'firing', ?)""",
                    (rule.id, rule.metric, rule.severity, msg),
                )
                await hub.broadcast("alerts", {"type": "firing", "rule": rule.name, "message": msg})
            elif not firing and open_alert:
                await db.execute(
                    "UPDATE alerts SET state='resolved', resolved_at=datetime('now') WHERE id=?",
                    (open_alert["id"],),
                )
                await hub.broadcast("alerts", {"type": "resolved", "rule": rule.name})

    async def _recent_samples(self, metric: str, window_s: int) -> list[Sample]:
        # Compute the LIMIT from the configured sample interval so a rule
        # with duration_s=3600 (1h) gets enough samples to actually evaluate
        # the window — the old hardcoded LIMIT 60 capped us at 10 minutes of
        # data at the default 10s sample interval, so duration-based rules
        # longer than 10 min were silently broken.
        from app.config import get_settings

        interval = max(get_settings().sampling.history_interval_seconds, 1.0)
        limit = max(int(window_s / interval) + 4, 60)
        rows = await get_db().fetch_all(
            """SELECT strftime('%s', ts) AS epoch, value FROM metric_history
               WHERE metric = ? ORDER BY ts DESC LIMIT ?""",
            (metric, limit),
        )
        return [Sample(ts=float(r["epoch"]), value=float(r["value"])) for r in reversed(rows)]


engine = AlertEngine()
