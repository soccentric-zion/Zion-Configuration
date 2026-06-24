"""Capability manifest: parser + endpoint + route-module map (#116).

Covers audit "config.yaml — master module switch" + "Capability manifest".
The generic-Linux build ships core sections 1-13 enabled and conditional
14-17 declared-but-disabled; a per-platform fork flips modules on/off in
``config.yaml`` only.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.config import (
    ROUTE_TO_MODULE,
    Settings,
    get_capabilities,
    load_settings,
)

# ---------------------------------------------------------------------------
# Manifest parser (unit tests)
# ---------------------------------------------------------------------------


def test_settings_default_manifest_matches_audit() -> None:
    """Empty config still produces a 17-module generic-Linux manifest.

    The audit's §17 baseline is: core 13 enabled, conditional 4 disabled.
    Every default must be a known apply_mode so the SPA can render the badge.
    """
    s = Settings()
    caps = s.capabilities
    assert caps.active_platform == "generic"
    plat = caps.platforms["generic"]
    # 13 enabled core modules + 4 declared-but-disabled = 17 entries.
    assert len(plat.modules) == 17
    enabled = {n for n, m in plat.modules.items() if m.enabled}
    assert "system" in enabled
    assert "ota" in enabled
    assert "factory_reset" in enabled
    # The four conditional ones are declared but disabled.
    for declared in ("coprocessor", "fpga", "industrial_io", "virtualization"):
        assert declared in plat.modules
        assert plat.modules[declared].enabled is False
    # apply_mode is one of the four known values — anything else is a typo.
    for name, m in plat.modules.items():
        assert m.apply_mode in {"live", "next-boot", "guarded", "read-only"}, name


def test_unknown_active_platform_falls_back(tmp_path: Path) -> None:
    """An active_platform pointing at a missing entry falls back to the
    first defined platform — the operator can fix the typo, but the
    dashboard still boots."""
    from app.config import get_active_platform as _gap

    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        """
capabilities:
  active_platform: arches-not-defined
  platforms:
    generic:
      modules:
        system: { enabled: true, apply_mode: live }
"""
    )
    # The function reads through get_settings() (cached at module level),
    # so we have to clear the cache and pre-load with our temp config.
    from app.config import get_settings, load_settings

    get_settings.cache_clear()
    load_settings(cfg)  # populates the cache
    p = _gap()
    # Fallback is the only defined platform ("generic").
    assert p.codename == "generic"
    # Restore the standard test config for the next test.
    get_settings.cache_clear()


def test_rejects_unknown_apply_mode() -> None:
    with pytest.raises(ValueError):
        Settings.model_validate(
            {
                "capabilities": {
                    "platforms": {"generic": {"modules": {"system": {"enabled": True, "apply_mode": "yolo"}}}}
                }
            }
        )


def test_rejects_empty_active_platform() -> None:
    with pytest.raises(ValueError):
        Settings.model_validate({"capabilities": {"active_platform": ""}})


def test_full_audit_block_loads(tmp_path: Path) -> None:
    """The full audit-style YAML loads and round-trips through the parser."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        """
capabilities:
  active_platform: acadia
  platforms:
    acadia:
      name: Raspberry Pi
      codename: Acadia
      description: Yocto.
      modules:
        system:     { enabled: true,  apply_mode: live }
        networking: { enabled: true,  apply_mode: live }
        ota:        { enabled: true,  apply_mode: guarded }
        fpga:       { enabled: false, apply_mode: guarded }
"""
    )
    s = load_settings(cfg)
    assert s.capabilities.active_platform == "acadia"
    plat = s.capabilities.platforms["acadia"]
    assert plat.name == "Raspberry Pi"
    assert plat.codename == "Acadia"
    assert plat.modules["ota"].enabled is True
    assert plat.modules["ota"].apply_mode == "guarded"
    assert plat.modules["fpga"].enabled is False


def test_get_capabilities_returns_serializable_dict() -> None:
    """The endpoint shape (audit §config.yaml — master module switch)."""
    caps = get_capabilities()
    assert set(caps) >= {
        "active_platform",
        "name",
        "codename",
        "description",
        "modules",
    }
    # Every module entry is a plain dict with the two declared fields.
    for _name, m in caps["modules"].items():
        assert isinstance(m, dict)
        assert "enabled" in m
        assert "apply_mode" in m


# ---------------------------------------------------------------------------
# Route -> module map (#116, audit)
# ---------------------------------------------------------------------------


def test_route_to_module_covers_every_spa_route() -> None:
    """Every SPA route declared in app.js maps to a known module.

    If a new page is added to the SPA without a matching entry in
    ROUTE_TO_MODULE, the capability gate silently treats it as enabled
    (which is safe — the worst case is "no gating"). The point of this
    test is to make the mapping explicit so reviewers notice when a
    page slips through.
    """
    spa_routes = {
        "/home",
        "/network",
        "/hardware",
        "/interfaces",
        "/media",
        "/storage",
        "/thermal",
        "/software",
        "/sysadmin",
        "/ota",
        "/files",
        "/security",
        "/cloud",
        "/settings",
    }
    assert set(ROUTE_TO_MODULE) == spa_routes
    # Every target module is one of the audit's 17 names.
    audit_modules = {
        "system",
        "networking",
        "peripherals",
        "boot",
        "services",
        "logs",
        "health",
        "ota",
        "security",
        "storage",
        "users",
        "backup",
        "factory_reset",
        "coprocessor",
        "fpga",
        "industrial_io",
        "virtualization",
    }
    assert set(ROUTE_TO_MODULE.values()) <= audit_modules


# ---------------------------------------------------------------------------
# /api/system/capabilities endpoint (viewer-readable)
# ---------------------------------------------------------------------------


def test_capabilities_endpoint_viewer_readable(client: object, tokens: dict[str, str]) -> None:
    headers = {"Authorization": f"Bearer {tokens['viewer']}"}
    res = client.get("/api/system/capabilities", headers=headers)  # type: ignore[attr-defined]
    assert res.status_code == 200
    body = res.json()
    assert "modules" in body
    # Generic build: ota enabled, fpga disabled.
    assert body["modules"]["ota"]["enabled"] is True
    assert body["modules"]["fpga"]["enabled"] is False


def test_capabilities_endpoint_requires_auth(client: object) -> None:
    res = client.get("/api/system/capabilities")  # type: ignore[attr-defined]
    assert res.status_code in (401, 403)
