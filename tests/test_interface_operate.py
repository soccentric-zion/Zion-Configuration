"""Tests for #145 — interface_operate, uart_write, mqtt_publish audit
rows include the params/data/payload so the action log can
reconstruct what was written to the bus.

audit.md Guardrail 1: every state-changing action must be auditable
and reconstructable from the audit log.
"""

from __future__ import annotations

import json


def _h(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _op(t: dict[str, str]) -> dict[str, str]:
    return _h(t["operator"])


def _admin(t: dict[str, str]) -> dict[str, str]:
    return _h(t["admin"])


# --- interface_operate -----------------------------------------------------
def test_interface_operate_audit_includes_params(client: object, tokens: dict[str, str]) -> None:
    """A GPIO write should land in the audit log with the params dict
    so a replay can reconstruct the bus traffic (#145)."""
    resp = client.post(  # type: ignore[attr-defined]
        "/api/interfaces/gpio/operate",
        json={"op": "write", "params": {"pin": 17, "value": 1}},
        headers=_op(tokens),
    )
    assert resp.status_code == 200, resp.text
    audit = client.get(  # type: ignore[attr-defined]
        "/api/security/audit?action=interface_operate&limit=5",
        headers=_admin(tokens),
    )
    assert audit.status_code == 200, audit.text
    rows = audit.json().get("entries") or []
    # Find the most recent matching row.
    assert rows, "expected at least one interface_operate audit row"
    row = rows[0]
    assert row["target"] == "gpio:write"
    # The audit row carries the JSON-encoded params (sort_keys for stability).
    expected = json.dumps({"pin": 17, "value": 1}, sort_keys=True)
    assert row["new_value"] == expected, (row, expected)


def test_interface_operate_audit_strips_confirm_and_plan_id(client: object, tokens: dict[str, str]) -> None:
    """The audit row must NOT include the confirm/plan_id keys so the
    row stays focused on the bus payload."""
    resp = client.post(  # type: ignore[attr-defined]
        "/api/interfaces/gpio/operate",
        json={"op": "write", "params": {"pin": 17, "value": 0, "confirm": True, "plan_id": 99}},
        headers=_op(tokens),
    )
    assert resp.status_code == 200, resp.text
    audit = client.get(  # type: ignore[attr-defined]
        "/api/security/audit?action=interface_operate&limit=1",
        headers=_admin(tokens),
    )
    assert audit.status_code == 200, audit.text
    row = audit.json()["entries"][0]
    parsed = json.loads(row["new_value"])
    assert parsed == {"pin": 17, "value": 0}, parsed


def test_interface_operate_audit_empty_params(client: object, tokens: dict[str, str]) -> None:
    """When params is empty the audit row carries an empty string
    rather than `{}` so the action log doesn't bloat."""
    resp = client.post(  # type: ignore[attr-defined]
        "/api/interfaces/modbus/operate",
        json={"op": "read", "params": {}},
        headers=_op(tokens),
    )
    assert resp.status_code == 200, resp.text
    audit = client.get(  # type: ignore[attr-defined]
        "/api/security/audit?action=interface_operate&limit=1",
        headers=_admin(tokens),
    )
    assert audit.status_code == 200, audit.text
    row = audit.json()["entries"][0]
    assert row["new_value"] == ""


# --- uart_write ------------------------------------------------------------
def _open_uart_port(client: object, tokens: dict[str, str], port: str) -> None:
    """Open the simulated UART port so write succeeds."""
    resp = client.post(  # type: ignore[attr-defined]
        f"/api/hardware/uart/{port}/open",
        headers=_op(tokens),
    )
    assert resp.status_code == 200, (port, resp.text)


def test_uart_write_audit_includes_data(client: object, tokens: dict[str, str]) -> None:
    """The uart_write audit row must carry the actual bytes written
    (truncated to 256 chars with a marker) so replay can reconstruct
    the bus traffic (#145)."""
    payload = "AT+CMD=hello\n"
    _open_uart_port(client, tokens, "ttyUSB0")
    resp = client.post(  # type: ignore[attr-defined]
        "/api/hardware/uart/ttyUSB0/write",
        json={"data": payload},
        headers=_op(tokens),
    )
    assert resp.status_code == 200, resp.text
    audit = client.get(  # type: ignore[attr-defined]
        "/api/security/audit?action=uart_write&limit=5",
        headers=_admin(tokens),
    )
    assert audit.status_code == 200, audit.text
    rows = audit.json().get("entries") or []
    assert rows
    assert rows[0]["target"] == "ttyUSB0"
    # Small payloads are stored verbatim.
    assert rows[0]["new_value"] == payload, rows[0]


def test_uart_write_audit_truncates_long_payloads(client: object, tokens: dict[str, str]) -> None:
    """Long payloads are truncated to 256 chars with a marker so the
    audit log stays bounded but the operator can still tell it was
    truncated."""
    long_data = "X" * 1024
    _open_uart_port(client, tokens, "ttyUSB0")
    resp = client.post(  # type: ignore[attr-defined]
        "/api/hardware/uart/ttyUSB0/write",
        json={"data": long_data},
        headers=_op(tokens),
    )
    assert resp.status_code == 200, resp.text
    audit = client.get(  # type: ignore[attr-defined]
        "/api/security/audit?action=uart_write&limit=1",
        headers=_admin(tokens),
    )
    assert audit.status_code == 200, audit.text
    row = audit.json()["entries"][0]
    assert "truncated" in row["new_value"]
    assert "total=1024" in row["new_value"]


# --- mqtt_publish ----------------------------------------------------------
def test_mqtt_publish_audit_short_payload(client: object, tokens: dict[str, str]) -> None:
    """Short payloads are stored verbatim; no truncation marker is added."""
    resp = client.post(  # type: ignore[attr-defined]
        "/api/hardware/mqtt/publish",
        json={"topic": "test/topic", "payload": "hello world"},
        headers=_admin(tokens),
    )
    assert resp.status_code == 200, resp.text
    audit = client.get(  # type: ignore[attr-defined]
        "/api/security/audit?action=mqtt_publish&limit=5",
        headers=_admin(tokens),
    )
    assert audit.status_code == 200, audit.text
    rows = audit.json().get("entries") or []
    assert rows
    publish_rows = [r for r in rows if r["action"] == "mqtt_publish"]
    assert publish_rows
    assert publish_rows[0]["target"] == "test/topic"
    assert publish_rows[0]["new_value"] == "hello world"


def test_mqtt_publish_audit_truncates_long_payload(client: object, tokens: dict[str, str]) -> None:
    """Long payloads are truncated to 256 chars in the audit log, and a
    separate ``mqtt_publish_truncated`` row records the original length
    so the operator can tell it was a preview (#145)."""
    long_payload = "Y" * 1024
    resp = client.post(  # type: ignore[attr-defined]
        "/api/hardware/mqtt/publish",
        json={"topic": "test/long", "payload": long_payload},
        headers=_admin(tokens),
    )
    assert resp.status_code == 200, resp.text
    audit = client.get(  # type: ignore[attr-defined]
        "/api/security/audit?action=mqtt_publish&limit=5",
        headers=_admin(tokens),
    )
    assert audit.status_code == 200, audit.text
    rows = audit.json().get("entries") or []
    publish_rows = [r for r in rows if r["action"] == "mqtt_publish" and r["target"] == "test/long"]
    assert publish_rows
    assert len(publish_rows[0]["new_value"]) == 256, publish_rows[0]
    # And a sibling truncated row exists.
    trunc = client.get(  # type: ignore[attr-defined]
        "/api/security/audit?action=mqtt_publish_truncated&limit=5",
        headers=_admin(tokens),
    )
    assert trunc.status_code == 200, trunc.text
    trows = [r for r in trunc.json().get("entries", []) if r["target"] == "test/long"]
    assert trows
    assert "original_bytes=1024" in trows[0]["new_value"]
