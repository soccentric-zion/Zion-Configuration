"""Telemetry: metric selection, store-and-forward, retention."""

from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import Field

from app.auth.deps import CurrentUser, require_admin, require_viewer
from app.db import audit
from app.db.database import get_db
from app.util.models import _StrictModel

router = APIRouter(prefix="/api/telemetry", tags=["telemetry"])


# Catalog of available metrics. The SPA renders the operator's per-metric
# enable/interval choice; the Sampler consults this to decide what to push.
_METRIC_CATALOG: tuple[dict[str, Any], ...] = (
    {"name": "cpu_total", "unit": "percent", "default_interval_s": 1.0},
    {"name": "cpu_per_core", "unit": "percent", "default_interval_s": 5.0},
    {"name": "ram", "unit": "percent", "default_interval_s": 1.0},
    {"name": "swap", "unit": "percent", "default_interval_s": 30.0},
    {"name": "load1", "unit": "float", "default_interval_s": 5.0},
    {"name": "temperature_cpu", "unit": "celsius", "default_interval_s": 5.0},
    {"name": "disk_usage", "unit": "percent", "default_interval_s": 60.0},
    {"name": "net_rx", "unit": "bytes/s", "default_interval_s": 1.0},
    {"name": "net_tx", "unit": "bytes/s", "default_interval_s": 1.0},
    {"name": "sensors_temp", "unit": "celsius", "default_interval_s": 10.0},
    {"name": "throttling", "unit": "bool", "default_interval_s": 30.0},
    {"name": "coprocessor_rtt", "unit": "ms", "default_interval_s": 60.0},
    {"name": "alert_count", "unit": "count", "default_interval_s": 60.0},
)


@router.get("/catalog")
async def catalog(_: CurrentUser = Depends(require_viewer)) -> dict[str, Any]:
    return {"metrics": list(_METRIC_CATALOG)}


@router.get("/selection")
async def get_selection(_: CurrentUser = Depends(require_viewer)) -> dict[str, Any]:
    """Per-metric operator overrides + defaults from the catalog."""
    rows = await get_db().fetch_all(
        "SELECT metric, enabled, interval_s, payload_format, updated_at " "FROM telemetry_metrics"
    )
    overrides = {r["metric"]: dict(r) for r in rows}
    merged: list[dict[str, Any]] = []
    for m in _METRIC_CATALOG:
        o = overrides.get(m["name"])
        merged.append(
            {
                "name": m["name"],
                "unit": m["unit"],
                "default_interval_s": m["default_interval_s"],
                "enabled": o["enabled"] if o else True,
                "interval_s": o["interval_s"] if o else m["default_interval_s"],
                "payload_format": o["payload_format"] if o else "json",
            }
        )
    return {"metrics": merged}


class _MetricConfig(_StrictModel):
    name: str = Field(min_length=1, max_length=64)
    enabled: bool = True
    interval_s: float = Field(ge=0.5, le=3600.0)
    payload_format: str = Field(default="json", pattern=r"^(json|csv|protobuf)$")


@router.put("/selection")
async def set_metric(body: _MetricConfig, user: CurrentUser = Depends(require_admin)) -> dict[str, Any]:
    """Set per-metric config (privileged write, audited)."""
    await get_db().execute(
        """
        INSERT INTO telemetry_metrics (metric, enabled, interval_s, payload_format, updated_at)
        VALUES (?, ?, ?, ?, datetime('now'))
        ON CONFLICT(metric) DO UPDATE SET
            enabled = excluded.enabled,
            interval_s = excluded.interval_s,
            payload_format = excluded.payload_format,
            updated_at = excluded.updated_at
        """,
        (body.name, int(body.enabled), body.interval_s, body.payload_format),
    )
    await audit.record(
        username=user.username,
        ip=user.ip,
        action="telemetry_metric_update",
        target=body.name,
        new_value=f"enabled={body.enabled}:interval={body.interval_s}",
    )
    return {"ok": True}


# --- store-and-forward queue --------------------------------------------------


@router.get("/queue")
async def queue_status(_: CurrentUser = Depends(require_viewer)) -> dict[str, Any]:
    """Store-and-forward queue depth (Features.md §Telemetry)."""
    row = await get_db().fetch_one("SELECT COUNT(*) AS c, MIN(queued_at) AS oldest FROM store_forward_queue")
    count = int(row["c"]) if row else 0
    oldest = row["oldest"] if row else None
    return {"depth": count, "oldest_queued_at": oldest, "capacity": 100_000}


@router.post("/queue/flush")
async def flush_queue(user: CurrentUser = Depends(require_admin)) -> dict[str, Any]:
    """Force a queue flush (privileged write, audited)."""
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    row = await get_db().fetch_one("SELECT COUNT(*) AS c FROM store_forward_queue")
    drained = int(row["c"]) if row else 0
    await get_db().execute("DELETE FROM store_forward_queue")
    await audit.record(
        username=user.username,
        ip=user.ip,
        action="telemetry_queue_flush",
        target="queue",
        new_value=f"drained={drained}",
    )
    return {"ok": True, "drained": drained, "flushed_at": now}


class _Retention(_StrictModel):
    days: int = Field(ge=1, le=3650)


@router.put("/retention")
async def set_retention(body: _Retention, user: CurrentUser = Depends(require_admin)) -> dict[str, Any]:
    await get_db().execute(
        "INSERT OR REPLACE INTO system_settings_kv (key, value, updated_at) " "VALUES (?, ?, datetime('now'))",
        ("telemetry.retention_days", str(body.days)),
    )
    await audit.record(
        username=user.username,
        ip=user.ip,
        action="telemetry_retention_update",
        target="retention",
        new_value=str(body.days),
    )
    return {"ok": True, "retention_days": body.days}


@router.get("/last-sync")
async def last_sync(_: CurrentUser = Depends(require_viewer)) -> dict[str, Any]:
    row = await get_db().fetch_one("SELECT value, updated_at FROM system_settings_kv WHERE key = 'telemetry.last_sync'")
    if row is None:
        return {"recorded": False}
    return {
        "recorded": True,
        "last_sync": row["value"],
        "updated_at": row["updated_at"],
    }
