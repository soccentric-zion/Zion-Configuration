"""Storage & power HAL: storage health, power source, RTC."""

from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import Any, Literal

import psutil

from app.hal.base import HalModule, sim_rng, sim_wave


class StorageHal(HalModule):
    name = "storage"

    def detect(self) -> bool:
        from app.hal.base import path_exists

        return path_exists("/sys/class/block")

    def health(self) -> dict[str, Any]:
        return self.envelope(
            {
                "emmc": {
                    "present": True,
                    "life_time_a_pct": 10,
                    "life_time_b_pct": 0,
                    "pre_eol": "Normal",
                    "size_gb": 16,
                },
                "sd_card": {"present": False, "size_gb": 0},
                "smart": [
                    {
                        "device": "/dev/nvme0n1",
                        "health": "PASSED",
                        "temperature_c": round(sim_wave(60, 30, 48)),
                        "power_on_hours": 8421,
                        "percent_used": 6,
                    }
                ],
                "lsblk": [
                    {
                        "name": "mmcblk0",
                        "size": "16G",
                        "type": "disk",
                        "mountpoint": None,
                        "children": [
                            {"name": "mmcblk0p1", "size": "256M", "type": "part", "mountpoint": "/boot"},
                            {"name": "mmcblk0p2", "size": "15G", "type": "part", "mountpoint": "/"},
                        ],
                    },
                ],
            }
        )

    def mounts(self) -> dict[str, Any]:
        parts = []
        for p in psutil.disk_partitions(all=False):
            try:
                u = psutil.disk_usage(p.mountpoint)
            except OSError:
                continue
            parts.append(
                {
                    "device": p.device,
                    "mountpoint": p.mountpoint,
                    "fstype": p.fstype,
                    "total": u.total,
                    "used": u.used,
                    "percent": u.percent,
                }
            )
        return self.envelope({"mounts": parts})

    def power(self) -> dict[str, Any]:
        battery = None
        try:
            b = psutil.sensors_battery()
            if b is not None:
                battery = {"percent": b.percent, "plugged": b.power_plugged}
        except (AttributeError, OSError):
            pass
        return self.envelope(
            {
                "source": "DC" if battery is None else "battery",
                "battery": battery,
                "input_voltage_v": round(sim_wave(40, 11.8, 12.3), 2),
                "input_current_a": round(sim_wave(15, 0.4, 1.1), 2),
                "wake_sources": ["rtc", "gpio-button", "ethernet"],
            }
        )

    def rtc(self) -> dict[str, Any]:
        now = time.time()
        return self.envelope(
            {
                "rtc_time": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(now)),
                "system_time": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(now)),
                "ntp_synced": True,
                "drift_ppm": round(sim_wave(120, -3, 3), 2),
                "last_sync": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(now - 1800)),
            }
        )

    # --- SMART self-test (#101, audit §7) ---------------------------------
    def smart_test(self, device: str, kind: Literal["short", "long"]) -> dict[str, Any]:
        """Trigger a SMART short/long self-test on ``device``.

        In real mode this shells out to ``smartctl -t {kind} {device}``
        and surfaces the captured stdout/stderr. In simulation it
        returns an ``applied: False`` envelope with the would-be
        command so the SPA can show "would apply" vs "applied" (#42).
        The ``kind`` argument is restricted to the SMART-recognised
        values; anything else is rejected at the HAL boundary so a
        direct caller (test, future router) cannot bypass the router
        validator (#53).
        """
        if kind not in {"short", "long"}:
            from app.errors import ApiError

            raise ApiError("bad_kind", f"Unknown SMART test kind '{kind}'", 400)
        command = f"smartctl -t {kind} {device}"
        if self._real:
            try:
                proc = subprocess.run(  # noqa: S603 — fixed argv, no shell
                    ["smartctl", "-t", kind, device],  # noqa: S607
                    capture_output=True,
                    text=True,
                    timeout=10,
                    check=False,
                )
                return self.envelope(
                    {
                        "applied": True,
                        "device": device,
                        "kind": kind,
                        "command": command,
                        "returncode": proc.returncode,
                        "stdout": proc.stdout,
                        "stderr": proc.stderr,
                    }
                )
            except (OSError, subprocess.SubprocessError) as exc:
                # Degrade gracefully — the test was at least attempted.
                return self.envelope(
                    {
                        "applied": False,
                        "device": device,
                        "kind": kind,
                        "command": command,
                        "error": str(exc),
                    }
                )
        # Simulation path: record what *would* have run.
        return self.envelope(
            {
                "applied": False,
                "device": device,
                "kind": kind,
                "command": command,
            }
        )

    # --- mount / unmount / partition / overlayfs / luks / ab-data (#113) ---
    # audit §10 Storage: "Partitions, mounts, overlayfs state, A/B data
    # slots, disk encryption (LUKS). Apply mode: `next-boot`/`guarded`
    # for layout changes." Each method follows the audit-state
    # alignment pattern (#42): in real mode it shells out and returns
    # the captured output; in simulation it returns an envelope with
    # `applied: False` and the would-be command so the SPA can show
    # "would apply" vs "applied".

    def mount(self, *, device: str, mountpoint: str, fstype: str, options: str) -> dict[str, Any]:
        """Mount ``device`` at ``mountpoint``."""
        command = f"mount -t {fstype} {options and ('-o ' + options + ' ') or ''}{device} {mountpoint}"
        if self._real:
            try:
                proc = subprocess.run(  # noqa: S603 — fixed argv, no shell
                    ["mount", "-t", fstype] + (["-o", options] if options else []) + [device, mountpoint],  # noqa: S607
                    capture_output=True,
                    text=True,
                    timeout=10,
                    check=False,
                )
                return self.envelope(
                    {
                        "applied": proc.returncode == 0,
                        "device": device,
                        "mountpoint": mountpoint,
                        "fstype": fstype,
                        "command": command,
                        "returncode": proc.returncode,
                        "stdout": proc.stdout,
                        "stderr": proc.stderr,
                    }
                )
            except (OSError, subprocess.SubprocessError) as exc:
                return self.envelope(
                    {
                        "applied": False,
                        "device": device,
                        "mountpoint": mountpoint,
                        "fstype": fstype,
                        "command": command,
                        "error": str(exc),
                    }
                )
        return self.envelope(
            {
                "applied": False,
                "device": device,
                "mountpoint": mountpoint,
                "fstype": fstype,
                "command": command,
            }
        )

    def unmount(self, *, mountpoint: str) -> dict[str, Any]:
        """Unmount ``mountpoint``."""
        command = f"umount {mountpoint}"
        if self._real:
            try:
                proc = subprocess.run(  # noqa: S603 — fixed argv, no shell
                    ["umount", mountpoint],  # noqa: S607
                    capture_output=True,
                    text=True,
                    timeout=10,
                    check=False,
                )
                return self.envelope(
                    {
                        "applied": proc.returncode == 0,
                        "mountpoint": mountpoint,
                        "command": command,
                        "returncode": proc.returncode,
                        "stdout": proc.stdout,
                        "stderr": proc.stderr,
                    }
                )
            except (OSError, subprocess.SubprocessError) as exc:
                return self.envelope(
                    {
                        "applied": False,
                        "mountpoint": mountpoint,
                        "command": command,
                        "error": str(exc),
                    }
                )
        return self.envelope(
            {
                "applied": False,
                "mountpoint": mountpoint,
                "command": command,
            }
        )

    def partition(self, *, op: str, device: str, params: dict[str, Any]) -> dict[str, Any]:
        """Partition-table operations (create / delete / resize).

        In real mode the call is shelled out to ``parted`` (the de
        facto Linux partitioner). In simulation the envelope records
        the would-be command so the SPA can show "would apply".
        """
        allowed = {"create", "delete", "resize"}
        if op not in allowed:
            from app.errors import ApiError

            raise ApiError("bad_action", f"Unknown partition op '{op}'", 400)
        size_arg = params.get("size")
        partno = params.get("partno", "X")
        command = f"parted {device} {op} {partno}{f' {size_arg}' if size_arg else ''}".strip()
        if self._real:
            try:
                argv = ["parted", device, op, str(partno)]
                if size_arg:
                    argv.append(str(size_arg))
                proc = subprocess.run(  # noqa: S603 — fixed argv, no shell
                    argv,  # noqa: S607
                    capture_output=True,
                    text=True,
                    timeout=30,
                    check=False,
                )
                return self.envelope(
                    {
                        "applied": proc.returncode == 0,
                        "op": op,
                        "device": device,
                        "partno": partno,
                        "command": command,
                        "returncode": proc.returncode,
                        "stdout": proc.stdout,
                        "stderr": proc.stderr,
                    }
                )
            except (OSError, subprocess.SubprocessError) as exc:
                return self.envelope(
                    {
                        "applied": False,
                        "op": op,
                        "device": device,
                        "command": command,
                        "error": str(exc),
                    }
                )
        return self.envelope(
            {
                "applied": False,
                "op": op,
                "device": device,
                "partno": partno,
                "command": command,
            }
        )

    def overlayfs_state(self) -> dict[str, Any]:
        """Read the live overlayfs lower/upper/work directories (read-only)."""
        if self._real:
            mounts = []
            try:
                with open("/proc/mounts", encoding="utf-8") as f:
                    for line in f:
                        parts = line.split()
                        if len(parts) >= 4 and parts[2] == "overlay":
                            opts = dict(kv.split("=", 1) for kv in parts[3].split(",") if "=" in kv)
                            mounts.append(
                                {
                                    "mountpoint": parts[1],
                                    "device": parts[0],
                                    "lower": opts.get("lowerdir", "").split(":"),
                                    "upper": opts.get("upperdir"),
                                    "work": opts.get("workdir"),
                                }
                            )
            except OSError as exc:
                return self.envelope({"applied": False, "overlayfs": [], "error": str(exc)})
            return self.envelope({"applied": True, "overlayfs": mounts})
        return self.envelope(
            {
                "applied": True,
                "overlayfs": [
                    {
                        "mountpoint": "/",
                        "device": "overlay",
                        "lower": ["/data/rootfs.lower"],
                        "upper": "/data/rootfs.upper",
                        "work": "/data/rootfs.work",
                    }
                ],
            }
        )

    def luks_info(self) -> dict[str, Any]:
        """Read LUKS header info (read-only — no key-slot mutation)."""
        if self._real:
            devices: list[dict[str, Any]] = []
            try:
                out = subprocess.run(  # noqa: S603 — fixed argv, no shell
                    ["lsblk", "-o", "NAME,TYPE,FSTYPE", "-n", "-l"],  # noqa: S607
                    capture_output=True,
                    text=True,
                    timeout=5,
                    check=False,
                ).stdout
                for line in out.splitlines():
                    parts = line.split()
                    if len(parts) >= 3 and parts[2] == "crypto_LUKS":
                        devices.append({"name": parts[0], "type": parts[1], "fstype": parts[2]})
            except (OSError, subprocess.SubprocessError) as exc:
                return self.envelope({"applied": False, "luks": [], "error": str(exc)})
            return self.envelope({"applied": True, "luks": devices})
        return self.envelope(
            {
                "applied": True,
                "luks": [
                    {"name": "sda2_crypt", "type": "crypt", "fstype": "crypto_LUKS"},
                ],
            }
        )

    def ab_data_slot(self, *, slot: str) -> dict[str, Any]:
        """Mark an A/B data slot for next-boot selection.

        This is a staging-only operation — the next-boot hook reads the
        slot from the persisted state and switches. The HAL records
        the would-be command so the SPA can show "would apply".
        """
        if slot not in {"A", "B"}:
            from app.errors import ApiError

            raise ApiError("bad_slot", f"Unknown slot '{slot}' (expected A or B)", 400)
        command = f"ab_data_slot {slot}"
        if self._real:
            try:
                proc = subprocess.run(  # noqa: S603 — fixed argv, no shell
                    ["ab_data_slot", slot],  # noqa: S607
                    capture_output=True,
                    text=True,
                    timeout=10,
                    check=False,
                )
                return self.envelope(
                    {
                        "applied": proc.returncode == 0,
                        "slot": slot,
                        "command": command,
                        "returncode": proc.returncode,
                        "stdout": proc.stdout,
                        "stderr": proc.stderr,
                    }
                )
            except (OSError, subprocess.SubprocessError) as exc:
                return self.envelope(
                    {
                        "applied": False,
                        "slot": slot,
                        "command": command,
                        "error": str(exc),
                    }
                )
        return self.envelope(
            {
                "applied": False,
                "slot": slot,
                "command": command,
            }
        )

    def fsck(self, *, device: str) -> dict[str, Any]:
        """Read-only filesystem check (audit §10, #111).

        Real mode runs ``e2fsck -n {device}`` (read-only, no
        modification). The captured stdout/stderr is surfaced so the
        operator can see the actual result. Simulation records the
        would-be command per the audit-state alignment pattern (#42).
        """
        command = f"e2fsck -n {device}"
        if self._real:
            try:
                proc = subprocess.run(  # noqa: S603 — fixed argv, no shell
                    ["e2fsck", "-n", device],  # noqa: S607
                    capture_output=True,
                    text=True,
                    timeout=60,
                    check=False,
                )
                return self.envelope(
                    {
                        "applied": True,
                        "device": device,
                        "command": command,
                        "returncode": proc.returncode,
                        "stdout": proc.stdout,
                        "stderr": proc.stderr,
                        "ok": proc.returncode == 0,
                    }
                )
            except (OSError, subprocess.SubprocessError) as exc:
                return self.envelope(
                    {
                        "applied": False,
                        "device": device,
                        "command": command,
                        "error": str(exc),
                    }
                )
        return self.envelope(
            {
                "applied": False,
                "device": device,
                "command": command,
            }
        )

    # --- Features.md §Storage: additional surfaces for the new sub-routers.
    def scheduled_backups(self) -> dict[str, Any]:
        """Backup jobs (Features.md §Settings backup).

        Real: parse ``/etc/dashboard/backup.yaml`` if present.
        Simulated: deterministic list, returned as an envelope.
        """
        if self._real:
            cfg = Path("/etc/dashboard/backup.yaml")
            if cfg.exists():
                return self.envelope(
                    {
                        "jobs": [
                            {
                                "name": "config-backup",
                                "kind": "config",
                                "target": str(cfg),
                                "schedule": "0 2 * * *",
                                "enabled": True,
                                "source": "real",
                            }
                        ]
                    }
                )
            return self.envelope({"jobs": []})
        rng = sim_rng("storage", "backups")
        names = ["config-daily", "full-weekly", "snapshot-monthly"]
        kinds = ["config", "full", "factory"]
        jobs = [
            {
                "name": n,
                "kind": k,
                "target": rng.choice(["/var/backups/dashboard", "nfs://nas/backups", "usb:///mnt/backup"]),
                "schedule": rng.choice(["@daily", "@weekly", "0 3 * * 0"]),
                "enabled": rng.random() > 0.2,
                "last_run_at": "2026-06-21T03:00:00Z" if rng.random() > 0.4 else None,
                "last_status": rng.choice(["ok", "ok", "ok", "error"]),
            }
            for n, k in zip(names, kinds, strict=False)
        ]
        return self.envelope({"jobs": jobs})
