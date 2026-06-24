"""Tests for the pydantic input-validation models (req.md: "Validate inputs
before applying (valid IP/mask, valid port range, rule sanity).").

The unit tests below exercise each model's validator in isolation; the
end-to-end ``set_network`` validation flow is in
``tests/test_apply_rollback.py``.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.util.models import (
    FirewallRuleCreate,
    NetworkLink,
    OtaRollback,
    OtaSlotSwitch,
    PortClose,
    RebootBody,
    ServiceAction,
    ShutdownBody,
    StaticNetworkConfig,
    is_valid_hostname,
)


# --- StaticNetworkConfig ---------------------------------------------------
def test_static_network_dhcp_minimal() -> None:
    cfg = StaticNetworkConfig(method="dhcp", confirm=True)
    assert cfg.method == "dhcp"
    assert cfg.rollback_armed_s == 60  # default


def test_static_network_static_full() -> None:
    cfg = StaticNetworkConfig(
        method="static",
        address="10.0.0.5",
        netmask="255.255.255.0",
        gateway="10.0.0.1",
        dns="8.8.8.8",
        confirm=True,
        rollback_armed_s=120,
    )
    assert cfg.address == "10.0.0.5"
    assert cfg.netmask == "255.255.255.0"


@pytest.mark.parametrize("bad_ip", ["not-an-ip", "256.256.256.256", "::1", "10.0.0"])
def test_static_network_rejects_bad_ip(bad_ip: str) -> None:
    with pytest.raises(ValidationError):
        StaticNetworkConfig(method="static", address=bad_ip, netmask="255.255.255.0", confirm=True)


@pytest.mark.parametrize("bad_mask", ["999.999.999.999", "255.0.255.0"])
def test_static_network_rejects_non_contiguous_mask(bad_mask: str) -> None:
    with pytest.raises(ValidationError):
        StaticNetworkConfig(method="static", address="10.0.0.5", netmask=bad_mask, confirm=True)


def test_static_network_accepts_cidr_mask() -> None:
    cfg = StaticNetworkConfig(method="static", address="10.0.0.5", netmask="/24", confirm=True)
    assert cfg.netmask == "/24"


@pytest.mark.parametrize("bad_method", ["foo", "STATIC", ""])
def test_static_network_rejects_bad_method(bad_method: str) -> None:
    with pytest.raises(ValidationError):
        StaticNetworkConfig(method=bad_method, confirm=True)  # type: ignore[arg-type]


@pytest.mark.parametrize("bad_window", [0, -1, 4, 601, 10_000])
def test_static_network_rejects_bad_rollback_window(bad_window: int) -> None:
    with pytest.raises(ValidationError):
        StaticNetworkConfig(method="dhcp", confirm=True, rollback_armed_s=bad_window)


# --- FirewallRuleCreate ----------------------------------------------------
def test_firewall_rule_minimal() -> None:
    r = FirewallRuleCreate(action="allow", port=80, protocol="tcp", confirm=True)
    assert r.action == "allow"


def test_firewall_rule_rejects_out_of_range_port() -> None:
    with pytest.raises(ValidationError):
        FirewallRuleCreate(action="allow", port=0, protocol="tcp", confirm=True)
    with pytest.raises(ValidationError):
        FirewallRuleCreate(action="allow", port=70000, protocol="tcp", confirm=True)


@pytest.mark.parametrize("bad_action", ["ACCEPT", "drop", ""])
def test_firewall_rule_rejects_bad_action(bad_action: str) -> None:
    with pytest.raises(ValidationError):
        FirewallRuleCreate(action=bad_action, port=80, protocol="tcp", confirm=True)  # type: ignore[arg-type]


@pytest.mark.parametrize("bad_proto", ["icmp", "sctp", ""])
def test_firewall_rule_rejects_bad_protocol(bad_proto: str) -> None:
    with pytest.raises(ValidationError):
        FirewallRuleCreate(action="allow", port=80, protocol=bad_proto, confirm=True)  # type: ignore[arg-type]


@pytest.mark.parametrize("bad_src", ["not-a-cidr", "10.0.0.999", "10.0.0.0/40"])
def test_firewall_rule_rejects_bad_source(bad_src: str) -> None:
    with pytest.raises(ValidationError):
        FirewallRuleCreate(action="allow", port=80, protocol="tcp", source=bad_src, confirm=True)


def test_firewall_rule_accepts_cidr_source() -> None:
    r = FirewallRuleCreate(action="deny", port=22, protocol="tcp", source="10.0.0.0/8", confirm=True)
    assert r.source == "10.0.0.0/8"


# --- PortClose / ServiceAction / NetworkLink / OTA ------------------------
def test_port_close_default() -> None:
    p = PortClose()
    assert p.protocol == "tcp"
    assert p.confirm is False


def test_service_action_default() -> None:
    s = ServiceAction()
    assert s.confirm is False


def test_network_link_default() -> None:
    n = NetworkLink()
    assert n.up is True
    assert n.confirm is False


def test_ota_slot_switch_only_a_b() -> None:
    OtaSlotSwitch(slot="A", confirm=True)
    OtaSlotSwitch(slot="B", confirm=True)
    with pytest.raises(ValidationError):
        OtaSlotSwitch(slot="C", confirm=True)  # type: ignore[arg-type]


def test_ota_rollback_confirm_optional() -> None:
    OtaRollback()
    OtaRollback(confirm=True)


def test_reboot_body_default_now_true() -> None:
    r = RebootBody()
    assert r.now is True


def test_shutdown_body_minimal() -> None:
    ShutdownBody(confirm=True)


# --- hostname validator ----------------------------------------------------
@pytest.mark.parametrize("good", ["host", "host-1", "h", "a" * 63, "my-host.example", "a.b.c.d.example.com"])
def test_is_valid_hostname_accepts(good: str) -> None:
    assert is_valid_hostname(good) is True


@pytest.mark.parametrize("bad", ["", "-host", "host-", "a" * 64, "host with space", "host.", ".host", "x" * 254])
def test_is_valid_hostname_rejects(bad: str) -> None:
    assert is_valid_hostname(bad) is False
