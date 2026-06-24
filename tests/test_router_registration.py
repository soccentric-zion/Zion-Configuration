"""Dynamic router registration: disabled features return 404.

Per Features.md §1 and audit.md Config, a feature that is disabled in
``config.yaml`` must produce NO reachable route. The URL is not
registered with FastAPI at all — the SPA nav also drops the entry.
These tests verify the gate is consulted at import time and that a
disabled group truly 404s.
"""

from __future__ import annotations


def test_resolve_routers_returns_a_nonempty_list() -> None:
    from app.router_modules import resolve_routers

    routers = resolve_routers()
    assert routers, "resolve_routers() returned no routers"
    for info, router in routers:
        assert router is not None
        assert info.module_name


def test_resolve_routers_no_duplicates() -> None:
    """The same router module must never be included twice — that
    would double-register every endpoint (and crash FastAPI on
    duplicate path+method)."""
    from app.router_modules import resolve_routers

    prefixes = [r.prefix for _info, r in resolve_routers()]
    assert len(prefixes) == len(set(prefixes)), f"duplicate prefixes: {prefixes}"


def test_disabled_module_url_404s(tmp_path) -> None:
    """Build a fresh app with notifications disabled in config; the
    /api/notifications/channels route should not be registered."""
    import os

    from app.config import get_settings
    from app.config_features import is_subfeature_enabled
    from app.router_modules import resolve_routers

    # Save state so we can restore the conftest's DASHBOARD_CONFIG
    # without reloading the config module (a reload would re-evaluate
    # the module-level ``_db`` singleton and break the next test).
    prev_config = os.environ.get("DASHBOARD_CONFIG")

    cfg = tmp_path / "config.yaml"
    # ``notifications`` is a sub-feature under the ``settings`` group, so
    # the gate fires when ``settings.notifications`` is explicitly false.
    # The explicit ``database.path`` ensures this test never touches the
    # real ``data/dashboard.db`` (or any file outside its tmpdir) — it
    # only inspects the route table, but a stray env-driven DB would
    # be created with the default path if we omitted it.
    cfg.write_text(
        f"database:\n"
        f"  path: \"{tmp_path / 'test.db'}\"\n"
        f"features:\n"
        f"  settings:\n"
        f"    notifications: false\n"
    )
    os.environ["DASHBOARD_CONFIG"] = str(cfg)
    get_settings.cache_clear()  # type: ignore[attr-defined]
    try:
        assert is_subfeature_enabled("settings", "notifications") is False
        routers = resolve_routers()
        prefixes = {r.prefix for _info, r in routers}
        assert not any(p.startswith("/api/notifications") for p in prefixes), (
            f"notifications routes registered despite config disabling them: "
            f"{[p for p in prefixes if 'notifications' in p]}"
        )
    finally:
        if prev_config is None:
            os.environ.pop("DASHBOARD_CONFIG", None)
        else:
            os.environ["DASHBOARD_CONFIG"] = prev_config
        get_settings.cache_clear()  # type: ignore[attr-defined]
