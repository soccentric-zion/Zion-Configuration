"""Tests for the SMART self-test endpoint (#101, audit §7).

audit.md §7 Health calls for a way to trigger a SMART short/long
self-test from the dashboard. The endpoint shells out to
``smartctl -t {kind} {device}`` in real mode and returns a
``applied: not self.simulated`` envelope in simulation so the SPA
can show "would apply" vs "applied" (#42). The endpoint is admin-only
and gated by the existing confirm machinery.
"""

from __future__ import annotations


def _h(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_smart_test_requires_confirm(client: object, tokens: dict[str, str]) -> None:
    """Without ``confirm: true`` the endpoint is rejected with 428."""
    resp = client.post(  # type: ignore[attr-defined]
        "/api/storage/smart-test",
        json={"device": "/dev/nvme0n1", "kind": "short"},
        headers=_h(tokens["admin"]),
    )
    assert resp.status_code == 428, resp.text


def test_smart_test_rejects_unknown_kind(client: object, tokens: dict[str, str]) -> None:
    """Only ``short`` and ``long`` are valid; other kinds fail pydantic
    validation (422) before the handler is even called."""
    resp = client.post(  # type: ignore[attr-defined]
        "/api/storage/smart-test",
        json={"device": "/dev/nvme0n1", "kind": "conveyance", "confirm": True},
        headers=_h(tokens["admin"]),
    )
    assert resp.status_code == 422, resp.text


def test_smart_test_rejects_empty_device(client: object, tokens: dict[str, str]) -> None:
    """A blank device string is rejected at the pydantic layer
    (Field min_length=1, max_length=128)."""
    resp = client.post(  # type: ignore[attr-defined]
        "/api/storage/smart-test",
        json={"device": "", "kind": "short", "confirm": True},
        headers=_h(tokens["admin"]),
    )
    assert resp.status_code == 422, resp.text


def test_smart_test_short_runs_in_simulation(client: object, tokens: dict[str, str]) -> None:
    """In simulation the endpoint returns an envelope with
    ``applied: False`` and the would-be command line, and audits the
    action so the operator's history is recorded."""
    resp = client.post(  # type: ignore[attr-defined]
        "/api/storage/smart-test",
        json={"device": "/dev/nvme0n1", "kind": "short", "confirm": True},
        headers=_h(tokens["admin"]),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["simulated"] is True
    assert body["applied"] is False
    assert body["device"] == "/dev/nvme0n1"
    assert body["kind"] == "short"
    assert body["command"] == "smartctl -t short /dev/nvme0n1"
    # Audit row carries the device as target and the kind as new_value
    # so the action log surfaces the exact op.
    audit = client.get(  # type: ignore[attr-defined]
        "/api/security/audit?action=storage_smart_test&limit=5",
        headers=_h(tokens["admin"]),
    )
    assert audit.status_code == 200, audit.text
    rows = audit.json().get("entries") or []
    assert any(r["target"] == "/dev/nvme0n1" and r["new_value"] == "short" for r in rows), rows


def test_smart_test_requires_admin(client: object, tokens: dict[str, str]) -> None:
    """A viewer/operator token is rejected with 403 — the endpoint is
    admin-only because it triggers an out-of-band hardware op."""
    for role in ("viewer", "operator"):
        resp = client.post(  # type: ignore[attr-defined]
            "/api/storage/smart-test",
            json={"device": "/dev/nvme0n1", "kind": "long", "confirm": True},
            headers=_h(tokens[role]),
        )
        assert resp.status_code == 403, (role, resp.text)
