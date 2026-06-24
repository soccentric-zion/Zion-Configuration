"""Group 6 — Software: systemd services, containers, OTA via SWUpdate."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, UploadFile

from app.auth.deps import CurrentUser, require_admin, require_operator, require_viewer
from app.db import audit
from app.db.database import get_db
from app.hal.registry import get_hal
from app.tasks import ota

router = APIRouter(prefix="/api/software", tags=["software"])


def _sw() -> Any:
    return get_hal("software")


# --- services --------------------------------------------------------------
@router.get("/services")
async def services(_: CurrentUser = Depends(require_viewer)) -> dict[str, Any]:
    return _sw().services()


@router.post("/services/{name}/{action}")
async def service_action(name: str, action: str, user: CurrentUser = Depends(require_operator)) -> dict[str, Any]:
    if action not in {"start", "stop", "restart", "enable", "disable"}:
        from app.errors import ApiError

        raise ApiError("bad_action", f"Unknown action '{action}'", 400)
    # Guardrail 3: protected units (dashboard, ssh, network, time-sync) are
    # rejected here as well, so the duplicate surface in the software
    # router can't bypass the sysadmin check (#90).
    from app.util.protected import assert_not_protected

    assert_not_protected("service", name, action)
    result = _sw().service_action(name, action)
    await audit.record(username=user.username, ip=user.ip, action=f"service_{action}", target=name)
    return result


@router.get("/services/{name}/journal")
async def service_journal(name: str, lines: int = 20, _: CurrentUser = Depends(require_viewer)) -> dict[str, Any]:
    return _sw().service_journal(name, lines)


# --- containers ------------------------------------------------------------
@router.get("/containers")
async def containers(_: CurrentUser = Depends(require_viewer)) -> dict[str, Any]:
    return _sw().containers()


@router.post("/containers/{name}/{action}")
async def container_action(name: str, action: str, user: CurrentUser = Depends(require_admin)) -> dict[str, Any]:
    if action not in {"start", "stop", "restart", "remove"}:
        from app.errors import ApiError

        raise ApiError("bad_action", f"Unknown action '{action}'", 400)
    result = _sw().container_action(name, action)
    await audit.record(username=user.username, ip=user.ip, action=f"container_{action}", target=name)
    return result


@router.post("/containers/pull")
async def container_pull(body: dict[str, str], user: CurrentUser = Depends(require_admin)) -> dict[str, Any]:
    result = _sw().container_pull(body.get("image", ""))
    await audit.record(username=user.username, ip=user.ip, action="container_pull", target=body.get("image"))
    return result


@router.get("/containers/{name}/logs")
async def container_logs(name: str, lines: int = 50, _: CurrentUser = Depends(require_viewer)) -> dict[str, Any]:
    return _sw().container_logs(name, lines)


# --- OTA -------------------------------------------------------------------
@router.get("/ota/versions")
async def ota_versions(_: CurrentUser = Depends(require_viewer)) -> dict[str, Any]:
    return ota.current_versions()


@router.get("/ota/history")
async def ota_history(_: CurrentUser = Depends(require_viewer)) -> dict[str, Any]:
    rows = await get_db().fetch_all("SELECT * FROM ota_history ORDER BY ts DESC, id DESC LIMIT 50")
    return {"history": rows}


@router.post("/ota/upload")
async def ota_upload(file: UploadFile, user: CurrentUser = Depends(require_admin)) -> dict[str, Any]:
    """Accept a .swu file and kick off the install in the background.

    Progress is streamed over ``/ws/ota``. This stores history and returns a
    job id the frontend subscribes to.
    """
    data = await file.read()
    job_id = await ota.start_install(file.filename or "update.swu", data, user.username)
    await audit.record(
        username=user.username,
        ip=user.ip,
        action="ota_upload",
        target=file.filename,
        new_value=f"{len(data)} bytes",
    )
    return {"job_id": job_id, "filename": file.filename, "size": len(data)}


@router.get("/ota/hawkbit")
async def hawkbit(_: CurrentUser = Depends(require_viewer)) -> dict[str, Any]:
    return ota.hawkbit_status()


@router.post("/ota/reboot")
async def ota_reboot(body: dict[str, bool], user: CurrentUser = Depends(require_admin)) -> dict[str, bool]:
    await audit.record(
        username=user.username,
        ip=user.ip,
        action="ota_reboot",
        new_value="now" if body.get("now") else "later",
    )
    return {"ok": True}
