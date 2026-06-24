"""Software HAL: systemd services and containers (Docker/Podman)."""

from __future__ import annotations

import re
import subprocess
import time
from typing import Any

from app.hal.base import HalModule, has_command, sim_rng, sim_wave


class SoftwareHal(HalModule):
    name = "software"

    def __init__(self, force_sim: bool = False) -> None:
        super().__init__(force_sim)
        self._units: dict[str, dict[str, Any]] = {
            str(u["name"]): u
            for u in [
                {
                    "name": "ssh.service",
                    "description": "OpenSSH server",
                    "load": "loaded",
                    "active": "active",
                    "sub": "running",
                    "enabled": "enabled",
                    "cpu_pct": 0.0,
                    "mem_bytes": 0,
                },
                {
                    "name": "docker.service",
                    "description": "Docker Engine",
                    "load": "loaded",
                    "active": "active",
                    "sub": "running",
                    "enabled": "enabled",
                    "cpu_pct": 0.0,
                    "mem_bytes": 0,
                },
                {
                    "name": "systemd-journald.service",
                    "description": "Journal Service",
                    "load": "loaded",
                    "active": "active",
                    "sub": "running",
                    "enabled": "static",
                    "cpu_pct": 0.0,
                    "mem_bytes": 0,
                },
                {
                    "name": "dashboard.service",
                    "description": "Device Dashboard",
                    "load": "loaded",
                    "active": "active",
                    "sub": "running",
                    "enabled": "enabled",
                    "cpu_pct": 0.0,
                    "mem_bytes": 0,
                },
                {
                    "name": "swupdate.service",
                    "description": "SWUpdate daemon",
                    "load": "loaded",
                    "active": "active",
                    "sub": "running",
                    "enabled": "enabled",
                    "cpu_pct": 0.0,
                    "mem_bytes": 0,
                },
                {
                    "name": "chronyd.service",
                    "description": "NTP client",
                    "load": "loaded",
                    "active": "active",
                    "sub": "running",
                    "enabled": "enabled",
                    "cpu_pct": 0.0,
                    "mem_bytes": 0,
                },
            ]
        }
        self._containers: dict[str, dict[str, Any]] = {
            str(c["name"]): c
            for c in [
                {
                    "name": "telemetry-agent",
                    "image": "telemetry:1.2",
                    "state": "running",
                    "started": time.time() - 7200,
                },
                {
                    "name": "edge-broker",
                    "image": "eclipse-mosquitto:2",
                    "state": "running",
                    "started": time.time() - 3600,
                },
            ]
        }

    def detect(self) -> bool:
        return has_command("systemctl")

    # --- services ----------------------------------------------------------
    def services(self) -> dict[str, Any]:
        units = list(self._units.values())
        # Per-unit resource usage (audit §5): real mode parses
        # ``systemctl show <unit>`` for CPUUsageNSec (cgroup accounting,
        # nanoseconds across the unit's lifetime) and MemoryCurrent (bytes
        # in the cgroup). Sim mode returns deterministic per-unit values so
        # the SPA renders consistent rows and the tests don't depend on
        # wall-clock jitter.
        for u in units:
            if u.get("active") != "active":
                u["cpu_pct"] = 0.0
                u["mem_bytes"] = 0
                continue
            if self._real:
                u["cpu_pct"], u["mem_bytes"] = _read_unit_resource_usage(u["name"])
            else:
                rng = sim_rng("unit", u["name"])
                u["cpu_pct"] = round(rng.uniform(0.1, 8.5), 2)
                u["mem_bytes"] = int(rng.uniform(8 * 1024 * 1024, 192 * 1024 * 1024))
        return self.envelope({"units": units})

    def service_action(self, name: str, action: str) -> dict[str, Any]:
        unit = self._units.get(name)
        if unit is None:
            unit = {
                "name": name,
                "description": name,
                "load": "loaded",
                "active": "inactive",
                "sub": "dead",
                "enabled": "disabled",
                "cpu_pct": 0.0,
                "mem_bytes": 0,
            }
            self._units[name] = unit
        if action == "start":
            unit.update(active="active", sub="running")
        elif action == "stop":
            unit.update(active="inactive", sub="dead")
        elif action == "restart":
            unit.update(active="active", sub="running")
        elif action == "enable":
            unit["enabled"] = "enabled"
        elif action == "disable":
            unit["enabled"] = "disabled"
        elif action == "mask":
            # mask makes the unit unstartable — even an explicit
            # ``systemctl start`` will fail. Audit §5 services hardening
            # flow (#108). In simulation we just tag the unit; in real
            # mode we'd ``systemctl mask <unit>`` and link it to /dev/null.
            unit["masked"] = True
        elif action == "unmask":
            unit["masked"] = False
        else:
            # Routers pre-validate, but the HAL is the canonical place to
            # enforce so any direct caller (tests, other routers) sees the
            # same error envelope (#53).
            from app.errors import ApiError

            raise ApiError("bad_action", f"Unknown action '{action}'", 400)
        return self.envelope({"unit": unit})

    def service_journal(self, name: str, lines: int = 20) -> dict[str, Any]:
        rng = sim_rng("journal", name)
        out = [
            f"{time.strftime('%b %d %H:%M:%S')} host {name}[{rng.randrange(100, 9999)}]: " f"message line {i}"
            for i in range(lines)
        ]
        return self.envelope({"unit": name, "lines": out})

    # --- containers --------------------------------------------------------
    def container_runtime(self) -> str:
        if has_command("docker"):
            return "docker"
        if has_command("podman"):
            return "podman"
        return "docker (simulated)"

    def containers(self) -> dict[str, Any]:
        out = []
        for c in self._containers.values():
            out.append(
                {
                    "name": c["name"],
                    "image": c["image"],
                    "state": c["state"],
                    "uptime_s": time.time() - c["started"] if c["state"] == "running" else 0,
                    "cpu_pct": round(sim_wave(10, 0.2, 12), 1) if c["state"] == "running" else 0,
                    "mem_mb": round(sim_wave(30, 40, 180), 1) if c["state"] == "running" else 0,
                }
            )
        return self.envelope({"runtime": self.container_runtime(), "containers": out})

    def container_action(self, name: str, action: str) -> dict[str, Any]:
        c = self._containers.get(name)
        if c is None and action != "pull":
            from app.errors import ApiError

            raise ApiError("container_not_found", f"Container '{name}' not found", 404)
        if action == "start" and c:
            c.update(state="running", started=time.time())
        elif action == "stop" and c:
            c["state"] = "exited"
        elif action == "restart" and c:
            c.update(state="running", started=time.time())
        elif action == "remove" and c:
            self._containers.pop(name, None)
        elif action == "pull":
            # pulling a new image is handled by container_pull(); calling
            # it via container_action with no matching record is allowed so
            # the router can route 'pull' to container_pull() directly.
            pass
        else:
            from app.errors import ApiError

            raise ApiError("bad_action", f"Unknown action '{action}'", 400)
        return self.envelope({"name": name, "action": action})

    def container_pull(self, image: str) -> dict[str, Any]:
        name = image.split(":")[0].split("/")[-1]
        self._containers[name] = {"name": name, "image": image, "state": "exited", "started": time.time()}
        return self.envelope({"image": image, "pulled": True})

    def container_logs(self, name: str, lines: int = 50) -> dict[str, Any]:
        rng = sim_rng("clogs", name)
        out = [f"[{time.strftime('%H:%M:%S')}] {name}: log entry {i} ({rng.randrange(1000)})" for i in range(lines)]
        return self.envelope({"name": name, "lines": out})


# --- helpers ---------------------------------------------------------------
# Parse ``systemctl show <unit> --property=CPUUsageNSec,MemoryCurrent`` once
# per services() call. The fields are nanoseconds-of-CPU across the unit's
# lifetime (cgroup accounting) and bytes-in-cgroup; CPU percentage is
# derived from a second snapshot 200ms apart. Tests do not exercise the
# real path (simulation is on by default), but a real box needs the
# numbers too (#100).

_SHOW_FIELDS = ("CPUUsageNSec", "MemoryCurrent")
_SHOW_RE = re.compile(r"^(CPUUsageNSec|MemoryCurrent)=(\S*)$")


def _read_unit_resource_usage(unit: str) -> tuple[float, int]:
    """Return ``(cpu_pct, mem_bytes)`` for an active unit. On any parse
    or subprocess failure, returns ``(0.0, 0)`` so a missing field never
    breaks the listing."""
    if not has_command("systemctl"):
        return 0.0, 0
    try:
        first = _show_unit(unit)
        time.sleep(0.2)
        second = _show_unit(unit)
    except (OSError, subprocess.SubprocessError):
        return 0.0, 0
    cpu_ns_1 = first.get("CPUUsageNSec")
    cpu_ns_2 = second.get("CPUUsageNSec")
    cpu_pct = 0.0
    if cpu_ns_1 is not None and cpu_ns_2 is not None:
        # 200ms sample window → fraction of one CPU; multiply by 100 for %.
        delta_ns = max(0, int(cpu_ns_2) - int(cpu_ns_1))
        cpu_pct = round(delta_ns / 0.2 / 1_000_000_000 * 100, 2)
    mem_bytes = 0
    mem_raw = second.get("MemoryCurrent")
    if mem_raw is not None and mem_raw != "[not set]":
        try:
            mem_bytes = int(mem_raw)
        except ValueError:
            mem_bytes = 0
    return cpu_pct, mem_bytes


def _show_unit(unit: str) -> dict[str, str]:
    out = subprocess.run(  # noqa: S603 — fixed argv, no shell
        ["systemctl", "show", unit, "--property=" + ",".join(_SHOW_FIELDS)],  # noqa: S607
        capture_output=True,
        text=True,
        timeout=2,
        check=False,
    )
    result: dict[str, str] = {}
    for line in out.stdout.splitlines():
        m = _SHOW_RE.match(line)
        if m:
            result[m.group(1)] = m.group(2)
    return result
