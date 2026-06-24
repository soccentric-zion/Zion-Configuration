"""Tests for the added sections: interfaces/devices, media, sysadmin, OTA."""

from __future__ import annotations


def _admin(tokens: dict[str, str]) -> dict[str, str]:
    return {"Authorization": f"Bearer {tokens['admin']}"}


def _op(tokens: dict[str, str]) -> dict[str, str]:
    return {"Authorization": f"Bearer {tokens['operator']}"}


def _viewer(tokens: dict[str, str]) -> dict[str, str]:
    return {"Authorization": f"Bearer {tokens['viewer']}"}


# --- section 1: hardware interfaces ----------------------------------------
def test_interfaces_list_enable_config_operate(client: object, tokens: dict[str, str]) -> None:
    op = _op(tokens)
    lst = client.get("/api/interfaces", headers=_viewer(tokens))  # type: ignore[attr-defined]
    assert lst.status_code == 200
    names = [i["name"] for i in lst.json()["interfaces"]]
    assert "i2c-1" in names and "can0" in names

    detail = client.get("/api/interfaces/i2c-1", headers=_viewer(tokens))  # type: ignore[attr-defined]
    assert detail.status_code == 200

    en = client.post("/api/interfaces/i2c-1/enable", json={"enabled": True}, headers=op)  # type: ignore[attr-defined]
    assert en.status_code == 200 and en.json()["enabled"] is True

    cfg = client.post(  # type: ignore[attr-defined]
        "/api/interfaces/uart0/config",
        json={"device": "/dev/ttyAMA0", "baud": 115200},
        headers=op,
    )
    assert cfg.status_code == 200, cfg.text

    client.post("/api/interfaces/i2c-1/enable", json={"enabled": True}, headers=op)  # type: ignore[attr-defined]
    operate = client.post(  # type: ignore[attr-defined]
        "/api/interfaces/i2c-1/operate", json={"op": "scan", "params": {}}, headers=op
    )
    assert operate.status_code == 200


def test_interface_operate_disabled_conflict(client: object, tokens: dict[str, str]) -> None:
    op = _op(tokens)
    client.post("/api/interfaces/spi-0/enable", json={"enabled": False}, headers=op)  # type: ignore[attr-defined]
    res = client.post(  # type: ignore[attr-defined]
        "/api/interfaces/spi-0/operate", json={"op": "read", "params": {}}, headers=op
    )
    assert res.status_code == 409


# --- section 6: devices ----------------------------------------------------
def test_device_crud(client: object, tokens: dict[str, str]) -> None:
    op = _op(tokens)
    create = client.post(  # type: ignore[attr-defined]
        "/api/devices",
        json={"name": "bme280", "kind": "sensor", "interface": "i2c-1", "address": "0x76"},
        headers=op,
    )
    assert create.status_code == 201
    dev_id = create.json()["id"]

    got = client.get(f"/api/devices/{dev_id}", headers=_viewer(tokens))  # type: ignore[attr-defined]
    assert got.status_code == 200 and got.json()["name"] == "bme280"

    en = client.post(f"/api/devices/{dev_id}/enable", json={"enabled": False}, headers=op)  # type: ignore[attr-defined]
    assert en.status_code == 200 and en.json()["enabled"] is False

    assert client.delete(f"/api/devices/{dev_id}", headers=op).status_code == 200  # type: ignore[attr-defined]


def test_device_create_requires_fields(client: object, tokens: dict[str, str]) -> None:
    res = client.post("/api/devices", json={"name": ""}, headers=_op(tokens))  # type: ignore[attr-defined]
    assert res.status_code == 400


# --- sections 2 & 3: media -------------------------------------------------
def test_media_cameras_displays_blocks(client: object, tokens: dict[str, str]) -> None:
    v = _viewer(tokens)
    cams = client.get("/api/media/cameras", headers=v)  # type: ignore[attr-defined]
    assert cams.status_code == 200 and cams.json()["cameras"]
    assert client.get("/api/media/displays", headers=v).status_code == 200  # type: ignore[attr-defined]
    blocks = client.get("/api/media/blocks", headers=v)  # type: ignore[attr-defined]
    assert blocks.status_code == 200
    # no hardware H.264 encoder is presented (#112 — req.md §3):
    # BCM2712 has no dedicated H.264 hardware encoder; the SPA must
    # never surface a HW H.264 entry. The path field must mention
    # software or V4L2 so a future regression can't silently flip it.
    encoders = [b for b in blocks.json()["blocks"] if b["kind"] == "encoder"]
    assert encoders
    enc = encoders[0]
    assert enc["status"]["hardware"] is False, enc
    assert "software" in enc["status"]["path"].lower() or "v4l2" in enc["status"]["path"].lower(), enc
    assert "H.264" in enc["status"]["codec"], enc
    assert client.get("/api/media/isp", headers=v).status_code == 200  # type: ignore[attr-defined]
    assert client.get("/api/media/hvs", headers=v).status_code == 200  # type: ignore[attr-defined]


def test_media_enable_config_and_jobs(client: object, tokens: dict[str, str]) -> None:
    op = _op(tokens)
    en = client.post("/api/media/csi0/enable", json={"enabled": True}, headers=op)  # type: ignore[attr-defined]
    assert en.status_code == 200
    cfg = client.post("/api/media/csi0/config", json={"width": 1280, "height": 720}, headers=op)  # type: ignore[attr-defined]
    assert cfg.status_code == 200 and cfg.json()["config"]["width"] == 1280

    start = client.post("/api/media/jobs/decode", json={"source": "csi0", "sink": "file"}, headers=op)  # type: ignore[attr-defined]
    assert start.status_code == 200
    job_id = start.json()["job_id"]
    st = client.get(f"/api/media/jobs/{job_id}/status", headers=_viewer(tokens))  # type: ignore[attr-defined]
    assert st.status_code == 200
    assert client.get("/api/media/jobs", headers=_viewer(tokens)).status_code == 200  # type: ignore[attr-defined]
    assert client.delete(f"/api/media/jobs/{job_id}", headers=op).status_code == 200  # type: ignore[attr-defined]


def test_media_bad_job_kind(client: object, tokens: dict[str, str]) -> None:
    res = client.post("/api/media/jobs/bogus", json={}, headers=_op(tokens))  # type: ignore[attr-defined]
    assert res.status_code == 400


# --- section 5: CM5 platform ----------------------------------------------
def test_platform_reads(client: object, tokens: dict[str, str]) -> None:
    v = _viewer(tokens)
    for path in ("/platform", "/pcie", "/emmc", "/gpio-banks", "/rtc", "/pmic"):
        assert client.get(f"/api/sysadmin{path}", headers=v).status_code == 200  # type: ignore[attr-defined]


# --- section 4: connectivity ----------------------------------------------
def test_connectivity(client: object, tokens: dict[str, str]) -> None:
    v = _viewer(tokens)
    assert client.get("/api/sysadmin/wifi/scan", headers=v).status_code == 200  # type: ignore[attr-defined]
    assert client.get("/api/sysadmin/bluetooth", headers=v).status_code == 200  # type: ignore[attr-defined]
    assert (
        client.post(  # type: ignore[attr-defined]
            "/api/sysadmin/wifi/connect",
            json={"ssid": "lab-wifi", "confirm": True},
            headers=_op(tokens),
        ).status_code
        == 200
    )
    assert (
        client.post(  # type: ignore[attr-defined]
            "/api/sysadmin/bluetooth/enable",
            json={"enabled": True, "confirm": True},
            headers=_op(tokens),
        ).status_code
        == 200
    )


# --- section 7: network / ports / firewall / services ----------------------
def test_network_config_and_link(client: object, tokens: dict[str, str]) -> None:
    admin = _admin(tokens)
    assert client.get("/api/sysadmin/network", headers=_viewer(tokens)).status_code == 200  # type: ignore[attr-defined]
    static = client.post(  # type: ignore[attr-defined]
        "/api/sysadmin/network/eth0/config",
        json={"method": "static", "address": "192.168.1.50", "netmask": "255.255.255.0"},
        headers=admin,
    )
    assert static.status_code == 200 and static.json()["address"] == "192.168.1.50"

    bad = client.post(  # type: ignore[attr-defined]
        "/api/sysadmin/network/eth0/config",
        json={"method": "static", "address": "not-an-ip", "netmask": "255.255.255.0"},
        headers=admin,
    )
    assert bad.status_code == 400

    # bringing an interface down requires confirmation
    no_confirm = client.post("/api/sysadmin/network/wlan0/link", json={"up": False}, headers=admin)  # type: ignore[attr-defined]
    assert no_confirm.status_code == 428
    confirmed = client.post(  # type: ignore[attr-defined]
        "/api/sysadmin/network/wlan0/link", json={"up": False, "confirm": True}, headers=admin
    )
    assert confirmed.status_code == 200
    assert client.post("/api/sysadmin/network/wlan0/link", json={"up": True}, headers=admin).status_code == 200  # type: ignore[attr-defined]


def test_ports_close_requires_confirm(client: object, tokens: dict[str, str]) -> None:
    admin = _admin(tokens)
    assert client.get("/api/sysadmin/ports", headers=_viewer(tokens)).status_code == 200  # type: ignore[attr-defined]
    # Port 22 is in the default protected set (Guardrail 3, #91), so the
    # protected-resources check fires first with 403 — even with confirm.
    assert client.post("/api/sysadmin/ports/22/close", json={}, headers=admin).status_code == 403  # type: ignore[attr-defined]
    assert (
        client.post("/api/sysadmin/ports/22/close", json={"confirm": True}, headers=admin).status_code  # type: ignore[attr-defined]
        == 403
    )
    # An unprotected port still requires confirm.
    assert client.post("/api/sysadmin/ports/9999/close", json={}, headers=admin).status_code == 428  # type: ignore[attr-defined]
    ok = client.post("/api/sysadmin/ports/9999/close", json={"confirm": True}, headers=admin)  # type: ignore[attr-defined]
    assert ok.status_code == 200


def test_firewall_crud(client: object, tokens: dict[str, str]) -> None:
    admin = _admin(tokens)
    assert client.get("/api/sysadmin/firewall", headers=_viewer(tokens)).status_code == 200  # type: ignore[attr-defined]
    # create requires confirm
    assert (
        client.post(  # type: ignore[attr-defined]
            "/api/sysadmin/firewall", json={"action": "allow", "port": 8080, "protocol": "tcp"}, headers=admin
        ).status_code
        == 428
    )
    created = client.post(  # type: ignore[attr-defined]
        "/api/sysadmin/firewall",
        json={"action": "allow", "port": 8080, "protocol": "tcp", "confirm": True},
        headers=admin,
    )
    assert created.status_code == 201
    rid = created.json()["id"]
    assert (
        client.post(  # type: ignore[attr-defined]
            f"/api/sysadmin/firewall/{rid}/enable", json={"enabled": False, "confirm": True}, headers=admin
        ).status_code
        == 200
    )
    # delete requires body-based confirm (was ?confirm=true, fixed in #35)
    assert client.delete(f"/api/sysadmin/firewall/{rid}", headers=admin).status_code == 428  # type: ignore[attr-defined]
    # query-param alone must NOT satisfy the guard anymore.
    assert client.delete(f"/api/sysadmin/firewall/{rid}?confirm=true", headers=admin).status_code == 428  # type: ignore[attr-defined]
    # body-based confirm with JSON content-type satisfies the guard.
    import json as _json

    r = client.request(  # type: ignore[attr-defined]
        "DELETE",
        f"/api/sysadmin/firewall/{rid}",
        content=_json.dumps({"confirm": True}),
        headers={**admin, "Content-Type": "application/json"},
    )
    assert r.status_code == 200, r.text


def test_firewall_bad_port(client: object, tokens: dict[str, str]) -> None:
    res = client.post(  # type: ignore[attr-defined]
        "/api/sysadmin/firewall",
        json={"action": "allow", "port": 99999, "protocol": "tcp", "confirm": True},
        headers=_admin(tokens),
    )
    assert res.status_code == 400


def test_services_and_status(client: object, tokens: dict[str, str]) -> None:
    op = _op(tokens)
    assert client.get("/api/sysadmin/services", headers=_viewer(tokens)).status_code == 200  # type: ignore[attr-defined]
    assert client.get("/api/sysadmin/devices", headers=_viewer(tokens)).status_code == 200  # type: ignore[attr-defined]
    assert client.get("/api/sysadmin/status", headers=_viewer(tokens)).status_code == 200  # type: ignore[attr-defined]
    # stop requires confirm
    assert client.post("/api/sysadmin/services/ssh/stop", json={}, headers=op).status_code == 428  # type: ignore[attr-defined]
    assert client.post("/api/sysadmin/services/ssh/stop", json={"confirm": True}, headers=op).status_code == 200  # type: ignore[attr-defined]
    assert client.post("/api/sysadmin/services/ssh/start", json={}, headers=op).status_code == 200  # type: ignore[attr-defined]


# --- section 8: OTA --------------------------------------------------------
def test_ota_status_history_system(client: object, tokens: dict[str, str]) -> None:
    v = _viewer(tokens)
    st = client.get("/api/ota/status", headers=v)  # type: ignore[attr-defined]
    assert st.status_code == 200 and "system" in st.json()
    assert client.get("/api/ota/history", headers=v).status_code == 200  # type: ignore[attr-defined]
    sysr = client.get("/api/ota/system", headers=v)  # type: ignore[attr-defined]
    assert sysr.status_code == 200 and sysr.json()["active_slot"] in {"A", "B"}


def test_ota_config_push(client: object, tokens: dict[str, str]) -> None:
    res = client.post(  # type: ignore[attr-defined]
        "/api/ota/config",
        json={
            "kind": "system",
            "version": "1.2",
            "name": "cfg-v2",
            "payload": {"sample_rate_hz": 100},
        },
        headers=_admin(tokens),
    )
    assert res.status_code == 200 and res.json()["applied"] is True


def test_ota_app_upload(client: object, tokens: dict[str, str]) -> None:
    """End-to-end: upload a real ed25519-signed .swu (audit §8, #120)."""
    import base64
    import hashlib
    import json as _json

    from cryptography.hazmat.primitives.asymmetric import ed25519
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    admin = _admin(tokens)
    sk = ed25519.Ed25519PrivateKey.generate()
    pk_b64 = base64.b64encode(sk.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)).decode("ascii")
    # Upload the public key first.
    key_resp = client.post(  # type: ignore[attr-defined]
        "/api/ota/signing-keys",
        json={"key_id": "smoke-key", "alg": "ed25519", "public_key": pk_b64},
        headers=admin,
    )
    assert key_resp.status_code == 200, key_resp.text

    bundle = b"fake-swu-bytes"
    payload = _json.dumps({"version": "smoke", "sha256": hashlib.sha256(bundle).hexdigest()})
    sig_b64 = base64.b64encode(sk.sign(payload.encode("utf-8"))).decode("ascii")
    sig_body = _json.dumps(
        {
            "alg": "ed25519",
            "signature": sig_b64,
            "public_key_id": "smoke-key",
            "signed_payload": payload,
        }
    )
    res = client.post(  # type: ignore[attr-defined]
        "/api/ota/app",
        files={"file": ("update.swu", bundle, "application/octet-stream")},
        data={"signature": sig_body},
        headers=admin,
    )
    assert res.status_code == 200 and "job_id" in res.json()


def test_ota_system_switch_rollback_reboot(client: object, tokens: dict[str, str]) -> None:
    admin = _admin(tokens)
    # confirmation enforced
    assert client.post("/api/ota/system/switch", json={"slot": "B"}, headers=admin).status_code == 428  # type: ignore[attr-defined]
    assert (
        client.post("/api/ota/system/switch", json={"slot": "B", "confirm": True}, headers=admin).status_code  # type: ignore[attr-defined]
        == 200
    )
    # An unknown slot value is now caught by the typed pydantic body
    # (OtaSlotSwitch.slot: Literal["A","B"]) — 422 instead of the
    # legacy 400 from the HAL (#97).
    assert client.post("/api/ota/system/switch", json={"slot": "X", "confirm": True}, headers=admin).status_code == 422  # type: ignore[attr-defined]
    assert client.post("/api/ota/system/rollback", json={}, headers=admin).status_code == 428  # type: ignore[attr-defined]
    assert client.post("/api/ota/system/rollback", json={"confirm": True}, headers=admin).status_code == 200  # type: ignore[attr-defined]
    assert client.post("/api/ota/reboot", json={}, headers=admin).status_code == 428  # type: ignore[attr-defined]
    assert client.post("/api/ota/reboot", json={"confirm": True, "now": True}, headers=admin).status_code == 200  # type: ignore[attr-defined]


def test_role_enforcement(client: object, tokens: dict[str, str]) -> None:
    viewer = _viewer(tokens)
    # viewer cannot mutate
    assert client.post("/api/interfaces/i2c-1/enable", json={"enabled": True}, headers=viewer).status_code == 403  # type: ignore[attr-defined]
    assert (  # operator cannot do admin-only firewall
        client.post(  # type: ignore[attr-defined]
            "/api/sysadmin/firewall",
            json={"action": "allow", "port": 80, "protocol": "tcp", "confirm": True},
            headers=_op(tokens),
        ).status_code
        == 403
    )


def test_factory_reset_wipes_user_data(client: object, tokens: dict[str, str]) -> None:
    """factory_reset must actually wipe user-configurable tables, not just
    audit the request (fix #27). Preserved: users, audit_log, metric_history.
    """
    import platform as _plat

    admin = _admin(tokens)
    # Seed something in a reset table (alert_rules).
    create_rule = client.post(  # type: ignore[attr-defined]
        "/api/cloud/alerts/rules",
        json={
            "name": "to-be-wiped",
            "metric": "cpu_temp",
            "condition": ">",
            "threshold": 80.0,
            "duration_s": 0,
            "severity": "warning",
        },
        headers=admin,
    )
    assert create_rule.status_code in (200, 201), create_rule.text
    # Now do the factory reset with the correct hostname confirmation.
    hostname = _plat.node()
    resp = client.post(  # type: ignore[attr-defined]
        "/api/settings/factory-reset",
        json={"confirm_hostname": hostname},
        headers=admin,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body.get("ok") is True
    assert "alert_rules" in body.get("reset_tables", [])
    # Verify the rule is gone.
    after = client.get("/api/cloud/alerts/rules", headers=admin)  # type: ignore[attr-defined]
    assert after.status_code == 200
    assert all(r.get("name") != "to-be-wiped" for r in after.json().get("rules", []))


def test_factory_reset_rejects_wrong_hostname(client: object, tokens: dict[str, str]) -> None:
    """The typed-hostname confirmation is stronger than a boolean — make sure
    a mismatched value is rejected (fix #27 regression guard)."""
    admin = _admin(tokens)
    resp = client.post(  # type: ignore[attr-defined]
        "/api/settings/factory-reset",
        json={"confirm_hostname": "obviously-wrong-hostname"},
        headers=admin,
    )
    assert resp.status_code == 400
