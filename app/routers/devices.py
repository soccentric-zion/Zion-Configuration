"""Device configuration layer (section 6).

Declaratively define attached devices (interface, address/bus, parameters,
register maps) and enable/disable them individually. Persisted in the ``devices``
table. Follows the existing CRUD + audit conventions (see cloud alert rules).
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends

from app.auth.deps import CurrentUser, require_operator, require_viewer
from app.db import audit
from app.db.database import get_db
from app.errors import ApiError
from app.tasks import next_boot

router = APIRouter(prefix="/api/devices", tags=["devices"])


def _row_to_device(r: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": r["id"],
        "name": r["name"],
        "kind": r["kind"],
        "interface": r["interface"],
        "address": r["address"],
        "params": json.loads(r["params"]),
        "register_map": json.loads(r["register_map"]),
        "enabled": bool(r["enabled"]),
    }


@router.get("")
async def list_devices(_: CurrentUser = Depends(require_viewer)) -> dict[str, Any]:
    rows = await get_db().fetch_all("SELECT * FROM devices ORDER BY id")
    return {"devices": [_row_to_device(r) for r in rows]}


@router.post("", status_code=201)
async def create_device(body: dict[str, Any], user: CurrentUser = Depends(require_operator)) -> dict[str, Any]:
    name = str(body.get("name", "")).strip()
    interface = str(body.get("interface", "")).strip()
    if not name or not interface:
        raise ApiError("bad_device", "name and interface are required", 400)
    did = await get_db().execute(
        """INSERT INTO devices (name, kind, interface, address, params, register_map, enabled)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            name,
            str(body.get("kind", "generic")),
            interface,
            body.get("address"),
            json.dumps(body.get("params", {}) or {}),
            json.dumps(body.get("register_map", []) or []),
            1 if body.get("enabled", True) else 0,
        ),
    )
    await audit.record(username=user.username, ip=user.ip, action="device_create", target=name)
    return {"id": did}


@router.get("/{device_id}")
async def get_device(device_id: int, _: CurrentUser = Depends(require_viewer)) -> dict[str, Any]:
    row = await get_db().fetch_one("SELECT * FROM devices WHERE id = ?", (device_id,))
    if row is None:
        raise ApiError("not_found", f"Unknown device {device_id}", 404)
    return _row_to_device(row)


@router.post("/{device_id}/enable")
async def enable_device(
    device_id: int, body: dict[str, Any], user: CurrentUser = Depends(require_operator)
) -> dict[str, Any]:
    row = await get_db().fetch_one("SELECT * FROM devices WHERE id = ?", (device_id,))
    if row is None:
        raise ApiError("not_found", f"Unknown device {device_id}", 404)
    enabled = 1 if body.get("enabled", True) else 0
    when = body.get("when", "live")
    if when == "next-boot":
        pending_id = await next_boot.arm(
            target=str(device_id),
            kind="device_enable",
            payload={"enabled": bool(enabled)},
            username=user.username,
        )
        await audit.record(
            username=user.username,
            ip=user.ip,
            action="device_enable_staged",
            target=row["name"],
            old_value=bool(row["enabled"]),
            new_value=f"enabled={bool(enabled)}:next-boot:pending={pending_id}",
        )
        return {
            "id": device_id,
            "enabled": bool(enabled),
            "when": "next-boot",
            "pending_id": pending_id,
        }
    await get_db().execute("UPDATE devices SET enabled = ? WHERE id = ?", (enabled, device_id))
    await audit.record(
        username=user.username,
        ip=user.ip,
        action="device_enable",
        target=row["name"],
        old_value=bool(row["enabled"]),
        new_value=bool(enabled),
    )
    return {"id": device_id, "enabled": bool(enabled)}


@router.delete("/{device_id}")
async def delete_device(device_id: int, user: CurrentUser = Depends(require_operator)) -> dict[str, bool]:
    row = await get_db().fetch_one("SELECT * FROM devices WHERE id = ?", (device_id,))
    if row is None:
        raise ApiError("not_found", f"Unknown device {device_id}", 404)
    await get_db().execute("DELETE FROM devices WHERE id = ?", (device_id,))
    await audit.record(username=user.username, ip=user.ip, action="device_delete", target=row["name"])
    return {"ok": True}
