"""Tests for #88 (ModbusWrite bounds) and #96 (MqttPublish payload cap).

Reference issues:
- #88: add ModbusWrite pydantic schema with bounds
- #96: add MqttPublish pydantic with payload cap
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.util.models import ModbusWrite, MqttPublish

# --- ModbusWrite -----------------------------------------------------------


def test_modbus_write_accepts_valid() -> None:
    m = ModbusWrite(unit_id=1, address=100, count=1, value=42)
    assert m.address == 100
    assert m.value == 42


def test_modbus_write_rejects_huge_address() -> None:
    with pytest.raises(ValidationError):
        ModbusWrite(unit_id=1, address=999_999, count=1, value=0)


def test_modbus_write_rejects_negative_address() -> None:
    with pytest.raises(ValidationError):
        ModbusWrite(unit_id=1, address=-1, count=1, value=0)


def test_modbus_write_rejects_huge_count() -> None:
    with pytest.raises(ValidationError):
        ModbusWrite(unit_id=1, address=0, count=999, value=0)


def test_modbus_write_rejects_invalid_unit_id() -> None:
    with pytest.raises(ValidationError):
        ModbusWrite(unit_id=300, address=0, count=1, value=0)


# --- MqttPublish -----------------------------------------------------------


def test_mqtt_publish_accepts_normal() -> None:
    m = MqttPublish(topic="foo/bar", payload="hello")
    assert m.topic == "foo/bar"
    assert m.payload == "hello"


def test_mqtt_publish_rejects_empty_topic() -> None:
    with pytest.raises(ValidationError):
        MqttPublish(topic="", payload="x")


def test_mqtt_publish_rejects_huge_topic() -> None:
    with pytest.raises(ValidationError):
        MqttPublish(topic="x" * 257, payload="x")


def test_mqtt_publish_rejects_huge_payload() -> None:
    with pytest.raises(ValidationError):
        MqttPublish(topic="x", payload="a" * (64 * 1024 + 1))


def test_mqtt_publish_accepts_64kib_payload() -> None:
    m = MqttPublish(topic="x", payload="a" * (64 * 1024))
    assert len(m.payload) == 64 * 1024


# --- Router integration ---------------------------------------------------


def test_modbus_router_rejects_huge_address(client: object, tokens: dict[str, str]) -> None:
    r = client.post(  # type: ignore[attr-defined]
        "/api/hardware/modbus/write",
        json={"unit_id": 1, "address": 999_999, "count": 1, "value": 0},
        headers={"Authorization": f"Bearer {tokens['admin']}"},
    )
    assert r.status_code == 422, r.text


def test_mqtt_router_rejects_huge_payload(client: object, tokens: dict[str, str]) -> None:
    r = client.post(  # type: ignore[attr-defined]
        "/api/hardware/mqtt/publish",
        json={"topic": "x", "payload": "a" * (64 * 1024 + 1)},
        headers={"Authorization": f"Bearer {tokens['admin']}"},
    )
    assert r.status_code == 422, r.text
