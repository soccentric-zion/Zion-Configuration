"""Regression test for interfaces.configure accepting arbitrary keys (#51).

`POST /api/interfaces/{name}/config` used to merge any keys into the
persisted config blob, so configuring i2c-1 with `{"baud": 100000,
"duty_ns": 5000}` (a PWM field) silently landed duty_ns in the DB.
Fix: per-kind pydantic schema with `extra="forbid"` rejects unknown
keys with 422.
"""

from __future__ import annotations


def _op(tokens: dict[str, str]) -> dict[str, str]:
    return {"Authorization": f"Bearer {tokens['operator']}"}


def test_configure_rejects_unknown_keys_for_kind(client: object, tokens: dict[str, str]) -> None:
    """i2c accepts only {bus, baud}; duty_ns is not a valid i2c field (#51)."""
    op = _op(tokens)
    resp = client.post(  # type: ignore[attr-defined]
        "/api/interfaces/i2c-1/config",
        json={"bus": 1, "baud": 100000, "duty_ns": 5000},
        headers=op,
    )
    assert resp.status_code == 422, resp.text


def test_configure_rejects_unknown_keys_for_uart(client: object, tokens: dict[str, str]) -> None:
    """UART accepts device/baud/bits/parity/stop; bitrate is a CAN field (#51)."""
    op = _op(tokens)
    resp = client.post(  # type: ignore[attr-defined]
        "/api/interfaces/uart0/config",
        json={"device": "/dev/ttyAMA0", "baud": 115200, "bitrate": 500000},
        headers=op,
    )
    assert resp.status_code == 422, resp.text


def test_configure_accepts_valid_uart_config(client: object, tokens: dict[str, str]) -> None:
    """A well-formed UART body still persists (#51)."""
    op = _op(tokens)
    resp = client.post(  # type: ignore[attr-defined]
        "/api/interfaces/uart0/config",
        json={"device": "/dev/ttyAMA0", "baud": 9600, "bits": 8, "parity": "none", "stop": 1},
        headers=op,
    )
    assert resp.status_code == 200, resp.text
    cfg = resp.json()["config"]
    assert cfg["baud"] == 9600
    assert cfg["device"] == "/dev/ttyAMA0"


def test_configure_validates_field_bounds(client: object, tokens: dict[str, str]) -> None:
    """Pydantic Field(ge, le) constraints reject out-of-range values (#51)."""
    op = _op(tokens)
    # I2C baud must be in [1_000, 10_000_000].
    resp = client.post(  # type: ignore[attr-defined]
        "/api/interfaces/i2c-1/config",
        json={"bus": 1, "baud": 10},  # too low
        headers=op,
    )
    assert resp.status_code == 422, resp.text
