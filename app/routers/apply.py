"""Apply-plan bundle endpoints (#99).

audit.md "Backend" section requires that first-boot provisioning can
stage changes across multiple sections (network, hostname, timezone,
…) and apply them atomically with a single rollback point. This
router exposes ``/api/apply/bundle`` to stage such bundles, and the
``/commit`` / ``/rollback`` siblings to settle or unwind them.

The actual plan lifecycle lives in ``app/tasks/apply_state.py``;
this module is the HTTP surface.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.auth.deps import CurrentUser, require_admin, require_viewer
from app.db import audit
from app.errors import ApiError
from app.tasks import apply_state, next_boot
from app.util.confirm import require_confirm

router = APIRouter(prefix="/api/apply", tags=["apply"])


class _BundleItem(BaseModel):
    target: str
    before: dict[str, Any]
    after: dict[str, Any]
    rollback_kind: str


class _BundleArm(BaseModel):
    items: list[_BundleItem] = Field(min_length=1)
    confirm: bool = False
    rollback_armed_s: int = Field(default=60, ge=5, le=600)


class _BundleSettle(BaseModel):
    confirm: bool = False


@router.post("/bundle")
async def arm_bundle(body: _BundleArm, user: CurrentUser = Depends(require_admin)) -> dict[str, Any]:
    """Stage a multi-section change as a single atomic bundle (#99)."""
    require_confirm(body.model_dump())
    result = await apply_state.arm_bundle(
        items=[i.model_dump() for i in body.items],
        rollback_armed_s=body.rollback_armed_s,
    )
    await audit.record(
        username=user.username,
        ip=user.ip,
        action="apply_bundle_arm",
        target=result["bundle_id"],
        new_value=f"{len(result['plan_ids'])} plans",
    )
    return {"ok": True, **result}


@router.post("/bundle/{bundle_id}/commit")
async def commit_bundle(
    bundle_id: str, body: _BundleSettle | None = None, user: CurrentUser = Depends(require_admin)
) -> dict[str, Any]:
    """Commit every armed plan in the bundle."""
    require_confirm(body.model_dump() if body is not None else None)
    return await apply_state.commit_bundle(bundle_id, username=user.username, ip=user.ip)


@router.post("/bundle/{bundle_id}/rollback")
async def rollback_bundle(
    bundle_id: str, body: _BundleSettle | None = None, user: CurrentUser = Depends(require_admin)
) -> dict[str, Any]:
    """Roll back every plan in the bundle."""
    require_confirm(body.model_dump() if body is not None else None)
    return await apply_state.rollback_bundle(bundle_id, reason="manual", username=user.username, ip=user.ip)


@router.get("/bundle/{bundle_id}")
async def get_bundle(bundle_id: str, _: CurrentUser = Depends(require_admin)) -> dict[str, Any]:
    """List every plan in the bundle (operator-visible status)."""
    plans = await apply_state.bundle_plans(bundle_id)
    return {
        "bundle_id": bundle_id,
        "plans": [
            {
                "id": p.id,
                "target": p.target,
                "rollback_kind": p.rollback_kind,
                "status": p.status,
                "armed_until": p.armed_until,
            }
            for p in plans
        ],
    }


# --- next-boot staging (#114, audit §3/§4/§10) -------------------------------
# The pending_overlays table (app/tasks/next_boot.py) lets an operator
# stage a hardware-touching write for "apply on next boot" instead of
# going live immediately. The list/commit/discard endpoints let the SPA
# show what's staged and let the boot-time hook settle them.


@router.get("/pending")
async def list_pending(_: CurrentUser = Depends(require_viewer)) -> dict[str, Any]:
    """Return every pending_overlay row, newest first."""
    return {"pending": await next_boot.list_pending()}


class _PendingCommit(BaseModel):
    confirm: bool = False


@router.post("/pending/commit")
async def commit_pending(
    body: _PendingCommit | None = None, user: CurrentUser = Depends(require_admin)
) -> dict[str, Any]:
    """Boot-time hook: apply every ``armed`` row to its live table (#114).

    In simulation the SPA calls this after a successful reboot so the
    audit-trail records when the staged change actually landed. In
    real hardware this is wired to the first-boot script.
    """
    require_confirm(body.model_dump() if body is not None else None)
    return await next_boot.commit_pending(username=user.username)


class _PendingDiscard(BaseModel):
    confirm: bool = False


@router.post("/pending/{pending_id}/discard")
async def discard_pending(
    pending_id: int,
    body: _PendingDiscard | None = None,
    user: CurrentUser = Depends(require_admin),
) -> dict[str, Any]:
    """Operator drop: mark a single armed row as discarded."""
    require_confirm(body.model_dump() if body is not None else None)
    try:
        return await next_boot.discard(pending_id, username=user.username)
    except ValueError as exc:
        raise ApiError("pending_not_found" if "unknown" in str(exc) else "pending_not_armed", str(exc), 400) from exc
