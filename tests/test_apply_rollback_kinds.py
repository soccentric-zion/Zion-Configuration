"""Tests for rollback_kind handler registry (#109, audit Guardrail 2).

audit.md Guardrail 2: every guarded write must be auto-reversible by
the watchdog. Previously ``_apply_rollback`` only handled
``net_interfaces`` and surfaced unknown kinds as a 500 (silently
swallowed by the watchdog). Now each kind registers a handler via
``register_rollback_kind`` decorator and unknown kinds raise
explicitly so an operator (or the watchdog's warning log) can see
the gap.
"""

from __future__ import annotations

import pytest

from app.errors import ApiError
from app.tasks import apply_state
from app.tasks.apply_state import ApplyPlan, register_rollback_kind


def _admin(tokens: dict[str, str]) -> dict[str, str]:
    return {"Authorization": f"Bearer {tokens['admin']}"}


def test_known_kinds_registered() -> None:
    """All production rollback kinds are registered at import time."""
    kinds = apply_state.known_rollback_kinds()
    for k in (
        "net_interfaces",
        "ota_slot",
        "ota_install",
        "boot_env",
        "factory_reset",
        "service_unit",
        "restore_apply",
    ):
        assert k in kinds, f"missing rollback handler for {k}"


def test_unknown_kind_raises_explicitly() -> None:
    """Arming a plan with an unregistered kind surfaces an
    ``unknown_rollback_kind`` ApiError instead of a silent 500 swallow."""
    plan = ApplyPlan(
        id=999,
        target="x",
        before={},
        after={},
        rollback_kind="not_a_real_kind",
        armed_until="2099-01-01T00:00:00",
        status="armed",
    )
    with pytest.raises(ApiError) as ei:
        import asyncio

        asyncio.run(apply_state._apply_rollback(plan))
    assert ei.value.code == "unknown_rollback_kind"
    assert ei.value.status_code == 500


def test_register_decorator_adds_handler() -> None:
    """``register_rollback_kind`` is the canonical entry point; tests can
    register a custom kind without monkey-patching the module dict."""

    @register_rollback_kind("test_custom_kind")
    async def _handler(plan: ApplyPlan) -> None:
        return None

    assert "test_custom_kind" in apply_state.known_rollback_kinds()


def test_net_interfaces_handler_restores_row(client: object, tokens: dict[str, str]) -> None:
    """The net_interfaces handler writes the before snapshot back to the
    DB — round-trip a real ``set_network`` so we exercise the production
    arm → rollback path."""
    # Use wlan0 (seeded as dhcp) so the post-rollback check is unambiguous
    # even after earlier tests leave eth0 in some other state.
    set_resp = client.post(  # type: ignore[attr-defined]
        "/api/settings/network/wlan0",
        json={"method": "static", "address": "192.168.99.10", "netmask": "255.255.255.0", "confirm": True},
        headers=_admin(tokens),
    )
    assert set_resp.status_code == 200, set_resp.text
    plan_id = set_resp.json()["plan_id"]
    # Now rollback manually.
    resp = client.post(  # type: ignore[attr-defined]
        "/api/settings/network/wlan0/apply/rollback",
        json={"plan_id": plan_id, "confirm": True},
        headers=_admin(tokens),
    )
    assert resp.status_code == 200, resp.text
    # Read back — wlan0 should be back to dhcp.
    nets = client.get(  # type: ignore[attr-defined]
        "/api/sysadmin/network",
        headers=_admin(tokens),
    ).json()
    wlan0 = next(i for i in nets["interfaces"] if i["name"] == "wlan0")
    assert wlan0["method"] == "dhcp"
