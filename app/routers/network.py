"""Group 2 — Network."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from app.auth.deps import CurrentUser, require_admin, require_viewer
from app.db import audit
from app.hal.registry import get_hal

router = APIRouter(prefix="/api/network", tags=["network"])


@router.get("/interfaces")
async def interfaces(_: CurrentUser = Depends(require_viewer)) -> dict[str, Any]:
    return get_hal("network").interfaces()


@router.get("/wifi")
async def wifi(_: CurrentUser = Depends(require_viewer)) -> dict[str, Any]:
    return get_hal("network").wifi()


@router.get("/cellular")
async def cellular(_: CurrentUser = Depends(require_viewer)) -> dict[str, Any]:
    return get_hal("network").cellular()


@router.get("/routing")
async def routing(_: CurrentUser = Depends(require_viewer)) -> dict[str, Any]:
    return get_hal("network").routing()


@router.get("/firewall")
async def firewall(_: CurrentUser = Depends(require_viewer)) -> dict[str, Any]:
    return get_hal("network").firewall()


@router.post("/firewall/zone/{zone}")
async def toggle_zone(zone: str, body: dict[str, bool], user: CurrentUser = Depends(require_admin)) -> dict[str, bool]:
    await audit.record(
        username=user.username,
        ip=user.ip,
        action="firewall_zone_toggle",
        target=zone,
        new_value=body.get("enabled"),
    )
    return {"ok": True}


@router.get("/ports")
async def ports(_: CurrentUser = Depends(require_viewer)) -> dict[str, Any]:
    return get_hal("network").open_ports()


@router.get("/remote-access")
async def remote_access(_: CurrentUser = Depends(require_viewer)) -> dict[str, Any]:
    return get_hal("network").remote_access()


@router.post("/vpn")
async def vpn_action(body: dict[str, str], user: CurrentUser = Depends(require_admin)) -> dict[str, Any]:
    """Take a VPN action (up/down/restart) on the WireGuard interface.

    The actual `wg-quick up/down` invocation lives in the network HAL
    so it can be a no-op in simulation (#42). On a real device the HAL
    shells out to ``wg-quick``; here we record intent + outcome so the
    audit log shows whether the op was actually applied, not just
    requested (#42, #102).
    """
    action = (body.get("action") or "").strip().lower()
    if action not in {"up", "down", "restart"}:
        from app.errors import ApiError

        raise ApiError("invalid_action", "action must be one of: up, down, restart", 422)
    target = (body.get("interface") or "wg0").strip()
    result = get_hal("network").vpn_apply(target=target, action=action)
    await audit.record(
        username=user.username,
        ip=user.ip,
        action="vpn_action",
        target=target,
        new_value=f"{action}:applied={bool(result.get('applied'))}",
    )
    return result


@router.get("/ping")
async def ping(target: str = "1.1.1.1", _: CurrentUser = Depends(require_viewer)) -> dict[str, Any]:
    return get_hal("network").ping(target)
