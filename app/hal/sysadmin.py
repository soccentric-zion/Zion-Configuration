"""System administration HAL (section 7).

Provides the live host state the system-admin section configures and controls:
network interface state, listening ports mapped to services, firewall ruleset
rendering, connectivity radios (WiFi scan, Bluetooth), and an aggregate system
status. All readings are deterministic simulations; the router owns persistence
(``net_interfaces``, ``firewall_rules``), validation, confirmation and audit.
"""

from __future__ import annotations

from typing import Any

from app.hal.base import HalModule, sim_rng, sim_wave


class SysadminHal(HalModule):
    name = "sysadmin"

    def detect(self) -> bool:
        from app.hal.base import path_exists

        return path_exists("/sys/class/net")

    # --- network interfaces ------------------------------------------------
    def net_status(self) -> dict[str, Any]:
        rng = sim_rng("netadmin")
        ifaces = [
            {
                "name": "eth0",
                "kind": "ethernet",
                "up": True,
                "carrier": True,
                "speed_mbps": 1000,
                "mac": "dc:a6:32:" + ":".join(f"{rng.randrange(256):02x}" for _ in range(3)),
                "rx_mbps": round(sim_wave(15, 0.1, 80), 2),
                "tx_mbps": round(sim_wave(18, 0.1, 60), 2),
            },
            {
                "name": "wlan0",
                "kind": "wifi",
                "up": True,
                "carrier": True,
                "speed_mbps": 433,
                "mac": "dc:a6:33:" + ":".join(f"{rng.randrange(256):02x}" for _ in range(3)),
                "rx_mbps": round(sim_wave(12, 0.1, 40), 2),
                "tx_mbps": round(sim_wave(14, 0.1, 20), 2),
            },
        ]
        return self.envelope({"interfaces": ifaces})

    # --- ports -------------------------------------------------------------
    def listening_ports(self) -> dict[str, Any]:
        ports = [
            {"port": 22, "protocol": "tcp", "service": "ssh", "address": "0.0.0.0", "pid": 412},
            {"port": 80, "protocol": "tcp", "service": "dashboard", "address": "0.0.0.0", "pid": 980},
            {"port": 1883, "protocol": "tcp", "service": "mosquitto", "address": "127.0.0.1", "pid": 655},
            {"port": 5353, "protocol": "udp", "service": "avahi", "address": "0.0.0.0", "pid": 333},
        ]
        return self.envelope({"ports": ports})

    # --- firewall ----------------------------------------------------------
    def render_ruleset(self, rules: list[dict[str, Any]]) -> dict[str, Any]:
        """Render an nftables-style ruleset from the persisted rules."""
        lines = ["table inet filter {", "  chain input {", "    type filter hook input priority 0; policy drop;"]
        for r in rules:
            if not r.get("enabled", 1):
                continue
            verdict = "accept" if r["action"] == "allow" else "drop"
            proto = r.get("protocol", "tcp")
            src = "" if r.get("source", "any") == "any" else f"ip saddr {r['source']} "
            lines.append(f"    {src}{proto} dport {r['port']} {verdict}")
        lines += ["  }", "}"]
        return self.envelope({"ruleset": "\n".join(lines)})

    # --- connectivity radios ----------------------------------------------
    def wifi_scan(self) -> dict[str, Any]:
        rng = sim_rng("wifiscan")
        nets = []
        for ssid in ("lab-wifi", "guest", "iot-net", "field-5g"):
            nets.append(
                {
                    "ssid": ssid,
                    "signal_dbm": -rng.randrange(35, 85),
                    "channel": rng.choice([1, 6, 11, 36, 44]),
                    "security": rng.choice(["WPA2", "WPA3", "open"]),
                }
            )
        return self.envelope({"networks": nets})

    def bluetooth(self) -> dict[str, Any]:
        return self.envelope(
            {
                "adapter": "hci0",
                "powered": True,
                "version": "5.0",
                "discoverable": False,
                "paired": [{"name": "Lab Headset", "mac": "AA:BB:CC:00:11:22"}],
            }
        )

    # --- write methods (#105, audit #42) -----------------------------------
    # Audit-state alignment: write endpoints that touch radio state must
    # surface an envelope with ``applied: not self.simulated`` so the SPA
    # can show "would apply" vs "applied" (#42) and the audit row
    # reflects what actually happened.

    def wifi_connect(self, ssid: str, psk: str | None) -> dict[str, Any]:
        """Bring up a Wi-Fi connection. In real mode this would shell
        out to ``nmcli device wifi connect`` (or wpa_supplicant); in
        simulation it returns the would-be applied envelope (#42, #105)."""
        return self.envelope(
            {
                "ssid": ssid,
                "psk_len": len(psk or ""),
                "applied": not self.simulated,
            }
        )

    def bluetooth_set_enabled(self, enabled: bool) -> dict[str, Any]:
        """Toggle the Bluetooth radio. In real mode this would invoke
        ``rfkill block/unblock bluetooth`` (or ``bluetoothctl power
        on/off``); in simulation it returns the would-be applied
        envelope (#42, #105)."""
        return self.envelope(
            {
                "enabled": enabled,
                "applied": not self.simulated,
            }
        )

    # --- aggregate status --------------------------------------------------
    def system_status(self, services_up: int, services_total: int, rules_active: int) -> dict[str, Any]:
        return self.envelope(
            {
                "interfaces_up": 2,
                "services_running": services_up,
                "services_total": services_total,
                "firewall_rules_active": rules_active,
                "uptime_load": round(sim_wave(60, 0.1, 1.5), 2),
            }
        )
