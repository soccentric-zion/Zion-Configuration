"""Network HAL: interfaces, wifi, firewall, ports, remote access."""

from __future__ import annotations

import socket
import time
from typing import Any

import psutil

from app.hal.base import HalModule, sim_rng, sim_wave

_prev_counters: dict[str, tuple[float, int, int]] = {}


class NetworkHal(HalModule):
    name = "network"

    def detect(self) -> bool:
        try:
            return bool(psutil.net_if_addrs())
        except Exception:  # pragma: no cover
            return False

    def interfaces(self) -> dict[str, Any]:
        result: list[dict[str, Any]] = []
        addrs = psutil.net_if_addrs()
        stats = psutil.net_if_stats()
        io = psutil.net_io_counters(pernic=True)
        now = time.time()
        for name, addr_list in addrs.items():
            ipv4 = next((a.address for a in addr_list if a.family == socket.AF_INET), None)
            ipv6 = next((a.address for a in addr_list if a.family == socket.AF_INET6), None)
            mac = next(
                (a.address for a in addr_list if getattr(a, "family", None) == psutil.AF_LINK),
                None,
            )
            st = stats.get(name)
            counters = io.get(name)
            rx = counters.bytes_recv if counters else 0
            tx = counters.bytes_sent if counters else 0
            rx_rate = tx_rate = 0.0
            prev = _prev_counters.get(name)
            if prev:
                dt = max(now - prev[0], 1e-6)
                rx_rate = max(0.0, (rx - prev[1]) / dt)
                tx_rate = max(0.0, (tx - prev[2]) / dt)
            _prev_counters[name] = (now, rx, tx)
            result.append(
                {
                    "name": name,
                    "ipv4": ipv4,
                    "ipv6": ipv6,
                    "mac": mac,
                    "is_up": st.isup if st else False,
                    "speed_mbps": st.speed if st else 0,
                    "rx_bytes": rx,
                    "tx_bytes": tx,
                    "rx_rate": rx_rate,
                    "tx_rate": tx_rate,
                }
            )
        return self.envelope({"interfaces": result})

    def wifi(self) -> dict[str, Any]:
        return self.envelope(
            {
                "present": self.simulated,
                "ssid": "lab-net-5g",
                "signal_dbm": round(sim_wave(30, -72, -48)),
                "bars": 3,
                "frequency_mhz": 5180,
                "security": "WPA2-PSK",
            }
        )

    def cellular(self) -> dict[str, Any]:
        return self.envelope(
            {
                "present": False,
                "carrier": None,
                "technology": None,
                "signal_dbm": None,
                "data_used_mb": 0,
            }
        )

    def routing(self) -> dict[str, Any]:
        return self.envelope(
            {
                "default_gateway": "192.168.1.1",
                "dns_servers": ["192.168.1.1", "1.1.1.1"],
                "routes": [
                    {"dest": "0.0.0.0/0", "gateway": "192.168.1.1", "iface": "eth0"},
                    {"dest": "192.168.1.0/24", "gateway": "*", "iface": "eth0"},
                ],
            }
        )

    def firewall(self) -> dict[str, Any]:
        return self.envelope(
            {
                "backend": "nftables",
                "enabled": True,
                "zones": [
                    {"name": "wan", "enabled": True, "policy": "drop"},
                    {"name": "lan", "enabled": True, "policy": "accept"},
                ],
                "ruleset": [
                    "table inet filter {",
                    "  chain input { type filter hook input priority 0; policy drop;",
                    "    ct state established,related accept",
                    "    iif lo accept",
                    "    tcp dport { 22, 8000 } accept",
                    "  }",
                    "}",
                ],
            }
        )

    def open_ports(self) -> dict[str, Any]:
        ports: list[dict[str, Any]] = []
        try:
            for c in psutil.net_connections(kind="inet"):
                if c.status == psutil.CONN_LISTEN and c.laddr:
                    ports.append(
                        {
                            "proto": "tcp" if c.type == socket.SOCK_STREAM else "udp",
                            "address": c.laddr.ip,
                            "port": c.laddr.port,
                            "pid": c.pid,
                        }
                    )
        except (psutil.AccessDenied, PermissionError):
            rng = sim_rng("ports")
            for port in (22, 80, 8000, 8090):
                ports.append({"proto": "tcp", "address": "0.0.0.0", "port": port, "pid": rng.randrange(100, 9999)})
        return self.envelope({"ports": ports})

    def remote_access(self) -> dict[str, Any]:
        return self.envelope(
            {
                "ssh_enabled": True,
                "ssh_port": 22,
                "ssh_sessions": [
                    {"user": "root", "from": "192.168.1.50", "since": "10:21"},
                ],
                "reverse_tunnel": {"active": False, "target": None},
                "vpn": {
                    "type": "wireguard",
                    "interface": "wg0",
                    "connected": False,
                    "peers": [],
                },
            }
        )

    def ping(self, target: str) -> dict[str, Any]:
        rng = sim_rng("ping", target, int(time.time()))
        latency = round(sim_wave(8, 4, 30) + rng.uniform(-1, 1), 2)
        return self.envelope({"target": target, "latency_ms": latency, "packet_loss_pct": 0.0})

    def vpn_apply(self, target: str, action: str) -> dict[str, Any]:
        """Apply a VPN up/down/restart action.

        In real mode this shells out to ``wg-quick {action} {target}``
        (WireGuard), captures stdout/stderr, and raises
        ``ApiError("vpn_apply_failed", ..., 500)`` on a non-zero exit
        so the router can return a clear error envelope. In simulation
        it returns a clear envelope so the SPA can distinguish
        "would apply" from "applied" (#42). Audit logging in the
        router records the ``applied`` flag so the action log shows
        what really happened.

        CLAUDE.md #53: the HAL is the canonical enforcement point.
        Unknown actions raise 400 — they don't silently return success.
        """
        if action not in {"up", "down", "restart"}:
            from app.errors import ApiError

            raise ApiError(
                "bad_action",
                f"vpn action '{action}' not supported (use 'up', 'down', or 'restart')",
                400,
            )
        if self._real:
            import subprocess

            from app.errors import ApiError

            try:
                proc = subprocess.run(  # noqa: S603 — fixed argv, no shell
                    ["wg-quick", action, target],  # noqa: S607
                    capture_output=True,
                    text=True,
                    timeout=10,
                    check=False,
                )
            except FileNotFoundError as exc:
                raise ApiError(
                    "vpn_apply_failed",
                    f"wg-quick not installed: {exc}",
                    500,
                ) from exc
            except (OSError, subprocess.SubprocessError) as exc:
                raise ApiError(
                    "vpn_apply_failed",
                    f"wg-quick {action} {target} failed to start: {exc}",
                    500,
                ) from exc
            if proc.returncode != 0:
                raise ApiError(
                    "vpn_apply_failed",
                    f"wg-quick {action} {target} exited {proc.returncode}: "
                    f"{(proc.stderr or proc.stdout).strip()[:500]}",
                    500,
                )
            return self.envelope(
                {
                    "target": target,
                    "action": action,
                    "applied": True,
                    "returncode": proc.returncode,
                    "stdout": proc.stdout,
                }
            )
        return self.envelope(
            {
                "target": target,
                "action": action,
                "applied": False,
            }
        )
