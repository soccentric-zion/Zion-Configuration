"""Next-boot staging table — pending_overlays (audit §3, §4, §10, #114).

Five surfaces can stage a hardware-touching write for "apply on next
boot" instead of going live immediately:

  - POST /api/interfaces/{name}/enable
  - POST /api/media/{name}/enable
  - POST /api/devices/{id}/enable
  - POST /api/sysadmin/services/{name}/enable|disable

The pending_overlays table holds the staged change; the boot-time hook
(POST /api/apply/pending/commit) applies it to the live table. The SPA
can list pending changes via GET /api/apply/pending.
"""

from __future__ import annotations

import json

# ---------------------------------------------------------------------------
# List / commit / discard endpoints
# ---------------------------------------------------------------------------


def test_pending_list_starts_empty(client: object, tokens: dict[str, str]) -> None:
    headers = {"Authorization": f"Bearer {tokens['viewer']}"}
    res = client.get("/api/apply/pending", headers=headers)  # type: ignore[attr-defined]
    assert res.status_code == 200
    assert res.json() == {"pending": []}


def test_pending_commit_requires_confirm(client: object, tokens: dict[str, str]) -> None:
    headers = {"Authorization": f"Bearer {tokens['admin']}"}
    res = client.post(  # type: ignore[attr-defined]
        "/api/apply/pending/commit", json={}, headers=headers
    )
    assert res.status_code == 428


def test_pending_discard_unknown_id_returns_400(client: object, tokens: dict[str, str]) -> None:
    headers = {"Authorization": f"Bearer {tokens['admin']}"}
    res = client.post(  # type: ignore[attr-defined]
        "/api/apply/pending/9999/discard",
        json={"confirm": True},
        headers=headers,
    )
    assert res.status_code == 400


# ---------------------------------------------------------------------------
# interface_enable staging
# ---------------------------------------------------------------------------


def test_interface_enable_live_default(client: object, tokens: dict[str, str]) -> None:
    headers = {"Authorization": f"Bearer {tokens['operator']}"}
    res = client.post(  # type: ignore[attr-defined]
        "/api/interfaces/i2c-1/enable",
        json={"enabled": True},
        headers=headers,
    )
    assert res.status_code == 200
    body = res.json()
    assert body["name"] == "i2c-1"
    assert body["enabled"] is True
    # Live path: no when/pending_id fields.
    assert "when" not in body
    assert "pending_id" not in body


def test_interface_enable_next_boot_stages_only(client: object, tokens: dict[str, str]) -> None:
    headers = {"Authorization": f"Bearer {tokens['operator']}"}
    res = client.post(  # type: ignore[attr-defined]
        "/api/interfaces/i2c-1/enable",
        json={"enabled": True, "when": "next-boot"},
        headers=headers,
    )
    assert res.status_code == 200
    body = res.json()
    assert body["when"] == "next-boot"
    assert body["pending_id"] > 0
    # The live table was NOT updated (the audit row + the new entry are
    # the only state change).
    listing = client.get(  # type: ignore[attr-defined]
        "/api/apply/pending", headers=headers
    )
    pending = listing.json()["pending"]
    assert len(pending) == 1
    row = pending[0]
    assert row["target"] == "i2c-1"
    assert row["kind"] == "interface_enable"
    assert row["status"] == "armed"
    assert json.loads(row["payload_json"]) == {"enabled": True}


def test_interface_enable_next_boot_commit_applies(client: object, tokens: dict[str, str]) -> None:
    op = {"Authorization": f"Bearer {tokens['operator']}"}
    admin = {"Authorization": f"Bearer {tokens['admin']}"}
    # Stage an i2c-1 disable.
    res = client.post(  # type: ignore[attr-defined]
        "/api/interfaces/i2c-1/enable",
        json={"enabled": False, "when": "next-boot"},
        headers=op,
    )
    assert res.status_code == 200
    pending_id = res.json()["pending_id"]
    # Commit (boot-time hook).
    commit = client.post(  # type: ignore[attr-defined]
        "/api/apply/pending/commit",
        json={"confirm": True},
        headers=admin,
    )
    assert commit.status_code == 200
    body = commit.json()
    assert body["applied_count"] == 1
    assert body["applied"][0]["target"] == "i2c-1"
    # The live hw_interfaces row now reflects the staged value.
    detail = client.get("/api/interfaces/i2c-1", headers={"Authorization": f"Bearer {tokens['viewer']}"})  # type: ignore[attr-defined]
    assert detail.json()["enabled"] is False
    # The pending row is committed.
    listing = client.get(  # type: ignore[attr-defined]
        "/api/apply/pending", headers=op
    )
    rows = listing.json()["pending"]
    matched = [r for r in rows if r["id"] == pending_id]
    assert matched and matched[0]["status"] == "committed"


def test_interface_enable_next_boot_discard(client: object, tokens: dict[str, str]) -> None:
    op = {"Authorization": f"Bearer {tokens['operator']}"}
    admin = {"Authorization": f"Bearer {tokens['admin']}"}
    res = client.post(  # type: ignore[attr-defined]
        "/api/interfaces/i2c-1/enable",
        json={"enabled": True, "when": "next-boot"},
        headers=op,
    )
    pending_id = res.json()["pending_id"]
    # Discard.
    res = client.post(  # type: ignore[attr-defined]
        f"/api/apply/pending/{pending_id}/discard",
        json={"confirm": True},
        headers=admin,
    )
    assert res.status_code == 200
    # Re-discard returns 400 (no longer armed).
    res = client.post(  # type: ignore[attr-defined]
        f"/api/apply/pending/{pending_id}/discard",
        json={"confirm": True},
        headers=admin,
    )
    assert res.status_code == 400


# ---------------------------------------------------------------------------
# media / device / service staging (parallel structure)
# ---------------------------------------------------------------------------


def test_media_enable_next_boot_stages(client: object, tokens: dict[str, str]) -> None:
    op = {"Authorization": f"Bearer {tokens['operator']}"}
    res = client.post(  # type: ignore[attr-defined]
        "/api/media/csi0/enable",
        json={"enabled": True, "when": "next-boot"},
        headers=op,
    )
    assert res.status_code == 200
    assert res.json()["when"] == "next-boot"
    assert res.json()["pending_id"] > 0


def test_device_enable_next_boot_stages(client: object, tokens: dict[str, str]) -> None:
    op = {"Authorization": f"Bearer {tokens['operator']}"}
    # Create a device first.
    create = client.post(  # type: ignore[attr-defined]
        "/api/devices",
        json={"name": "bme280-pending", "kind": "sensor", "interface": "i2c-1", "address": "0x76"},
        headers=op,
    )
    assert create.status_code == 201
    dev_id = create.json()["id"]
    # Stage a disable.
    res = client.post(  # type: ignore[attr-defined]
        f"/api/devices/{dev_id}/enable",
        json={"enabled": False, "when": "next-boot"},
        headers=op,
    )
    assert res.status_code == 200
    body = res.json()
    assert body["when"] == "next-boot"
    assert body["pending_id"] > 0


def test_service_enable_next_boot_stages(client: object, tokens: dict[str, str]) -> None:
    op = {"Authorization": f"Bearer {tokens['operator']}"}
    res = client.post(  # type: ignore[attr-defined]
        "/api/sysadmin/services/cron/enable",
        json={"when": "next-boot"},
        headers=op,
    )
    assert res.status_code == 200
    body = res.json()
    assert body["when"] == "next-boot"
    assert body["pending_id"] > 0


def test_service_disable_next_boot_stages(client: object, tokens: dict[str, str]) -> None:
    op = {"Authorization": f"Bearer {tokens['operator']}"}
    res = client.post(  # type: ignore[attr-defined]
        "/api/sysadmin/services/cron/disable",
        json={"confirm": True, "when": "next-boot"},
        headers=op,
    )
    assert res.status_code == 200
    body = res.json()
    assert body["when"] == "next-boot"


def test_service_stop_live_path_still_works(client: object, tokens: dict[str, str]) -> None:
    """The live service stop path is preserved when `when` is not set."""
    op = {"Authorization": f"Bearer {tokens['operator']}"}
    res = client.post(  # type: ignore[attr-defined]
        "/api/sysadmin/services/cron/stop",
        json={"confirm": True},
        headers=op,
    )
    assert res.status_code == 200
    # No pending_id: the live path went to the HAL.
    assert "pending_id" not in res.json()


# ---------------------------------------------------------------------------
# Audit alignment — staging records the intent
# ---------------------------------------------------------------------------


def test_interface_enable_staging_audits_intent(client: object, tokens: dict[str, str]) -> None:
    op = {"Authorization": f"Bearer {tokens['operator']}"}
    res = client.post(  # type: ignore[attr-defined]
        "/api/interfaces/uart0/enable",
        json={"enabled": True, "when": "next-boot"},
        headers=op,
    )
    assert res.status_code == 200
    # The audit log row is recorded with a distinct action so the
    # who/when of the staging is recoverable.
    rows = client.get(  # type: ignore[attr-defined]
        "/api/security/audit",
        params={"action": "interface_enable_staged", "limit": 50},
        headers=op,
    )
    assert rows.status_code == 200
    actions = [r.get("action") for r in rows.json().get("entries", [])]
    assert "interface_enable_staged" in actions


# ---------------------------------------------------------------------------
# Role enforcement
# ---------------------------------------------------------------------------


def test_pending_commit_is_admin_only(client: object, tokens: dict[str, str]) -> None:
    op = {"Authorization": f"Bearer {tokens['operator']}"}
    res = client.post(  # type: ignore[attr-defined]
        "/api/apply/pending/commit",
        json={"confirm": True},
        headers=op,
    )
    assert res.status_code == 403


def test_pending_discard_is_admin_only(client: object, tokens: dict[str, str]) -> None:
    op = {"Authorization": f"Bearer {tokens['operator']}"}
    res = client.post(  # type: ignore[attr-defined]
        "/api/apply/pending/1/discard",
        json={"confirm": True},
        headers=op,
    )
    assert res.status_code == 403


def test_pending_list_is_viewer_readable(client: object, tokens: dict[str, str]) -> None:
    headers = {"Authorization": f"Bearer {tokens['viewer']}"}
    res = client.get("/api/apply/pending", headers=headers)  # type: ignore[attr-defined]
    assert res.status_code == 200
    assert "pending" in res.json()
