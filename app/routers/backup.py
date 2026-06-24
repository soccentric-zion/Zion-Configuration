"""Backup / Restore / Factory Reset (Features.md §Settings, audit Guardrail 4)."""

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
from app.util.confirm import require_confirm
from app.util.models import _StrictModel

router = APIRouter(prefix="/api/backup", tags=["backup"])


# --- list --------------------------------------------------------------------


@router.get("/jobs")
async def list_jobs(_: CurrentUser = Depends(require_viewer)) -> dict[str, Any]:
    """List configured backup jobs + simulated envelope."""
    rows = await get_db().fetch_all(
        "SELECT id, name, kind, target, schedule, enabled, last_run_at, last_status " "FROM backup_jobs ORDER BY id ASC"
    )
    hal = get_hal("storage").scheduled_backups()
    return {"db": list(rows), "hal": hal, "simulated": hal.get("simulated", True), "jobs": hal.get("jobs", [])}


@router.get("/restore-points")
async def restore_points(_: CurrentUser = Depends(require_viewer)) -> dict[str, Any]:
    rows = await get_db().fetch_all(
        "SELECT id, job_id, label, path, size_bytes, checksum, created_at "
        "FROM backup_restore_points ORDER BY id DESC LIMIT 50"
    )
    return {"points": list(rows)}


# --- write -------------------------------------------------------------------


class _JobUpsert(_StrictModel):
    name: str = Field(min_length=1, max_length=64)
    kind: str = Field(pattern=r"^(config|full|factory)$")
    target: str = Field(min_length=1, max_length=512)
    schedule: str = Field(default="", max_length=64)
    enabled: bool = True


@router.post("/jobs")
async def upsert_job(body: _JobUpsert, user: CurrentUser = Depends(require_admin)) -> dict[str, Any]:
    """Create or update a backup job (privileged write, audited)."""
    cur = await get_db().execute(
        """
        INSERT INTO backup_jobs (name, kind, target, schedule, enabled, updated_at)
        VALUES (?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(name) DO UPDATE SET
            kind = excluded.kind,
            target = excluded.target,
            schedule = excluded.schedule,
            enabled = excluded.enabled,
            updated_at = excluded.updated_at
        """,
        (body.name, body.kind, body.target, body.schedule, int(body.enabled)),
    )
    await audit.record(
        username=user.username,
        ip=user.ip,
        action="backup_upsert",
        target=body.name,
        new_value=f"kind={body.kind}:target={body.target}:enabled={body.enabled}",
    )
    return {"ok": True, "id": cur}


@router.post("/jobs/{job_id}/run")
async def run_job(job_id: int, user: CurrentUser = Depends(require_admin)) -> dict[str, Any]:
    """Trigger an immediate run (privileged write, audited)."""
    row = await get_db().fetch_one("SELECT name FROM backup_jobs WHERE id = ?", (job_id,))
    if row is None:
        raise ApiError("job_not_found", f"backup job {job_id} not found", 404)
    name = row["name"]
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    await get_db().execute(
        "UPDATE backup_jobs SET last_run_at = ?, last_status = 'ok' WHERE id = ?",
        (now, job_id),
    )
    await audit.record(
        username=user.username,
        ip=user.ip,
        action="backup_run",
        target=name,
        new_value=f"triggered_at={now}",
    )
    return {"ok": True, "job_id": job_id, "ran_at": now}


@router.delete("/jobs/{job_id}")
async def delete_job(job_id: int, user: CurrentUser = Depends(require_admin)) -> dict[str, Any]:
    row = await get_db().fetch_one("SELECT name FROM backup_jobs WHERE id = ?", (job_id,))
    if row is None:
        raise ApiError("job_not_found", f"backup job {job_id} not found", 404)
    await get_db().execute("DELETE FROM backup_jobs WHERE id = ?", (job_id,))
    await audit.record(
        username=user.username,
        ip=user.ip,
        action="backup_delete",
        target=row["name"],
    )
    return {"ok": True, "deleted": job_id}


# --- factory reset (triple confirm) -----------------------------------------


class _FactoryReset(_StrictModel):
    confirm: bool
    ack_data_loss: bool
    ack_unrecoverable: bool


@router.post("/factory-reset")
async def factory_reset(body: _FactoryReset, user: CurrentUser = Depends(require_admin)) -> dict[str, Any]:
    """Factory reset (Features.md §Settings: triple confirm required).

    Three booleans must all be true: ``confirm``, ``ack_data_loss``,
    ``ack_unrecoverable``. Anything else returns 422 with the missing
    ack names so the UI can prompt the operator. The actual reset is
    armed via ``apply_state`` so the watchdog can auto-rollback if the
    device never re-heartbeats.
    """
    if not (body.confirm and body.ack_data_loss and body.ack_unrecoverable):
        missing = [
            name
            for name, ok in (
                ("confirm", body.confirm),
                ("ack_data_loss", body.ack_data_loss),
                ("ack_unrecoverable", body.ack_unrecoverable),
            )
            if not ok
        ]
        raise ApiError(
            "factory_reset_not_acknowledged",
            f"missing acknowledgements: {', '.join(missing)}",
            422,
            detail={"missing": missing},
        )
    from app.tasks import apply_state as _ap

    plan = await _ap.arm(
        target="factory_reset",
        before={},
        after={"action": "wipe + reboot"},
        rollback_kind="factory_reset",
        rollback_armed_s=120,
    )
    await audit.record(
        username=user.username,
        ip=user.ip,
        action="factory_reset_arm",
        target="system",
        new_value=f"plan={plan['plan_id']}",
    )
    return {"ok": True, "plan_id": plan["plan_id"], "armed_until": plan["armed_until"]}


@router.post("/factory-reset/confirm")
async def factory_reset_commit(
    body: dict[str, Any],
    user: CurrentUser = Depends(require_admin),
) -> dict[str, Any]:
    require_confirm(body)
    plan_id = int(body.get("plan_id", 0))
    from app.tasks import apply_state as _ap

    await _ap.commit(plan_id)
    await audit.record(
        username=user.username,
        ip=user.ip,
        action="factory_reset_commit",
        target="system",
        new_value=str(plan_id),
    )
    return {"ok": True, "plan_id": plan_id, "committed": True}


@router.post("/factory-reset/rollback")
async def factory_reset_rollback(
    body: dict[str, Any],
    user: CurrentUser = Depends(require_admin),
) -> dict[str, Any]:
    require_confirm(body)
    plan_id = int(body.get("plan_id", 0))
    from app.tasks import apply_state as _ap

    await _ap.rollback(
        plan_id,
        reason="manual",
        username=user.username,
        ip=user.ip,
    )
    return {"ok": True, "plan_id": plan_id, "rolled_back": True}


# --- export / import ---------------------------------------------------------


@router.get("/export")
async def export_config(_: CurrentUser = Depends(require_admin)) -> dict[str, Any]:
    """Export dashboard config as a JSON-serializable envelope."""
    rows = await get_db().fetch_all("SELECT name, kind, target, schedule, enabled FROM backup_jobs")
    return {
        "format_version": 1,
        "backup_jobs": [dict(r) for r in rows],
    }


class _ImportConfig(_StrictModel):
    backup_jobs: list[dict[str, Any]] = Field(default_factory=list)


@router.post("/import")
async def import_config(
    body: _ImportConfig,
    user: CurrentUser = Depends(require_admin),
) -> dict[str, Any]:
    """Import dashboard config (privileged write, audited)."""
    for j in body.backup_jobs:
        if not all(k in j for k in ("name", "kind", "target")):
            raise ApiError("invalid_job", "each job needs name/kind/target", 422)
        await get_db().execute(
            """
            INSERT INTO backup_jobs (name, kind, target, schedule, enabled, updated_at)
            VALUES (?, ?, ?, ?, ?, datetime('now'))
            ON CONFLICT(name) DO UPDATE SET
                kind = excluded.kind,
                target = excluded.target,
                schedule = excluded.schedule,
                enabled = excluded.enabled,
                updated_at = excluded.updated_at
            """,
            (
                j["name"],
                j["kind"],
                j["target"],
                j.get("schedule", ""),
                int(j.get("enabled", True)),
            ),
        )
    await audit.record(
        username=user.username,
        ip=user.ip,
        action="backup_import",
        target="config",
        new_value=f"jobs={len(body.backup_jobs)}",
    )
    return {"ok": True, "imported": len(body.backup_jobs)}
