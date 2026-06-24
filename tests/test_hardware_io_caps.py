"""Regression tests for hardware I/O integer caps (issue #33).

gpio_set, pwm_set, i2c, can_frames, and can_inject previously accepted any
int / string length, letting a malicious caller bloat the in-memory HAL
state or the audit log. They must now reject out-of-range values with
HTTP 422 (path/query) or 400 (body validation).
"""

from __future__ import annotations


def _op(t: dict[str, str]) -> dict[str, str]:
    return {"Authorization": f"Bearer {t['operator']}"}


def _admin(t: dict[str, str]) -> dict[str, str]:
    return {"Authorization": f"Bearer {t['admin']}"}


def test_gpio_set_rejects_huge_pin(client: object, tokens: dict[str, str]) -> None:
    r = client.post(  # type: ignore[attr-defined]
        "/api/hardware/gpio/99999999",
        json={"value": 1},
        headers=_op(tokens),
    )
    assert r.status_code == 422


def test_gpio_set_rejects_negative_pin(client: object, tokens: dict[str, str]) -> None:
    r = client.post(  # type: ignore[attr-defined]
        "/api/hardware/gpio/-1",
        json={"value": 1},
        headers=_op(tokens),
    )
    assert r.status_code == 422


def test_gpio_set_accepts_in_range_pin(client: object, tokens: dict[str, str]) -> None:
    r = client.post(  # type: ignore[attr-defined]
        "/api/hardware/gpio/17",
        json={"value": 1},
        headers=_op(tokens),
    )
    assert r.status_code == 200, r.text


def test_pwm_set_rejects_huge_channel(client: object, tokens: dict[str, str]) -> None:
    r = client.post(  # type: ignore[attr-defined]
        "/api/hardware/pwm/9999",
        json={"period_ns": 1_000_000, "duty_ns": 0},
        headers=_op(tokens),
    )
    assert r.status_code == 422


def test_pwm_set_rejects_huge_period(client: object, tokens: dict[str, str]) -> None:
    r = client.post(  # type: ignore[attr-defined]
        "/api/hardware/pwm/0",
        json={"period_ns": 10**18, "duty_ns": 0},
        headers=_op(tokens),
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "bad_period"


def test_pwm_set_rejects_huge_duty(client: object, tokens: dict[str, str]) -> None:
    r = client.post(  # type: ignore[attr-defined]
        "/api/hardware/pwm/0",
        json={"period_ns": 1_000_000, "duty_ns": 10**18},
        headers=_op(tokens),
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "bad_duty"


def test_pwm_set_accepts_in_range(client: object, tokens: dict[str, str]) -> None:
    r = client.post(  # type: ignore[attr-defined]
        "/api/hardware/pwm/0",
        json={"period_ns": 1_000_000, "duty_ns": 500_000},
        headers=_op(tokens),
    )
    assert r.status_code == 200, r.text


def test_i2c_rejects_huge_bus(client: object, tokens: dict[str, str]) -> None:
    r = client.get(  # type: ignore[attr-defined]
        "/api/hardware/i2c?bus=99999",
        headers=_op(tokens),
    )
    assert r.status_code == 422


def test_i2c_rejects_negative_bus(client: object, tokens: dict[str, str]) -> None:
    r = client.get(  # type: ignore[attr-defined]
        "/api/hardware/i2c?bus=-1",
        headers=_op(tokens),
    )
    assert r.status_code == 422


def test_can_frames_rejects_huge_n(client: object, tokens: dict[str, str]) -> None:
    r = client.get(  # type: ignore[attr-defined]
        "/api/hardware/can/frames?n=999999",
        headers=_op(tokens),
    )
    assert r.status_code == 422


def test_can_inject_rejects_huge_id(client: object, tokens: dict[str, str]) -> None:
    r = client.post(  # type: ignore[attr-defined]
        "/api/hardware/can/inject",
        json={"id": 10**18, "data": "0102030405060708"},
        headers=_admin(tokens),
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "bad_can_id"


def test_can_inject_rejects_huge_data(client: object, tokens: dict[str, str]) -> None:
    r = client.post(  # type: ignore[attr-defined]
        "/api/hardware/can/inject",
        json={"id": 0x123, "data": "A" * 4096},
        headers=_admin(tokens),
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "bad_can_data"


def test_can_inject_accepts_normal_frame(client: object, tokens: dict[str, str]) -> None:
    r = client.post(  # type: ignore[attr-defined]
        "/api/hardware/can/inject",
        json={"id": 0x123, "data": "0102030405060708"},
        headers=_admin(tokens),
    )
    assert r.status_code == 200, r.text
