"""Interfaces HAL: configurable hardware buses (section 1).

Covers the bus interfaces that are individually enableable/disableable and
configurable from the dashboard: GPIO, I²C, SPI, UART, RS485/RS422, CAN/CAN-FD,
Modbus RTU/TCP and 1-Wire. Enable state and configuration are persisted by the
router in the ``hw_interfaces`` table; this HAL provides the canonical default
set, live status, and runtime operations (read/write/configure).

All readings are deterministic simulations so the panel is fully demoable on a
dev machine; ``detect`` decides whether the real bus is present.
"""

from __future__ import annotations

import time
from typing import Any

from app.hal.base import HalModule, path_exists, sim_rng, sim_wave

# Canonical interface set with sane default configuration. The router seeds the
# DB from this on first use, then owns enable/disable + config persistence.
DEFAULTS: list[dict[str, Any]] = [
    {"name": "gpio", "kind": "gpio", "config": {"banks": 1, "pins": 40}},
    {"name": "i2c-1", "kind": "i2c", "config": {"bus": 1, "baud": 100000}},
    {"name": "spi-0", "kind": "spi", "config": {"bus": 0, "mode": 0, "speed_hz": 1_000_000}},
    {
        "name": "uart0",
        "kind": "uart",
        "config": {"device": "/dev/ttyAMA0", "baud": 115200, "bits": 8, "parity": "none", "stop": 1},
    },
    {
        "name": "rs485-0",
        "kind": "rs485",
        "config": {"device": "/dev/ttySC0", "baud": 9600, "termination": True, "de_gpio": 18},
    },
    {"name": "rs422-0", "kind": "rs422", "config": {"device": "/dev/ttySC1", "baud": 9600, "duplex": "full"}},
    {"name": "can0", "kind": "can", "config": {"bitrate": 500000}},
    {"name": "canfd0", "kind": "canfd", "config": {"nom_bitrate": 500000, "data_bitrate": 2_000_000}},
    {"name": "modbus", "kind": "modbus", "config": {"mode": "rtu", "unit": 1, "baud": 9600}},
    {"name": "w1", "kind": "onewire", "config": {"gpio": 4}},
]

_KINDS = {d["kind"] for d in DEFAULTS}


class InterfacesHal(HalModule):
    name = "interfaces"

    def detect(self) -> bool:
        # Treat the bus subsystem as present when the GPIO sysfs exists.
        return path_exists("/sys/class/gpio") or path_exists("/dev/gpiochip0")

    def defaults(self) -> list[dict[str, Any]]:
        return [dict(d, config=dict(d["config"])) for d in DEFAULTS]

    def kinds(self) -> set[str]:
        return set(_KINDS)

    # --- live status -------------------------------------------------------
    def status(self, name: str, kind: str, config: dict[str, Any]) -> dict[str, Any]:
        """Return a simulated live status snapshot for one interface."""
        rng = sim_rng("iface", name, kind)
        base: dict[str, Any] = {"name": name, "kind": kind, "link": "up"}
        if kind == "uart":
            base.update(
                {
                    "rx_bytes": int(sim_wave(30, 1000, 50000, phase=hash(name) % 7)),
                    "tx_bytes": int(sim_wave(40, 1000, 40000, phase=hash(name) % 5)),
                    "framing_errors": rng.randrange(0, 3),
                    "overruns": 0,
                }
            )
        elif kind in {"rs485", "rs422"}:
            base.update(
                {
                    "rx_frames": int(sim_wave(25, 10, 5000, phase=hash(name) % 9)),
                    "tx_frames": int(sim_wave(25, 10, 5000, phase=hash(name) % 3)),
                    "bus_errors": rng.randrange(0, 2),
                    "direction": "rx",
                }
            )
        elif kind in {"can", "canfd"}:
            base.update(
                {
                    "state": "ERROR-ACTIVE",
                    "rx_err": rng.randrange(0, 4),
                    "tx_err": rng.randrange(0, 4),
                    "bus_load_pct": round(sim_wave(20, 5, 60, phase=hash(name) % 11), 1),
                    "fd": kind == "canfd",
                }
            )
        elif kind == "onewire":
            ids = [f"28-{rng.randrange(0, 1 << 48):012x}" for _ in range(rng.randint(1, 3))]
            base.update({"devices": ids, "parasitic": False})
        elif kind == "i2c":
            present = sorted(rng.sample(range(0x03, 0x78), 3))
            base.update({"devices": [f"0x{a:02x}" for a in present]})
        elif kind == "spi":
            base.update({"chip_selects": [f"spidev{config.get('bus', 0)}.{cs}" for cs in (0, 1)]})
        elif kind == "modbus":
            base.update({"connected": True, "mode": config.get("mode", "rtu"), "poll_ms": 250})
        elif kind == "gpio":
            base.update({"exported": rng.randrange(2, 8), "banks": config.get("banks", 1)})
        return self.envelope(base)

    # --- runtime operations ------------------------------------------------
    def operate(self, kind: str, name: str, op: str, params: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
        """Perform a simulated runtime operation (read/write/configure)."""
        rng = sim_rng("op", name, op, time.time())
        if kind == "uart":
            if op == "write":
                data = str(params.get("data", ""))
                return self.envelope({"op": "write", "bytes": len(data.encode())})
            if op == "read":
                n = int(params.get("count", 16))
                return self.envelope({"op": "read", "data": "".join(f"{rng.randrange(256):02x}" for _ in range(n))})
        if kind in {"rs485", "rs422"}:
            if op == "send":
                frame = str(params.get("frame", ""))
                return self.envelope({"op": "send", "echo": frame, "ack": True})
        if kind in {"can", "canfd"}:
            if op == "send":
                return self.envelope(
                    {
                        "op": "send",
                        "id": params.get("id", "0x123"),
                        "dlc": int(params.get("dlc", 8)),
                        "fd": kind == "canfd",
                        "sent": True,
                    }
                )
        if kind == "onewire" and op == "read":
            addr = str(params.get("address", "28-000000000000"))
            return self.envelope(
                {"op": "read", "address": addr, "temperature_c": round(sim_wave(15, 18, 30, phase=hash(addr) % 7), 2)}
            )
        if kind == "modbus":
            if op == "read":
                start = int(params.get("address", 0))
                count = int(params.get("count", 4))
                regs = [{"address": start + i, "value": rng.randrange(0, 65536)} for i in range(count)]
                return self.envelope({"op": "read", "registers": regs})
            if op == "write":
                return self.envelope(
                    {"op": "write", "address": int(params.get("address", 0)), "value": int(params.get("value", 0))}
                )
        if kind == "i2c" and op == "scan":
            present = sorted(rng.sample(range(0x03, 0x78), 4))
            return self.envelope({"op": "scan", "devices": [f"0x{a:02x}" for a in present]})
        if kind == "gpio" and op == "write":
            return self.envelope(
                {"op": "write", "pin": int(params.get("pin", 0)), "value": 1 if params.get("value") else 0}
            )
        # No matching (kind, op) branch. CLAUDE.md #53: the HAL is the
        # canonical enforcement point — direct callers (tests, future
        # routers) must see 400, not a silent 200 with ``supported: False``.
        from app.errors import ApiError

        raise ApiError(
            "bad_action",
            f"op '{op}' not supported on kind '{kind}'",
            400,
        )

    # --- device-tree overlay commit (#107, audit §3) ----------------------
    # audit.md §3 Peripherals: "Write changes as device-tree overlay /
    # config fragment." The enable/disable endpoint only updates the
    # ``hw_interfaces`` table today; the live kernel/devfs is untouched.
    # On a real device this method would shell out to
    # ``dtoverlay -d /boot/overlays {name}`` (Raspberry Pi) or write to
    # ``/sys/kernel/config/device-tree/overlays/{name}`` (configfs). In
    # simulation it returns the would-be applied envelope so the SPA
    # can show "would apply" vs "applied" (#42).

    def commit_overlay(self, name: str, enabled: bool) -> dict[str, Any]:
        """Commit the device-tree overlay for an interface enable/disable.

        Returns the would-be applied envelope with the overlay path
        and a ``loaded`` flag. ``enabled=False`` removes the overlay;
        ``enabled=True`` applies it.
        """
        path = f"/boot/firmware/overlays/{name}.dtbo"
        return self.envelope(
            {
                "name": name,
                "enabled": enabled,
                "overlay_path": path,
                "loaded": enabled,
                "applied": not self.simulated,
            }
        )
