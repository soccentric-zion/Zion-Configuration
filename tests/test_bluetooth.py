"""Tests for #122 — bluetooth/enable requires explicit confirmation.

Toggling the Bluetooth radio can lock out a Bluetooth-only headless
flow, so the endpoint must require `confirm: true` and use a pydantic
body that rejects typos.
"""

from __future__ import annotations


def _op(tokens: dict[str, str]) -> dict[str, str]:
    return {"Authorization": f"Bearer {tokens['operator']}"}


def test_bluetooth_enable_requires_confirm(client: object, tokens: dict[str, str]) -> None:
    # No confirm → 428
    resp = client.post(  # type: ignore[attr-defined]
        "/api/sysadmin/bluetooth/enable",
        json={"enabled": True},
        headers=_op(tokens),
    )
    assert resp.status_code == 428, resp.text


def test_bluetooth_disable_requires_confirm(client: object, tokens: dict[str, str]) -> None:
    resp = client.post(  # type: ignore[attr-defined]
        "/api/sysadmin/bluetooth/enable",
        json={"enabled": False},
        headers=_op(tokens),
    )
    assert resp.status_code == 428, resp.text


def test_bluetooth_enable_with_confirm_succeeds(client: object, tokens: dict[str, str]) -> None:
    resp = client.post(  # type: ignore[attr-defined]
        "/api/sysadmin/bluetooth/enable",
        json={"enabled": True, "confirm": True},
        headers=_op(tokens),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["enabled"] is True


def test_bluetooth_disable_with_confirm_audits_action(client: object, tokens: dict[str, str]) -> None:
    admin = {"Authorization": f"Bearer {tokens['admin']}"}
    resp = client.post(  # type: ignore[attr-defined]
        "/api/sysadmin/bluetooth/enable",
        json={"enabled": False, "confirm": True},
        headers=_op(tokens),
    )
    assert resp.status_code == 200
    # Audit row should record the action + new state (#122)
    audit = client.get(  # type: ignore[attr-defined]
        "/api/security/audit",
        headers=admin,
    )
    assert audit.status_code == 200
    actions = [r["action"] for r in audit.json().get("entries", [])]
    assert "bluetooth_disable" in actions


def test_bluetooth_rejects_unknown_keys(client: object, tokens: dict[str, str]) -> None:
    # extra="forbid" via pydantic → 422
    resp = client.post(  # type: ignore[attr-defined]
        "/api/sysadmin/bluetooth/enable",
        json={"enabled": True, "confirm": True, "extra_typo": 1},
        headers=_op(tokens),
    )
    assert resp.status_code == 422, resp.text
