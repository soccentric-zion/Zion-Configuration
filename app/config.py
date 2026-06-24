"""Configuration loading and validation.

The whole application is configured from a single ``config.yaml``. This module
loads it, validates it with pydantic, and exposes a typed, cached settings
object. Every field has a sensible default so the dashboard runs out of the box
on a dev laptop with no config file at all.
"""

from __future__ import annotations

import os
import secrets
from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator


class ServerConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8000
    local_vendor: bool = False

    @field_validator("port")
    @classmethod
    def _valid_port(cls, v: int) -> int:
        if not 1 <= v <= 65535:
            raise ValueError("server.port must be between 1 and 65535")
        return v


class AuthConfig(BaseModel):
    jwt_secret: str = Field(default_factory=lambda: secrets.token_urlsafe(48))
    jwt_algorithm: str = "HS256"
    # 10 min default. JWT lives in localStorage (#41) — a stolen token
    # grants full access, so we keep the window as short as the SPA's
    # re-login friction allows. The full switch to HttpOnly cookies is
    # a breaking SPA change and is tracked separately.
    access_token_ttl_minutes: int = 10
    max_failed_logins: int = 5
    lockout_minutes: int = 15
    default_admin_user: str = "admin"
    default_admin_password: str = "admin"

    @field_validator("jwt_secret")
    @classmethod
    def _non_empty_secret(cls, v: str) -> str:
        # A placeholder/empty secret is replaced with a random one so the app
        # never runs with a guessable signing key.
        if not v or v == "change-me-to-a-long-random-string":
            return secrets.token_urlsafe(48)
        return v


class DatabaseConfig(BaseModel):
    path: str = "data/dashboard.db"


class SwupdateConfig(BaseModel):
    base_url: str = "http://127.0.0.1:8090"
    ws_url: str = "ws://127.0.0.1:8090/ws"


class FilesConfig(BaseModel):
    allowed_paths: list[str] = Field(default_factory=lambda: ["/etc", "/var/log", "/data"])
    max_edit_bytes: int = 1_048_576


class SamplingConfig(BaseModel):
    metrics_interval_seconds: float = 1.0
    history_interval_seconds: float = 10.0
    history_retention_days: int = 7


class ThresholdsConfig(BaseModel):
    cpu_temp_warn_c: float = 70
    cpu_temp_crit_c: float = 85
    disk_usage_warn_pct: float = 85
    mem_usage_warn_pct: float = 90


class SimulationConfig(BaseModel):
    force: bool = False


class ProtectedConfig(BaseModel):
    """Override lists for protected resources (audit Guardrail 3).

    Each field, when non-empty, is *added* to the corresponding default set in
    ``app.util.protected``. The defaults are always kept (e.g. dashboard.service,
    ssh.service, port 8000). An empty list means "use defaults only".
    """

    services: list[str] = Field(default_factory=list)
    ports: list[int] = Field(default_factory=list)
    interfaces: list[str] = Field(default_factory=list)


# Capability manifest (audit "config.yaml — master module switch", #116).
#
# The manifest advertises which high-level modules the active platform exposes
# to the dashboard. The SPA hides nav entries and blocks routes whose module
# is `enabled: false`, and the apply pipeline uses `apply_mode` to decide
# whether a state change is live, next-boot, guarded, or read-only.
#
# For the generic Linux build the audit ships all 13 core modules enabled and
# the 4 conditional ones (coprocessor / fpga / industrial_io / virtualization)
# declared but disabled. A per-platform fork sets `active_platform` and the
# per-module flags — no code changes needed.
_ApplyMode = Literal["live", "next-boot", "guarded", "read-only"]


def _default_modules() -> dict[str, ModuleConfig]:
    """Generic-Linux capability manifest (audit §17).

    Core sections 1-13 are enabled; conditional 14-17 are declared so a per-
    platform fork can flip them on by editing config.yaml — no code change.
    """
    enabled: list[tuple[str, _ApplyMode]] = [
        ("system", "live"),
        ("networking", "live"),
        ("peripherals", "next-boot"),
        ("boot", "guarded"),
        ("services", "live"),
        ("logs", "live"),
        ("health", "read-only"),
        ("ota", "guarded"),
        ("security", "guarded"),
        ("storage", "guarded"),
        ("users", "live"),
        ("backup", "guarded"),
        ("factory_reset", "guarded"),
    ]
    disabled: list[tuple[str, _ApplyMode]] = [
        ("coprocessor", "guarded"),
        ("fpga", "guarded"),
        ("industrial_io", "next-boot"),
        ("virtualization", "guarded"),
    ]
    return {
        name: ModuleConfig(enabled=(name in {n for n, _ in enabled}), apply_mode=mode)
        for name, mode in enabled + disabled
    }


class ModuleConfig(BaseModel):
    """One entry in the capability manifest (audit Config)."""

    enabled: bool = True
    apply_mode: _ApplyMode = "live"

    @field_validator("apply_mode")
    @classmethod
    def _known_apply_mode(cls, v: str) -> str:
        allowed = {"live", "next-boot", "guarded", "read-only"}
        if v not in allowed:
            raise ValueError(f"apply_mode must be one of {sorted(allowed)}")
        return v


class PlatformConfig(BaseModel):
    """A single platform block in the capability manifest.

    The audit reserves `name` for the customer-facing silicon name and
    `codename` for the internal-only project name. The SPA only ever shows
    `name` in customer-facing copy.
    """

    name: str = "Generic Linux"
    codename: str = "generic"
    description: str = ""
    modules: dict[str, ModuleConfig] = Field(default_factory=_default_modules)


# Active-platform catalog. A fork that wants to add a new silicon
# (e.g. `arches: { ... }`) extends this mapping at module-import time; the
# rest of the app only ever reads through `get_settings().capabilities`.
_DEFAULT_PLATFORMS: dict[str, PlatformConfig] = {
    "generic": PlatformConfig(
        name="Generic Linux",
        codename="generic",
        description="Generic embedded Linux build.",
    ),
    "acadia": PlatformConfig(
        name="Raspberry Pi",
        codename="Acadia",
        description=(
            "Custom Yocto-based embedded Linux turning the world's most "
            "popular SBC into a real industrial product platform."
        ),
    ),
}


class CapabilitiesConfig(BaseModel):
    """Top-level capability manifest block (audit "Capability manifest")."""

    active_platform: str = "generic"
    platforms: dict[str, PlatformConfig] = Field(default_factory=lambda: dict(_DEFAULT_PLATFORMS))

    @field_validator("active_platform")
    @classmethod
    def _known_platform(cls, v: str) -> str:
        # Cross-checked at use-site against `platforms`; here we only
        # guarantee a non-empty string. An unknown active_platform
        # resolves to the first defined platform at read time.
        if not v:
            raise ValueError("active_platform must be a non-empty string")
        return v


# --- feature tree (Features.md) ---------------------------------------------
# The Features.md spec mandates a per-sub-feature toggleable tree: groups →
# panels → individual sub-features. Absent = hidden completely (no route, no
# nav entry, no panel). This block is the source of truth. Backend refuses
# to register routers for disabled groups, and the SPA reads the resolved
# tree at /api/config/features to build the entire UI.
#
# The defaults below cover the generic Linux build; a per-platform fork
# overrides only the entries that differ from the generic.
# Sentinel `True` is a flag for "all sub-features in this group enabled"
# — the resolver flattens it to the canonical tree at startup.
def _all_on() -> dict[str, object]:
    return {"_all": True}


_GroupName = Literal[
    "overview",
    "network",
    "hardware",
    "storage",
    "thermal",
    "ota",
    "telemetry",
    "cloud",
    "security",
    "users",
    "logs",
    "settings",
]


# Default feature tree for the generic Linux build. Every group has every
# sub-feature enabled, except platform-specific groups (`coprocessor`, `fpga`,
# `pru`, `gpu`, `industrial_io`) which are off by default and surface only on
# the relevant platform's config block.
def _default_features() -> dict[str, dict[str, object]]:
    return {
        "overview": _all_on(),
        "network": _all_on(),
        "hardware": _all_on(),
        "storage": _all_on(),
        "thermal": _all_on(),
        "ota": _all_on(),
        "telemetry": _all_on(),
        "cloud": _all_on(),
        "security": _all_on(),
        "users": _all_on(),
        "logs": _all_on(),
        "settings": _all_on(),
        # Platform-specific: only flip on via platform config.yaml.
        "coprocessor": {"_all": False},
        "fpga": {"_all": False},
        "pru": {"_all": False},
        "gpu": {"_all": False},
        "industrial_io": {"_all": False},
    }


class FeaturesConfig(BaseModel):
    """Per-sub-feature toggle tree (Features.md §1).

    Each top-level key is a group (e.g. ``network``). Each value is either
    ``{"_all": true|false}`` (toggle the whole group) or a mapping of
    sub-feature → bool. Missing keys default to enabled — so a minimal
    config block can list only the few features the operator wants to
    disable.

    Accepts both the natural operator format::

        features:
          settings:
            notifications: false

    and the explicit-pydantic format::

        features:
          groups:
            settings:
              notifications: false
    """

    groups: dict[str, dict[str, object]] = Field(default_factory=lambda: _default_features())

    @model_validator(mode="before")
    @classmethod
    def _accept_natural_format(cls, v: object) -> object:
        """If the caller passed a flat ``{group: ...}`` mapping (the
        natural YAML form operators write), wrap it in ``{"groups": ...}``
        so pydantic routes it into the right field."""
        if isinstance(v, dict) and "groups" not in v and any(isinstance(val, dict) for val in v.values()):
            return {"groups": v}
        return v

    def is_group_enabled(self, group: str) -> bool:
        if group not in self.groups:
            return True
        grp = self.groups[group]
        if not isinstance(grp, dict):
            return True
        if grp.get("_all") is False:
            # Group explicitly disabled — only return True if the group dict
            # explicitly opts some sub-features in (override pattern).
            return any(v is True for k, v in grp.items() if k != "_all")
        if grp.get("_all") is True:
            return True
        # If neither _all nor any sub-feature, treat as enabled.
        return True

    def is_subfeature_enabled(self, group: str, sub: str) -> bool:
        if group not in self.groups:
            return True
        grp = self.groups[group]
        if not isinstance(grp, dict):
            return True
        if grp.get("_all") is False:
            # Group disabled, but a per-sub override may re-enable.
            v = grp.get(sub)
            return v is True
        if grp.get("_all") is True:
            return True
        # Partial: only sub-features explicitly listed are honored; absent
        # = enabled (preserves "absent = hidden completely" for explicit
        # ``False`` and "absent = visible" otherwise).
        v = grp.get(sub)
        if v is None:
            return True
        return bool(v)


class Settings(BaseModel):
    server: ServerConfig = Field(default_factory=ServerConfig)
    auth: AuthConfig = Field(default_factory=AuthConfig)
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    swupdate: SwupdateConfig = Field(default_factory=SwupdateConfig)
    files: FilesConfig = Field(default_factory=FilesConfig)
    sampling: SamplingConfig = Field(default_factory=SamplingConfig)
    thresholds: ThresholdsConfig = Field(default_factory=ThresholdsConfig)
    simulation: SimulationConfig = Field(default_factory=SimulationConfig)
    protected: ProtectedConfig = Field(default_factory=ProtectedConfig)
    capabilities: CapabilitiesConfig = Field(default_factory=CapabilitiesConfig)
    features: FeaturesConfig = Field(default_factory=FeaturesConfig)


def _config_path() -> Path:
    """Resolve the config file path (env override -> ./config.yaml)."""
    env = os.environ.get("DASHBOARD_CONFIG")
    if env:
        return Path(env)
    return Path("config.yaml")


def load_settings(path: Path | None = None) -> Settings:
    """Load and validate settings from ``config.yaml`` (or defaults)."""
    cfg_path = path or _config_path()
    data: dict[str, object] = {}
    if cfg_path.exists():
        raw = yaml.safe_load(cfg_path.read_text()) or {}
        if not isinstance(raw, dict):
            raise ValueError(f"{cfg_path} must contain a YAML mapping at the top level")
        data = raw
    return Settings.model_validate(data)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings accessor used throughout the app."""
    return load_settings()


# --- capability manifest helpers -------------------------------------------
# The SPA's "hide disabled modules" feature (#116, audit Config) drives its
# nav and route-gate from this resolved view. Kept in app.config so the
# platform block and the SPA-facing dict share a single source of truth.

# Map: SPA hash route -> manifest module name. The audit's 17 module list
# doesn't 1:1 with the 14 SPA routes; entries that share a module group
# collapse onto a single capability (e.g. `/hardware` and `/interfaces` are
# both `peripherals`).
ROUTE_TO_MODULE: dict[str, str] = {
    "/home": "system",
    "/network": "networking",
    "/hardware": "peripherals",
    "/interfaces": "peripherals",
    "/media": "peripherals",
    "/storage": "storage",
    "/thermal": "health",
    "/software": "system",
    "/sysadmin": "services",
    "/ota": "ota",
    "/files": "logs",
    "/security": "security",
    "/cloud": "system",
    "/settings": "users",
}


def get_active_platform() -> PlatformConfig:
    """Return the PlatformConfig named by ``active_platform``.

    Falls back to the first defined platform if the active_platform string
    is unknown (e.g. a fork removed a platform and didn't update the
    active_platform). The fallback path is logged at startup so the
    operator notices.
    """
    import logging

    cap = get_settings().capabilities
    plat = cap.platforms.get(cap.active_platform)
    if plat is None:
        plat = next(iter(cap.platforms.values()))
        logging.getLogger("app.config").warning(
            "active_platform=%r not in platforms=%s; falling back to %r",
            cap.active_platform,
            sorted(cap.platforms),
            plat.codename,
        )
    return plat


def get_capabilities() -> dict[str, object]:
    """Return the active platform as a JSON-serializable dict.

    Shape (audit "config.yaml — master module switch"):
        {
            "active_platform": "acadia",
            "name": "Raspberry Pi",
            "codename": "Acadia",
            "description": "...",
            "modules": {"system": {"enabled": true, "apply_mode": "live"}, ...},
        }
    """
    plat = get_active_platform()
    return {
        "active_platform": get_settings().capabilities.active_platform,
        "name": plat.name,
        "codename": plat.codename,
        "description": plat.description,
        "modules": {n: m.model_dump() for n, m in plat.modules.items()},
    }
