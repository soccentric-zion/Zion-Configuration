"""Maintenance: db stats, vacuum, diagnostic bundle, log level, cache clear."""

from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import Field

from app.auth.deps import CurrentUser, require_admin, require_viewer
from app.db import audit
from app.db.database import get_db
from app.hal.registry import get_hal
from app.util.confirm import require_confirm
from app.util.models import _StrictModel

router = APIRouter(prefix="/api/maintenance", tags=["maintenance"])


@router.get("/db-stats")
async def db_stats(_: CurrentUser = Depends(require_viewer)) -> dict[str, Any]:
    """Database size + vacuum status (Features.md §Maintenance)."""
    hal = get_hal("system").db_stats()
    # Layered with the row count for each main table — cheap and useful.
    tables = ("users", "audit_log", "apply_plans", "metric_history", "notifications", "backup_jobs")
    counts: dict[str, int] = {}
    for t in tables:
        try:
            row = await get_db().fetch_one(f"SELECT COUNT(*) AS c FROM {t}")  # noqa: S608
            counts[t] = int(row["c"]) if row else 0
        except Exception:  # noqa: BLE001
            counts[t] = 0
    return {"hal": hal, "row_counts": counts}


class _Confirm(_StrictModel):
    confirm: bool


@router.post("/vacuum")
async def vacuum(body: _Confirm, user: CurrentUser = Depends(require_admin)) -> dict[str, Any]:
    """Run SQLite VACUUM (privileged write, audited)."""
    require_confirm(body.model_dump())
    await get_db().execute("VACUUM")
    await get_db().execute(
        "INSERT INTO maintenance_log (action, detail, actor) VALUES (?, ?, ?)",
        ("vacuum", "manual VACUUM", user.username),
    )
    await audit.record(
        username=user.username,
        ip=user.ip,
        action="db_vacuum",
        target="sqlite",
    )
    return {"ok": True}


@router.get("/diagnostic-bundle")
async def diagnostic_bundle(user: CurrentUser = Depends(require_admin)) -> dict[str, Any]:
    """Build a diagnostic bundle (privileged read, audited)."""
    envelope = get_hal("system").diagnostic_bundle()
    await audit.record(
        username=user.username,
        ip=user.ip,
        action="diagnostic_bundle",
        target="system",
        new_value=envelope.get("filename", ""),
    )
    return envelope


@router.post("/clear-cache")
async def clear_cache(user: CurrentUser = Depends(require_admin)) -> dict[str, Any]:
    """Clear dashboard caches (privileged write, audited).

    No cache layer exists yet in the SPA hot path — the dashboard reads
    fresh from the API every request. The endpoint exists so the SPA
    has a "Clear cache" button that audits and persists intent; the
    record in ``maintenance_log`` lets an operator prove they tried.
    """
    await get_db().execute(
        "INSERT INTO maintenance_log (action, detail, actor) VALUES (?, ?, ?)",
        ("clear_cache", "no-op: no cache layer present", user.username),
    )
    await audit.record(
        username=user.username,
        ip=user.ip,
        action="clear_cache",
        target="settings",
    )
    return {"ok": True, "cleared_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}


class _LogLevel(_StrictModel):
    level: str = Field(pattern=r"^(DEBUG|INFO|WARNING|ERROR|CRITICAL)$")


@router.post("/log-level")
async def set_log_level(body: _LogLevel, user: CurrentUser = Depends(require_admin)) -> dict[str, Any]:
    """Set dashboard log level at runtime (privileged write, audited)."""
    import logging as _logging

    _logging.getLogger().setLevel(body.level)
    await get_db().execute(
        "INSERT INTO maintenance_log (action, detail, actor) VALUES (?, ?, ?)",
        ("log_level", body.level, user.username),
    )
    await audit.record(
        username=user.username,
        ip=user.ip,
        action="log_level",
        target="root",
        new_value=body.level,
    )
    return {"ok": True, "level": body.level}


@router.get("/scheduled-jobs")
async def scheduled_jobs(_: CurrentUser = Depends(require_viewer)) -> dict[str, Any]:
    """Scheduled jobs (Features.md §Maintenance)."""
    rows = await get_db().fetch_all(
        "SELECT id, name, cron, action, enabled, last_run_at, last_status " "FROM scheduled_jobs ORDER BY id ASC"
    )
    hal = get_hal("system").scheduled_jobs()
    return {"db": list(rows), "hal": hal}


class _ScheduledJobUpsert(_StrictModel):
    name: str = Field(min_length=1, max_length=64)
    cron: str = Field(min_length=1, max_length=64)
    action: str = Field(min_length=1, max_length=128)
    enabled: bool = True


@router.post("/scheduled-jobs")
async def upsert_job(
    body: _ScheduledJobUpsert,
    user: CurrentUser = Depends(require_admin),
) -> dict[str, Any]:
    cur = await get_db().execute(
        """
        INSERT INTO scheduled_jobs (name, cron, action, enabled)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(name) DO UPDATE SET
            cron = excluded.cron,
            action = excluded.action,
            enabled = excluded.enabled
        """,
        (body.name, body.cron, body.action, int(body.enabled)),
    )
    await audit.record(
        username=user.username,
        ip=user.ip,
        action="scheduled_job_upsert",
        target=body.name,
        new_value=f"cron={body.cron}",
    )
    return {"ok": True, "id": cur}
