"""Feature-tree resolution (Features.md §1).

The spec mandates a per-sub-feature toggleable tree that drives:
- backend router registration (disabled → no route registered, 404)
- frontend sidebar and route gate (disabled → no nav entry, redirect)
- HAL module instantiation gating (disabled HAL methods return envelope
  with ``simulated: True`` and a ``disabled: True`` flag)

This module resolves the raw ``settings.features.groups`` dict into a
canonical tree the SPA can consume at ``GET /api/config/features``.

The contract:
- A group is enabled iff it has at least one enabled sub-feature (or
  ``_all: true``).
- A sub-feature is enabled unless explicitly ``false``, or its group
  has ``_all: false`` AND the sub-feature is not explicitly ``true``.
- Missing groups and missing sub-features default to **enabled** (so a
  minimal config block can list only what to disable).
"""

from __future__ import annotations

from typing import Any

from app.config import get_settings


def get_feature_tree() -> dict[str, Any]:
    """Return the resolved feature tree as a JSON-serializable dict.

    Shape::

        {
            "groups": {
                "<group>": {
                    "enabled": true,
                    "subfeatures": {
                        "<sub>": true,
                        ...
                    }
                },
                ...
            },
            "schema_version": 1,
        }
    """
    cfg = get_settings()
    raw_groups: dict[str, object] = dict(cfg.features.groups or {})
    # Always include the full known group set so the SPA gets a complete
    # tree even when ``config.yaml`` doesn't list any features (default
    # boot, hermetic tests, etc.). Missing entries default to enabled.
    for known in _KNOWN_SUBFEATURES:
        raw_groups.setdefault(known, None)
    groups: dict[str, dict[str, Any]] = {}
    for name, raw in raw_groups.items():
        if raw is None or not isinstance(raw, dict):
            groups[name] = {"enabled": True, "subfeatures": {k: True for k in _known_subfeatures(name)}}
            continue
        sub_map = {k: bool(v) for k, v in raw.items() if k != "_all"}
        all_flag = raw.get("_all")
        if all_flag is True:
            group_enabled = True
            # All sub-features are enabled, including ones the operator
            # didn't list (they appear in the tree so the SPA can render
            # the full panel).
            sub_map = {k: True for k, v in sub_map.items() if v}
            # Pad missing sub-features as True.
            for sub in _known_subfeatures(name):
                sub_map.setdefault(sub, True)
        elif all_flag is False:
            # Group disabled, but a sub-feature can override back to True.
            group_enabled = any(v is True for v in sub_map.values())
            for sub in _known_subfeatures(name):
                sub_map.setdefault(sub, False)
        else:
            # Partial: only listed sub-features get the explicit value,
            # others default to True (absent = enabled).
            group_enabled = True
            for sub in _known_subfeatures(name):
                if sub not in sub_map:
                    sub_map[sub] = True
        groups[name] = {"enabled": group_enabled, "subfeatures": sub_map}
    return {"groups": groups, "schema_version": 1}


def is_group_enabled(group: str) -> bool:
    """True if a group has at least one enabled sub-feature."""
    tree = get_feature_tree()["groups"]
    grp = tree.get(group)
    if grp is None:
        return True
    return bool(grp.get("enabled", True))


def is_subfeature_enabled(group: str, sub: str) -> bool:
    """True unless explicitly disabled."""
    tree = get_feature_tree()["groups"]
    grp = tree.get(group)
    if grp is None:
        return True
    subs = grp.get("subfeatures") or {}
    if sub in subs:
        return bool(subs[sub])
    return True


# --- canonical sub-feature catalog (Features.md) ---------------------------
# Used to fill in defaults so the SPA gets a complete tree regardless of
# how minimal the operator's config.yaml is.

_KNOWN_SUBFEATURES: dict[str, tuple[str, ...]] = {
    "overview": ("device_identity", "health_status", "live_metrics", "power_thermal"),
    "network": (
        "interfaces",
        "wifi",
        "dns_routing",
        "firewall",
        "cellular",
        "vpn",
        "ntp",
        "proxy",
    ),
    "hardware": (
        "gpio",
        "pwm",
        "adc",
        "coprocessor",
        "sensors",
        "i2c",
        "spi",
        "uart",
        "can",
        "usb",
        "display",
        "camera",
        "audio",
        "gpu",
        "fpga",
        "pru",
        "leds",
        "relays",
        "buttons",
        "overlays",
        "clocks",
        "pinmux",
    ),
    "storage": (
        "disks",
        "emmc_health",
        "sd_card",
        "smart",
        "filesystems",
        "partitions",
        "raid_lvm",
        "ab_partitions",
        "mount_manager",
        "quotas",
        "encryption",
        "swap",
        "cleanup",
        "backup_targets",
    ),
    "thermal": (
        "zones",
        "trip_points",
        "fan_control",
        "cooling_policy",
        "throttling",
        "heat_map",
        "history",
        "alerts_limits",
        "gpu_thermal",
        "fpga_thermal",
        "coprocessor_thermal",
        "cooling_devices",
    ),
    "ota": (
        "system_ota",
        "coprocessor_fota",
        "fpga_bitstream",
        "channels",
        "scheduling",
        "delta",
        "verification",
        "staging",
        "bundles",
        "recovery",
        "fleet",
    ),
    "telemetry": (
        "connection",
        "metric_selection",
        "store_forward",
        "scheduling",
        "topics",
        "last_sync",
        "retention",
        "security",
    ),
    "cloud": (
        "provider",
        "connection",
        "provisioning",
        "twin",
        "messaging",
        "certs",
        "remote_mgmt",
        "resilience",
        "diagnostics",
    ),
    "security": (
        "firewall",
        "ssh",
        "certificates",
        "secrets",
        "access_control",
        "audit",
        "hardening",
        "network_security",
        "compliance",
        "updates",
    ),
    "users": (
        "users",
        "roles",
        "groups",
        "permissions",
        "authentication",
        "sessions",
        "access_policy",
        "directory",
        "audit",
        "self_service",
    ),
    "logs": (
        "journal",
        "dmesg",
        "syslog",
        "application",
        "service",
        "coprocessor",
        "audit",
        "boot",
        "custom",
        "search",
        "export",
        "management",
        "live",
    ),
    "settings": (
        "dashboard_users",
        "dashboard_config",
        "appearance",
        "system",
        "access_auth",
        "network_defaults",
        "notifications",
        "backup_restore",
        "integrations",
        "maintenance",
        "about",
    ),
    "coprocessor": ("link", "firmware", "transport", "fota"),
    "fpga": ("bitstream", "fabric", "overlay"),
    "pru": ("state", "firmware", "shared_memory"),
    "gpu": ("utilization", "memory", "thermal", "inference"),
    "industrial_io": ("hwmon", "iio", "dt_overlays"),
    # Legacy / convenience group name (not in Features.md but used by the
    # SPA summary tile and several legacy callers). Always-on.
    "system": ("summary", "about"),
}


def _known_subfeatures(group: str) -> tuple[str, ...]:
    return _KNOWN_SUBFEATURES.get(group, ())
