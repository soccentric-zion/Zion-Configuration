"""Regression test for #53 — software.service_action / container_action
must reject unknown actions at the HAL level, not silently succeed.

Routers pre-validate, but the HAL is the canonical enforcement point:
any direct caller (tests, future router) should see the same error
envelope.
"""

from __future__ import annotations

import pytest

from app.errors import ApiError
from app.hal.registry import get_hal


def test_service_action_rejects_unknown() -> None:
    sw = get_hal("software")
    with pytest.raises(ApiError) as excinfo:
        sw.service_action("ssh.service", "frobnicate")
    assert excinfo.value.status_code == 400
    assert excinfo.value.code == "bad_action"
    assert "frobnicate" in str(excinfo.value.message)


def test_container_action_rejects_unknown() -> None:
    sw = get_hal("software")
    # A real container must exist so the 404 path doesn't fire first.
    sw.container_pull("test-img:1.0")
    with pytest.raises(ApiError) as excinfo:
        sw.container_action("test-img", "frobnicate")
    assert excinfo.value.status_code == 400
    assert excinfo.value.code == "bad_action"
    assert "frobnicate" in str(excinfo.value.message)


def test_service_action_known_actions_still_work() -> None:
    """Sanity: the new `else` branch didn't break the happy path."""
    sw = get_hal("software")
    for action in ("start", "stop", "restart", "enable", "disable"):
        result = sw.service_action("ssh.service", action)
        assert result["simulated"] is True
        assert result["unit"]["name"] == "ssh.service"


def test_container_action_known_actions_still_work() -> None:
    sw = get_hal("software")
    sw.container_pull("happy-img:1.0")
    name = "happy-img"
    for action in ("start", "stop", "restart", "remove"):
        result = sw.container_action(name, action)
        assert result["simulated"] is True
        assert result["action"] == action


@pytest.mark.parametrize("garbage", ["frobnicate", "", "STOP", "Start "])
def test_service_action_unknown_never_succeeds(garbage: str) -> None:
    """Parametrised regression for #53 — every unknown service_action
    string must raise ``bad_action`` from the HAL, not silently succeed
    (#103)."""
    sw = get_hal("software")
    with pytest.raises(ApiError) as excinfo:
        sw.service_action("ssh.service", garbage)
    assert excinfo.value.status_code == 400
    assert excinfo.value.code == "bad_action"


@pytest.mark.parametrize("garbage", ["frobnicate", "", "STOP", "exec"])
def test_container_action_unknown_never_succeeds(garbage: str) -> None:
    """Parametrised regression for #53 — every unknown container_action
    string must raise ``bad_action`` from the HAL, not silently succeed
    (#103)."""
    sw = get_hal("software")
    sw.container_pull("box-img:1.0")
    with pytest.raises(ApiError) as excinfo:
        sw.container_action("box-img", garbage)
    assert excinfo.value.status_code == 400
    assert excinfo.value.code == "bad_action"
