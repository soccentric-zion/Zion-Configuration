"""Hardware HAL: GPIO, PWM, ADC, peripheral discovery, sensors, buses."""

from __future__ import annotations

import time
from typing import Any

from app.hal.base import HalModule, sim_rng, sim_wave


class HardwareHal(HalModule):
    name = "hardware"

    def __init__(self, force_sim: bool = False) -> None:
        super().__init__(force_sim)
        # Stateful simulated GPIO/PWM so toggles persist within a process.
        self._gpio: dict[int, dict[str, Any]] = {
            17: {"direction": "out", "value": 0},
            18: {"direction": "out", "value": 1},
            27: {"direction": "in", "value": 0},
        }
        self._pwm: dict[int, dict[str, Any]] = {
            0: {"period_ns": 1_000_000, "duty_ns": 500_000, "enabled": True},
            1: {"period_ns": 2_000_000, "duty_ns": 250_000, "enabled": False},
        }
        # Stateful simulated UART: per-port config, open state, and a small
        # echo buffer so a `write` is observable via a subsequent `read`.
        self._uart: dict[str, dict[str, Any]] = {
            "ttyAMA0": {
                "baud": 115_200,
                "data_bits": 8,
                "parity": "none",
                "stop_bits": 1,
                "flow": "none",
                "open": False,
                "echo": b"",
            },
            "ttyUSB0": {
                "baud": 9600,
                "data_bits": 8,
                "parity": "none",
                "stop_bits": 1,
                "flow": "none",
                "open": False,
                "echo": b"",
            },
        }
        # Stateful simulated RS485 / RS422 ports. RS485 has a bus address and
        # can collide (half-duplex); RS422 is full-duplex and does not.
        self._rs485: dict[str, dict[str, Any]] = {
            "ttyRS485-0": {
                "mode": "rs485",
                "baud": 19200,
                "termination": False,
                "address": 1,
                "enabled": True,
                "echo": b"",
            },
            "ttyRS422-0": {
                "mode": "rs422",
                "baud": 115200,
                "termination": True,
                "address": 0,
                "enabled": True,
                "echo": b"",
            },
        }
        # Stateful simulated 1-Wire bus (typically /sys/bus/w1/devices on Linux).
        self._onewire: dict[str, Any] = {
            "bus": "w1-bus",
            "devices": {
                "28-00000a1b2c3d": {
                    "address": "28-00000a1b2c3d",
                    "family": 0x28,
                    "name": "DS18B20",
                    "temp_lo": 18.0,
                    "temp_hi": 28.0,
                },
                "28-00000beef000": {
                    "address": "28-00000beef000",
                    "family": 0x28,
                    "name": "DS18B20",
                    "temp_lo": 40.0,
                    "temp_hi": 65.0,
                },
                "3a-00000abcde01": {
                    "address": "3a-00000abcde01",
                    "family": 0x3A,
                    "name": "DS2413",
                },
            },
        }

    def detect(self) -> bool:
        from app.hal.base import path_exists

        return path_exists("/sys/class/gpio")

    # --- GPIO --------------------------------------------------------------
    def gpio_list(self) -> dict[str, Any]:
        pins = []
        for pin, st in sorted(self._gpio.items()):
            value = st["value"]
            if st["direction"] == "in":
                value = int(sim_wave(5, 0, 1.99, phase=pin))
            pins.append({"pin": pin, "direction": st["direction"], "value": value})
        return self.envelope({"pins": pins})

    def gpio_set(self, pin: int, value: int) -> dict[str, Any]:
        if pin not in self._gpio:
            self._gpio[pin] = {"direction": "out", "value": 0}
        self._gpio[pin]["value"] = 1 if value else 0
        return self.envelope({"pin": pin, "value": self._gpio[pin]["value"]})

    def gpio_export(self, pin: int, direction: str) -> dict[str, Any]:
        self._gpio[pin] = {"direction": direction, "value": 0}
        return self.envelope({"pin": pin, "direction": direction})

    def gpio_unexport(self, pin: int) -> dict[str, Any]:
        self._gpio.pop(pin, None)
        return self.envelope({"pin": pin, "exported": False})

    # --- PWM ---------------------------------------------------------------
    def pwm_list(self) -> dict[str, Any]:
        return self.envelope({"channels": [{"channel": c, **st} for c, st in sorted(self._pwm.items())]})

    def pwm_set(self, channel: int, period_ns: int, duty_ns: int, enabled: bool) -> dict[str, Any]:
        duty_ns = max(0, min(duty_ns, period_ns))
        self._pwm[channel] = {"period_ns": period_ns, "duty_ns": duty_ns, "enabled": enabled}
        return self.envelope({"channel": channel, **self._pwm[channel]})

    # --- ADC / IIO ---------------------------------------------------------
    def adc(self) -> dict[str, Any]:
        channels = []
        for ch in range(4):
            channels.append(
                {
                    "channel": ch,
                    "raw": int(sim_wave(6 + ch, 200, 3800, phase=ch)),
                    "voltage": round(sim_wave(6 + ch, 0.1, 3.3, phase=ch), 3),
                }
            )
        return self.envelope({"channels": channels})

    # --- sensors (hwmon + IIO) --------------------------------------------
    def sensors(self) -> dict[str, Any]:
        defs = [
            ("cpu_temp", "temperature", "°C", 38, 62),
            ("board_temp", "temperature", "°C", 30, 45),
            ("humidity", "humidity", "%", 35, 55),
            ("pressure", "pressure", "hPa", 1005, 1018),
            ("vin", "voltage", "V", 11.8, 12.3),
            ("iin", "current", "A", 0.4, 1.2),
        ]
        out = []
        for name, kind, unit, lo, hi in defs:
            out.append(
                {
                    "name": name,
                    "type": kind,
                    "unit": unit,
                    "value": round(sim_wave(20, lo, hi, phase=hash(name) % 7), 2),
                }
            )
        return self.envelope({"sensors": out})

    # --- peripheral discovery ---------------------------------------------
    def usb(self) -> dict[str, Any]:
        return self.envelope(
            {
                "tree": [
                    {
                        "bus": 1,
                        "device": 1,
                        "name": "Linux Foundation 3.0 root hub",
                        "children": [
                            {"bus": 1, "device": 4, "name": "Generic USB2.0 Hub", "children": []},
                        ],
                    },
                    {"bus": 2, "device": 1, "name": "Linux Foundation 2.0 root hub", "children": []},
                ]
            }
        )

    def i2c_scan(self, bus: int = 1) -> dict[str, Any]:
        rng = sim_rng("i2c", bus)
        present = sorted(rng.sample(range(0x03, 0x78), 4))
        grid = [{"address": f"0x{a:02x}", "present": a in present} for a in range(0x03, 0x78)]
        return self.envelope({"bus": bus, "devices": [f"0x{a:02x}" for a in present], "grid": grid})

    def spi(self) -> dict[str, Any]:
        return self.envelope({"devices": ["spidev0.0", "spidev0.1"]})

    def bluetooth(self) -> dict[str, Any]:
        return self.envelope(
            {
                "adapter": "hci0",
                "powered": True,
                "paired": [{"name": "Lab Headset", "mac": "AA:BB:CC:00:11:22"}],
                "visible": [{"name": "Phone", "mac": "DE:AD:BE:EF:00:01"}],
            }
        )

    def can_status(self) -> dict[str, Any]:
        return self.envelope(
            {
                "interfaces": [
                    {"name": "can0", "state": "ERROR-ACTIVE", "bitrate": 500000, "rx_err": 0, "tx_err": 0},
                ]
            }
        )

    def can_frames(self, n: int = 10) -> dict[str, Any]:
        rng = sim_rng("can", int(time.time()))
        frames = []
        for _ in range(n):
            frames.append(
                {
                    "ts": round(time.time(), 3),
                    "id": f"0x{rng.randrange(0, 0x800):03X}",
                    "dlc": 8,
                    "data": " ".join(f"{rng.randrange(256):02X}" for _ in range(8)),
                }
            )
        return self.envelope({"frames": frames})

    # --- MQTT / Modbus -----------------------------------------------------
    def mqtt_status(self) -> dict[str, Any]:
        return self.envelope(
            {
                "connected": True,
                "broker": "tcp://127.0.0.1:1883",
                "topics": ["devices/+/telemetry", "devices/+/cmd"],
                "messages": [
                    {"topic": "devices/lab1/telemetry", "payload": '{"temp":42.1}', "ts": round(time.time(), 1)},
                ],
            }
        )

    def mqtt_publish(self, topic: str, payload: str) -> dict[str, Any]:
        """Publish a message on an MQTT topic.

        In simulation the message is not actually sent but the call
        returns a clear envelope so the SPA can distinguish
        "would publish" from "published" (#42). A real device would
        use a real MQTT client (e.g. paho-mqtt) here.
        """
        return self.envelope(
            {
                "topic": topic,
                "payload_bytes": len(payload.encode("utf-8")),
                "published": not self.simulated,
            }
        )

    def modbus_read(self, unit: int, function: int, address: int, count: int) -> dict[str, Any]:
        rng = sim_rng("modbus", unit, function, address)
        registers = [{"address": address + i, "value": rng.randrange(0, 65536)} for i in range(count)]
        return self.envelope({"unit": unit, "function": function, "registers": registers})

    # --- UART --------------------------------------------------------------
    def uart_list(self) -> dict[str, Any]:
        ports = [
            {
                "port": name,
                "baud": st["baud"],
                "data_bits": st["data_bits"],
                "parity": st["parity"],
                "stop_bits": st["stop_bits"],
                "flow": st["flow"],
                "open": st["open"],
            }
            for name, st in sorted(self._uart.items())
        ]
        return self.envelope({"ports": ports})

    def uart_config(
        self,
        port: str,
        baud: int,
        data_bits: int = 8,
        parity: str = "none",
        stop_bits: int = 1,
        flow: str = "none",
    ) -> dict[str, Any]:
        if port not in self._uart:
            self._uart[port] = {
                "baud": 115_200,
                "data_bits": 8,
                "parity": "none",
                "stop_bits": 1,
                "flow": "none",
                "open": False,
                "echo": b"",
            }
        if baud not in {1200, 2400, 4800, 9600, 19200, 38400, 57600, 115200, 230400, 460800, 921600}:
            from app.errors import ApiError

            raise ApiError("bad_baud", f"Unsupported baud rate: {baud}", 400)
        if data_bits not in {5, 6, 7, 8}:
            from app.errors import ApiError

            raise ApiError("bad_data_bits", f"data_bits must be 5/6/7/8 (got {data_bits})", 400)
        if parity not in {"none", "even", "odd"}:
            from app.errors import ApiError

            raise ApiError("bad_parity", f"parity must be none/even/odd (got {parity})", 400)
        if stop_bits not in {1, 2}:
            from app.errors import ApiError

            raise ApiError("bad_stop_bits", f"stop_bits must be 1 or 2 (got {stop_bits})", 400)
        if flow not in {"none", "rtscts", "xonxoff"}:
            from app.errors import ApiError

            raise ApiError("bad_flow", f"flow must be none/rtscts/xonxoff (got {flow})", 400)
        st = self._uart[port]
        st.update(
            baud=baud,
            data_bits=data_bits,
            parity=parity,
            stop_bits=stop_bits,
            flow=flow,
        )
        return self.envelope({"port": port, **{k: v for k, v in st.items() if k != "echo"}})

    def uart_open(self, port: str) -> dict[str, Any]:
        if port not in self._uart:
            from app.errors import ApiError

            raise ApiError("port_not_found", f"Unknown UART port '{port}'", 404)
        self._uart[port]["open"] = True
        return self.envelope({"port": port, "open": True})

    def uart_close(self, port: str) -> dict[str, Any]:
        if port not in self._uart:
            from app.errors import ApiError

            raise ApiError("port_not_found", f"Unknown UART port '{port}'", 404)
        self._uart[port]["open"] = False
        return self.envelope({"port": port, "open": False})

    def uart_write(self, port: str, data: str) -> dict[str, Any]:
        if port not in self._uart:
            from app.errors import ApiError

            raise ApiError("port_not_found", f"Unknown UART port '{port}'", 404)
        st = self._uart[port]
        if not st["open"]:
            from app.errors import ApiError

            raise ApiError("port_closed", f"UART port '{port}' is closed", 409)
        encoded = data.encode("utf-8", errors="replace")
        # Loopback echo: simulated writes are observable on the next read.
        st["echo"] = (st["echo"] + encoded)[-1024:]
        return self.envelope({"port": port, "written_bytes": len(encoded)})

    def uart_read(self, port: str, max_bytes: int = 256) -> dict[str, Any]:
        if port not in self._uart:
            from app.errors import ApiError

            raise ApiError("port_not_found", f"Unknown UART port '{port}'", 404)
        st = self._uart[port]
        chunk = bytes(st["echo"][:max_bytes])
        st["echo"] = st["echo"][max_bytes:]
        return self.envelope(
            {
                "port": port,
                "open": st["open"],
                "data": chunk.decode("utf-8", errors="replace"),
                "bytes": len(chunk),
            }
        )

    # --- RS485 / RS422 -----------------------------------------------------
    def rs485_list(self) -> dict[str, Any]:
        ports = [
            {
                "port": name,
                "mode": st["mode"],
                "baud": st["baud"],
                "termination": st["termination"],
                "address": st["address"],
                "enabled": st["enabled"],
            }
            for name, st in sorted(self._rs485.items())
        ]
        return self.envelope({"ports": ports})

    def rs485_config(
        self,
        port: str,
        mode: str,
        baud: int,
        termination: bool = False,
        address: int = 0,
        enabled: bool = True,
    ) -> dict[str, Any]:
        if mode not in {"rs485", "rs422"}:
            from app.errors import ApiError

            raise ApiError("bad_mode", f"mode must be 'rs485' or 'rs422' (got {mode!r})", 400)
        if baud not in {1200, 2400, 4800, 9600, 19200, 38400, 57600, 115200, 230400, 460800, 921600}:
            from app.errors import ApiError

            raise ApiError("bad_baud", f"Unsupported baud rate: {baud}", 400)
        if not 0 <= address <= 255:
            from app.errors import ApiError

            raise ApiError("bad_address", "RS485 address must be 0..255", 400)
        if port not in self._rs485:
            self._rs485[port] = {
                "mode": mode,
                "baud": baud,
                "termination": termination,
                "address": address,
                "enabled": enabled,
                "echo": b"",
            }
        else:
            st = self._rs485[port]
            st.update(mode=mode, baud=baud, termination=termination, address=address, enabled=enabled)
        return self.envelope({"port": port, **{k: v for k, v in self._rs485[port].items() if k != "echo"}})

    def rs485_enable(self, port: str, enabled: bool) -> dict[str, Any]:
        if port not in self._rs485:
            from app.errors import ApiError

            raise ApiError("port_not_found", f"Unknown RS485/RS422 port '{port}'", 404)
        self._rs485[port]["enabled"] = bool(enabled)
        return self.envelope({"port": port, "enabled": self._rs485[port]["enabled"]})

    def rs485_send(self, port: str, frame: str) -> dict[str, Any]:
        if port not in self._rs485:
            from app.errors import ApiError

            raise ApiError("port_not_found", f"Unknown RS485/RS422 port '{port}'", 404)
        st = self._rs485[port]
        if not st["enabled"]:
            from app.errors import ApiError

            raise ApiError("port_disabled", f"RS485/RS422 port '{port}' is disabled", 409)
        encoded = frame.encode("utf-8", errors="replace")
        # Simulated half-duplex collision on RS485: random 5% loss.
        rng = sim_rng("rs485", port, len(st["echo"]))
        if st["mode"] == "rs485" and rng.random() < 0.05:
            return self.envelope({"port": port, "sent_bytes": len(encoded), "collision": True})
        st["echo"] = (st["echo"] + encoded)[-1024:]
        return self.envelope({"port": port, "sent_bytes": len(encoded), "collision": False})

    def rs485_recv(self, port: str, max_bytes: int = 256) -> dict[str, Any]:
        if port not in self._rs485:
            from app.errors import ApiError

            raise ApiError("port_not_found", f"Unknown RS485/RS422 port '{port}'", 404)
        st = self._rs485[port]
        chunk = bytes(st["echo"][:max_bytes])
        st["echo"] = st["echo"][max_bytes:]
        return self.envelope(
            {
                "port": port,
                "enabled": st["enabled"],
                "data": chunk.decode("utf-8", errors="replace"),
                "bytes": len(chunk),
            }
        )

    # --- 1-Wire ------------------------------------------------------------
    def onewire_list(self) -> dict[str, Any]:
        return self.envelope(
            {
                "bus": self._onewire["bus"],
                "devices": list(self._onewire["devices"].values()),
            }
        )

    def onewire_read(self, address: str) -> dict[str, Any]:
        dev = self._onewire["devices"].get(address)
        if dev is None:
            from app.errors import ApiError

            raise ApiError("device_not_found", f"Unknown 1-Wire device '{address}'", 404)
        # Family-28 (DS18B20): temperature in °C, in [lo, hi] of its range.
        if dev["family"] == 0x28:
            temp = round(sim_wave(20, dev["temp_lo"], dev["temp_hi"], phase=hash(address) % 7), 2)
            payload: dict[str, Any] = {"address": address, "family": "0x28", "temperature_c": temp}
        elif dev["family"] == 0x3A:
            # DS2413 PIO: 2-channel binary state.
            payload = {
                "address": address,
                "family": "0x3A",
                "pio_a": bool(int(sim_wave(15, 0, 1.99, phase=0)) >= 1),
                "pio_b": bool(int(sim_wave(17, 0, 1.99, phase=0)) >= 1),
            }
        else:
            payload = {"address": address, "family": f"0x{dev['family']:02X}"}
        return self.envelope(payload)

    def onewire_write(self, address: str, value: int) -> dict[str, Any]:
        dev = self._onewire["devices"].get(address)
        if dev is None:
            from app.errors import ApiError

            raise ApiError("device_not_found", f"Unknown 1-Wire device '{address}'", 404)
        if dev["family"] != 0x3A:
            from app.errors import ApiError

            raise ApiError("not_writable", f"1-Wire device '{address}' is read-only", 400)
        if value not in (0, 1, 2, 3):
            from app.errors import ApiError

            raise ApiError("bad_value", "PIO value must be 0..3 (A|B|AB)", 400)
        return self.envelope({"address": address, "value": value})
