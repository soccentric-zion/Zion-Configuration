"""System administration (section 7) + CM5 platform (5) + connectivity (4).

Configure and control the host: network interfaces (static/DHCP, up/down), ports
(list/close), firewall rules (CRUD + enable/disable), services (start/stop/...),
system-level device enable/disable, and live system status. Also surfaces the
CM5 "if present" platform I/O (PCIe, eMMC, GPIO banks, RTC/battery, PMIC) and
connectivity radios (WiFi scan/connect, Bluetooth).

Privileged, lockout-capable actions (interface down, port close, firewall change,
service stop) require an explicit ``confirm: true`` from the dashboard and admin
role; inputs are validated before anything is applied.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query

from app.auth.deps import CurrentUser, require_admin, require_operator, require_viewer
from app.db import audit
from app.db.database import get_db
from app.errors import ApiError
from app.hal.registry import get_hal
from app.tasks import next_boot
from app.util.confirm import require_confirm
from app.util.models import BluetoothToggle, WifiConnectProfile
from app.util.protected import _ports as _protected_ports
from app.util.protected import assert_not_protected
from app.util.validate import valid_ip, valid_netmask, valid_port, valid_protocol

router = APIRouter(prefix="/api/sysadmin", tags=["sysadmin"])

_NET_DEFAULTS = [
    {"name": "lo", "method": "static"},
    {"name": "eth0", "method": "dhcp"},
    {"name": "wlan0", "method": "dhcp"},
]


def _sa() -> Any:
    return get_hal("sysadmin")


def _platform() -> Any:
    return get_hal("platform")


# --- platform (section 5: CM5 "if present") --------------------------------
@router.get("/platform")
async def platform(_: CurrentUser = Depends(require_viewer)) -> dict[str, Any]:
    return _platform().module_info()


@router.get("/pcie")
async def pcie(_: CurrentUser = Depends(require_viewer)) -> dict[str, Any]:
    return _platform().pcie()


@router.get("/emmc")
async def emmc(_: CurrentUser = Depends(require_viewer)) -> dict[str, Any]:
    return _platform().emmc()


@router.get("/gpio-banks")
async def gpio_banks(_: CurrentUser = Depends(require_viewer)) -> dict[str, Any]:
    return _platform().gpio_banks()


@router.get("/rtc")
async def rtc(_: CurrentUser = Depends(require_viewer)) -> dict[str, Any]:
    return _platform().rtc_battery()


@router.get("/pmic")
async def pmic(_: CurrentUser = Depends(require_viewer)) -> dict[str, Any]:
    return _platform().pmic()


# --- connectivity radios (section 4) ---------------------------------------
@router.get("/wifi/scan")
async def wifi_scan(_: CurrentUser = Depends(require_viewer)) -> dict[str, Any]:
    return _sa().wifi_scan()


@router.post("/wifi/connect")
async def wifi_connect(body: WifiConnectProfile, user: CurrentUser = Depends(require_operator)) -> dict[str, Any]:
    """Connect to a Wi-Fi network and persist the profile so the HAL
    can re-apply on boot (#110, audit §2, #142).

    The action is lockout-capable (associating with a wrong SSID on
    the only radio can leave the host unreachable), so ``confirm:
    true`` is required per req.md §7 — enforced via ``require_confirm``
    so an empty body still hits the role guard before the 422.
    """
    require_confirm(body.model_dump())
    result = _sa().wifi_connect(ssid=body.ssid, psk=body.psk)
    # Persist the profile so the HAL can re-apply on next boot. The
    # ON CONFLICT clause updates the credentials in place — a single
    # SSID has one canonical profile. ``applied`` is recorded in the
    # audit row so the action log shows the actual state change (#42).
    await get_db().execute(
        """INSERT INTO wifi_networks (ssid, psk, country, eap, identity, updated_at)
           VALUES (?, ?, ?, ?, ?, datetime('now'))
           ON CONFLICT(ssid) DO UPDATE SET
               psk = excluded.psk,
               country = excluded.country,
               eap = excluded.eap,
               identity = excluded.identity,
               updated_at = excluded.updated_at""",
        (body.ssid, body.psk, body.country, body.eap, body.identity),
    )
    # Audit row carries the country and a redacted PSK marker so the
    # log shows what was set without leaking the secret (#142).
    psk_marker = "***" if body.psk else ""
    await audit.record(
        username=user.username,
        ip=user.ip,
        action="wifi_connect",
        target=body.ssid,
        new_value=f"country={body.country}:psk={psk_marker}:applied={bool(result.get('applied'))}",
    )
    return {"profile": body.model_dump(), "applied": result.get("applied")}


@router.get("/wifi/profiles")
async def wifi_profiles(_: CurrentUser = Depends(require_viewer)) -> dict[str, Any]:
    """List persisted Wi-Fi profiles (#110)."""
    rows = await get_db().fetch_all(
        "SELECT id, ssid, country, eap, identity, priority, created_at, updated_at "
        "FROM wifi_networks ORDER BY priority ASC, id ASC"
    )
    # Never return the PSK in the listing — the operator-facing panel
    # only needs to know which networks are configured.
    return {
        "profiles": [
            {
                "id": r["id"],
                "ssid": r["ssid"],
                "country": r["country"],
                "eap": r["eap"],
                "identity": r["identity"],
                "priority": r["priority"],
                "created_at": r["created_at"],
                "updated_at": r["updated_at"],
                "has_psk": bool(r["ssid"]),
            }
            for r in rows
        ]
    }


@router.delete("/wifi/profiles/{profile_id}")
async def wifi_profile_delete(profile_id: int, user: CurrentUser = Depends(require_admin)) -> dict[str, Any]:
    """Delete a persisted Wi-Fi profile (admin-only, #110)."""
    row = await get_db().fetch_one("SELECT ssid FROM wifi_networks WHERE id = ?", (profile_id,))
    if row is None:
        raise ApiError("not_found", f"Unknown wifi profile {profile_id}", 404)
    await get_db().execute("DELETE FROM wifi_networks WHERE id = ?", (profile_id,))
    await audit.record(username=user.username, ip=user.ip, action="wifi_profile_delete", target=str(profile_id))
    return {"ok": True, "id": profile_id, "ssid": row["ssid"]}


@router.get("/bluetooth")
async def bluetooth(_: CurrentUser = Depends(require_viewer)) -> dict[str, Any]:
    return _sa().bluetooth()


@router.post("/bluetooth/enable")
async def bluetooth_enable(body: BluetoothToggle, user: CurrentUser = Depends(require_operator)) -> dict[str, Any]:
    # Toggling the radio can lock out a Bluetooth-only headless flow;
    # require_confirm keeps the action explicit (#122). The pydantic
    # body's extra="forbid" rejects unknown keys with 422.
    require_confirm(body.model_dump())
    action = "bluetooth_enable" if body.enabled else "bluetooth_disable"
    result = _sa().bluetooth_set_enabled(body.enabled)
    await audit.record(
        username=user.username,
        ip=user.ip,
        action=action,
        new_value=f"{body.enabled}:applied={bool(result.get('applied'))}",
    )
    return result


# --- network interfaces (section 7) ----------------------------------------
async def _ensure_net_seed() -> None:
    db = get_db()
    row = await db.fetch_one("SELECT COUNT(*) AS n FROM net_interfaces")
    if row and row["n"]:
        return
    for d in _NET_DEFAULTS:
        await db.execute(
            "INSERT OR IGNORE INTO net_interfaces (name, method, up) VALUES (?, ?, 1)", (d["name"], d["method"])
        )


@router.get("/network")
async def network(_: CurrentUser = Depends(require_viewer)) -> dict[str, Any]:
    await _ensure_net_seed()
    rows = await get_db().fetch_all("SELECT * FROM net_interfaces ORDER BY name")
    live = _sa().net_status()["interfaces"]
    live_by_name = {i["name"]: i for i in live}
    out = []
    for r in rows:
        out.append(
            {
                "name": r["name"],
                "method": r["method"],
                "address": r["address"],
                "netmask": r["netmask"],
                "gateway": r["gateway"],
                "dns": r["dns"],
                "up": bool(r["up"]),
                "live": live_by_name.get(r["name"]),
            }
        )
    return {"interfaces": out}


@router.post("/network/{name}/config")
async def network_config(name: str, body: dict[str, Any], user: CurrentUser = Depends(require_admin)) -> dict[str, Any]:
    await _ensure_net_seed()
    row = await get_db().fetch_one("SELECT * FROM net_interfaces WHERE name = ?", (name,))
    if row is None:
        raise ApiError("not_found", f"Unknown interface '{name}'", 404)
    method = str(body.get("method", "dhcp"))
    if method not in {"dhcp", "static"}:
        raise ApiError("bad_method", "method must be 'dhcp' or 'static'", 400)
    address = netmask = gateway = dns = None
    if method == "static":
        address = valid_ip(str(body.get("address", "")), "address")
        netmask = valid_netmask(str(body.get("netmask", "")))
        if body.get("gateway"):
            gateway = valid_ip(str(body["gateway"]), "gateway")
        if body.get("dns"):
            dns = valid_ip(str(body["dns"]), "dns")
    await get_db().execute(
        """UPDATE net_interfaces SET method=?, address=?, netmask=?, gateway=?, dns=?,
           updated_at=datetime('now') WHERE name=?""",
        (method, address, netmask, gateway, dns, name),
    )
    await audit.record(username=user.username, ip=user.ip, action="net_config", target=name, new_value=method)
    return {"name": name, "method": method, "address": address, "netmask": netmask, "gateway": gateway, "dns": dns}


@router.post("/network/{name}/link")
async def network_link(name: str, body: dict[str, Any], user: CurrentUser = Depends(require_admin)) -> dict[str, Any]:
    await _ensure_net_seed()
    row = await get_db().fetch_one("SELECT * FROM net_interfaces WHERE name = ?", (name,))
    if row is None:
        raise ApiError("not_found", f"Unknown interface '{name}'", 404)
    # Cross-reference with the live kernel view: if psutil doesn't see
    # the interface, the row is stale and the operator shouldn't be told
    # the link is "up" when there's no such device. In simulation, the
    # live view always knows about the seed, so this guard doesn't
    # false-positive (#50).
    live = {i["name"] for i in _sa().net_status()["interfaces"]}
    if name not in live:
        raise ApiError(
            "interface_not_present",
            f"Interface '{name}' is not present on this device",
            404,
        )
    up = bool(body.get("up", True))
    if not up:  # bringing an interface down can lock out the host
        # Guardrail 3: loopback (and any platform-configured management
        # iface) is protected from being brought down (#90).
        assert_not_protected("interface", name)
        require_confirm(body)
    await get_db().execute(
        "UPDATE net_interfaces SET up=?, updated_at=datetime('now') WHERE name=?", (1 if up else 0, name)
    )
    await audit.record(
        username=user.username, ip=user.ip, action="net_link", target=name, new_value="up" if up else "down"
    )
    return {"name": name, "up": up}


# --- ports (section 7) -----------------------------------------------------
@router.get("/ports")
async def ports(_: CurrentUser = Depends(require_viewer)) -> dict[str, Any]:
    return _sa().listening_ports()


@router.post("/ports/{port}/close")
async def close_port(port: int, body: dict[str, Any], user: CurrentUser = Depends(require_admin)) -> dict[str, Any]:
    valid_port(port)
    # Guardrail 3: the dashboard's own port, SSH, DNS, NTP, etc. are
    # protected. Refuse 403 *before* the confirm check so a misclick
    # with confirm=true is still rejected (#91).
    assert_not_protected("port", str(port))
    require_confirm(body)
    await audit.record(username=user.username, ip=user.ip, action="port_close", target=str(port))
    return {"ok": True, "port": port, "closed": True}


# --- firewall (section 7) --------------------------------------------------
@router.get("/firewall")
async def firewall(_: CurrentUser = Depends(require_viewer)) -> dict[str, Any]:
    rows = await get_db().fetch_all("SELECT * FROM firewall_rules ORDER BY position, id")
    rules = [
        {
            "id": r["id"],
            "action": r["action"],
            "port": r["port"],
            "protocol": r["protocol"],
            "source": r["source"],
            "destination": r["destination"],
            "enabled": bool(r["enabled"]),
            "position": r["position"],
        }
        for r in rows
    ]
    rendered = _sa().render_ruleset(rules)
    return {"rules": rules, "ruleset": rendered["ruleset"]}


def _would_lock_out_dashboard(existing: list[dict[str, Any]], port: int) -> bool:
    """Return True if the ruleset, after a (hypothetical) ``deny port`` on the
    protected ``port``, would leave the dashboard unreachable.

    The HAL's default policy is ``drop``; the ruleset reaches ``deny``
    for any protected port that has no lower-positioned ``allow`` for the
    same port. We treat a new ``deny`` on a protected port as a lockout
    unless the *combined* existing + proposed ruleset contains an
    earlier ``allow`` for that port (#148, audit Guardrail 3).

    ``existing`` is the raw row list from ``firewall_rules``; the
    proposed rule is implied by ``port`` (always a deny for the
    caller-path in this file).
    """
    # Combine existing + proposed. ``enabled`` defaults to 1.
    enabled: list[tuple[int, dict[str, Any]]] = []
    for r in existing:
        if not r.get("enabled", 1):
            continue
        enabled.append((int(r.get("position", 0)), r))
    # The proposed rule is appended at position ``max(position)+1`` so
    # it runs last — i.e. it does NOT shadow any earlier allow.
    max_pos = max((p for p, _ in enabled), default=0)
    proposed = {
        "action": "deny",
        "port": port,
        "enabled": 1,
        "position": max_pos + 1,
    }
    enabled.append((max_pos + 1, proposed))
    # Sort by (position, id) — matches the read endpoint's ORDER BY.
    enabled.sort(key=lambda pr: (pr[0], int(pr[1].get("id", 0))))
    # The first enabled rule that targets our port decides allow/deny.
    for _, r in enabled:
        if int(r.get("port", 0)) == port:
            return r.get("action") == "deny"
    # Should not happen because we just added a deny for `port`, but
    # be conservative: no explicit rule = default policy drop = deny.
    return True


@router.post("/firewall", status_code=201)
async def create_rule(
    body: dict[str, Any],
    force: bool = Query(False),
    user: CurrentUser = Depends(require_admin),
) -> dict[str, Any]:
    require_confirm(body)
    action = str(body.get("action", "allow"))
    if action not in {"allow", "deny"}:
        raise ApiError("bad_action", "action must be 'allow' or 'deny'", 400)
    port = valid_port(int(body.get("port", 0)))
    protocol = valid_protocol(str(body.get("protocol", "tcp")))
    source = str(body.get("source", "any"))
    if source != "any":
        valid_ip(source, "source")
    # Guardrail 3: a deny rule on a protected port without a
    # lower-positioned allow for the same port is a self-lockout.
    # The operator may opt in with `?force=true` for explicit lockout
    # intent (rare, but legal — e.g. an out-of-band console wants to
    # disable remote access to force local-only ops) (#148).
    if action == "deny" and port in _protected_ports() and not force:
        existing = await get_db().fetch_all("SELECT * FROM firewall_rules")
        if _would_lock_out_dashboard(existing, port):
            raise ApiError(
                "would_lock_out_dashboard",
                f"Denying protected port {port} with no lower-positioned allow would "
                f"lock the dashboard out. Pass ?force=true to override.",
                422,
                detail={"port": port, "force_required": True},
            )
    rid = await get_db().execute(
        """INSERT INTO firewall_rules (action, port, protocol, source, destination, enabled, position)
           VALUES (?, ?, ?, ?, ?, 1, ?)""",
        (action, port, protocol, source, str(body.get("destination", "any")), int(body.get("position", 0))),
    )
    await audit.record(
        username=user.username, ip=user.ip, action="firewall_create", target=f"{action}:{port}/{protocol}"
    )
    return {"id": rid}


@router.post("/firewall/{rule_id}/enable")
async def enable_rule(rule_id: int, body: dict[str, Any], user: CurrentUser = Depends(require_admin)) -> dict[str, Any]:
    row = await get_db().fetch_one("SELECT * FROM firewall_rules WHERE id = ?", (rule_id,))
    if row is None:
        raise ApiError("not_found", f"Unknown rule {rule_id}", 404)
    require_confirm(body)
    enabled = 1 if body.get("enabled", True) else 0
    await get_db().execute("UPDATE firewall_rules SET enabled = ? WHERE id = ?", (enabled, rule_id))
    await audit.record(
        username=user.username,
        ip=user.ip,
        action="firewall_enable",
        target=str(rule_id),
        old_value=bool(row["enabled"]),
        new_value=bool(enabled),
    )
    return {"id": rule_id, "enabled": bool(enabled)}


@router.delete("/firewall/{rule_id}")
async def delete_rule(
    rule_id: int,
    body: dict[str, Any] | None = None,
    user: CurrentUser = Depends(require_admin),
) -> dict[str, bool]:
    row = await get_db().fetch_one("SELECT * FROM firewall_rules WHERE id = ?", (rule_id,))
    if row is None:
        raise ApiError("not_found", f"Unknown rule {rule_id}", 404)
    # Body-based confirm to match every other privileged endpoint in the
    # codebase (#35). Query-param `?confirm=true` was inconsistent and
    # broke the SPA's standard confirm dialog flow.
    require_confirm(body)
    await get_db().execute("DELETE FROM firewall_rules WHERE id = ?", (rule_id,))
    await audit.record(username=user.username, ip=user.ip, action="firewall_delete", target=str(rule_id))
    return {"ok": True}


# --- services (section 7) --------------------------------------------------
@router.get("/services")
async def services(_: CurrentUser = Depends(require_viewer)) -> dict[str, Any]:
    return get_hal("software").services()


@router.post("/services/{name}/{action}")
async def service_action(
    name: str, action: str, body: dict[str, Any] | None = None, user: CurrentUser = Depends(require_operator)
) -> dict[str, Any]:
    if action not in {"start", "stop", "restart", "enable", "disable", "mask", "unmask"}:
        raise ApiError("bad_action", f"Unknown action '{action}'", 400)
    # Guardrail 3: the dashboard, SSH, NetworkManager/systemd-networkd,
    # and time-sync units cannot be stop/disabled/restarted via the
    # dashboard even with admin role + confirm. The check runs *before*
    # require_confirm so a protected unit is rejected earlier with a
    # clearer 403 code (#90).
    assert_not_protected("service", name, action)
    if action in {"stop", "disable", "restart", "mask"}:
        # mask is destructive (prevents start) so it sits in the same
        # confirm-required bucket as stop/disable/restart (#108).
        require_confirm(body or {})
    # Next-boot staging (#114, audit §4). enable/disable are the
    # candidates most likely to be staged — start/stop on a unit that
    # is not yet enabled is a no-op at the next boot. An unknown
    # `when` falls through to live (so old callers don't break).
    when = (body or {}).get("when", "live")
    if when == "next-boot" and action in {"enable", "disable"}:
        pending_id = await next_boot.arm(
            target=name,
            kind=f"service_{action}",
            payload={"enabled": action == "enable"},
            username=user.username,
        )
        await audit.record(
            username=user.username,
            ip=user.ip,
            action=f"service_{action}_staged",
            target=name,
            new_value=f"next-boot:pending={pending_id}",
        )
        return {"name": name, "action": action, "when": "next-boot", "pending_id": pending_id}
    result = get_hal("software").service_action(name, action)
    await audit.record(username=user.username, ip=user.ip, action=f"service_{action}", target=name)
    return result


# --- system-level devices (section 7) --------------------------------------
@router.get("/devices")
async def devices(_: CurrentUser = Depends(require_viewer)) -> dict[str, Any]:
    rows = await get_db().fetch_all("SELECT name, kind, enabled FROM hw_interfaces ORDER BY kind, name")
    return {"devices": [{"name": r["name"], "kind": r["kind"], "enabled": bool(r["enabled"])} for r in rows]}


# --- aggregate status ------------------------------------------------------
@router.get("/status")
async def status(_: CurrentUser = Depends(require_viewer)) -> dict[str, Any]:
    svc = get_hal("software").services().get("units", [])
    running = sum(1 for s in svc if s.get("active") == "active")
    rules = await get_db().fetch_all("SELECT enabled FROM firewall_rules")
    active = sum(1 for r in rules if r["enabled"])
    return _sa().system_status(running, len(svc), active)


# --- top processes (audit §7) ----------------------------------------------
@router.get("/top-processes")
async def top_processes(
    limit: int = Query(20, ge=1, le=100),
    _: CurrentUser = Depends(require_viewer),
) -> dict[str, Any]:
    """Top N processes by CPU + memory, capped at 100 (#136, audit §7)."""
    return get_hal("system").top_processes(limit=limit)
