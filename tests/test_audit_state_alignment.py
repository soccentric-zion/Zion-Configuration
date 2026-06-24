"""Tests for audit-state alignment on wifi/bt/boot write endpoints (#105).

CLAUDE.md Don't #42 + audit CQ22–CQ24: when a write endpoint audit-logs
a state change, the audit row must reflect what *really* happened, not
just what was requested. The three endpoints below were returning
``{"ok": True}`` while still recording a successful audit; the fix
moves the work into the relevant HALs so the envelope carries
``applied: not self.simulated`` and the router records that flag in
``new_value`` (#42).
"""

from __future__ import annotations


def _admin(tokens: dict[str, str]) -> dict[str, str]:
    return {"Authorization": f"Bearer {tokens['admin']}"}


def _op(tokens: dict[str, str]) -> dict[str, str]:
    return {"Authorization": f"Bearer {tokens['operator']}"}


# --- wifi_connect ---------------------------------------------------------
def test_wifi_connect_returns_applied_envelope(client: object, tokens: dict[str, str]) -> None:
    """In simulation, /api/sysadmin/wifi/connect returns the
    ``applied: False`` envelope so the SPA can show "would apply"
    vs "applied" (#42, #105)."""
    resp = client.post(  # type: ignore[attr-defined]
        "/api/sysadmin/wifi/connect",
        json={"ssid": "lab-wifi", "psk": "supersecret", "confirm": True},
        headers=_op(tokens),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # (#110) the router now returns {"profile": ..., "applied": ...} so
    # the SPA can show the persisted profile alongside the would-apply
    # flag.
    assert body["applied"] is False
    assert body["profile"]["ssid"] == "lab-wifi"


def test_wifi_connect_audits_applied_flag(client: object, tokens: dict[str, str]) -> None:
    """The audit row's new_value carries the ``applied`` flag so the
    operator can tell from the log that the op was simulated, not
    actually executed (#42, #105)."""
    client.post(  # type: ignore[attr-defined]
        "/api/sysadmin/wifi/connect",
        json={"ssid": "audit-ssid", "psk": "supersecret", "confirm": True},
        headers=_op(tokens),
    )
    audit = client.get(  # type: ignore[attr-defined]
        "/api/security/audit?action=wifi_connect&limit=5",
        headers=_admin(tokens),
    )
    rows = audit.json().get("entries") or []
    # (#110, #142) new_value format: "country=XX:psk=***:applied=False|True"
    assert any(r["target"] == "audit-ssid" and "applied=" in (r.get("new_value") or "") for r in rows), rows


def test_wifi_connect_rejects_empty_ssid(client: object, tokens: dict[str, str]) -> None:
    """An empty SSID is rejected at the pydantic layer (min_length=1)."""
    resp = client.post(  # type: ignore[attr-defined]
        "/api/sysadmin/wifi/connect",
        json={"ssid": ""},
        headers=_op(tokens),
    )
    assert resp.status_code == 422, resp.text


# --- bluetooth_enable ----------------------------------------------------
def test_bluetooth_enable_returns_applied_envelope(client: object, tokens: dict[str, str]) -> None:
    """Toggling BT returns the applied envelope so the SPA can
    distinguish "would apply" vs "applied" (#42, #105)."""
    resp = client.post(  # type: ignore[attr-defined]
        "/api/sysadmin/bluetooth/enable",
        json={"enabled": True, "confirm": True},
        headers=_op(tokens),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["simulated"] is True
    assert body["applied"] is False
    assert body["enabled"] is True


def test_bluetooth_enable_audits_applied_flag(client: object, tokens: dict[str, str]) -> None:
    """The audit row records the applied flag in new_value (#42, #105)."""
    client.post(  # type: ignore[attr-defined]
        "/api/sysadmin/bluetooth/enable",
        json={"enabled": False, "confirm": True},
        headers=_op(tokens),
    )
    audit = client.get(  # type: ignore[attr-defined]
        "/api/security/audit?action=bluetooth_disable&limit=5",
        headers=_admin(tokens),
    )
    rows = audit.json().get("entries") or []
    assert any(r.get("new_value") == "False:applied=False" for r in rows), rows


# --- boot/rollback -------------------------------------------------------
def test_boot_rollback_returns_applied_envelope(client: object, tokens: dict[str, str]) -> None:
    """boot/rollback returns the applied envelope with from/to slot info
    so the SPA can show "would apply" vs "applied" (#42, #105)."""
    resp = client.post(  # type: ignore[attr-defined]
        "/api/overview/boot/rollback",
        json={"confirm": True},
        headers=_admin(tokens),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["simulated"] is True
    assert body["applied"] is False
    assert body["from_slot"] in {"A", "B"}
    assert body["to_slot"] in {"A", "B"}
    assert body["from_slot"] != body["to_slot"]


def test_boot_rollback_audits_applied_flag(client: object, tokens: dict[str, str]) -> None:
    """The audit row records the slot transition and the applied
    flag (#42, #105)."""
    client.post(  # type: ignore[attr-defined]
        "/api/overview/boot/rollback",
        json={"confirm": True},
        headers=_admin(tokens),
    )
    audit = client.get(  # type: ignore[attr-defined]
        "/api/security/audit?action=boot_rollback&limit=5",
        headers=_admin(tokens),
    )
    rows = audit.json().get("entries") or []
    # new_value should look like "A->B:applied=False" (or "B->A:...").
    matches = [r for r in rows if (r.get("new_value") or "").endswith(":applied=False")]
    assert matches, rows
    assert any("->" in (r.get("new_value") or "") for r in matches)
