"""Group 9 — Cloud & Connectivity: provisioning, telemetry, alerts."""

from __future__ import annotations

import secrets
from typing import Any

from fastapi import APIRouter, Depends

from app.auth.deps import CurrentUser, require_admin, require_operator, require_viewer
from app.db import audit
from app.db.database import get_db
from app.errors import ApiError
from app.util.confirm import require_confirm
from app.util.models import AlertSnooze

router = APIRouter(prefix="/api/cloud", tags=["cloud"])

_VALID_TRANSITIONS = {
    "unprovisioned": "claimed",
    "claimed": "registered",
    "registered": "active",
}


async def _ensure_provisioning_row() -> dict[str, Any]:
    db = get_db()
    row = await db.fetch_one("SELECT * FROM provisioning WHERE id = 1")
    if row is None:
        await db.execute("INSERT INTO provisioning (id, state) VALUES (1, 'unprovisioned')")
        row = await db.fetch_one("SELECT * FROM provisioning WHERE id = 1")
    assert row is not None
    return row


# --- provisioning ----------------------------------------------------------
@router.get("/provisioning")
async def provisioning(_: CurrentUser = Depends(require_viewer)) -> dict[str, Any]:
    return {"provisioning": await _ensure_provisioning_row()}


@router.post("/provisioning/advance")
async def advance_state(user: CurrentUser = Depends(require_admin)) -> dict[str, Any]:
    row = await _ensure_provisioning_row()
    current = row["state"]
    nxt = _VALID_TRANSITIONS.get(current)
    if nxt is None:
        raise ApiError("invalid_transition", f"No transition from '{current}'", 400)
    await get_db().execute("UPDATE provisioning SET state = ?, updated_at = datetime('now') WHERE id = 1", (nxt,))
    await audit.record(
        username=user.username,
        ip=user.ip,
        action="provisioning_advance",
        target="state",
        old_value=current,
        new_value=nxt,
    )
    return {"state": nxt}


@router.post("/provisioning/endpoint")
async def set_endpoint(body: dict[str, Any], user: CurrentUser = Depends(require_admin)) -> dict[str, Any]:
    endpoint = str(body.get("endpoint", "") or "").strip()
    if not endpoint:
        raise ApiError("missing_field", "endpoint is required", 400)
    if not (endpoint.startswith("http://") or endpoint.startswith("https://")):
        raise ApiError("bad_endpoint", "endpoint must be an http(s):// URL", 400)
    # _ensure_provisioning_row first: a fresh DB has no row, and a bare
    # UPDATE WHERE id=1 would silently affect 0 rows. Same pattern as
    # advance_state / claim_token.
    await _ensure_provisioning_row()
    await get_db().execute("UPDATE provisioning SET endpoint = ? WHERE id = 1", (endpoint,))
    # Mirror the write to system_settings_kv so the System SPA can read
    # the same value via /api/settings/system/ota-endpoint without
    # re-querying the provisioning row (#124, audit §1 + §8). The KV
    # row is the canonical "operator override" — the provisioning row
    # is the lifecycle mirror.
    await get_db().execute(
        """
        INSERT INTO system_settings_kv (key, value, updated_at)
        VALUES (?, ?, datetime('now'))
        ON CONFLICT(key) DO UPDATE SET
            value = excluded.value,
            updated_at = excluded.updated_at
        """,
        ("ota.endpoint", endpoint),
    )
    await audit.record(username=user.username, ip=user.ip, action="cloud_endpoint_set", new_value=endpoint)
    return {"ok": True, "endpoint": endpoint}


@router.post("/provisioning/claim-token")
async def claim_token(user: CurrentUser = Depends(require_admin)) -> dict[str, str]:
    token = secrets.token_urlsafe(24)
    await get_db().execute("UPDATE provisioning SET claim_token = ? WHERE id = 1", (token,))
    await audit.record(username=user.username, ip=user.ip, action="claim_token_generate")
    return {"claim_token": token}


@router.get("/twin")
async def device_twin(_: CurrentUser = Depends(require_viewer)) -> dict[str, Any]:
    return {
        "simulated": True,
        "reported": {"firmware": "1.4.2", "online": True},
        "desired": {"firmware": "1.4.2", "telemetry_interval": 60},
    }


# --- telemetry -------------------------------------------------------------
@router.get("/telemetry")
async def telemetry(_: CurrentUser = Depends(require_viewer)) -> dict[str, Any]:
    db = get_db()
    depth_row = await db.fetch_one("SELECT COUNT(*) AS n FROM telemetry_queue WHERE sent = 0")
    sent_row = await db.fetch_one("SELECT COUNT(*) AS n FROM telemetry_queue WHERE sent = 1")
    last_row = await db.fetch_one("SELECT ts FROM telemetry_queue ORDER BY id DESC LIMIT 1")
    return {
        "catalog": [
            {"metric": "cpu_total", "enabled": True, "interval_s": 60},
            {"metric": "mem_percent", "enabled": True, "interval_s": 60},
            {"metric": "cpu_temp_c", "enabled": True, "interval_s": 30},
        ],
        "queue_depth": depth_row["n"] if depth_row else 0,
        "sent_total": sent_row["n"] if sent_row else 0,
        "last_sync": last_row["ts"] if last_row else None,
        "bandwidth_today_kb": 0,
        "retention_days": 7,
    }


@router.post("/telemetry/flush", status_code=200)
async def flush_telemetry(user: CurrentUser = Depends(require_admin)) -> dict[str, Any]:
    """Mark every queued telemetry row as sent.

    In simulation there is no real cloud endpoint to push to — instead
    the operator (or a cron-driven test) flips the rows so the queue
    depth goes back to zero (#44). On a real device the background
    pusher would do this after the cloud POST succeeds.
    """
    db = get_db()
    depth_row = await db.fetch_one("SELECT COUNT(*) AS n FROM telemetry_queue WHERE sent = 0")
    depth = depth_row["n"] if depth_row else 0
    if depth:
        await db.execute("UPDATE telemetry_queue SET sent = 1 WHERE sent = 0")
    await audit.record(
        username=user.username,
        ip=user.ip,
        action="telemetry_flush",
        target="queue",
        new_value=str(depth),
    )
    return {"ok": True, "flushed": depth}


@router.post("/telemetry/{metric}")
async def toggle_metric(
    metric: str, body: dict[str, Any], user: CurrentUser = Depends(require_operator)
) -> dict[str, bool]:
    await audit.record(
        username=user.username,
        ip=user.ip,
        action="telemetry_toggle",
        target=metric,
        new_value=body.get("enabled"),
    )
    return {"ok": True}


# --- alerts ----------------------------------------------------------------
@router.get("/alerts/rules")
async def alert_rules(_: CurrentUser = Depends(require_viewer)) -> dict[str, Any]:
    rows = await get_db().fetch_all("SELECT * FROM alert_rules ORDER BY id")
    return {"rules": rows}


@router.post("/alerts/rules", status_code=201)
async def create_rule(body: dict[str, Any], user: CurrentUser = Depends(require_operator)) -> dict[str, Any]:
    if body.get("condition") not in {">", "<", "==", "rate"}:
        raise ApiError("bad_condition", "condition must be one of > < == rate", 400)
    rid = await get_db().execute(
        """INSERT INTO alert_rules (name, metric, condition, threshold, duration_s, severity, enabled)
           VALUES (?, ?, ?, ?, ?, ?, 1)""",
        (
            body["name"],
            body["metric"],
            body["condition"],
            float(body["threshold"]),
            int(body.get("duration_s", 0)),
            body.get("severity", "warning"),
        ),
    )
    await audit.record(username=user.username, ip=user.ip, action="alert_rule_create", target=body["name"])
    return {"id": rid}


@router.delete("/alerts/rules/{rule_id}")
async def delete_rule(
    rule_id: int,
    body: dict[str, Any] | None = None,
    user: CurrentUser = Depends(require_operator),
) -> dict[str, bool]:
    # Operator-level destructive: enforce explicit body confirm (#37).
    require_confirm(body)
    await get_db().execute("DELETE FROM alert_rules WHERE id = ?", (rule_id,))
    await audit.record(username=user.username, ip=user.ip, action="alert_rule_delete", target=str(rule_id))
    return {"ok": True}


@router.get("/alerts")
async def alerts(_: CurrentUser = Depends(require_viewer)) -> dict[str, Any]:
    rows = await get_db().fetch_all("SELECT * FROM alerts ORDER BY fired_at DESC LIMIT 100")
    unread = sum(1 for r in rows if r["state"] == "firing" and not r["acked_at"])
    return {"alerts": rows, "unread": unread}


@router.post("/alerts/{alert_id}/ack")
async def ack_alert(alert_id: int, user: CurrentUser = Depends(require_operator)) -> dict[str, bool]:
    await get_db().execute(
        "UPDATE alerts SET state='acknowledged', acked_at=datetime('now'), acked_by=? WHERE id=?",
        (user.username, alert_id),
    )
    await audit.record(username=user.username, ip=user.ip, action="alert_ack", target=str(alert_id))
    return {"ok": True}


@router.post("/alerts/{alert_id}/snooze")
async def snooze_alert(
    alert_id: int, body: AlertSnooze, user: CurrentUser = Depends(require_operator)
) -> dict[str, bool]:
    hours = body.hours
    await get_db().execute(
        "UPDATE alerts SET snooze_until = datetime('now', ?) WHERE id = ?",
        (f"+{hours} hours", alert_id),
    )
    await audit.record(
        username=user.username, ip=user.ip, action="alert_snooze", target=str(alert_id), new_value=f"{hours}h"
    )
    return {"ok": True}
