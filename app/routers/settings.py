"""Group 10 — Settings: system, network, localization, backup/restore."""

from __future__ import annotations

import io
import json
import platform
from typing import Any

from fastapi import APIRouter, Depends, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import field_validator

from app.auth.deps import CurrentUser, require_admin, require_viewer
from app.config import get_settings
from app.db import audit
from app.db.database import get_db
from app.hal.registry import get_hal
from app.tasks import apply_state, backup
from app.util.confirm import require_confirm
from app.util.models import (
    KNOWN_LOCALES,
    AppearanceUpdate,
    LocaleUpdate,
    OtaEndpointUpdate,
    RebootBody,
    ShutdownBody,
    StaticNetworkConfig,
    UiPrefsUpdate,
    _StrictModel,
)

router = APIRouter(prefix="/api/settings", tags=["settings"])


_NET_DEFAULTS = [
    {"name": "eth0", "method": "dhcp"},
    {"name": "wlan0", "method": "dhcp"},
]

# system_settings_kv: keys we persist operator-set values for. Used by
# set_hostname / set_timezone so an audit log entry corresponds to an
# actual state change (#42).
_KV_HOSTNAME = "hostname"
_KV_TIMEZONE = "timezone"
_KV_OTA_ENDPOINT = "ota.endpoint"
# Features.md Settings → "Hostname, timezone, NTP, time sync, locale,
# keyboard layout, reboot, shutdown". Operator-set values live in KV so
# backup/restore and the audit trail are consistent with the other
# system settings.
_KV_NTP_ENABLED = "ntp.enabled"
_KV_NTP_SERVERS = "ntp.servers"
_KV_KEYBOARD_LAYOUT = "keyboard.layout"
# Features.md Settings → "Bind address/port, allowed origins (CORS),
# behind-proxy settings". The bind address + port are fixed at boot and
# cannot be hot-applied (the server has to be re-launched to bind a new
# socket); the editable knobs are the proxy / CORS flags. Operator-set
# values are persisted in KV so backup/restore and audit pick them up.
_KV_NET_BIND_ADDRESS = "network.bind_address"
_KV_NET_ALLOWED_ORIGINS = "network.allowed_origins"
_KV_NET_BEHIND_PROXY = "network.behind_proxy"
_KV_NET_TRUSTED_PROXIES = "network.trusted_proxies"
# Features.md Settings → "Auth method (local/PAM/LDAP/SSO)". The
# dashboard ships with a local credential store; PAM / LDAP / SSO are
# declared so the SPA can render the picker and the operator can
# pre-select a method, but only ``local`` is wired through. The KV
# record is the operator's recorded intent — useful as a display
# value and as a single source of truth for backup/restore.
_KV_AUTH_METHOD = "auth.method"


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


async def _ensure_net_seed() -> None:
    db = get_db()
    row = await db.fetch_one("SELECT COUNT(*) AS n FROM net_interfaces")
    if row and row["n"]:
        return
    for d in _NET_DEFAULTS:
        await db.execute(
            "INSERT OR IGNORE INTO net_interfaces (name, method, up) VALUES (?, ?, 1)",
            (d["name"], d["method"]),
        )


@router.get("/system")
async def system_settings(_: CurrentUser = Depends(require_viewer)) -> dict[str, Any]:
    import time as _time

    # The operator may have set a custom hostname/timezone via the
    # /hostname and /timezone endpoints. Persisted values win over the
    # live OS reading so the audit log and the next SPA view agree (#42).
    # A fresh install sees the real OS values until it overrides.
    persisted_hostname = await _kv_get(_KV_HOSTNAME)
    persisted_timezone = await _kv_get(_KV_TIMEZONE)
    ntp_enabled_raw = await _kv_get(_KV_NTP_ENABLED)
    ntp_servers_raw = await _kv_get(_KV_NTP_SERVERS)
    if ntp_servers_raw:
        ntp_servers = [s.strip() for s in ntp_servers_raw.split(",") if s.strip()]
    else:
        # Default pool list (Features.md Settings). Stable seed so the
        # SPA renders sensibly on a fresh install.
        ntp_servers = ["pool.ntp.org", "time.google.com"]
    ntp_enabled = (ntp_enabled_raw != "false") if ntp_enabled_raw is not None else True
    return {
        "hostname": persisted_hostname or platform.node(),
        "timezone": persisted_timezone or _time.tzname[0],
        "ntp_servers": ntp_servers,
        "ntp_enabled": ntp_enabled,
        "keyboard_layout": await _kv_get(_KV_KEYBOARD_LAYOUT) or "",
        "db_size_bytes": await get_db().db_size_bytes(),
    }


@router.post("/hostname")
async def set_hostname(body: dict[str, str], user: CurrentUser = Depends(require_admin)) -> dict[str, bool]:
    new_value = body.get("hostname", "").strip()
    if not new_value:
        from app.errors import ApiError

        raise ApiError("invalid_hostname", "hostname must be a non-empty string", 422)
    old_value = await _kv_get(_KV_HOSTNAME) or platform.node()
    await _kv_set(_KV_HOSTNAME, new_value)
    await audit.record(
        username=user.username,
        ip=user.ip,
        action="set_hostname",
        old_value=old_value,
        new_value=new_value,
    )
    return {"ok": True, "applied": True}


@router.post("/timezone")
async def set_timezone(body: dict[str, str], user: CurrentUser = Depends(require_admin)) -> dict[str, bool]:
    new_value = body.get("timezone", "").strip()
    if not new_value:
        from app.errors import ApiError

        raise ApiError("invalid_timezone", "timezone must be a non-empty string", 422)
    # Validate the timezone is one Python knows about. Catches typos early
    # rather than persisting a bogus value that later breaks log timestamps.
    try:
        import zoneinfo

        if new_value not in zoneinfo.available_timezones():
            from app.errors import ApiError

            raise ApiError("invalid_timezone", f"unknown timezone: {new_value}", 422)
    except ApiError:
        raise
    except Exception:  # pragma: no cover  - zoneinfo missing on very old Pythons
        pass
    old_value = await _kv_get(_KV_TIMEZONE)
    await _kv_set(_KV_TIMEZONE, new_value)
    await audit.record(
        username=user.username,
        ip=user.ip,
        action="set_timezone",
        old_value=old_value,
        new_value=new_value,
    )
    return {"ok": True, "applied": True}


@router.get("/timezones")
async def timezones(_: CurrentUser = Depends(require_viewer)) -> dict[str, Any]:
    try:
        import zoneinfo

        zones = sorted(zoneinfo.available_timezones())
    except Exception:  # pragma: no cover
        zones = ["UTC", "Europe/Berlin", "America/New_York", "Asia/Shanghai"]
    return {"timezones": zones}


# --- NTP + keyboard layout (Features.md Settings) -------------------------
# operator-set NTP config and keyboard layout live in system_settings_kv
# (same store as hostname / timezone). A backup / restore picks them up
# automatically. These are presentation / panel-config changes, not
# privileged device writes — no apply_state, no `confirm` body.


@router.put("/ntp")
async def set_ntp(body: dict[str, Any], user: CurrentUser = Depends(require_admin)) -> dict[str, Any]:
    """Persist NTP enable flag + server list.

    Body shape: ``{"enabled": bool, "servers": ["pool.ntp.org", ...]}``.
    Both keys optional; a partial PUT updates only what's present.
    Each server is a hostname (no scheme, no path) — ``address[:port]``
    is accepted so an NTS-capable pool can be addressed.
    """
    from app.errors import ApiError

    written: list[str] = []
    if "enabled" in body:
        if not isinstance(body["enabled"], bool):
            raise ApiError("invalid_ntp_enabled", "'enabled' must be a boolean", 422)
        await _kv_set(_KV_NTP_ENABLED, "true" if body["enabled"] else "false")
        written.append(f"enabled={body['enabled']}")
    if "servers" in body:
        servers = body["servers"]
        if not isinstance(servers, list):
            raise ApiError("invalid_ntp_servers", "'servers' must be a list", 422)
        if not servers:
            raise ApiError("empty_ntp_servers", "at least one NTP server is required", 422)
        import re as _re

        host_re = _re.compile(r"^[A-Za-z0-9._\-]+(:\d{1,5})?$")
        cleaned: list[str] = []
        for s in servers:
            if not isinstance(s, str) or not host_re.match(s):
                raise ApiError("invalid_ntp_server", f"invalid NTP server: {s!r}", 422)
            cleaned.append(s)
        await _kv_set(_KV_NTP_SERVERS, ",".join(cleaned))
        written.append(f"servers={','.join(cleaned)}")
    await audit.record(
        username=user.username,
        ip=user.ip,
        action="set_ntp",
        target="system",
        new_value=",".join(written) if written else None,
    )
    return {"ok": True, "applied": written}


@router.put("/keyboard-layout")
async def set_keyboard_layout(body: dict[str, str], user: CurrentUser = Depends(require_admin)) -> dict[str, Any]:
    """Persist operator-chosen keyboard layout.

    Body: ``{"layout": "us"}`` (or any other XKB / Windows layout
    code). Stored in KV; a backup / restore picks it up. Not a
    privileged device write — the dashboard itself does not apply
    the layout (the SPA's <kbd> picker is a hint for the operator's
    browser, not a daemon) — so no ``confirm`` and no apply_state.
    """
    from app.errors import ApiError

    layout = body.get("layout", "").strip()
    if not layout:
        raise ApiError("invalid_keyboard_layout", "layout must be a non-empty string", 422)
    if len(layout) > 64:
        raise ApiError("invalid_keyboard_layout", "layout must be <= 64 chars", 422)
    await _kv_set(_KV_KEYBOARD_LAYOUT, layout)
    await audit.record(
        username=user.username,
        ip=user.ip,
        action="set_keyboard_layout",
        target="system",
        new_value=layout,
    )
    return {"ok": True, "layout": layout}


# --- time sync (Features.md §Settings System) ------------------------------
# Operator-triggered NTP re-sync. Real mode shells out to chronyc /
# systemd-timesyncd; simulated mode returns the HAL "would-apply"
# envelope (#42). Admin-only; not a 60s auto-rollback candidate —
# there is no meaningful "before" state to roll back to.


@router.get("/time/status")
async def time_status(_: CurrentUser = Depends(require_viewer)) -> dict[str, Any]:
    """Current NTP sync status (read-only)."""
    return get_hal("system").time_status()


@router.post("/time/sync")
async def time_sync(user: CurrentUser = Depends(require_admin)) -> dict[str, Any]:
    """Force a one-shot NTP re-sync (privileged write, audited).

    Body is empty — there's no parameter the operator can set; the HAL
    invokes the system time daemon with sensible defaults.
    """
    result = get_hal("system").time_sync()
    await audit.record(
        username=user.username,
        ip=user.ip,
        action="time_sync",
        target="system",
        new_value=(f"applied={bool(result.get('applied'))}" f":source={result.get('source', 'unknown')}"),
    )
    return result


# --- OTA endpoint (audit §1, §8, #124) ------------------------------------
# audit §1 System panel expects the OTA endpoint URL under
# /api/settings/system; the canonical write path is also mirrored from
# /api/cloud/provisioning/endpoint. The KV row is the operator-override
# source of truth (#42); the provisioning row is the lifecycle mirror
# that the cloud provisioning flow expects to read.


async def _read_provisioning_endpoint() -> str | None:
    """Read ``provisioning.endpoint`` (id=1) for fallback.

    The provisioning row may be absent on a fresh DB; ``_ensure`` is
    too heavy for a read path so we just return None in that case —
    the SPA can show "not configured".
    """
    row = await get_db().fetch_one("SELECT endpoint FROM provisioning WHERE id = 1")
    if row is None:
        return None
    val = row["endpoint"]
    return val if isinstance(val, str) and val else None


@router.get("/system/ota-endpoint")
async def get_ota_endpoint(_: CurrentUser = Depends(require_viewer)) -> dict[str, Any]:
    """Active OTA endpoint URL (audit §1, §8, #124).

    Prefers the operator-override stored in ``system_settings_kv``;
    falls back to the lifecycle mirror in ``provisioning.endpoint``
    so a legacy write through the cloud endpoint is still honored.
    """
    persisted = await _kv_get(_KV_OTA_ENDPOINT)
    if persisted:
        return {"endpoint": persisted, "persistent": True}
    legacy = await _read_provisioning_endpoint()
    if legacy:
        return {"endpoint": legacy, "persistent": True}
    return {"endpoint": None, "persistent": True}


@router.put("/system/ota-endpoint")
async def put_ota_endpoint(body: OtaEndpointUpdate, user: CurrentUser = Depends(require_admin)) -> dict[str, Any]:
    """Persist the OTA endpoint URL (audit §1, §8, #124).

    Body validated by the ``OtaEndpointUpdate`` pydantic model — bad
    URLs (non-http(s)://, oversize) return 422 via the global
    validation handler. The KV write is the operator-override
    source of truth; the provisioning row is mirrored so the cloud
    provisioning flow still reads the same value.
    """
    require_confirm(body.model_dump())
    old_value = await _kv_get(_KV_OTA_ENDPOINT) or (await _read_provisioning_endpoint())
    db = get_db()
    # Ensure the provisioning row exists so the mirror write cannot
    # silently affect 0 rows on a fresh DB.
    await db.execute("INSERT OR IGNORE INTO provisioning (id, state) VALUES (1, 'unprovisioned')")
    await db.execute(
        "UPDATE provisioning SET endpoint = ?, updated_at = datetime('now') WHERE id = 1",
        (body.endpoint,),
    )
    await _kv_set(_KV_OTA_ENDPOINT, body.endpoint)
    await audit.record(
        username=user.username,
        ip=user.ip,
        action="set_ota_endpoint",
        target="ota",
        old_value=old_value,
        new_value=body.endpoint,
    )
    return {"ok": True, "endpoint": body.endpoint, "applied": True}


@router.post("/network/{iface}")
async def set_network(
    iface: str, body: StaticNetworkConfig, user: CurrentUser = Depends(require_admin)
) -> dict[str, Any]:
    """Apply a static network config and arm a rollback plan.

    The plan expires in ``body.rollback_armed_s`` (default 60, max 600)
    unless the SPA confirms it via
    ``POST /api/settings/network/{iface}/apply/confirm``, in which case
    the change is committed. The watchdog ``apply_watchdog`` auto-rolls
    back any plan whose window elapses.

    The body is validated by the ``StaticNetworkConfig`` pydantic model:
    bad IP/netmask/method/rollback-window return 422 via FastAPI's
    built-in validation handler, which the global
    ``app.errors.validation_exception_handler`` wraps.
    """
    require_confirm({"confirm": body.confirm})
    await _ensure_net_seed()
    row = await get_db().fetch_one("SELECT * FROM net_interfaces WHERE name = ?", (iface,))
    if row is None:
        from app.errors import ApiError

        raise ApiError("not_found", f"Unknown interface '{iface}'", 404)
    # Static mode: address + netmask required and parsed.
    if body.method == "static" and (body.address is None or body.netmask is None):
        from app.errors import ApiError

        raise ApiError("missing_field", "static mode requires address and netmask", 400)
    method = body.method
    address = body.address or row["address"]
    netmask = body.netmask or row["netmask"]
    gateway = body.gateway or row["gateway"]
    dns = body.dns or row["dns"]
    # Snapshot the pre-apply state for the rollback plan.
    before = {
        "name": row["name"],
        "method": row["method"],
        "address": row["address"],
        "netmask": row["netmask"],
        "gateway": row["gateway"],
        "dns": row["dns"],
        "up": bool(row["up"]),
    }
    # Apply the change.
    await get_db().execute(
        """UPDATE net_interfaces
           SET method=?, address=?, netmask=?, gateway=?, dns=?, updated_at=datetime('now')
           WHERE name=?""",
        (method, address, netmask, gateway, dns, iface),
    )
    plan = await apply_state.arm(
        target=f"network:{iface}",
        before=before,
        after={
            "name": iface,
            "method": method,
            "address": address,
            "netmask": netmask,
            "gateway": gateway,
            "dns": dns,
            "up": before["up"],
        },
        rollback_kind="net_interfaces",
        rollback_armed_s=body.rollback_armed_s,
    )
    await audit.record(
        username=user.username,
        ip=user.ip,
        action="set_network",
        target=iface,
        new_value=body.model_dump_json(),
    )
    return {
        "ok": True,
        "plan_id": plan["plan_id"],
        "armed_until": plan["armed_until"],
        "rollback_armed_s": plan["rollback_armed_s"],
    }


@router.post("/network/{iface}/apply/confirm")
async def set_network_confirm(
    iface: str, body: dict[str, Any], user: CurrentUser = Depends(require_admin)
) -> dict[str, Any]:
    """Commit a previously-armed set_network plan (dashboard reachable with new config)."""
    require_confirm(body)
    plan_id = int(body.get("plan_id", 0))
    await apply_state.commit(plan_id)
    await audit.record(
        username=user.username, ip=user.ip, action="set_network_commit", target=iface, new_value=str(plan_id)
    )
    return {"ok": True, "plan_id": plan_id, "committed": True}


@router.post("/network/{iface}/apply/rollback")
async def set_network_rollback(
    iface: str, body: dict[str, Any], user: CurrentUser = Depends(require_admin)
) -> dict[str, Any]:
    """Manually rollback a set_network plan (e.g. user noticed the new config is bad)."""
    require_confirm(body)
    plan_id = int(body.get("plan_id", 0))
    # apply_state.rollback now records the audit entry itself with the
    # triggering user, so we no longer need a duplicate set_network_rollback
    # row here (#48).
    await apply_state.rollback(plan_id, reason="manual", username=user.username, ip=user.ip)
    return {"ok": True, "plan_id": plan_id, "rolled_back": True}


@router.post("/reboot")
async def reboot(body: RebootBody | None = None, user: CurrentUser = Depends(require_admin)) -> dict[str, bool]:
    require_confirm(body.model_dump() if body is not None else None)
    await audit.record(username=user.username, ip=user.ip, action="reboot", target="system")
    return {"ok": True}


@router.post("/shutdown")
async def shutdown(body: ShutdownBody | None = None, user: CurrentUser = Depends(require_admin)) -> dict[str, bool]:
    require_confirm(body.model_dump() if body is not None else None)
    await audit.record(username=user.username, ip=user.ip, action="shutdown", target="system")
    return {"ok": True}


@router.post("/factory-reset")
async def factory_reset(body: dict[str, str], user: CurrentUser = Depends(require_admin)) -> dict[str, Any]:
    # factory_reset uses typed hostname confirmation (stronger than a boolean).
    if body.get("confirm_hostname") != platform.node():
        from app.errors import ApiError

        raise ApiError("confirm_mismatch", "Hostname confirmation did not match", 400)
    # Wipe all user-configurable data tables. Preserved: users (admin must
    # re-seed) and audit_log (forensic record survives the reset).
    db = get_db()
    reset_tables = (
        "alert_rules",
        "restore_points",
        "ota_history",
        "firewall_rules",
        "media_blocks",
        "provisioning",
        "hw_interfaces",
        "devices",
    )
    # Snapshot the system_settings_kv rows so a rollback (e.g. via the
    # apply-state watchdog) can restore them. The factory_reset
    # rollback handler is registered in app/tasks/apply_state.py
    # (rollback_kind="factory_reset") and re-applies this list.
    settings_rows = await db.fetch_all("SELECT key, value FROM system_settings_kv")
    plan = await apply_state.arm(
        target="system:factory-reset",
        before={"rows": settings_rows},
        after={"reset_tables": list(reset_tables)},
        rollback_kind="factory_reset",
        rollback_armed_s=int(body.get("rollback_armed_s", 60)),
    )
    for tbl in reset_tables:
        await db.execute(f"DELETE FROM {tbl}")
    await audit.record(
        username=user.username,
        ip=user.ip,
        action="factory_reset",
        target="system",
        new_value=(f"plan_id={plan['plan_id']}:tables={','.join(reset_tables)}" f":armed_until={plan['armed_until']}"),
    )
    return {
        "ok": True,
        "reset_tables": list(reset_tables),
        "plan_id": plan["plan_id"],
        "armed_until": plan["armed_until"],
        "rollback_armed_s": plan["rollback_armed_s"],
    }


# --- localization (#144, audit §1) ----------------------------------------
_KV_LOCALE = "localization.locale"


@router.get("/localization")
async def localization(_: CurrentUser = Depends(require_viewer)) -> dict[str, Any]:
    """Return the known locale list + the operator-selected locale
    (if any). The SPA uses the known list to populate its picker; the
    selected value is what the i18n loader activates.

    The two are merged: when the operator has picked a locale that is
    NOT in the known set (e.g. ``de_AT`` before a translation ships),
    it is still returned so the UI can display "selected: de_AT
    (fallback to de)" rather than silently snapping to the previous
    choice.
    """
    selected = await _kv_get(_KV_LOCALE)
    return {
        "languages": sorted(KNOWN_LOCALES),
        "selected": selected,
    }


@router.put("/localization")
async def set_localization(body: LocaleUpdate, user: CurrentUser = Depends(require_admin)) -> dict[str, Any]:
    """Persist an operator-chosen locale. Admin-only because it
    changes the UI for every subsequent session (per #144 / audit §1).

    This is NOT a privileged device write — locale is a UI string,
    not a device state change — so we do NOT require ``confirm`` and
    we do NOT arm an apply-state plan. The change is recorded in
    the audit log for traceability.
    """
    await _kv_set(_KV_LOCALE, body.locale)
    await audit.record(
        username=user.username,
        ip=user.ip,
        action="set_locale",
        target="system",
        new_value=body.locale,
    )
    return {"ok": True, "locale": body.locale}


# --- UI prefs (Features.md Settings: Dashboard Config) --------------------
# Persists operator-chosen SPA behavior: default landing page, items
# per page in tables, and the live WebSocket refresh interval. Stored
# in ``system_settings_kv`` (one row per key) so a future backup /
# restore / factory-reset can capture them. Viewer-readable so the
# SPA can pick up prefs on mount; admin-writable (audited).
_KV_UI_LANDING = "ui.landing_page"
_KV_UI_ITEMS_PER_PAGE = "ui.items_per_page"
_KV_UI_REFRESH_S = "ui.refresh_interval_s"


@router.get("/ui-prefs")
async def get_ui_prefs(_: CurrentUser = Depends(require_viewer)) -> dict[str, Any]:
    """Return the operator-set SPA behavior (Features.md Settings)."""
    return {
        "landing_page": await _kv_get(_KV_UI_LANDING),
        "items_per_page": await _kv_get(_KV_UI_ITEMS_PER_PAGE),
        "refresh_interval_s": await _kv_get(_KV_UI_REFRESH_S),
    }


@router.put("/ui-prefs")
async def set_ui_prefs(body: UiPrefsUpdate, user: CurrentUser = Depends(require_admin)) -> dict[str, Any]:
    """Persist SPA behavior overrides (Features.md Settings).

    Each field is optional; only the ones present are written. The
    rest are left untouched so a partial PUT doesn't reset the
    others. Audit-logs the changed keys.
    """
    written: list[str] = []
    if body.landing_page is not None:
        await _kv_set(_KV_UI_LANDING, body.landing_page)
        written.append(f"landing_page={body.landing_page}")
    if body.items_per_page is not None:
        await _kv_set(_KV_UI_ITEMS_PER_PAGE, str(body.items_per_page))
        written.append(f"items_per_page={body.items_per_page}")
    if body.refresh_interval_s is not None:
        await _kv_set(_KV_UI_REFRESH_S, str(body.refresh_interval_s))
        written.append(f"refresh_interval_s={body.refresh_interval_s}")
    await audit.record(
        username=user.username,
        ip=user.ip,
        action="ui_prefs_update",
        target="settings",
        new_value=",".join(written) if written else None,
    )
    return {"ok": True, "applied": written}


# --- backup / restore ------------------------------------------------------
async def _collect_config() -> dict[str, Any]:
    rules = await get_db().fetch_all("SELECT * FROM alert_rules")
    return {
        "config": get_settings().model_dump(mode="json"),
        "alert_rules": rules,
    }


@router.post("/backup")
async def create_backup(body: dict[str, str], user: CurrentUser = Depends(require_admin)) -> StreamingResponse:
    password = body.get("password", "")
    if len(password) < 4:
        from app.errors import ApiError

        raise ApiError("weak_password", "Backup password must be at least 4 characters", 400)
    payload = await _collect_config()
    blob = backup.encrypt_backup(payload, password)
    await audit.record(username=user.username, ip=user.ip, action="backup_create", target="config")
    return StreamingResponse(
        io.BytesIO(blob),
        media_type="application/octet-stream",
        headers={"Content-Disposition": 'attachment; filename="dashboard-backup.enc"'},
    )


@router.post("/restore/preview")
async def restore_preview(password: str, file: UploadFile, _: CurrentUser = Depends(require_admin)) -> dict[str, Any]:
    blob = await file.read()
    data = backup.decrypt_backup(blob, password)
    return {"valid": True, "preview": data}


@router.post("/restore/apply")
async def restore_apply(
    body: dict[str, Any] | None = None,
    user: CurrentUser = Depends(require_admin),
) -> dict[str, Any]:
    """Apply a previously-staged ``restore_points`` row (audit §12, #130).

    Body: ``{"plan_id": <restore_points.id>, "confirm": true,
    "rollback_armed_s": 60}``. The endpoint arms an apply plan
    (rollback_kind="restore_apply") so a watchdog timeout (no
    heartbeat within the window) auto-rolls back to the
    pre-restore snapshot. A fresh heartbeat within the window
    auto-commits the plan.
    """
    from app.errors import ApiError

    body = body or {}
    require_confirm(body)
    plan_id_raw = body.get("plan_id")
    if not isinstance(plan_id_raw, int):
        raise ApiError("missing_plan_id", "Body must include 'plan_id' (int)", 422)
    rollback_armed_s = int(body.get("rollback_armed_s", 60))
    if rollback_armed_s < 5 or rollback_armed_s > 600:
        raise ApiError("invalid_window", "rollback_armed_s must be in [5, 600]", 422)

    db = get_db()
    # Stage 1: load the staged payload (must exist).
    row = await db.fetch_one("SELECT payload FROM restore_points WHERE id = ?", (plan_id_raw,))
    if row is None:
        raise ApiError("restore_point_not_found", f"No restore_points row {plan_id_raw}", 404)
    staged = json.loads(row["payload"])
    staged_rules = staged.get("alert_rules", [])
    if not isinstance(staged_rules, list):
        raise ApiError("bad_payload", "restore_points payload 'alert_rules' is not a list", 400)

    # Stage 2: snapshot the current alert_rules so a rollback can
    # restore them. The rollback handler at app/tasks/apply_state.py
    # (rollback_kind="restore_apply") re-applies this map.
    current_rules = await db.fetch_all("SELECT * FROM alert_rules")
    before_tables = {"alert_rules": {str(r["id"]): dict(r) for r in current_rules}}

    # Stage 3: arm the apply plan.
    plan = await apply_state.arm(
        target=f"restore:point:{plan_id_raw}",
        before={"tables": before_tables},
        after={"restore_point_id": plan_id_raw, "rule_count": len(staged_rules)},
        rollback_kind="restore_apply",
        rollback_armed_s=rollback_armed_s,
    )

    # Stage 4: apply — delete-then-insert the staged rules. The
    # rollback snapshot was captured *before* this mutation so a
    # watchdog expiry returns the device to the pre-restore state.
    await db.execute("DELETE FROM alert_rules")
    for rule in staged_rules:
        if not isinstance(rule, dict):
            continue
        await db.execute(
            """INSERT INTO alert_rules
               (name, metric, condition, threshold, duration_s, severity, enabled)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                rule.get("name", "restored"),
                rule.get("metric", ""),
                rule.get("condition", ">"),
                float(rule.get("threshold", 0)),
                int(rule.get("duration_s", 0)),
                rule.get("severity", "warning"),
                1 if rule.get("enabled", True) else 0,
            ),
        )

    await audit.record(
        username=user.username,
        ip=user.ip,
        action="restore_apply",
        target=f"restore_point:{plan_id_raw}",
        new_value=(
            f"plan_id={plan['plan_id']}" f":rule_count={len(staged_rules)}" f":armed_until={plan['armed_until']}"
        ),
    )
    return {
        "ok": True,
        "applied_rule_count": len(staged_rules),
        "plan_id": plan["plan_id"],
        "armed_until": plan["armed_until"],
        "rollback_armed_s": plan["rollback_armed_s"],
    }


@router.get("/restore-points")
async def restore_points(_: CurrentUser = Depends(require_viewer)) -> dict[str, Any]:
    rows = await get_db().fetch_all("SELECT id, ts, name FROM restore_points ORDER BY ts DESC, id DESC")
    return {"restore_points": rows}


@router.post("/restore-points")
async def create_restore_point(body: dict[str, str], user: CurrentUser = Depends(require_admin)) -> dict[str, Any]:
    payload = json.dumps(await _collect_config())
    rid = await get_db().execute(
        "INSERT INTO restore_points (name, payload) VALUES (?, ?)",
        (body.get("name", "manual"), payload),
    )
    await audit.record(username=user.username, ip=user.ip, action="restore_point_create", target=body.get("name"))
    return {"id": rid}


@router.delete("/restore-points/{rid}")
async def delete_restore_point(rid: int, user: CurrentUser = Depends(require_admin)) -> dict[str, bool]:
    await get_db().execute("DELETE FROM restore_points WHERE id = ?", (rid,))
    await audit.record(username=user.username, ip=user.ip, action="restore_point_delete", target=str(rid))
    return {"ok": True}


# --- Appearance (Features.md Settings: Appearance) -------------------------
# Persists operator-chosen look-and-feel for the SPA. None of these are
# privileged device writes (appearance is a UI concern, not device
# state) so no ``confirm`` and no apply_state. Stored in
# ``system_settings_kv`` so a backup / restore picks them up.
_KV_APPEARANCE_THEME = "appearance.theme"
_KV_APPEARANCE_ACCENT = "appearance.accent_color"
_KV_APPEARANCE_DATE = "appearance.date_format"
_KV_APPEARANCE_TIME = "appearance.time_format"
_KV_APPEARANCE_UNITS = "appearance.units"
_KV_APPEARANCE_DENSITY = "appearance.density"
_KV_APPEARANCE_SIDEBAR = "appearance.sidebar_default"


@router.get("/appearance")
async def get_appearance(_: CurrentUser = Depends(require_viewer)) -> dict[str, Any]:
    """Return the operator-set appearance prefs (Features.md Settings)."""
    return {
        "theme": await _kv_get(_KV_APPEARANCE_THEME),
        "accent_color": await _kv_get(_KV_APPEARANCE_ACCENT),
        "date_format": await _kv_get(_KV_APPEARANCE_DATE),
        "time_format": await _kv_get(_KV_APPEARANCE_TIME),
        "units": await _kv_get(_KV_APPEARANCE_UNITS),
        "density": await _kv_get(_KV_APPEARANCE_DENSITY),
        "sidebar_default": await _kv_get(_KV_APPEARANCE_SIDEBAR),
    }


@router.put("/appearance")
async def set_appearance(body: AppearanceUpdate, user: CurrentUser = Depends(require_admin)) -> dict[str, Any]:
    """Persist appearance overrides (Features.md Settings).

    Each field is optional; only the ones present are written. The
    rest are left untouched so a partial PUT doesn't reset the
    others. Audit-logs the changed keys (the *values*, not the full
    state — an appearance reset should not spam the audit log with
    every unchanged key on every save).
    """
    written: list[str] = []
    if body.theme is not None:
        await _kv_set(_KV_APPEARANCE_THEME, body.theme)
        written.append(f"theme={body.theme}")
    if body.accent_color is not None:
        await _kv_set(_KV_APPEARANCE_ACCENT, body.accent_color)
        written.append(f"accent_color={body.accent_color}")
    if body.date_format is not None:
        await _kv_set(_KV_APPEARANCE_DATE, body.date_format)
        written.append(f"date_format={body.date_format}")
    if body.time_format is not None:
        await _kv_set(_KV_APPEARANCE_TIME, body.time_format)
        written.append(f"time_format={body.time_format}")
    if body.units is not None:
        await _kv_set(_KV_APPEARANCE_UNITS, body.units)
        written.append(f"units={body.units}")
    if body.density is not None:
        await _kv_set(_KV_APPEARANCE_DENSITY, body.density)
        written.append(f"density={body.density}")
    if body.sidebar_default is not None:
        await _kv_set(_KV_APPEARANCE_SIDEBAR, body.sidebar_default)
        written.append(f"sidebar_default={body.sidebar_default}")
    await audit.record(
        username=user.username,
        ip=user.ip,
        action="appearance_update",
        target="settings",
        new_value=",".join(written) if written else None,
    )
    return {"ok": True, "applied": written}


# --- network defaults (Features.md §Settings Network Defaults) --------------
# Bind address + port are read from the live server config (server.host,
# server.port) because they cannot be hot-applied — uvicorn is bound to
# the socket and changing it would mean re-launching the process. The
# editable knobs are the CORS / proxy knobs, which are saved to KV and
# reported back so the SPA can show what is actually in effect.
class _NetworkDefaultsUpdate(_StrictModel):
    bind_address: str | None = None
    allowed_origins: list[str] | None = None
    behind_proxy: bool | None = None
    trusted_proxies: list[str] | None = None


@router.get("/network-defaults")
async def get_network_defaults(_: CurrentUser = Depends(require_viewer)) -> dict[str, Any]:
    """Return the dashboard network defaults (Features.md §Settings Network Defaults).

    ``bind_address`` and ``port`` come from the live server config (yaml
    at boot). The other four knobs are operator-settable via PUT and
    live in ``system_settings_kv``. The GET surfaces both so the SPA
    can render the active state without separate fetches.
    """
    s = get_settings().server
    bind = await _kv_get(_KV_NET_BIND_ADDRESS) or s.host
    origins_raw = await _kv_get(_KV_NET_ALLOWED_ORIGINS) or ""
    behind_raw = await _kv_get(_KV_NET_BEHIND_PROXY)
    proxies_raw = await _kv_get(_KV_NET_TRUSTED_PROXIES) or ""
    origins = [o for o in origins_raw.split(",") if o.strip()] if origins_raw else []
    proxies = [p for p in proxies_raw.split(",") if p.strip()] if proxies_raw else []
    return {
        "bind_address": bind,
        "port": s.port,
        "allowed_origins": origins,
        "behind_proxy": bool(behind_raw == "1"),
        "trusted_proxies": proxies,
        # ``restart_required`` is a UI hint: bind_address / port edits
        # need a process restart, the CORS / proxy knobs take effect on
        # the next request after save.
        "restart_required_for": ["bind_address", "port"],
    }


@router.put("/network-defaults")
async def put_network_defaults(
    body: _NetworkDefaultsUpdate,
    user: CurrentUser = Depends(require_admin),
) -> dict[str, Any]:
    """Persist operator-set network defaults (audit §1, Features.md).

    Each field is optional — only present keys are written. Bind
    address is persisted for the SPA to display but does NOT take
    effect until restart (uvicorn is already bound). The other
    knobs take effect on the next request after save.
    """
    written: list[str] = []
    if body.bind_address is not None:
        bind = body.bind_address.strip()
        # IPv4, IPv6, hostname — anything that resolves to an address
        # is acceptable; the operator is responsible for choosing a
        # reachable value.
        if not bind:
            from app.errors import ApiError

            raise ApiError("invalid_bind_address", "bind_address must be non-empty", 422)
        await _kv_set(_KV_NET_BIND_ADDRESS, bind)
        written.append(f"bind_address={bind}")
    if body.allowed_origins is not None:
        # Whitespace-strip each entry; reject empty strings so the
        # operator gets feedback instead of a silent no-op.
        cleaned = [o.strip() for o in body.allowed_origins if o.strip()]
        await _kv_set(_KV_NET_ALLOWED_ORIGINS, ",".join(cleaned))
        written.append(f"allowed_origins={len(cleaned)}")
    if body.behind_proxy is not None:
        await _kv_set(_KV_NET_BEHIND_PROXY, "1" if body.behind_proxy else "0")
        written.append(f"behind_proxy={'on' if body.behind_proxy else 'off'}")
    if body.trusted_proxies is not None:
        cleaned = [p.strip() for p in body.trusted_proxies if p.strip()]
        await _kv_set(_KV_NET_TRUSTED_PROXIES, ",".join(cleaned))
        written.append(f"trusted_proxies={len(cleaned)}")
    await audit.record(
        username=user.username,
        ip=user.ip,
        action="network_defaults_update",
        target="settings",
        new_value=",".join(written) if written else None,
    )
    return {"ok": True, "applied": written}


# --- auth method (Features.md §Settings Access & Auth) ----------------------
# Reports the active authentication method (one of local/PAM/LDAP/SSO)
# and persists an operator-chosen value. Today only ``local`` is wired;
# the picker exists so the operator can declare intent and so the
# feature shows up as toggleable per the spec ("Auth method").
_AUTH_METHODS: tuple[str, ...] = ("local", "pam", "ldap", "sso")


@router.get("/auth-method")
async def get_auth_method(_: CurrentUser = Depends(require_viewer)) -> dict[str, Any]:
    """Return the active auth method (Features.md §Settings Access & Auth)."""
    method = await _kv_get(_KV_AUTH_METHOD) or "local"
    return {
        "method": method,
        "available": list(_AUTH_METHODS),
        # wired=True only for the method that is actually implemented.
        # The picker still offers the rest so the operator can declare
        # intent (and see what is on/off in the platform), but the
        # router never refuses local credentials regardless of the
        # value here — see login in app/routers/auth.py.
        "wired": {"local": True, "pam": False, "ldap": False, "sso": False},
    }


class _AuthMethodUpdate(_StrictModel):
    method: str

    @field_validator("method")
    @classmethod
    def _known(cls, v: str) -> str:
        if v not in _AUTH_METHODS:
            raise ValueError(f"method must be one of {list(_AUTH_METHODS)}")
        return v


@router.put("/auth-method")
async def put_auth_method(
    body: _AuthMethodUpdate,
    user: CurrentUser = Depends(require_admin),
) -> dict[str, Any]:
    """Persist operator-chosen auth method (audit §1, Features.md)."""
    await _kv_set(_KV_AUTH_METHOD, body.method)
    await audit.record(
        username=user.username,
        ip=user.ip,
        action="auth_method_update",
        target="auth",
        new_value=body.method,
    )
    return {"ok": True, "method": body.method}
