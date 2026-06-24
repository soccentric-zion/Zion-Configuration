"""Protected-resource lists (audit.md Guardrail 3).

A protected resource cannot be stop/disabled/down through the dashboard API,
even by an admin with the body-level ``confirm: true`` token. Audit Guardrail
3 (audit.md) requires:

    "block stop/disable of critical units (network, SSH, the dashboard
    itself); block writes to fuses / secure-boot key enrollment (status only)."

The lists are the canonical reference. Routers and HALs call
``assert_not_protected`` *before* the role check / confirm check so that a
protected-resource action is rejected earlier (403) with a clearer code than
the generic 403 the role guard would emit. This is the second line of defence
after the role guard, not a replacement for it.

The list is configurable through the dashboard's ``config.yaml`` under
``protected`` so a per-platform fork can extend it (e.g. add a vendor daemon).
The defaults below cover the generic-Linux build.
"""

from __future__ import annotations

from typing import Any

from app.errors import ApiError

# Services that must never be stop/disabled/restart/masked via the dashboard.
# The dashboard is in this list because stopping it would lock the operator
# out of the very control plane they are using. SSH is here because
# remote-only devices lose all access if it stops. systemd-networkd /
# NetworkManager are here because stopping them on the only uplink NIC is a
# lockout. chronyd / systemd-timesyncd are here because clock drift breaks
# certificate validation, audit, and every other guarded flow.
DEFAULT_PROTECTED_SERVICES: frozenset[str] = frozenset(
    {
        "dashboard.service",
        "ssh.service",
        "sshd.service",
        "NetworkManager.service",
        "systemd-networkd.service",
        "chronyd.service",
        "systemd-timesyncd.service",
    }
)

# Network ports the dashboard must not close on the host. 22 = SSH, 53 = DNS,
# 67/68 = DHCP, 80/443 = HTTP/HTTPS, 123/323 = NTP, 8000 = the dashboard
# itself. Operators can override via config.yaml.
DEFAULT_PROTECTED_PORTS: frozenset[int] = frozenset({22, 53, 67, 68, 80, 123, 323, 443, 8000})

# Loopback is always protected. A platform may extend this with the management
# interface in config.yaml (e.g. "eth0" for a single-NIC device).
DEFAULT_PROTECTED_INTERFACES: frozenset[str] = frozenset({"lo"})

# Destructive actions on services that are blocked. ``start``/``enable`` are
# intentionally NOT in this set — bringing a service up is rarely lockout-
# capable. ``restart`` IS in the set for the same reason ``stop`` is: it
# transiently stops the unit.
DEFAULT_PROTECTED_SERVICE_ACTIONS: frozenset[str] = frozenset({"stop", "disable", "restart", "mask"})


def _services() -> frozenset[str]:
    """Resolve the active service-protection set, honoring config overrides."""
    try:
        from app.config import get_settings  # local import: avoid circular

        cfg = get_settings().protected
    except Exception:
        cfg = None
    if cfg and getattr(cfg, "services", None):
        return frozenset(set(DEFAULT_PROTECTED_SERVICES) | set(cfg.services))
    return DEFAULT_PROTECTED_SERVICES


def _ports() -> frozenset[int]:
    try:
        from app.config import get_settings

        cfg = get_settings().protected
    except Exception:
        cfg = None
    if cfg and getattr(cfg, "ports", None):
        return frozenset(set(DEFAULT_PROTECTED_PORTS) | {int(p) for p in cfg.ports})
    return DEFAULT_PROTECTED_PORTS


def _interfaces() -> frozenset[str]:
    try:
        from app.config import get_settings

        cfg = get_settings().protected
    except Exception:
        cfg = None
    if cfg and getattr(cfg, "interfaces", None):
        return frozenset(set(DEFAULT_PROTECTED_INTERFACES) | set(cfg.interfaces))
    return DEFAULT_PROTECTED_INTERFACES


def _service_actions() -> frozenset[str]:
    return DEFAULT_PROTECTED_SERVICE_ACTIONS


def assert_not_protected(kind: str, name: str, action: str | None = None) -> None:
    """Raise ``ApiError(403, 'protected_resource')`` if the resource is protected.

    Parameters
    ----------
    kind:
        One of ``'service'``, ``'port'``, ``'interface'``.
    name:
        The resource name (service unit, port number as string, interface).
    action:
        Required for ``kind == 'service'``; the requested action
        (e.g. ``'stop'``, ``'disable'``, ``'mask'``).
    """
    kind_norm = kind.strip().lower()
    if kind_norm == "service":
        if action is None or action not in _service_actions():
            return  # non-destructive action (start, enable) is allowed
        if name in _services():
            raise ApiError(
                "protected_resource",
                f"Service '{name}' is protected from {action}; see Protected Resources in the UI",
                403,
                detail={"kind": "service", "name": name, "action": action},
            )
        return
    if kind_norm == "port":
        try:
            port_int = int(name)
        except (TypeError, ValueError):
            return  # not a numeric port; routers should already validate
        if port_int in _ports():
            raise ApiError(
                "protected_resource",
                f"Port {port_int} is protected; refusing to close",
                403,
                detail={"kind": "port", "name": port_int},
            )
        return
    if kind_norm == "interface":
        if name in _interfaces():
            raise ApiError(
                "protected_resource",
                f"Interface '{name}' is protected from link changes",
                403,
                detail={"kind": "interface", "name": name},
            )
        return
    raise ApiError("protected_unknown_kind", f"Unknown protected-resource kind '{kind}'", 500)


def list_protected() -> dict[str, Any]:
    """Return the active protection sets for the ``GET /api/system/protected-resources`` endpoint."""
    return {
        "services": sorted(_services()),
        "ports": sorted(_ports()),
        "interfaces": sorted(_interfaces()),
        "service_actions_blocked": sorted(_service_actions()),
    }
