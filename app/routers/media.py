"""Camera/Display (CSI/DSI) + SoC media/codec/compute blocks (sections 2 & 3).

CSI cameras (detect/enable/configure/live-stream), DSI displays (detect/enable/
configure), and the BCM2712 blocks: GPU, HEVC hardware decoder, V4L2 *software*
encode (no hardware H.264 encoder on this SoC), ISP and HVS. Enable state + config
persist in ``media_blocks``; the live feed is streamed over ``/ws/camera``.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends

from app.auth.deps import CurrentUser, require_operator, require_viewer
from app.db import audit
from app.db.database import get_db
from app.errors import ApiError
from app.hal.registry import get_hal
from app.tasks import next_boot

router = APIRouter(prefix="/api/media", tags=["media"])


def _media() -> Any:
    return get_hal("media")


async def _ensure_seed() -> None:
    db = get_db()
    row = await db.fetch_one("SELECT COUNT(*) AS n FROM media_blocks")
    if row and row["n"]:
        return
    hal = _media()
    for d in hal.camera_defaults() + hal.display_defaults() + hal.block_defaults():
        await db.execute(
            "INSERT OR IGNORE INTO media_blocks (name, kind, enabled, config) VALUES (?, ?, 1, ?)",
            (d["name"], d["kind"], json.dumps(d["config"])),
        )


async def _rows(*kinds: str) -> list[dict[str, Any]]:
    await _ensure_seed()
    placeholders = ",".join("?" for _ in kinds)
    return await get_db().fetch_all(
        f"SELECT * FROM media_blocks WHERE kind IN ({placeholders}) ORDER BY name", tuple(kinds)
    )


async def _get_block(name: str) -> dict[str, Any]:
    await _ensure_seed()
    row = await get_db().fetch_one("SELECT * FROM media_blocks WHERE name = ?", (name,))
    if row is None:
        raise ApiError("not_found", f"Unknown media block '{name}'", 404)
    return row


# --- cameras ---------------------------------------------------------------
@router.get("/cameras")
async def cameras(_: CurrentUser = Depends(require_viewer)) -> dict[str, Any]:
    hal = _media()
    out = []
    for r in await _rows("camera"):
        cfg = json.loads(r["config"])
        out.append(
            {
                "name": r["name"],
                "enabled": bool(r["enabled"]),
                "config": cfg,
                "status": hal.camera_status(r["name"], cfg) if r["enabled"] else {"streaming": False},
            }
        )
    return {"simulated": hal.simulated, "cameras": out}


# --- displays --------------------------------------------------------------
@router.get("/displays")
async def displays(_: CurrentUser = Depends(require_viewer)) -> dict[str, Any]:
    out = []
    for r in await _rows("display"):
        out.append({"name": r["name"], "enabled": bool(r["enabled"]), "config": json.loads(r["config"])})
    return {"simulated": _media().simulated, "displays": out}


# --- SoC codec / compute blocks -------------------------------------------
@router.get("/blocks")
async def blocks(_: CurrentUser = Depends(require_viewer)) -> dict[str, Any]:
    hal = _media()
    out = []
    for r in await _rows("gpu", "decoder", "encoder", "isp", "scaler"):
        out.append(
            {
                "name": r["name"],
                "kind": r["kind"],
                "enabled": bool(r["enabled"]),
                "config": json.loads(r["config"]),
                "status": hal.block_status(r["name"], r["kind"]),
            }
        )
    return {"simulated": hal.simulated, "blocks": out}


# --- enable / configure (shared across cameras, displays, blocks) ----------
@router.post("/{name}/enable")
async def set_enabled(name: str, body: dict[str, Any], user: CurrentUser = Depends(require_operator)) -> dict[str, Any]:
    r = await _get_block(name)
    enabled = 1 if body.get("enabled", True) else 0
    when = body.get("when", "live")
    if when == "next-boot":
        pending_id = await next_boot.arm(
            target=name,
            kind="media_enable",
            payload={"enabled": bool(enabled)},
            username=user.username,
        )
        await audit.record(
            username=user.username,
            ip=user.ip,
            action="media_enable_staged",
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
        "UPDATE media_blocks SET enabled = ?, updated_at = datetime('now') WHERE name = ?", (enabled, name)
    )
    await audit.record(
        username=user.username,
        ip=user.ip,
        action="media_enable",
        target=name,
        old_value=bool(r["enabled"]),
        new_value=bool(enabled),
    )
    return {"name": name, "enabled": bool(enabled)}


@router.post("/{name}/config")
async def configure(name: str, body: dict[str, Any], user: CurrentUser = Depends(require_operator)) -> dict[str, Any]:
    r = await _get_block(name)
    cfg = json.loads(r["config"])
    cfg.update(body or {})
    await get_db().execute(
        "UPDATE media_blocks SET config = ?, updated_at = datetime('now') WHERE name = ?", (json.dumps(cfg), name)
    )
    await audit.record(
        username=user.username, ip=user.ip, action="media_config", target=name, new_value=json.dumps(body)
    )
    return {"name": name, "config": cfg}


# --- ISP tuning (tied to the CSI camera section) ---------------------------
@router.get("/isp")
async def isp(_: CurrentUser = Depends(require_viewer)) -> dict[str, Any]:
    r = await _get_block("isp")
    return {"name": "isp", "config": json.loads(r["config"]), "status": _media().block_status("isp", "isp")}


# --- HVS -------------------------------------------------------------------
@router.get("/hvs")
async def hvs(_: CurrentUser = Depends(require_viewer)) -> dict[str, Any]:
    r = await _get_block("hvs")
    return {"name": "hvs", "config": json.loads(r["config"]), "status": _media().block_status("hvs", "scaler")}


# --- decode / encode jobs --------------------------------------------------
@router.get("/jobs")
async def jobs(_: CurrentUser = Depends(require_viewer)) -> dict[str, Any]:
    return _media().jobs()


@router.post("/jobs/{kind}")
async def start_job(kind: str, body: dict[str, Any], user: CurrentUser = Depends(require_operator)) -> dict[str, Any]:
    if kind not in {"decode", "encode"}:
        raise ApiError("bad_kind", "kind must be 'decode' or 'encode'", 400)
    result = _media().start_job(kind, str(body.get("source", "")), str(body.get("sink", "")))
    await audit.record(username=user.username, ip=user.ip, action=f"media_{kind}_start", target=result.get("job_id"))
    return result


@router.get("/jobs/{job_id}/status")
async def job_status(job_id: str, _: CurrentUser = Depends(require_viewer)) -> dict[str, Any]:
    return _media().job_status(job_id)


@router.delete("/jobs/{job_id}")
async def stop_job(job_id: str, user: CurrentUser = Depends(require_operator)) -> dict[str, Any]:
    result = _media().stop_job(job_id)
    await audit.record(username=user.username, ip=user.ip, action="media_job_stop", target=job_id)
    return result
