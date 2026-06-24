"""Group 1 — Overview: device identity, system health, boot management."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from app.auth.deps import CurrentUser, require_admin, require_viewer
from app.db import audit
from app.errors import ApiError
from app.hal.registry import get_hal
from app.tasks import apply_state
from app.util.confirm import require_confirm
from app.util.models import UbootEnvSet

router = APIRouter(prefix="/api/overview", tags=["overview"])


@router.get("/identity")
async def identity(_: CurrentUser = Depends(require_viewer)) -> dict[str, Any]:
    return get_hal("system").identity()


@router.get("/health")
async def health(_: CurrentUser = Depends(require_viewer)) -> dict[str, Any]:
    return get_hal("system").health()


@router.get("/disks")
async def disks(_: CurrentUser = Depends(require_viewer)) -> dict[str, Any]:
    return get_hal("system").disks()


@router.get("/boot")
async def boot(_: CurrentUser = Depends(require_viewer)) -> dict[str, Any]:
    return get_hal("system").boot()


@router.post("/boot/uboot-env")
async def set_uboot_env(
    body: UbootEnvSet | None = None,
    user: CurrentUser = Depends(require_admin),
) -> dict[str, Any]:
    """Set a U-Boot env variable (audit §4, #131, #151).

    Body is validated by ``UbootEnvSet``: the ``key`` must match the
    U-Boot env alphabet *and* be in the allow-list of known keys
    (``boot_partion`` — a typo — is rejected with 422). ``value`` is
    bounded to 256 chars. ``confirm: true`` is required (privileged
    write).

    The router arms an ``apply_state`` plan with ``rollback_kind=
    "boot_env"`` so a bad write can be rolled back by the watchdog
    if the device never re-heartbeats, or manually via the
    ``/apply/rollback`` endpoint (#151, Guardrail 2).
    """
    if body is None:
        raise ApiError("invalid_body", "Body must include 'key' and 'value'", 422)
    require_confirm(body.model_dump())
    # Capture the previous value so the rollback handler can restore
    # it (#151). The HAL's `boot()` envelope exposes the simulated env
    # table; in real mode that would be a `fw_printenv` round-trip.
    hal = get_hal("system")
    current_env = hal.boot().get("uboot_env") or {}
    previous_value = str(current_env.get(body.key, ""))
    result = hal.set_uboot_env(body.key, body.value)
    plan = await apply_state.arm(
        target=f"boot_env:{body.key}",
        before={"key": body.key, "value": previous_value},
        after={"key": body.key, "value": body.value},
        rollback_kind="boot_env",
        rollback_armed_s=body.rollback_armed_s,
    )
    await audit.record(
        username=user.username,
        ip=user.ip,
        action="set_uboot_env",
        target=body.key,
        new_value=(f"value={body.value}" f":plan={plan['plan_id']}" f":applied={bool(result.get('applied'))}"),
    )
    return {
        "ok": True,
        "applied": bool(result.get("applied")),
        "plan_id": plan["plan_id"],
        "armed_until": plan["armed_until"],
        "rollback_armed_s": plan["rollback_armed_s"],
    }


@router.post("/boot/uboot-env/apply/confirm")
async def set_uboot_env_confirm(
    body: dict[str, Any],
    user: CurrentUser = Depends(require_admin),
) -> dict[str, Any]:
    """Commit a previously-armed set_uboot_env plan (dashboard reachable with new config)."""
    require_confirm(body)
    plan_id = int(body.get("plan_id", 0))
    await apply_state.commit(plan_id)
    await audit.record(
        username=user.username,
        ip=user.ip,
        action="set_uboot_env_commit",
        target="boot_env",
        new_value=str(plan_id),
    )
    return {"ok": True, "plan_id": plan_id, "committed": True}


@router.post("/boot/uboot-env/apply/rollback")
async def set_uboot_env_rollback(
    body: dict[str, Any],
    user: CurrentUser = Depends(require_admin),
) -> dict[str, Any]:
    """Manually rollback a set_uboot_env plan (e.g. user noticed the new config is bad)."""
    require_confirm(body)
    plan_id = int(body.get("plan_id", 0))
    await apply_state.rollback(
        plan_id,
        reason="manual",
        username=user.username,
        ip=user.ip,
    )
    return {"ok": True, "plan_id": plan_id, "rolled_back": True}


@router.post("/boot/rollback")
async def rollback(
    body: dict[str, Any] | None = None,
    user: CurrentUser = Depends(require_admin),
) -> dict[str, Any]:
    require_confirm(body)
    result = get_hal("system").boot_rollback()
    await audit.record(
        username=user.username,
        ip=user.ip,
        action="boot_rollback",
        target="slot",
        new_value=(f"{result.get('from_slot')}->{result.get('to_slot')}" f":applied={bool(result.get('applied'))}"),
    )
    return result
