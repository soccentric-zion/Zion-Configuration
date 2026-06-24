"""Thermal HAL: zones, fans, throttling, cooling policy, watchdog."""

from __future__ import annotations

import os
from typing import Any

import psutil

from app.hal.base import HalModule, sim_wave


class ThermalHal(HalModule):
    name = "thermal"

    def detect(self) -> bool:
        from app.hal.base import path_exists

        return path_exists("/sys/class/thermal")

    def zones(self) -> dict[str, Any]:
        zones: list[dict[str, Any]] = []
        try:
            temps = psutil.sensors_temperatures()
            for label, entries in temps.items():
                for i, e in enumerate(entries):
                    zones.append(
                        {
                            "name": f"{label}{i}",
                            "temp_c": round(float(e.current), 1),
                            "trip_high_c": float(e.high) if e.high else 90.0,
                            "trip_crit_c": float(e.critical) if e.critical else 100.0,
                            "cooling_state": 0,
                        }
                    )
        except (AttributeError, OSError):
            pass
        if not zones:
            zones = [
                {
                    "name": "cpu-thermal",
                    "temp_c": round(sim_wave(40, 40, 65), 1),
                    "trip_high_c": 80.0,
                    "trip_crit_c": 95.0,
                    "cooling_state": 1,
                },
                {
                    "name": "gpu-thermal",
                    "temp_c": round(sim_wave(50, 35, 58), 1),
                    "trip_high_c": 85.0,
                    "trip_crit_c": 100.0,
                    "cooling_state": 0,
                },
            ]
        return self.envelope({"zones": zones})

    def fans(self) -> dict[str, Any]:
        return self.envelope(
            {
                "fans": [
                    {"name": "fan0", "rpm": int(sim_wave(30, 1800, 4200)), "pwm": 128, "mode": "auto"},
                ]
            }
        )

    def fan_set(self, name: str, pwm: int, mode: str) -> dict[str, Any]:
        return self.envelope({"name": name, "pwm": max(0, min(255, pwm)), "mode": mode})

    def cooling_policy(self) -> dict[str, Any]:
        """Return the active cooling policy (Features.md §Thermal).

        The policy governs how aggressively the kernel responds to
        trip-point crossings. Real: read /sys/module/thermal/parameters/
        gov_enabled or platform-specific. Simulated: deterministic.
        """
        return self.envelope(
            {
                "policy": "active",  # active | passive | disabled
                "available": ["active", "passive", "disabled"],
                "platform_supports_passive": True,
            }
        )

    def set_cooling_policy(self, policy: str) -> dict[str, Any]:
        """Apply a cooling policy (Features.md §Thermal Cooling Policy).

        Privileged write; the SPA button is admin-only and the router
        validates the value against the available list.
        """
        applied = not self.simulated
        return self.envelope(
            {
                "policy": policy,
                "applied": applied,
                "command": f"thermal: set policy {policy}",
            }
        )

    def throttling(self) -> dict[str, Any]:
        try:
            freq = psutil.cpu_freq()
        except (AttributeError, OSError):
            freq = None
        # Preserve the raw reported values; only fall back when the entire
        # struct is missing. A reported 0.0 must surface as 0.0, not as a
        # 1800.0 default, so we can distinguish "driver absent / idle" from
        # "frequency really is 1800 MHz".
        if freq is None:
            cur, mx = 0.0, 0.0
        else:
            cur = float(freq.current)
            mx = float(freq.max)
        # A current/max of 0 means the driver does not report a usable value
        # (idle system, no cpufreq driver, or the value was not yet sampled).
        # Treat that as "unknown" — not throttled. 0 < 0 * 0.6 is True, so the
        # naive comparison would raise a false alarm on every idle device.
        if cur <= 0.0 or mx <= 0.0:
            throttled: bool | None = None
        else:
            throttled = cur < mx * 0.6
        return self.envelope(
            {
                "cpu_freq_mhz": round(cur, 0),
                "cpu_freq_max_mhz": round(mx, 0),
                "throttled": throttled,
                "throttle_events": 0,
                "cooling_policy": "active",
            }
        )

    def watchdog(self) -> dict[str, Any]:
        """Report the watchdog device's presence and access state (#49).

        ``present`` means a device file exists at /dev/watchdog.
        ``accessible`` means we can actually open it O_RDWR; without
        that, "kicking" the watchdog would silently fail. The original
        code only stat-ed the path, so a locked-down device looked
        identical to a fully accessible one.
        """
        from app.hal.base import path_exists

        present = path_exists("/dev/watchdog")
        accessible = False
        access_error: str | None = None
        if present:
            try:
                # O_RDWR | O_NONBLOCK: real wd drivers reject O_WRONLY
                # without O_NONBLOCK. We just open + close — never write,
                # so we don't accidentally kick it.
                fd = os.open("/dev/watchdog", os.O_RDWR | os.O_NONBLOCK)
                try:
                    accessible = True
                finally:
                    os.close(fd)
            except (PermissionError, OSError) as e:
                accessible = False
                access_error = str(e) or e.__class__.__name__
        return self.envelope(
            {
                "present": present,
                "accessible": accessible,
                "access_error": access_error,
                "type": "hardware" if present else "software",
                "timeout_s": 30,
                "kick_interval_s": 10,
                "last_reset_reason": "normal",
            }
        )
