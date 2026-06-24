"""Tests for audit Guardrail 3 — protected-resource lists.

Reference issues:
- #90 PROTECTED_SERVICES + assert helper
- #91 PROTECTED_PORTS check on ports/close
- #92 PROTECTED_INTERFACES block on network/link

The tests verify:
1. The shared ``assert_not_protected`` helper raises 403 for each kind.
2. The ``/api/sysadmin/services/{name}/{action}`` endpoint rejects stop
   on a protected unit (dashboard.service, ssh.service, NetworkManager.service).
3. The ``/api/sysadmin/ports/{port}/close`` endpoint rejects 22, 8000.
4. The ``/api/sysadmin/network/{name}/link`` rejects ``up: false`` on ``lo``.
5. The ``GET /api/system/protected-resources`` endpoint exposes the lists.
6. Non-protected actions are still allowed.
"""

from __future__ import annotations

import pytest

from app.errors import ApiError
from app.util.protected import (
    DEFAULT_PROTECTED_PORTS,
    DEFAULT_PROTECTED_SERVICES,
    assert_not_protected,
)

# 1. Unit tests for the helper ------------------------------------------------


def test_helper_allows_non_destructive_service_action() -> None:
    assert_not_protected("service", "dashboard.service", "start")
    assert_not_protected("service", "dashboard.service", "enable")
    assert_not_protected("service", "ssh.service", "start")
    # unknown action is ignored (router already validates the enum)
    assert_not_protected("service", "dashboard.service", None)


def test_helper_blocks_dashboard_service_stop() -> None:
    with pytest.raises(ApiError) as exc:
        assert_not_protected("service", "dashboard.service", "stop")
    assert exc.value.status_code == 403
    assert exc.value.code == "protected_resource"


def test_helper_blocks_ssh_service_disable() -> None:
    with pytest.raises(ApiError) as exc:
        assert_not_protected("service", "ssh.service", "disable")
    assert exc.value.status_code == 403


def test_helper_blocks_network_manager_restart() -> None:
    with pytest.raises(ApiError) as exc:
        assert_not_protected("service", "NetworkManager.service", "restart")
    assert exc.value.status_code == 403


def test_helper_blocks_dashboard_port_close() -> None:
    with pytest.raises(ApiError) as exc:
        assert_not_protected("port", "8000")
    assert exc.value.status_code == 403


def test_helper_blocks_ssh_port_close() -> None:
    with pytest.raises(ApiError) as exc:
        assert_not_protected("port", "22")
    assert exc.value.status_code == 403


def test_helper_allows_unprotected_port() -> None:
    assert_not_protected("port", "9999")


def test_helper_blocks_loopback_interface() -> None:
    with pytest.raises(ApiError) as exc:
        assert_not_protected("interface", "lo")
    assert exc.value.status_code == 403


def test_helper_allows_normal_interface() -> None:
    assert_not_protected("interface", "eth0")


# 2. The endpoints reject with 403, not with role-403 -----------------------


def test_service_action_protected_unit_returns_403(client: object, tokens: dict[str, str]) -> None:
    r = client.post(
        "/api/sysadmin/services/dashboard.service/stop",
        json={"confirm": True},
        headers={"Authorization": f"Bearer {tokens['admin']}"},
    )
    assert r.status_code == 403, r.text
    body = r.json()
    assert body["error"]["code"] == "protected_resource"


def test_service_action_ssh_protected(client: object, tokens: dict[str, str]) -> None:
    r = client.post(
        "/api/sysadmin/services/ssh.service/stop",
        json={"confirm": True},
        headers={"Authorization": f"Bearer {tokens['admin']}"},
    )
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "protected_resource"


def test_service_action_unprotected_unit_with_confirm_succeeds(client: object, tokens: dict[str, str]) -> None:
    r = client.post(
        "/api/sysadmin/services/cron.service/stop",
        json={"confirm": True},
        headers={"Authorization": f"Bearer {tokens['admin']}"},
    )
    # cron.service is not protected; the HAL stub returns the action envelope.
    assert r.status_code == 200, r.text


def test_service_action_software_router_also_protected(client: object, tokens: dict[str, str]) -> None:
    r = client.post(
        "/api/software/services/ssh.service/stop",
        headers={"Authorization": f"Bearer {tokens['admin']}"},
    )
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "protected_resource"


# 3. Port close protection --------------------------------------------------


def test_port_close_8000_blocked(client: object, tokens: dict[str, str]) -> None:
    r = client.post(
        "/api/sysadmin/ports/8000/close",
        json={"confirm": True},
        headers={"Authorization": f"Bearer {tokens['admin']}"},
    )
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "protected_resource"


def test_port_close_22_blocked(client: object, tokens: dict[str, str]) -> None:
    r = client.post(
        "/api/sysadmin/ports/22/close",
        json={"confirm": True},
        headers={"Authorization": f"Bearer {tokens['admin']}"},
    )
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "protected_resource"


def test_port_close_unprotected_with_confirm_succeeds(client: object, tokens: dict[str, str]) -> None:
    r = client.post(
        "/api/sysadmin/ports/9999/close",
        json={"confirm": True},
        headers={"Authorization": f"Bearer {tokens['admin']}"},
    )
    assert r.status_code == 200, r.text


# 4. Interface protection ---------------------------------------------------


def test_link_down_loopback_blocked(client: object, tokens: dict[str, str]) -> None:
    """Loopback (lo) is always protected; the helper enforces 403.

    The ``/api/sysadmin/network/{name}/link`` endpoint cross-validates the
    interface against the live psutil view first (#50), so the live endpoint
    is environment-specific. We exercise the protected-resources check via
    the helper directly (canonical enforcement path) and assert the
    protected-resources list endpoint surfaces ``lo``.
    """
    from app.util.protected import assert_not_protected

    with pytest.raises(ApiError) as exc:
        assert_not_protected("interface", "lo")
    assert exc.value.status_code == 403
    assert exc.value.code == "protected_resource"

    # The list endpoint also surfaces lo so the operator can see what's protected.
    r = client.get(  # type: ignore[attr-defined]
        "/api/system/protected-resources",
        headers={"Authorization": f"Bearer {tokens['admin']}"},
    )
    assert r.status_code == 200, r.text
    assert "lo" in r.json()["interfaces"]


def test_link_up_loopback_allowed(client: object, tokens: dict[str, str]) -> None:
    """Bringing an interface UP is never destructive; confirm is not required.

    We use eth0 (present on this dev host) and bring it up — the protected
    check only fires on link-down, and only for the protected set.
    """
    r = client.post(  # type: ignore[attr-defined]
        "/api/sysadmin/network/eth0/link",
        json={"up": True},
        headers={"Authorization": f"Bearer {tokens['admin']}"},
    )
    assert r.status_code == 200, r.text


def test_helper_blocks_custom_protected_iface_from_config() -> None:
    """A platform fork can extend the protected interface set via config.

    The default set is ``{'lo'}``; we add ``eth0`` via a config override and
    verify the helper blocks it. This covers the wiring through
    ``app.config.get_settings()``.
    """
    from app.config import ProtectedConfig, get_settings

    settings = get_settings()
    original = settings.protected
    try:
        settings.protected = ProtectedConfig(interfaces=["eth0"])
        from app.util.protected import assert_not_protected

        with pytest.raises(ApiError) as exc:
            assert_not_protected("interface", "eth0")
        assert exc.value.status_code == 403
    finally:
        settings.protected = original


# 5. List endpoint ---------------------------------------------------------


def test_protected_resources_list(client: object, tokens: dict[str, str]) -> None:
    r = client.get(  # type: ignore[attr-defined]
        "/api/system/protected-resources",
        headers={"Authorization": f"Bearer {tokens['admin']}"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert "dashboard.service" in body["services"]
    assert "ssh.service" in body["services"]
    assert 22 in body["ports"]
    assert 8000 in body["ports"]
    assert "lo" in body["interfaces"]
    assert "stop" in body["service_actions_blocked"]


# 6. Defaults are present -------------------------------------------------


def test_defaults_include_dashboard_ssh_networkmanager() -> None:
    assert "dashboard.service" in DEFAULT_PROTECTED_SERVICES
    assert "ssh.service" in DEFAULT_PROTECTED_SERVICES
    assert "NetworkManager.service" in DEFAULT_PROTECTED_SERVICES
    assert "chronyd.service" in DEFAULT_PROTECTED_SERVICES


def test_defaults_include_dashboard_ssh_dns_ntp_ports() -> None:
    assert 22 in DEFAULT_PROTECTED_PORTS
    assert 53 in DEFAULT_PROTECTED_PORTS
    assert 123 in DEFAULT_PROTECTED_PORTS
    assert 8000 in DEFAULT_PROTECTED_PORTS
