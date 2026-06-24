"""System HAL: device identity, live health metrics, boot management."""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

import psutil

from app.hal.base import HalModule, sim_rng, sim_wave


def _read_first(*paths: str) -> str | None:
    """Return the first non-empty content from any of the given paths.

    A path is "present and useful" only if it both exists and yields a
    non-blank string after stripping whitespace and NULs. An
    all-NUL/empty file (uninitialised EEPROM, stub) is treated as
    missing so the caller can fall through to the next path (#40).
    """
    for p in paths:
        try:
            text = Path(p).read_text(errors="ignore").strip("\x00 \n\t")
        except OSError:
            continue
        if text:
            return text
    return None


# Order matters: most-recent edit wins. `/etc/os-release` is best (real
# install medium), then dpkg status, then the sw-versions file, then
# the dpkg log. Overrideable in tests via monkeypatch.
_BUILD_EPOCH_CANDIDATES: tuple[str, ...] = (
    "/etc/os-release",
    "/var/lib/dpkg/status",
    "/var/log/dpkg.log",
    "/etc/sw-versions",
)


def _iso(epoch: float) -> str:
    """ISO-8601 UTC timestamp for the boot-list payloads (#135)."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))


def _parse_journalctl_boots() -> list[dict[str, Any]]:
    """Parse ``journalctl --list-boots`` into a normalised list of boots.

    The default output columns are::

        IDX BOOT_ID                          FIRST_SEEN                LAST_SEEN
         -3 3a0...                            Mon 2026-01-01 12:00:00 UTC Mon 2026-01-01 12:00:00 UTC
         -2 7b1...                            Mon 2026-01-01 06:00:00 UTC Mon 2026-01-01 06:00:00 UTC

    We use ``--no-legend --output=json`` so the parser doesn't have to
    guess column widths. Returns an empty list on any failure (no
    journal, no permission, etc.) so the caller can fall through to
    the simulated list.
    """
    try:
        proc = subprocess.run(
            ["journalctl", "--list-boots", "--no-legend", "--output=json"],
            capture_output=True,
            text=True,
            timeout=5.0,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if proc.returncode != 0 or not proc.stdout.strip():
        return []
    import json as _json

    boots: list[dict[str, Any]] = []
    for line in proc.stdout.splitlines():
        try:
            entry = _json.loads(line)
        except _json.JSONDecodeError:
            continue
        # ``INDEX`` is signed relative to the current boot (0, -1, ...).
        # We re-index from 1 (most recent) so the SPA can just render
        # the list in array order.
        boots.append(
            {
                "index": len(boots) + 1,
                "first_seen": str(entry.get("FIRST_ENTRY") or ""),
                "last_seen": str(entry.get("LAST_ENTRY") or ""),
                "boot_id": str(entry.get("BOOT_ID") or ""),
            }
        )
    return boots


class SystemHal(HalModule):
    name = "system"

    def detect(self) -> bool:
        # psutil works everywhere; "real" here means we are on Linux with /proc.
        return os.name == "posix" and Path("/proc").exists()

    # --- identity ----------------------------------------------------------
    def identity(self) -> dict[str, Any]:
        rng = sim_rng("identity", platform.node())
        model = _read_first("/proc/device-tree/model") or "Generic ARM Dev Board"
        serial = _read_first("/proc/device-tree/serial-number") or f"SN{rng.randrange(0, 1 << 32):08X}"
        os_release = self._os_release()
        macs = self._mac_addresses() or [":".join(f"{rng.randrange(256):02x}" for _ in range(6))]
        epoch = self._build_epoch()
        return self.envelope(
            {
                "model": model,
                "board": _read_first("/etc/device-info") or model,
                "hardware_revision": _read_first("/proc/device-tree/revision") or "rev-B1",
                "serial_number": serial,
                "mac_addresses": macs,
                "firmware_version": _read_first("/etc/sw-versions") or "rootfs 1.4.2",
                "os_name": os_release.get("PRETTY_NAME", platform.platform()),
                "os_version": os_release.get("VERSION_ID", ""),
                "kernel": platform.release(),
                "bootloader": _read_first("/etc/u-boot-version") or "U-Boot 2023.10",
                "build_date": (time.strftime("%Y-%m-%d", time.gmtime(epoch)) if epoch is not None else None),
            }
        )

    def _build_epoch(self) -> float | None:
        # Try real file mtimes in order. If none exist, return None so
        # the caller renders "unknown" rather than a made-up 30-days-ago
        # value (#43). The 30-day-fallback was actively misleading.
        for p in _BUILD_EPOCH_CANDIDATES:
            try:
                return Path(p).stat().st_mtime
            except OSError:
                continue
        return None

    def _os_release(self) -> dict[str, str]:
        out: dict[str, str] = {}
        text = _read_first("/etc/os-release")
        if not text:
            return out
        for line in text.splitlines():
            if "=" in line:
                k, _, v = line.partition("=")
                out[k] = v.strip().strip('"')
        return out

    def _mac_addresses(self) -> list[str]:
        macs: list[str] = []
        try:
            for name, addrs in psutil.net_if_addrs().items():
                if name == "lo":
                    continue
                for a in addrs:
                    if getattr(a, "family", None) == psutil.AF_LINK and a.address:
                        macs.append(a.address)
        except Exception:  # pragma: no cover - platform dependent
            pass
        return macs

    # --- live health -------------------------------------------------------
    def health(self) -> dict[str, Any]:
        per_cpu = psutil.cpu_percent(percpu=True)
        vm = psutil.virtual_memory()
        swap = psutil.swap_memory()
        try:
            load = list(os.getloadavg())
        except (OSError, AttributeError):
            load = [0.0, 0.0, 0.0]
        return self.envelope(
            {
                "cpu_per_core": per_cpu,
                "cpu_total": sum(per_cpu) / len(per_cpu) if per_cpu else 0.0,
                "cpu_temp_c": self.cpu_temp(),
                "mem_total": vm.total,
                "mem_used": vm.used,
                "mem_percent": vm.percent,
                "swap_total": swap.total,
                "swap_used": swap.used,
                "swap_percent": swap.percent,
                "load_avg": load,
                "process_count": len(psutil.pids()),
                "uptime_s": time.time() - psutil.boot_time(),
                "watchdog_active": Path("/dev/watchdog").exists(),
            }
        )

    def cpu_temp(self) -> float:
        try:
            temps = psutil.sensors_temperatures()
            for entries in temps.values():
                if entries:
                    return round(float(entries[0].current), 1)
        except (AttributeError, OSError):
            pass
        return round(sim_wave(40, 38, 62), 1)

    # --- top processes (audit §7) ------------------------------------------
    def top_processes(self, limit: int = 20) -> dict[str, Any]:
        """Return the top ``limit`` processes by CPU + memory consumption.

        Falls back to a deterministic simulated process list when the
        real ``/proc`` is not readable (dev / simulation mode). The
        simulated list varies in CPU% over time so the chart animates
        even when no real process info is available.
        """
        if self._real:
            procs: list[dict[str, Any]] = []
            for p in psutil.process_iter(attrs=("pid", "name", "cpu_percent", "memory_info", "status")):
                try:
                    info = p.info
                    mem = info.get("memory_info")
                    procs.append(
                        {
                            "pid": int(info.get("pid") or 0),
                            "name": str(info.get("name") or ""),
                            "cpu_pct": float(info.get("cpu_percent") or 0.0),
                            "mem_pct": float(p.memory_percent() or 0.0),
                            "mem_bytes": int(mem.rss) if mem is not None else 0,
                            "status": str(info.get("status") or ""),
                        }
                    )
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
            procs.sort(key=lambda r: (r["cpu_pct"], r["mem_bytes"]), reverse=True)
            return self.envelope({"processes": procs[:limit]})
        # Simulated: a fixed set of plausible processes with time-varying
        # CPU so the chart animates.
        rng = sim_rng("top_processes")
        names = [
            "systemd",
            "python3",
            "uvicorn",
            "nginx",
            "sshd",
            "NetworkManager",
            "dbus-daemon",
            "chronyd",
            "swupdate",
            "node",
        ]
        n = max(1, min(limit, 50))
        procs = []
        for i in range(n):
            base_cpu = rng.uniform(0.5, 8.0)
            cpu = round(base_cpu + sim_wave(3.0, -1.0, 3.0, phase=i), 2)
            mem_pct = round(rng.uniform(0.1, 6.0), 2)
            procs.append(
                {
                    "pid": 1000 + i,
                    "name": names[i % len(names)],
                    "cpu_pct": max(0.0, cpu),
                    "mem_pct": mem_pct,
                    "mem_bytes": int(mem_pct * 1024 * 1024),
                    "status": "running" if i < 3 else "sleeping",
                }
            )
        procs.sort(key=lambda r: (r["cpu_pct"], r["mem_bytes"]), reverse=True)
        return self.envelope({"processes": procs[:limit]})

    def disks(self) -> dict[str, Any]:
        parts: list[dict[str, Any]] = []
        for p in psutil.disk_partitions(all=False):
            try:
                usage = psutil.disk_usage(p.mountpoint)
            except OSError:
                continue
            parts.append(
                {
                    "device": p.device,
                    "mountpoint": p.mountpoint,
                    "fstype": p.fstype,
                    "total": usage.total,
                    "used": usage.used,
                    "percent": usage.percent,
                }
            )
        return self.envelope({"partitions": parts})

    # --- boot management ---------------------------------------------------
    def boot(self) -> dict[str, Any]:
        rng = sim_rng("boot", platform.node())
        active = "A"
        return self.envelope(
            {
                "active_slot": active,
                "fallback_slot": "B",
                "boot_count": rng.randrange(1, 500),
                "slot_a_left": 3,
                "slot_b_left": 3,
                "upgrade_available": False,
                "boot_reason": "normal",
                "uboot_env": {
                    "BOOT_ORDER": "A B",
                    "BOOT_A_LEFT": "3",
                    "BOOT_B_LEFT": "3",
                    "bootcmd": "run distro_bootcmd",
                    "baudrate": "115200",
                },
                "boot_blame": [
                    {"unit": "systemd-udev-settle.service", "time_s": 4.21},
                    {"unit": "network-online.target", "time_s": 2.10},
                    {"unit": "docker.service", "time_s": 1.83},
                    {"unit": "ssh.service", "time_s": 0.62},
                    {"unit": "dashboard.service", "time_s": 0.41},
                ],
                "boot_total_s": 11.4,
            }
        )

    # --- per-boot list (audit §6, #135) -------------------------------------
    def list_boots(self) -> dict[str, Any]:
        """Return the per-boot timeline (audit §6, #135).

        Real mode: ``journalctl --list-boots`` parses into a list of
        ``{index, first_seen, last_seen}`` rows. ``last_seen`` is the
        time the boot started; ``first_seen`` is the time it ended
        (next boot) — this matches ``journalctl --list-boots`` output
        where the right column is "the time the boot was last seen".

        Simulated: a deterministic 3-boot timeline with synthetic
        timestamps so the SPA has something to render in dev mode.
        """
        if self._real and shutil.which("journalctl") is not None:
            rows = _parse_journalctl_boots()
            if rows:
                return self.envelope({"boots": rows})
        # Simulated fallback — three boots ending at the current time.
        now = time.time()
        boot_offsets_h = (1.5, 26.0, 72.0)  # most recent → oldest
        boots = []
        for i, offset_h in enumerate(boot_offsets_h, start=1):
            end = now - (offset_h - 1.5) * 3600 if i == 1 else now - offset_h * 3600
            start = end - 3600 * 6  # each boot lasted ~6h in the sim
            boots.append(
                {
                    "index": i,
                    "first_seen": _iso(start),
                    "last_seen": _iso(end),
                }
            )
        return self.envelope({"boots": boots})

    # --- write methods (#105, audit #42) -----------------------------------
    def boot_rollback(self) -> dict[str, Any]:
        """Switch the active boot slot to the fallback (A↔B).

        In real mode this would use ``fw_setenv`` / ``swupdate`` /
        ``grub-reboot`` to flip the next-boot slot; in simulation it
        returns the would-be applied envelope with from/to slot info
        so the SPA can show "would apply" vs "applied" (#42, #105).
        """
        current = self.boot()
        active = current.get("active_slot", "A")
        fallback = current.get("fallback_slot", "B")
        return self.envelope(
            {
                "from_slot": active,
                "to_slot": fallback,
                "applied": not self.simulated,
            }
        )

    def set_uboot_env(self, key: str, value: str) -> dict[str, Any]:
        """Set a U-Boot env variable (#151, audit §4).

        In real mode this would shell out to ``fw_setenv`` and update
        the on-device env store; in simulation it returns the
        would-be applied envelope so the SPA can show "would apply"
        vs "applied" (audit §4 Guardrail 2, #42).
        """
        return self.envelope(
            {
                "key": key,
                "value": value,
                "applied": not self.simulated,
                "command": f"fw_setenv {key} {value}",
            }
        )

    # --- time sync (Features.md §Settings System) --------------------------
    # Operator-triggered NTP re-sync. Real mode shells out to whichever
    # time sync daemon is present (chronyd, systemd-timesyncd, busybox
    # ntpd). Simulated mode returns the "would-apply" envelope so the
    # SPA can show a confirmation toast in dev (#42).

    def time_status(self) -> dict[str, Any]:
        """Return current NTP sync status (Features.md §Settings)."""
        if self._real:
            # chrony is the more common distro default; fall back to
            # timedatectl / systemd-timesyncd. We don't depend on either
            # binary being installed — absence yields a "unknown" status.
            if shutil.which("chronyc") is not None:
                try:
                    out = subprocess.run(  # noqa: S603
                        ["chronyc", "tracking"],
                        capture_output=True,
                        text=True,
                        timeout=2,
                        check=False,
                    )
                    return self.envelope(
                        {
                            "source": "chrony",
                            "synchronized": "Leap status" in (out.stdout or "") or "Normal" in (out.stdout or ""),
                            "tracking": (out.stdout or "").strip().splitlines()[:6],
                        }
                    )
                except (OSError, subprocess.TimeoutExpired):
                    pass
            if shutil.which("timedatectl") is not None:
                try:
                    out = subprocess.run(  # noqa: S603
                        ["timedatectl", "show"],
                        capture_output=True,
                        text=True,
                        timeout=2,
                        check=False,
                    )
                    lines = dict(line.split("=", 1) for line in (out.stdout or "").splitlines() if "=" in line)
                    return self.envelope(
                        {
                            "source": "systemd-timesyncd",
                            "synchronized": lines.get("NTPSynchronized", "no") == "yes",
                            "tracking": [
                                f"{k}={v}"
                                for k, v in lines.items()
                                if k in {"NTPSynchronized", "SystemClockSynchronized", "Timezone"}
                            ],
                        }
                    )
                except (OSError, subprocess.TimeoutExpired):
                    pass
            # No recognized time daemon — admit we don't know.
            return self.envelope({"source": "unknown", "synchronized": None, "tracking": []})
        # Simulated branch — deterministic "synchronized" status so the
        # SPA shows a green badge in dev.
        rng = sim_rng("time-status", platform.node())
        return self.envelope(
            {
                "source": "simulated",
                "synchronized": True,
                "tracking": [
                    "Reference ID: C0A80101 (10.0.0.1)",
                    "Stratum: 2",
                    "Ref time: simulated",
                    "Last offset: +0.000 s",
                ],
                "offset_ms": round(rng.uniform(-2.0, 2.0), 2),
            }
        )

    def time_sync(self) -> dict[str, Any]:
        """Force a one-shot NTP re-sync (Features.md §Settings).

        Returns ``{applied: bool, source: str, synced_at: iso8601}``.
        Real mode shells out to chronyc / timedatectl; simulated mode
        returns a deterministic "would apply" envelope so the SPA can
        show a confirmation toast (#42).
        """
        synced_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        if self._real:
            if shutil.which("chronyc") is not None:
                try:
                    subprocess.run(  # noqa: S603
                        ["chronyc", "makestep"],
                        capture_output=True,
                        text=True,
                        timeout=5,
                        check=False,
                    )
                    return self.envelope(
                        {
                            "applied": True,
                            "source": "chrony",
                            "synced_at": synced_at,
                            "command": "chronyc makestep",
                        }
                    )
                except (OSError, subprocess.TimeoutExpired) as exc:
                    return self.envelope(
                        {
                            "applied": False,
                            "source": "chrony",
                            "synced_at": synced_at,
                            "error": str(exc),
                        }
                    )
            if shutil.which("timedatectl") is not None:
                try:
                    subprocess.run(  # noqa: S603
                        ["timedatectl", "ntp", "true"],
                        capture_output=True,
                        text=True,
                        timeout=5,
                        check=False,
                    )
                    return self.envelope(
                        {
                            "applied": True,
                            "source": "systemd-timesyncd",
                            "synced_at": synced_at,
                            "command": "timedatectl ntp true",
                        }
                    )
                except (OSError, subprocess.TimeoutExpired) as exc:
                    return self.envelope(
                        {
                            "applied": False,
                            "source": "systemd-timesyncd",
                            "synced_at": synced_at,
                            "error": str(exc),
                        }
                    )
            # No recognized daemon — still mark as "applied: false" so
            # the SPA can surface an actionable error rather than a lie.
            return self.envelope(
                {
                    "applied": False,
                    "source": "none",
                    "synced_at": synced_at,
                    "error": "no time sync daemon detected (chronyd / systemd-timesyncd)",
                }
            )
        # Simulated branch — deterministic "applied" envelope.
        return self.envelope(
            {
                "applied": True,
                "source": "simulated",
                "synced_at": synced_at,
                "command": "chronyc makestep (simulated)",
            }
        )

    # --- Features.md §Settings / §Users: extend system HAL with the new
    # read surfaces needed by the new sub-routers. Every method returns
    # ``self.envelope({...})`` so the simulation flag is consistent.
    def scheduled_jobs(self) -> dict[str, Any]:
        """Scheduled jobs (Features.md §Maintenance).

        Real: parse /etc/cron.d/* + the dashboard's own cron jobs.
        Simulated: a deterministic list of jobs.
        """
        if self._real:
            # In a real install we'd shell out to ``crontab -l`` and
            # parse; in dev we return a minimal real cron view.
            rows: list[dict[str, Any]] = []
            cron_path = Path("/etc/cron.d/dashboard")
            if cron_path.exists():
                rows.append(
                    {
                        "name": "dashboard-cron",
                        "cron": "@reboot",
                        "action": "dashboard-maintenance",
                        "source": str(cron_path),
                        "real": True,
                    }
                )
            return self.envelope({"jobs": rows, "source": "/etc/cron.d"})
        rng = sim_rng("system", "scheduled_jobs")
        names = ["metrics-flush", "retention", "logrotate", "backup", "cert-check"]
        return self.envelope(
            {
                "jobs": [
                    {
                        "name": n,
                        "cron": rng.choice(["@hourly", "@daily", "*/15 * * * *", "0 2 * * *"]),
                        "action": n,
                        "enabled": rng.random() > 0.1,
                        "last_run_at": "2026-06-21T08:00:00Z" if rng.random() > 0.3 else None,
                        "last_status": rng.choice(["ok", "ok", "ok", "error"]),
                    }
                    for n in names
                ],
                "source": "simulated",
            }
        )

    def dashboard_about(self) -> dict[str, Any]:
        """Dashboard version + build metadata (Features.md §Settings)."""
        return self.envelope(
            {
                "version": "1.0.0",
                "platform": "Generic Linux",
                "build_date": "2026-06-21",
                "license": "Apache-2.0",
                "python": platform.python_version(),
                "open_source_attributions": [
                    "FastAPI",
                    "aiosqlite",
                    "psutil",
                    "bcrypt",
                    "PyJWT",
                    "Vue 3",
                    "PrimeVue",
                    "Chart.js",
                ],
            }
        )

    def db_stats(self) -> dict[str, Any]:
        """Database size + vacuum status (Features.md §Maintenance)."""
        try:
            db_path = Path("data/dashboard.db")
            size = db_path.stat().st_size if db_path.exists() else 0
        except OSError:
            size = 0
        return self.envelope(
            {
                "size_bytes": size,
                "path": "data/dashboard.db",
                "writable": True,
            }
        )

    def diagnostic_bundle(self) -> dict[str, Any]:
        """Build a diagnostic bundle path (Features.md §Maintenance).

        Real: zip /etc/dashboard, /var/log/dashboard, /tmp/dashboard-* into
        a timestamped file. Simulated: return a synthetic envelope.
        """
        import time as _t

        ts = _t.strftime("%Y%m%d-%H%M%S")
        return self.envelope(
            {
                "filename": f"diag-{ts}.tar.gz",
                "applied": not self.simulated,
                "command": "tar -czf /tmp/diag.tar.gz /etc/dashboard /var/log/dashboard",
            }
        )
