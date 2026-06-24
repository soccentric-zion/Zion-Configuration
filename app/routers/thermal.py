"""Group 5 — Thermal & Watchdog."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, Query

from app.auth.deps import CurrentUser, require_admin, require_operator, require_viewer
from app.db import audit
from app.db.database import get_db
from app.hal.registry import get_hal
from app.util.confirm import require_confirm
from app.util.models import WatchdogConfigUpdate, _StrictModel

router = APIRouter(prefix="/api/thermal", tags=["thermal"])


@router.get("/zones")
async def zones(_: CurrentUser = Depends(require_viewer)) -> dict[str, Any]:
    return get_hal("thermal").zones()


@router.get("/fans")
async def fans(_: CurrentUser = Depends(require_viewer)) -> dict[str, Any]:
    return get_hal("thermal").fans()


@router.post("/fans/{name}")
async def set_fan(name: str, body: dict[str, Any], user: CurrentUser = Depends(require_operator)) -> dict[str, Any]:
    result = get_hal("thermal").fan_set(name, int(body.get("pwm", 128)), body.get("mode", "auto"))
    await audit.record(username=user.username, ip=user.ip, action="fan_set", target=name, new_value=result)
    return result


@router.get("/throttling")
async def throttling(_: CurrentUser = Depends(require_viewer)) -> dict[str, Any]:
    return get_hal("thermal").throttling()


@router.get("/trip-points")
async def trip_points(_: CurrentUser = Depends(require_viewer)) -> dict[str, Any]:
    """Per-zone trip points (Features.md §Thermal Trip Points).

    Lifted from the HAL ``zones()`` payload so the SPA can render a
    dedicated "Trip Points" panel without having to filter /zones.
    Each zone exposes its ``trip_high_c`` / ``trip_crit_c`` boundary
    so the operator can see the buffer between current temp and the
    configured limit.
    """
    envelope = get_hal("thermal").zones()
    rows: list[dict[str, Any]] = []
    for z in envelope.get("zones", []):
        rows.append(
            {
                "name": z.get("name"),
                "temp_c": z.get("temp_c"),
                "trip_high_c": z.get("trip_high_c"),
                "trip_crit_c": z.get("trip_crit_c"),
                "cooling_state": z.get("cooling_state"),
            }
        )
    return {"zones": rows, "simulated": envelope.get("simulated", True)}


@router.get("/cooling-policy")
async def get_cooling_policy(_: CurrentUser = Depends(require_viewer)) -> dict[str, Any]:
    """Active cooling policy (Features.md §Thermal Cooling Policy)."""
    return get_hal("thermal").cooling_policy()


class _CoolingPolicySet(_StrictModel):
    policy: Literal["active", "passive", "disabled"]
    confirm: bool


@router.put("/cooling-policy")
async def set_cooling_policy(
    body: _CoolingPolicySet,
    user: CurrentUser = Depends(require_admin),
) -> dict[str, Any]:
    """Set the cooling policy (privileged write, audited)."""
    require_confirm(body.model_dump())
    result = get_hal("thermal").set_cooling_policy(body.policy)
    await audit.record(
        username=user.username,
        ip=user.ip,
        action="cooling_policy_set",
        target="thermal",
        new_value=f"policy={body.policy}:applied={bool(result.get('applied'))}",
    )
    return result


@router.get("/history")
async def thermal_history(
    limit: int = Query(60, ge=1, le=1000),
    _: CurrentUser = Depends(require_viewer),
) -> dict[str, Any]:
    """Last N samples of ``cpu_temp_c`` (Features.md §Thermal History).

    Read from ``metric_history`` where the sampler stores every
    ``history_interval_seconds`` tick. The HAL's live temperature
    remains on ``/zones``; this endpoint powers the sparkline.
    """
    rows = await get_db().fetch_all(
        "SELECT ts, value FROM metric_history WHERE metric = 'cpu_temp_c' " "ORDER BY id DESC LIMIT ?",
        (limit,),
    )
    # Reverse so the SPA renders in chronological order.
    points = [{"ts": r["ts"], "temp_c": float(r["value"])} for r in reversed(rows)]
    return {"metric": "cpu_temp_c", "points": points, "count": len(points)}


_KV_WATCHDOG_PREFIX = "watchdog."


async def _kv_set(key: str, value: str) -> None:
    """Persist a single key/value in ``system_settings_kv`` (#42).

    UPSERT semantics match the helpers in ``app/routers/settings.py``
    and ``app/routers/security.py`` — the helper is duplicated here
    intentionally so a shared module does not couple this router to
    the others' lifecycles.
    """
    await get_db().execute(
        """
        INSERT INTO system_settings_kv (key, value, updated_at)
        VALUES (?, ?, datetime('now'))
        ON CONFLICT(key) DO UPDATE SET
            value = excluded.value,
            updated_at = excluded.updated_at
        """,
        (key, value),
    )


async def _kv_get(key: str) -> str | None:
    row = await get_db().fetch_one("SELECT value FROM system_settings_kv WHERE key = ?", (key,))
    if row is None:
        return None
    val = row["value"]
    return val if isinstance(val, str) else None


@router.get("/watchdog")
async def watchdog(_: CurrentUser = Depends(require_viewer)) -> dict[str, Any]:
    """Watchdog status with operator-set overrides overlaid (audit §1, #129).

    The live HAL envelope is the base; persisted
    ``system_settings_kv`` rows for ``watchdog.timeout_s``,
    ``watchdog.kick_interval_s``, and ``watchdog.nowayout`` override
    the HAL values so the audit log and the next SPA view agree
    (#42). The HAL's ``accessible``/``access_error``/``present``
    fields are preserved so the UI can render a real status (#49).
    """
    envelope = get_hal("thermal").watchdog()
    timeout_s = await _kv_get(f"{_KV_WATCHDOG_PREFIX}timeout_s")
    kick_interval_s = await _kv_get(f"{_KV_WATCHDOG_PREFIX}kick_interval_s")
    nowayout = await _kv_get(f"{_KV_WATCHDOG_PREFIX}nowayout")
    if timeout_s is not None:
        try:
            envelope["timeout_s"] = int(timeout_s)
        except (TypeError, ValueError):
            pass
    if kick_interval_s is not None:
        try:
            envelope["kick_interval_s"] = int(kick_interval_s)
        except (TypeError, ValueError):
            pass
    if nowayout is not None:
        envelope["nowayout"] = nowayout == "1"
    return envelope


@router.put("/watchdog")
async def put_watchdog(
    body: WatchdogConfigUpdate,
    user: CurrentUser = Depends(require_admin),
) -> dict[str, Any]:
    """Persist operator-set watchdog config (audit §1, #129).

    All three fields are written. ``extra="forbid"`` rejects typos.
    The router enforces ``confirm: true`` (privileged write — 428
    without it).
    """
    require_confirm(body.model_dump())
    written: list[str] = []
    await _kv_set(f"{_KV_WATCHDOG_PREFIX}timeout_s", str(body.timeout_s))
    written.append(f"timeout_s={body.timeout_s}")
    await _kv_set(f"{_KV_WATCHDOG_PREFIX}kick_interval_s", str(body.kick_interval_s))
    written.append(f"kick_interval_s={body.kick_interval_s}")
    await _kv_set(f"{_KV_WATCHDOG_PREFIX}nowayout", "1" if body.nowayout else "0")
    written.append(f"nowayout={body.nowayout}")
    await audit.record(
        username=user.username,
        ip=user.ip,
        action="watchdog_config_update",
        target="watchdog",
        new_value=",".join(written),
    )
    return {"ok": True, "applied": written}


@router.post("/watchdog/test")
async def watchdog_test(
    body: dict[str, Any] | None = None,
    user: CurrentUser = Depends(require_admin),
) -> dict[str, bool]:
    # Lockout-capable: firing the watchdog in production is a no-go without
    # explicit UI confirmation, so we now require it server-side too.
    require_confirm(body)
    await audit.record(username=user.username, ip=user.ip, action="watchdog_test_fire", target="watchdog")
    return {"ok": True, "armed": True}
