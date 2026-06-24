"""Scheduled jobs (Features.md §Maintenance)."""

from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import Field

from app.auth.deps import CurrentUser, require_admin, require_viewer
from app.db import audit
from app.db.database import get_db
from app.errors import ApiError
from app.hal.registry import get_hal
from app.util.models import _StrictModel

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


@router.get("")
async def list_jobs(_: CurrentUser = Depends(require_viewer)) -> dict[str, Any]:
    """List all scheduled jobs (DB + HAL)."""
    rows = await get_db().fetch_all(
        "SELECT id, name, cron, action, enabled, last_run_at, last_status " "FROM scheduled_jobs ORDER BY id"
    )
    hal = get_hal("system").scheduled_jobs()
    return {"db": list(rows), "hal": hal}


class _JobUpsert(_StrictModel):
    name: str = Field(min_length=1, max_length=64)
    cron: str = Field(min_length=1, max_length=64)
    action: str = Field(min_length=1, max_length=128)
    enabled: bool = True


@router.post("")
async def upsert_job(body: _JobUpsert, user: CurrentUser = Depends(require_admin)) -> dict[str, Any]:
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
        action="job_upsert",
        target=body.name,
        new_value=f"cron={body.cron}",
    )
    return {"ok": True, "id": cur}


@router.post("/{job_id}/run")
async def run_job(job_id: int, user: CurrentUser = Depends(require_admin)) -> dict[str, Any]:
    """Trigger an immediate run (privileged write, audited)."""
    row = await get_db().fetch_one("SELECT name FROM scheduled_jobs WHERE id = ?", (job_id,))
    if row is None:
        raise ApiError("job_not_found", f"job {job_id} not found", 404)
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    await get_db().execute(
        "UPDATE scheduled_jobs SET last_run_at = ?, last_status = 'ok' WHERE id = ?",
        (now, job_id),
    )
    await audit.record(
        username=user.username,
        ip=user.ip,
        action="job_run",
        target=row["name"],
        new_value=now,
    )
    return {"ok": True, "ran_at": now}


@router.delete("/{job_id}")
async def delete_job(job_id: int, user: CurrentUser = Depends(require_admin)) -> dict[str, Any]:
    row = await get_db().fetch_one("SELECT name FROM scheduled_jobs WHERE id = ?", (job_id,))
    if row is None:
        raise ApiError("job_not_found", f"job {job_id} not found", 404)
    await get_db().execute("DELETE FROM scheduled_jobs WHERE id = ?", (job_id,))
    await audit.record(
        username=user.username,
        ip=user.ip,
        action="job_delete",
        target=row["name"],
    )
    return {"ok": True, "deleted": job_id}
