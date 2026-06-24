"""Group 4 — Storage & Power."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends
from pydantic import Field

from app.auth.deps import CurrentUser, require_admin, require_viewer
from app.db import audit
from app.errors import ApiError
from app.hal.registry import get_hal
from app.util.confirm import require_confirm
from app.util.models import _StrictModel

router = APIRouter(prefix="/api/storage", tags=["storage"])


@router.get("/health")
async def health(_: CurrentUser = Depends(require_viewer)) -> dict[str, Any]:
    return get_hal("storage").health()


@router.get("/mounts")
async def mounts(_: CurrentUser = Depends(require_viewer)) -> dict[str, Any]:
    return get_hal("storage").mounts()


# --- fsck: real e2fsck invocation (#111, audit §10) -----------------------
# The previous stub returned {"ok": True, "started": True} without
# actually invoking e2fsck (audit-state alignment #42). The HAL now
# shells out to `e2fsck -n {device}` (read-only) in real mode and
# records the would-be command in simulation mode.
@router.post("/fsck")
async def fsck(body: dict[str, Any], user: CurrentUser = Depends(require_admin)) -> dict[str, Any]:
    require_confirm(body)
    device = str(body.get("device") or "")
    if not device:
        raise ApiError("missing_device", "fsck requires `device`", 400)
    result = get_hal("storage").fsck(device=device)
    await audit.record(
        username=user.username,
        ip=user.ip,
        action="fsck",
        target=device,
        new_value=result.get("command"),
    )
    return result


@router.get("/power")
async def power(_: CurrentUser = Depends(require_viewer)) -> dict[str, Any]:
    return get_hal("storage").power()


@router.post("/power/suspend")
async def suspend(
    body: dict[str, Any] | None = None,
    user: CurrentUser = Depends(require_admin),
) -> dict[str, bool]:
    require_confirm(body)
    await audit.record(username=user.username, ip=user.ip, action="suspend", target="system")
    return {"ok": True}


@router.get("/rtc")
async def rtc(_: CurrentUser = Depends(require_viewer)) -> dict[str, Any]:
    return get_hal("storage").rtc()


@router.post("/rtc")
async def set_rtc(body: dict[str, Any], user: CurrentUser = Depends(require_admin)) -> dict[str, bool]:
    require_confirm(body)
    await audit.record(username=user.username, ip=user.ip, action="set_rtc", new_value=body.get("time"))
    return {"ok": True}


# --- SMART self-test (#101, audit §7) --------------------------------------
class _SmartTestBody(_StrictModel):
    device: str = Field(min_length=1, max_length=128)
    kind: Literal["short", "long"]
    confirm: bool = False


@router.post("/smart-test")
async def smart_test(body: _SmartTestBody, user: CurrentUser = Depends(require_admin)) -> dict[str, Any]:
    """Trigger a SMART short/long self-test on ``device`` (#101)."""
    require_confirm(body.model_dump())
    result = get_hal("storage").smart_test(body.device, body.kind)
    await audit.record(
        username=user.username,
        ip=user.ip,
        action="storage_smart_test",
        target=body.device,
        new_value=body.kind,
    )
    return result


# --- mount / unmount / partition / overlayfs / luks / ab-data (#113, audit §10) ---
# The audit requires the dashboard to expose layout-change writes with
# confirm + audit. Each endpoint delegates to the HAL, which shelles
# out in real mode and records the would-be command in simulation.

_ALLOWED_FS = {"ext2", "ext3", "ext4", "vfat", "ntfs", "btrfs", "f2fs", "xfs", "iso9660"}


class _MountBody(_StrictModel):
    device: str = Field(min_length=1, max_length=256)
    mountpoint: str = Field(min_length=1, max_length=512)
    fstype: Literal["ext2", "ext3", "ext4", "vfat", "ntfs", "btrfs", "f2fs", "xfs", "iso9660"]
    options: str = Field(default="", max_length=512)
    confirm: bool = False


@router.post("/mount")
async def mount(body: _MountBody, user: CurrentUser = Depends(require_admin)) -> dict[str, Any]:
    """Mount ``device`` at ``mountpoint`` (#113, audit §10)."""
    require_confirm(body.model_dump())
    # Guardrail 3: refuse to mount on a protected path (the dashboard
    # /etc/static dir is read-only).
    if body.mountpoint.startswith("/etc") or body.mountpoint.startswith("/data/dashboard.db"):
        raise ApiError("protected_path", f"mountpoint {body.mountpoint!r} is protected", 403)
    result = get_hal("storage").mount(
        device=body.device,
        mountpoint=body.mountpoint,
        fstype=body.fstype,
        options=body.options,
    )
    await audit.record(
        username=user.username,
        ip=user.ip,
        action="storage_mount",
        target=body.device,
        new_value=f"{body.mountpoint} ({body.fstype})",
    )
    return result


class _UnmountBody(_StrictModel):
    mountpoint: str = Field(min_length=1, max_length=512)
    confirm: bool = False


@router.post("/unmount")
async def unmount(body: _UnmountBody, user: CurrentUser = Depends(require_admin)) -> dict[str, Any]:
    """Unmount ``mountpoint`` (#113, audit §10)."""
    require_confirm(body.model_dump())
    result = get_hal("storage").unmount(mountpoint=body.mountpoint)
    await audit.record(
        username=user.username,
        ip=user.ip,
        action="storage_unmount",
        target=body.mountpoint,
    )
    return result


class _PartitionBody(_StrictModel):
    op: Literal["create", "delete", "resize"]
    device: str = Field(min_length=1, max_length=256)
    partno: int | None = Field(default=None, ge=1, le=128)
    size: str | None = Field(default=None, max_length=32)
    confirm: bool = False


@router.post("/partition")
async def partition(body: _PartitionBody, user: CurrentUser = Depends(require_admin)) -> dict[str, Any]:
    """Partition-table operations (create/delete/resize) (#113, audit §10)."""
    require_confirm(body.model_dump())
    params: dict[str, Any] = {}
    if body.partno is not None:
        params["partno"] = body.partno
    if body.size is not None:
        params["size"] = body.size
    result = get_hal("storage").partition(op=body.op, device=body.device, params=params)
    await audit.record(
        username=user.username,
        ip=user.ip,
        action=f"storage_partition_{body.op}",
        target=body.device,
        new_value=str(params),
    )
    return result


@router.get("/overlayfs")
async def overlayfs(_: CurrentUser = Depends(require_viewer)) -> dict[str, Any]:
    """Read-only overlayfs lower/upper/work state (#113, audit §10)."""
    return get_hal("storage").overlayfs_state()


@router.get("/luks")
async def luks(_: CurrentUser = Depends(require_viewer)) -> dict[str, Any]:
    """Read-only LUKS header info (#113, audit §10).

    The endpoint is read-only on purpose — key-slot mutation goes
    through the system installer, not the dashboard. A future PR can
    add a guarded write path if the audit demands it.
    """
    return get_hal("storage").luks_info()


class _AbDataSlot(_StrictModel):
    slot: Literal["A", "B"]
    confirm: bool = False


@router.post("/ab-data/slot")
async def ab_data_slot(body: _AbDataSlot, user: CurrentUser = Depends(require_admin)) -> dict[str, Any]:
    """Mark an A/B data slot for the next-boot selector (#113, audit §10).

    The change is staged so the next reboot flips the data slot.
    The audit row carries the who/when; the HAL envelope reports
    "applied" in real mode and "would apply" in simulation.
    """
    require_confirm(body.model_dump())
    result = get_hal("storage").ab_data_slot(slot=body.slot)
    await audit.record(
        username=user.username,
        ip=user.ip,
        action="storage_ab_data_slot",
        target="ab-data",
        new_value=body.slot,
    )
    return result
