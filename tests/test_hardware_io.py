"""Hardware, storage and network write-actions (HAL mutation paths)."""

from __future__ import annotations


def _admin(tokens: dict[str, str]) -> dict[str, str]:
    return {"Authorization": f"Bearer {tokens['admin']}"}


def _op(tokens: dict[str, str]) -> dict[str, str]:
    return {"Authorization": f"Bearer {tokens['operator']}"}


def test_gpio_export_set_unexport(client: object, tokens: dict[str, str]) -> None:
    op = _op(tokens)
    export = client.post(  # type: ignore[attr-defined]
        "/api/hardware/gpio/17/export", json={"direction": "out"}, headers=op
    )
    assert export.status_code == 200
    setv = client.post(  # type: ignore[attr-defined]
        "/api/hardware/gpio/17", json={"value": 1}, headers=op
    )
    assert setv.status_code == 200
    assert setv.json()["value"] == 1
    unexport = client.delete("/api/hardware/gpio/17", headers=op)  # type: ignore[attr-defined]
    assert unexport.status_code == 200


def test_pwm_set(client: object, tokens: dict[str, str]) -> None:
    resp = client.post(  # type: ignore[attr-defined]
        "/api/hardware/pwm/0",
        json={"period_ns": 1000000, "duty_ns": 500000, "enabled": True},
        headers=_op(tokens),
    )
    assert resp.status_code == 200


def test_can_inject_mqtt_modbus(client: object, tokens: dict[str, str]) -> None:
    admin = _admin(tokens)
    assert (
        client.post(  # type: ignore[attr-defined]
            "/api/hardware/can/inject", json={"id": "0x123", "data": "DEADBEEF"}, headers=admin
        ).status_code
        == 200
    )
    assert (
        client.post(  # type: ignore[attr-defined]
            "/api/hardware/mqtt/publish", json={"topic": "test/x", "payload": "1"}, headers=admin
        ).status_code
        == 200
    )
    assert (
        client.post(  # type: ignore[attr-defined]
            "/api/hardware/modbus/write", json={"unit_id": 1, "address": 4, "count": 1, "value": 99}, headers=admin
        ).status_code
        == 200
    )


def test_storage_fsck_suspend_rtc(client: object, tokens: dict[str, str]) -> None:
    admin = _admin(tokens)
    # confirm=true is required for these privileged endpoints (#34).
    assert (
        client.post(  # type: ignore[attr-defined]
            "/api/storage/fsck", json={"device": "/dev/mmcblk0p2", "confirm": True}, headers=admin
        ).status_code
        == 200
    )
    assert (
        client.post(  # type: ignore[attr-defined]
            "/api/storage/power/suspend", json={"confirm": True}, headers=admin
        ).status_code
        == 200
    )
    assert (
        client.post(  # type: ignore[attr-defined]
            "/api/storage/rtc", json={"time": "2026-01-01T00:00:00", "confirm": True}, headers=admin
        ).status_code
        == 200
    )


def test_network_firewall_and_vpn(client: object, tokens: dict[str, str]) -> None:
    admin = _admin(tokens)
    assert (
        client.post(  # type: ignore[attr-defined]
            "/api/network/firewall/zone/public", json={"enabled": True}, headers=admin
        ).status_code
        == 200
    )
    assert (
        client.post(  # type: ignore[attr-defined]
            "/api/network/vpn", json={"action": "up"}, headers=admin
        ).status_code
        == 200
    )


def test_file_upload(client: object, tokens: dict[str, str], sandbox: object) -> None:
    admin = _admin(tokens)
    resp = client.post(  # type: ignore[attr-defined]
        "/api/files/upload",
        params={"path": str(sandbox)},
        files={"file": ("uploaded.bin", b"binary-data", "application/octet-stream")},
        headers=admin,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["ok"] is True


def test_uart_list_config_open_write_read(client: object, tokens: dict[str, str]) -> None:
    op = _op(tokens)
    # list
    r = client.get("/api/hardware/uart", headers=op)  # type: ignore[attr-defined]
    assert r.status_code == 200
    ports = {p["port"] for p in r.json()["ports"]}
    assert "ttyAMA0" in ports
    # config
    r = client.post(  # type: ignore[attr-defined]
        "/api/hardware/uart/ttyAMA0/config",
        json={"baud": 9600, "data_bits": 8, "parity": "none", "stop_bits": 1, "flow": "none"},
        headers=op,
    )
    assert r.status_code == 200
    assert r.json()["baud"] == 9600
    # bad baud -> 400
    r = client.post(  # type: ignore[attr-defined]
        "/api/hardware/uart/ttyAMA0/config",
        json={"baud": 1234},
        headers=op,
    )
    assert r.status_code == 400
    # write to closed port -> 409
    r = client.post(  # type: ignore[attr-defined]
        "/api/hardware/uart/ttyAMA0/write", json={"data": "hello"}, headers=op
    )
    assert r.status_code == 409
    # open
    assert client.post("/api/hardware/uart/ttyAMA0/open", headers=op).status_code == 200  # type: ignore[attr-defined]
    # write
    r = client.post(  # type: ignore[attr-defined]
        "/api/hardware/uart/ttyAMA0/write", json={"data": "PING"}, headers=op
    )
    assert r.status_code == 200
    assert r.json()["written_bytes"] == 4
    # read echo
    r = client.get("/api/hardware/uart/ttyAMA0/read", headers=op)  # type: ignore[attr-defined]
    assert r.status_code == 200
    assert r.json()["data"] == "PING"


def test_rs485_list_config_send_recv(client: object, tokens: dict[str, str]) -> None:
    op = _op(tokens)
    # list
    r = client.get("/api/hardware/rs485", headers=op)  # type: ignore[attr-defined]
    assert r.status_code == 200
    ports = {p["port"] for p in r.json()["ports"]}
    assert {"ttyRS485-0", "ttyRS422-0"} <= ports
    # bad mode
    r = client.post(  # type: ignore[attr-defined]
        "/api/hardware/rs485/ttyRS485-0/config",
        json={"mode": "rs232"},
        headers=op,
    )
    assert r.status_code == 400
    # valid config
    r = client.post(  # type: ignore[attr-defined]
        "/api/hardware/rs485/ttyRS485-0/config",
        json={"mode": "rs485", "baud": 38400, "termination": True, "address": 7, "enabled": True},
        headers=op,
    )
    assert r.status_code == 200
    assert r.json()["address"] == 7
    # disable
    r = client.post(  # type: ignore[attr-defined]
        "/api/hardware/rs485/ttyRS485-0/enable", json={"enabled": False}, headers=op
    )
    assert r.status_code == 200
    assert r.json()["enabled"] is False
    # send while disabled -> 409
    r = client.post(  # type: ignore[attr-defined]
        "/api/hardware/rs485/ttyRS485-0/send", json={"frame": "PING"}, headers=op
    )
    assert r.status_code == 409
    # re-enable
    r = client.post(  # type: ignore[attr-defined]
        "/api/hardware/rs485/ttyRS485-0/enable", json={"enabled": True}, headers=op
    )
    assert r.status_code == 200
    # send
    r = client.post(  # type: ignore[attr-defined]
        "/api/hardware/rs485/ttyRS485-0/send", json={"frame": "PING"}, headers=op
    )
    assert r.status_code == 200
    assert r.json()["sent_bytes"] == 4
    # recv echo
    r = client.get("/api/hardware/rs485/ttyRS485-0/recv", headers=op)  # type: ignore[attr-defined]
    assert r.status_code == 200
    assert r.json()["data"] == "PING"


def test_onewire_list_read_write(client: object, tokens: dict[str, str]) -> None:
    op = _op(tokens)
    admin = {"Authorization": f"Bearer {tokens['admin']}"}
    # list
    r = client.get("/api/hardware/onewire", headers=op)  # type: ignore[attr-defined]
    assert r.status_code == 200
    addrs = {d["address"] for d in r.json()["devices"]}
    assert "28-00000a1b2c3d" in addrs
    # read temperature from DS18B20
    r = client.post(  # type: ignore[attr-defined]
        "/api/hardware/onewire/28-00000a1b2c3d/read", headers=op
    )
    assert r.status_code == 200
    body = r.json()
    assert body["family"] == "0x28"
    assert isinstance(body["temperature_c"], int | float)
    # read PIO from DS2413
    r = client.post(  # type: ignore[attr-defined]
        "/api/hardware/onewire/3a-00000abcde01/read", headers=op
    )
    assert r.status_code == 200
    assert "pio_a" in r.json() and "pio_b" in r.json()
    # write PIO
    r = client.post(  # type: ignore[attr-defined]
        "/api/hardware/onewire/3a-00000abcde01/write", json={"value": 3}, headers=admin
    )
    assert r.status_code == 200
    assert r.json()["value"] == 3
    # write to read-only DS18B20 -> 400
    r = client.post(  # type: ignore[attr-defined]
        "/api/hardware/onewire/28-00000a1b2c3d/write", json={"value": 0}, headers=admin
    )
    assert r.status_code == 400
    # unknown address
    r = client.post(  # type: ignore[attr-defined]
        "/api/hardware/onewire/00-deadbeef/read", headers=op
    )
    assert r.status_code == 404


def test_modbus_count_capped_to_prevent_dos(client: object, tokens: dict[str, str]) -> None:
    """modbus_read must reject count > 125 with 422 (fix #28). A malicious
    caller can't ask for 10M registers and exhaust memory."""
    h = {"Authorization": f"Bearer {tokens['admin']}"}
    # OK: small count
    ok = client.get("/api/hardware/modbus?count=8", headers=h)  # type: ignore[attr-defined]
    assert ok.status_code == 200
    # Reject: count = 0
    zero = client.get("/api/hardware/modbus?count=0", headers=h)  # type: ignore[attr-defined]
    assert zero.status_code == 422
    # Reject: huge count
    big = client.get("/api/hardware/modbus?count=1000000", headers=h)  # type: ignore[attr-defined]
    assert big.status_code == 422
