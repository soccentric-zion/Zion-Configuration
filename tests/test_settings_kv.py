"""Regression tests for set_hostname / set_timezone persistence (#42).

The endpoints used to audit-log the change but never actually persist
the new value, so the SPA's next /api/settings/system read showed the
*old* hostname/timezone while the audit log recorded the *new* one.

Fix: persist the operator-chosen value in system_settings_kv. The read
endpoint falls back to platform.node() / time.tzname when no override
is present, so a fresh install still shows the real OS values.
"""

from __future__ import annotations


def _admin(tokens: dict[str, str]) -> dict[str, str]:
    return {"Authorization": f"Bearer {tokens['admin']}"}


def test_set_hostname_persists_and_is_read_back(client: object, tokens: dict[str, str]) -> None:
    h = _admin(tokens)
    resp = client.post("/api/settings/hostname", json={"hostname": "lab-gw-42"}, headers=h)  # type: ignore[attr-defined]
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body == {"ok": True, "applied": True}

    # The new value is read back from /api/settings/system.
    sys_resp = client.get("/api/settings/system", headers=h)  # type: ignore[attr-defined]
    assert sys_resp.status_code == 200
    assert sys_resp.json()["hostname"] == "lab-gw-42"


def test_set_timezone_persists_and_is_read_back(client: object, tokens: dict[str, str]) -> None:
    h = _admin(tokens)
    resp = client.post("/api/settings/timezone", json={"timezone": "Europe/Berlin"}, headers=h)  # type: ignore[attr-defined]
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body == {"ok": True, "applied": True}

    sys_resp = client.get("/api/settings/system", headers=h)  # type: ignore[attr-defined]
    assert sys_resp.status_code == 200
    assert sys_resp.json()["timezone"] == "Europe/Berlin"


def test_set_hostname_rejects_empty(client: object, tokens: dict[str, str]) -> None:
    h = _admin(tokens)
    resp = client.post("/api/settings/hostname", json={"hostname": ""}, headers=h)  # type: ignore[attr-defined]
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "invalid_hostname"


def test_set_timezone_rejects_unknown(client: object, tokens: dict[str, str]) -> None:
    h = _admin(tokens)
    resp = client.post("/api/settings/timezone", json={"timezone": "Mars/Olympus"}, headers=h)  # type: ignore[attr-defined]
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "invalid_timezone"


def test_set_hostname_audits_old_and_new(client: object, tokens: dict[str, str]) -> None:
    h = _admin(tokens)
    # Use unique values so other tests' entries don't shadow ours.
    marker = "test-audit-marker-001"
    first = f"{marker}-first"
    second = f"{marker}-second"
    client.post("/api/settings/hostname", json={"hostname": first}, headers=h)  # type: ignore[attr-defined]
    # Overwrite — old_value should now be the previously-persisted value.
    resp = client.post("/api/settings/hostname", json={"hostname": second}, headers=h)  # type: ignore[attr-defined]
    assert resp.status_code == 200

    audit_resp = client.get(  # type: ignore[attr-defined]
        "/api/security/audit?action=set_hostname&limit=200", headers=h
    )
    assert audit_resp.status_code == 200
    rows = audit_resp.json()["entries"]
    # Find the entries we just wrote.
    first_rows = [r for r in rows if r.get("new_value") == first]
    second_rows = [r for r in rows if r.get("new_value") == second]
    assert len(first_rows) == 1, f"expected one audit row for {first}, got {first_rows}"
    assert len(second_rows) == 1, f"expected one audit row for {second}, got {second_rows}"
    # The 'first' write had no prior persisted value, so old_value is whatever
    # the read path returned at that point (None, or platform.node()).
    # The 'second' write must record old_value == first, since that's the
    # value being overwritten (#42).
    assert second_rows[0]["old_value"] == first


def test_vpn_action_returns_simulated_envelope(client: object, tokens: dict[str, str]) -> None:
    h = _admin(tokens)
    resp = client.post("/api/network/vpn", json={"action": "up"}, headers=h)  # type: ignore[attr-defined]
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["action"] == "up"
    assert body["target"] == "wg0"
    # In simulation `applied` is False and `simulated` is True so the SPA
    # can show "would apply" instead of "applied" (#42).
    assert body["applied"] is False
    assert body["simulated"] is True


def test_vpn_action_rejects_unknown(client: object, tokens: dict[str, str]) -> None:
    h = _admin(tokens)
    resp = client.post("/api/network/vpn", json={"action": "explode"}, headers=h)  # type: ignore[attr-defined]
    assert resp.status_code == 422


def test_mqtt_publish_returns_simulated_envelope(client: object, tokens: dict[str, str]) -> None:
    h = _admin(tokens)
    resp = client.post(  # type: ignore[attr-defined]
        "/api/hardware/mqtt/publish",
        json={"topic": "lab/temp", "payload": "22.1"},
        headers=h,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["topic"] == "lab/temp"
    assert body["payload_bytes"] == len(b"22.1")
    assert body["published"] is False
    assert body["simulated"] is True


def test_mqtt_publish_rejects_empty_topic(client: object, tokens: dict[str, str]) -> None:
    h = _admin(tokens)
    resp = client.post(  # type: ignore[attr-defined]
        "/api/hardware/mqtt/publish",
        json={"topic": "", "payload": "x"},
        headers=h,
    )
    assert resp.status_code == 422
