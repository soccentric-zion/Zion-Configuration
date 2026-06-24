"""Tests for #121 — HAL bad_action enforcement.

Reference issue: #121 — ``interfaces.operate`` and ``vpn_apply`` must raise
``ApiError(400, "bad_action")`` for unknown op/action pairs, not silently
return 200 with ``supported: False``.

CLAUDE.md Do #53: routers pre-validate action params, but the HAL is the
canonical enforcement point. Direct callers (tests, future routers) must
not get silent 200s.
"""

from __future__ import annotations

import pytest

from app.errors import ApiError
from app.hal.registry import get_hal


def test_interfaces_operate_unknown_op_raises_400() -> None:
    """``interfaces.operate(kind="gpio", op="fly")`` raises bad_action 400."""
    hal = get_hal("interfaces")
    with pytest.raises(ApiError) as exc:
        hal.operate(kind="gpio", name="x", op="fly", params={}, config={})
    assert exc.value.status_code == 400
    assert exc.value.code == "bad_action"


def test_interfaces_operate_unknown_kind_raises_400() -> None:
    """``interfaces.operate(kind="nonexistent", op="read")`` raises bad_action 400."""
    hal = get_hal("interfaces")
    with pytest.raises(ApiError) as exc:
        hal.operate(kind="nonexistent", name="x", op="read", params={}, config={})
    assert exc.value.status_code == 400
    assert exc.value.code == "bad_action"


def test_interfaces_operate_known_op_succeeds() -> None:
    """Known (kind, op) pairs still return the simulated envelope."""
    hal = get_hal("interfaces")
    r = hal.operate(kind="i2c", name="i2c-1", op="scan", params={}, config={})
    assert "devices" in r
    assert r["op"] == "scan"


def test_vpn_apply_unknown_action_raises_400() -> None:
    """``network.vpn_apply(action="teleport")`` raises bad_action 400."""
    hal = get_hal("network")
    with pytest.raises(ApiError) as exc:
        hal.vpn_apply(target="wg0", action="teleport")
    assert exc.value.status_code == 400
    assert exc.value.code == "bad_action"


def test_vpn_apply_known_action_succeeds() -> None:
    """Known actions return the simulated envelope (#42 audit-state-alignment)."""
    hal = get_hal("network")
    r = hal.vpn_apply(target="wg0", action="up")
    assert r["action"] == "up"
    assert "applied" in r
