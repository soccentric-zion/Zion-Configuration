"""Cross-cutting meta endpoints: device summary, simulation map, history."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import Field

from app.auth.deps import CurrentUser, require_admin, require_viewer
from app.config import get_capabilities, get_settings
from app.db import audit
from app.db.database import get_db
from app.hal.registry import any_simulated, get_hal, simulated_map
from app.tasks import apply_state
from app.util.models import _StrictModel

router = APIRouter(prefix="/api/system", tags=["system"])


@router.get("/summary")
async def summary(_: CurrentUser = Depends(require_viewer)) -> dict[str, Any]:
    identity = get_hal("system").identity()
    return {
        "device_name": identity["model"],
        "serial": identity["serial_number"],
        "any_simulated": any_simulated(),
        "simulated": simulated_map(),
        "version": identity["firmware_version"],
    }


@router.get("/simulation")
async def simulation(_: CurrentUser = Depends(require_viewer)) -> dict[str, Any]:
    return {"any_simulated": any_simulated(), "modules": simulated_map()}


@router.get("/protected-resources")
async def protected_resources(_: CurrentUser = Depends(require_viewer)) -> dict[str, Any]:
    """List the active protection sets (audit Guardrail 3, #90).

    The SPA renders this as a "Read-only" panel so operators know which
    resources cannot be changed from the dashboard. The actual enforcement
    is in ``app/util/protected.py::assert_not_protected``; this endpoint
    is informational.
    """
    from app.util.protected import list_protected

    return list_protected()


# Cap the history `limit` so a request can't ask for 10M rows (#39).
# Metric history is a chart, 10k points is more than any dashboard can
# render at once.
_HISTORY_MAX_LIMIT = 10_000


@router.get("/history")
async def history(
    metric: str,
    limit: int = Query(300, ge=1, le=_HISTORY_MAX_LIMIT),
    _: CurrentUser = Depends(require_viewer),
) -> dict[str, Any]:
    rows = await get_db().fetch_all(
        """SELECT ts, value FROM metric_history WHERE metric = ?
           ORDER BY ts DESC, id DESC LIMIT ?""",
        (metric, limit),
    )
    return {"metric": metric, "points": list(reversed(rows))}


@router.get("/config-flags")
async def config_flags(_: CurrentUser = Depends(require_viewer)) -> dict[str, Any]:
    cfg = get_settings()
    return {
        "local_vendor": cfg.server.local_vendor,
        "metrics_interval_s": cfg.sampling.metrics_interval_seconds,
    }


# --- capability manifest (audit Config, #116) ------------------------------
# The dashboard reads this once on shell init and uses it to (a) filter
# the sidebar nav and (b) gate the router (any route whose module is
# `enabled: false` redirects to /overview). The endpoint is viewer-readable
# because every page already trusts the SPA to render — and the SPA still
# re-checks before showing the user anything.
@router.get("/capabilities")
async def capabilities(_: CurrentUser = Depends(require_viewer)) -> dict[str, Any]:
    return get_capabilities()


# --- feature tree (Features.md §1) -----------------------------------------
# The resolved per-sub-feature tree lives at ``/api/config/features`` per
# the spec, mounted by ``app/routers/config_meta.py``. Keep the surface
# here for the rest of the meta endpoints only.


@router.post("/health/heartbeat")
async def health_heartbeat(
    body: dict[str, Any] | None = None,
    user: CurrentUser = Depends(require_admin),
) -> dict[str, Any]:
    """Record that this device is healthy with the current config (#93).

    Called by the SPA after a reboot / network change / OTA-switch to
    tell the apply-plan watchdog "I'm back, keep my changes." Without
    this, a successful reboot with a slow SPA reconnect was being
    auto-rolled back by the watchdog's pure timer (audit Guardrail 2).
    The watchdog consults ``system_heartbeat.last_seen_at`` and
    auto-commits armed plans when the heartbeat is fresh.
    """
    source = (body or {}).get("source")
    result = await apply_state.record_heartbeat(source=source)
    await audit.record(
        username=user.username,
        ip=user.ip,
        action="health_heartbeat",
        target="system",
        new_value=source,
    )
    return {"ok": True, **result}


@router.get("/health/heartbeat")
async def health_heartbeat_get(_: CurrentUser = Depends(require_viewer)) -> dict[str, Any]:
    """Read the last recorded heartbeat (operator-visible status)."""
    row = await apply_state.last_heartbeat()
    if row is None:
        return {"recorded": False, "last_seen_at": None, "source": None}
    return {"recorded": True, **row}


# --- system thresholds (#106, audit §7) ------------------------------------
# Audit-state-alignment (#42): operator-editable thresholds live in
# ``system_settings_kv``; the static ``ThresholdsConfig`` provides the
# safe default. The effective value returned by ``GET`` is the persisted
# override if present, otherwise the static default. This way the audit
# row reflects a real state change and a fresh install still ships with
# sensible defaults.

_THRESHOLD_KEYS = (
    "thresholds.cpu_temp_warn_c",
    "thresholds.cpu_temp_crit_c",
    "thresholds.disk_usage_warn_pct",
    "thresholds.mem_usage_warn_pct",
)


async def _kv_get(key: str) -> str | None:
    row = await get_db().fetch_one("SELECT value FROM system_settings_kv WHERE key = ?", (key,))
    if row is None:
        return None
    val = row["value"]
    return val if isinstance(val, str) else None


async def _kv_set(key: str, value: str) -> None:
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


async def _effective_thresholds() -> dict[str, float]:
    cfg = get_settings().thresholds
    persisted = {
        "thresholds.cpu_temp_warn_c": await _kv_get("thresholds.cpu_temp_warn_c"),
        "thresholds.cpu_temp_crit_c": await _kv_get("thresholds.cpu_temp_crit_c"),
        "thresholds.disk_usage_warn_pct": await _kv_get("thresholds.disk_usage_warn_pct"),
        "thresholds.mem_usage_warn_pct": await _kv_get("thresholds.mem_usage_warn_pct"),
    }
    return {
        "cpu_temp_warn_c": float(persisted["thresholds.cpu_temp_warn_c"] or cfg.cpu_temp_warn_c),
        "cpu_temp_crit_c": float(persisted["thresholds.cpu_temp_crit_c"] or cfg.cpu_temp_crit_c),
        "disk_usage_warn_pct": float(persisted["thresholds.disk_usage_warn_pct"] or cfg.disk_usage_warn_pct),
        "mem_usage_warn_pct": float(persisted["thresholds.mem_usage_warn_pct"] or cfg.mem_usage_warn_pct),
    }


@router.get("/thresholds")
async def thresholds(_: CurrentUser = Depends(require_viewer)) -> dict[str, Any]:
    """Return the effective system thresholds (override → default)."""
    return await _effective_thresholds()


class _ThresholdsUpdate(_StrictModel):
    """Operator-editable thresholds. None means "leave unchanged"."""

    cpu_temp_warn_c: float | None = Field(default=None, ge=0, le=120)
    cpu_temp_crit_c: float | None = Field(default=None, ge=0, le=120)
    disk_usage_warn_pct: float | None = Field(default=None, ge=0, le=100)
    mem_usage_warn_pct: float | None = Field(default=None, ge=0, le=100)


@router.put("/thresholds")
async def update_thresholds(body: _ThresholdsUpdate, user: CurrentUser = Depends(require_admin)) -> dict[str, Any]:
    """Persist operator-set thresholds in ``system_settings_kv`` and
    audit the change (#42, #106). None-valued fields are left at
    their previous value."""
    changes: list[tuple[str, float]] = []
    for field, kv_key in (
        ("cpu_temp_warn_c", "thresholds.cpu_temp_warn_c"),
        ("cpu_temp_crit_c", "thresholds.cpu_temp_crit_c"),
        ("disk_usage_warn_pct", "thresholds.disk_usage_warn_pct"),
        ("mem_usage_warn_pct", "thresholds.mem_usage_warn_pct"),
    ):
        value = getattr(body, field)
        if value is None:
            continue
        old = (await _kv_get(kv_key)) or ""
        await _kv_set(kv_key, str(value))
        changes.append((kv_key, float(value)))
        await audit.record(
            username=user.username,
            ip=user.ip,
            action="threshold_update",
            target=kv_key,
            old_value=old,
            new_value=str(value),
        )
    return {"ok": True, "changed": [k for k, _ in changes], "thresholds": await _effective_thresholds()}
