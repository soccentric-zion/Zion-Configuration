"""Router-module registry: gate which FastAPI routers get registered.

The Features.md spec mandates "absent = hidden completely: no route
registered, no menu item, no panel." This module is the backend half of
that contract — it maps each router module to the feature group (and
optional sub-feature) that controls its registration. ``app.main`` calls
``iter_enabled_routers()`` and only ``include_router``s the modules whose
group/sub is enabled.

Adding a new router:
1. Add the import to ``app.main``'s router tuple.
2. Add a ``RouterInfo`` entry to ``ROUTER_REGISTRY`` with the module's
   group + optional sub-feature.
3. The router's ``api`` decorator prefix MUST be unique per module
   (use ``/api/<topic>``).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.config_features import is_subfeature_enabled

if TYPE_CHECKING:
    from fastapi import APIRouter


@dataclass(frozen=True)
class RouterInfo:
    """One entry in the router registry.

    Attributes:
        module_name: short identifier for logging/diagnostics.
        feature_group: top-level group in ``config.features.groups``.
        feature_sub: optional sub-feature within the group. If empty,
            only the group needs to be enabled.
    """

    module_name: str
    feature_group: str
    feature_sub: str = ""


# Module imports are deferred to ``resolve_routers()`` so this module is
# importable without pulling FastAPI app modules in (matters for tests that
# only need the gate).
_REGISTRY: tuple[RouterInfo, ...] = (
    # --- core: always on ---
    RouterInfo("auth", "users", "users"),
    RouterInfo("meta", "settings", "dashboard_config"),
    RouterInfo("apply", "settings", "dashboard_config"),
    RouterInfo("config_meta", "settings", "dashboard_config"),
    # --- overview (always on for the dashboard's whole purpose) ---
    RouterInfo("overview", "overview"),
    # --- network ---
    RouterInfo("network", "network", "interfaces"),
    RouterInfo("interfaces", "network", "interfaces"),
    # --- hardware ---
    RouterInfo("hardware", "hardware"),
    RouterInfo("media", "hardware", "camera"),
    # --- storage ---
    RouterInfo("storage", "storage", "disks"),
    # --- thermal ---
    RouterInfo("thermal", "thermal", "zones"),
    # --- ota / software ---
    RouterInfo("software", "ota", "system_ota"),
    RouterInfo("ota", "ota", "system_ota"),
    # --- files / logs ---
    RouterInfo("files", "logs", "application"),
    # --- security ---
    RouterInfo("security", "security", "firewall"),
    # --- cloud / telemetry ---
    RouterInfo("cloud", "cloud", "connection"),
    # --- settings / sysadmin ---
    RouterInfo("settings", "settings"),
    RouterInfo("sysadmin", "settings", "system"),
    RouterInfo("devices", "settings", "system"),
    # --- new sub-routers (Layer 2) ---
    RouterInfo("backup", "settings", "backup_restore"),
    RouterInfo("notifications", "settings", "notifications"),
    RouterInfo("maintenance", "settings", "maintenance"),
    RouterInfo("integrations", "settings", "integrations"),
    RouterInfo("access", "security", "access_control"),
    RouterInfo("sessions", "users", "sessions"),
    RouterInfo("roles", "users", "roles"),
    RouterInfo("groups", "users", "groups"),
    RouterInfo("policy", "users", "authentication"),
    RouterInfo("directory", "users", "directory"),
    RouterInfo("telemetry", "telemetry"),
    RouterInfo("jobs", "settings", "maintenance"),
    RouterInfo("logs_journal", "logs", "journal"),
    RouterInfo("files_log", "logs", "application"),
    RouterInfo("about", "settings", "about"),
)


def is_router_enabled(info: RouterInfo) -> bool:
    if not info.feature_sub:
        # Only group-level gating. The group is enabled if any sub-feature
        # is on (or ``_all: true``); we treat "any sub enabled" as
        # "group on" for router-mount purposes.
        from app.config_features import is_group_enabled

        return is_group_enabled(info.feature_group)
    return is_subfeature_enabled(info.feature_group, info.feature_sub)


def enabled_router_infos() -> list[RouterInfo]:
    return [info for info in _REGISTRY if is_router_enabled(info)]


def resolve_routers() -> list[tuple[RouterInfo, APIRouter]]:
    """Return ``(info, router)`` pairs for every enabled router.

    Disabled modules are skipped — they do not register any URL, so the
    SPA receives a 404 if it tries to call them, exactly per spec.
    """
    import importlib
    import logging

    out: list[tuple[RouterInfo, APIRouter]] = []
    for info in _REGISTRY:
        if not is_router_enabled(info):
            logging.getLogger("app.router_modules").info(
                "router disabled: %s (group=%s sub=%s)",
                info.module_name,
                info.feature_group,
                info.feature_sub,
            )
            continue
        try:
            mod = importlib.import_module(f"app.routers.{info.module_name}")
        except ModuleNotFoundError:
            # Future router not yet shipped. Skip silently.
            continue
        router = getattr(mod, "router", None)
        if router is None:
            continue
        out.append((info, router))
    return out
