"""Hardware interface configuration (section 1).

Enable/disable, configure and operate the bus interfaces — GPIO, I²C, SPI, UART,
RS485/RS422, CAN/CAN-FD, Modbus RTU/TCP, 1-Wire. Enable state and configuration
are persisted in ``hw_interfaces``; live status and runtime operations come from
the interfaces HAL. Mirrors the existing router conventions (role guards, audit).
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import ValidationError

from app.auth.deps import CurrentUser, require_operator, require_viewer
from app.db import audit
from app.db.database import get_db
from app.errors import ApiError
from app.hal.registry import get_hal
from app.tasks import next_boot
from app.util.models import interface_config_model

router = APIRouter(prefix="/api/interfaces", tags=["interfaces"])


def _ifaces() -> Any:
    return get_hal("interfaces")


async def _ensure_seed() -> None:
    db = get_db()
    row = await db.fetch_one("SELECT COUNT(*) AS n FROM hw_interfaces")
    if row and row["n"]:
        return
    for d in _ifaces().defaults():
        await db.execute(
            "INSERT OR IGNORE INTO hw_interfaces (name, kind, enabled, config) VALUES (?, ?, 1, ?)",
            (d["name"], d["kind"], json.dumps(d["config"])),
        )


async def _get_row(name: str) -> dict[str, Any]:
    await _ensure_seed()
    row = await get_db().fetch_one("SELECT * FROM hw_interfaces WHERE name = ?", (name,))
    if row is None:
        raise ApiError("not_found", f"Unknown interface '{name}'", 404)
    return row


@router.get("")
async def list_interfaces(_: CurrentUser = Depends(require_viewer)) -> dict[str, Any]:
    await _ensure_seed()
    rows = await get_db().fetch_all("SELECT * FROM hw_interfaces ORDER BY kind, name")
    hal = _ifaces()
    out = []
    for r in rows:
        cfg = json.loads(r["config"])
        status = hal.status(r["name"], r["kind"], cfg) if r["enabled"] else {"link": "down"}
        out.append(
            {
                "name": r["name"],
                "kind": r["kind"],
                "enabled": bool(r["enabled"]),
                "config": cfg,
                "status": status,
            }
        )
    return {"simulated": hal.simulated, "interfaces": out}


@router.get("/{name}")
async def get_interface(name: str, _: CurrentUser = Depends(require_viewer)) -> dict[str, Any]:
    r = await _get_row(name)
    cfg = json.loads(r["config"])
    return {
        "name": r["name"],
        "kind": r["kind"],
        "enabled": bool(r["enabled"]),
        "config": cfg,
        "status": _ifaces().status(r["name"], r["kind"], cfg),
    }


@router.post("/{name}/enable")
async def set_enabled(name: str, body: dict[str, Any], user: CurrentUser = Depends(require_operator)) -> dict[str, Any]:
    r = await _get_row(name)
    enabled = 1 if body.get("enabled", True) else 0
    when = body.get("when", "live")
    if when == "next-boot":
        # Stage only — skip the live write so a misconfigured interface
        # does not lock the operator out at the current boot (#114).
        pending_id = await next_boot.arm(
            target=name,
            kind="interface_enable",
            payload={"enabled": bool(enabled)},
            username=user.username,
        )
        await audit.record(
            username=user.username,
            ip=user.ip,
            action="interface_enable_staged",
            target=name,
            old_value=bool(r["enabled"]),
            new_value=f"enabled={bool(enabled)}:next-boot:pending={pending_id}",
        )
        return {
            "name": name,
            "enabled": bool(enabled),
            "when": "next-boot",
            "pending_id": pending_id,
        }
    await get_db().execute(
        "UPDATE hw_interfaces SET enabled = ?, updated_at = datetime('now') WHERE name = ?",
        (enabled, name),
    )
    # audit §3: write the change as a device-tree overlay / config fragment
    # (#107). In real mode the HAL would shell out to `dtoverlay` / configfs;
    # in simulation the envelope still reports `applied` so the audit row
    # reflects a real state change (#42).
    result = _ifaces().commit_overlay(name, bool(enabled))
    await audit.record(
        username=user.username,
        ip=user.ip,
        action="interface_enable",
        target=name,
        old_value=bool(r["enabled"]),
        new_value=f"enabled={bool(enabled)}:applied={bool(result.get('applied'))}",
    )
    return {"name": name, "enabled": bool(enabled), "overlay": result}


@router.post("/{name}/config")
async def configure(name: str, body: dict[str, Any], user: CurrentUser = Depends(require_operator)) -> dict[str, Any]:
    r = await _get_row(name)
    # Per-kind pydantic schema rejects unknown keys (e.g. duty_ns on i2c),
    # #51. Pydantic v2 raises ValidationError which FastAPI converts to
    # 422 automatically — we don't need to catch it ourselves.
    model = interface_config_model(r["kind"])
    if model is None:
        raise ApiError("unknown_kind", f"No config schema for kind '{r['kind']}'", 400)
    # .model_validate raises pydantic.ValidationError on extra keys
    # (extra="forbid"). Convert to a 422 ApiError with the first message
    # so the SPA sees a consistent error envelope (#51).
    try:
        validated = model.model_validate(body or {})
    except ValidationError as e:
        raise ApiError(
            "invalid_config",
            f"Config for kind '{r['kind']}' rejected: {e.errors()[0]['msg']}",
            422,
        ) from e
    cfg = json.loads(r["config"])
    cfg.update(validated.model_dump())
    await get_db().execute(
        "UPDATE hw_interfaces SET config = ?, updated_at = datetime('now') WHERE name = ?",
        (json.dumps(cfg), name),
    )
    await audit.record(
        username=user.username, ip=user.ip, action="interface_config", target=name, new_value=json.dumps(body)
    )
    return {"name": name, "config": cfg}


@router.post("/{name}/operate")
async def operate(name: str, body: dict[str, Any], user: CurrentUser = Depends(require_operator)) -> dict[str, Any]:
    r = await _get_row(name)
    if not r["enabled"]:
        raise ApiError("disabled", f"Interface '{name}' is disabled", 409)
    op = str(body.get("op", ""))
    params = body.get("params", {}) or {}
    result = _ifaces().operate(r["kind"], name, op, params, json.loads(r["config"]))
    # Guardrail 1: include the params dict in the audit row so the
    # action log can reconstruct what was written to the bus. Strip
    # the confirm/plan_id keys so the audit row stays focused on the
    # bus payload (#145).
    audit_params = {k: v for k, v in params.items() if k not in {"confirm", "plan_id"}}
    await audit.record(
        username=user.username,
        ip=user.ip,
        action="interface_operate",
        target=f"{name}:{op}",
        new_value=json.dumps(audit_params, sort_keys=True) if audit_params else "",
    )
    return result
