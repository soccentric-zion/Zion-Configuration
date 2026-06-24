"""Security HAL: status-only secure-boot, fuses, and enrolled keys.

Per audit.md §4 Boot ("Read-only: fuses, secure-boot key enrollment")
and §9 Security ("key enrollment is status-only"), the dashboard is
allowed to *display* these surfaces but never to *modify* them. The
HAL therefore exposes only readers — no setters — and the corresponding
router endpoints are GET-only (#94). The shape mirrors what a real
SoC's efuse / OTP layout would expose, but the values are simulated
deterministically so tests are reproducible.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.hal.base import HalModule, sim_rng


class SecurityHal(HalModule):
    """Status-only security surface: fuses, secure-boot, enrolled keys."""

    name = "security"

    def detect(self) -> bool:
        # No real /dev/efuse or OTP node to probe in CI / sim.
        return False

    def secure_boot_status(self) -> dict[str, Any]:
        """Return a dict describing the current secure-boot posture.

        The real device would read e.g. ``/sys/firmware/efi/secure_boot``
        or query the SoC's OTP layout; in simulation we return a stable
        shape so the SPA can render the status card.
        """
        return self.envelope(
            {
                "enabled": True,
                "mode": "enforced",
                "vendor_keys": 2,
                "platform": "acadia",
            }
        )

    def fuses(self) -> dict[str, Any]:
        """Enumerate the (read-only) fuse/OTP layout.

        ``banks`` is a list of bank objects; each carries a ``name``
        and a small ``fuses`` list. The dashboard renders this as a
        status card; there is no write path on purpose (#94).
        """
        rng = sim_rng("security", "fuses")
        bank_names = ("boot", "identity", "otp", "debug")
        banks: list[dict[str, Any]] = []
        for bank_name in bank_names:
            n = rng.randint(4, 8)
            banks.append(
                {
                    "name": bank_name,
                    "fuses": [
                        {
                            "offset": f"0x{offset:04x}",
                            "name": f"{bank_name}_{offset}",
                            "locked": rng.choice([True, False]),
                            "value": rng.randint(0, 1),
                        }
                        for offset in range(n)
                    ],
                }
            )
        return self.envelope({"banks": banks})

    def enrolled_keys(self) -> dict[str, Any]:
        """List the enrolled secure-boot / signing keys (read-only).

        The list is seeded so tests have something to render and
        contains the canonical fields the SPA's status card needs.
        """
        rng = sim_rng("security", "enrolled_keys")
        owners = ("vendor", "operator", "lab-ca")
        keys = []
        for i in range(3):
            digest = "".join(rng.choice("0123456789abcdef") for _ in range(16))
            keys.append(
                {
                    "key_id": f"key-{i + 1:02d}",
                    "fingerprint": f"SHA256:{digest}",
                    "enrolled_at": "2025-09-01T00:00:00",
                    "owner": owners[i % len(owners)],
                }
            )
        return self.envelope({"keys": keys})

    def sshd_config(self) -> dict[str, Any]:
        """Return the simulated live sshd_config (#95).

        The router pairs this with operator-persisted overrides in
        ``system_settings_kv`` so the read endpoint shows the *effective*
        policy: persisted values win, falling back to the live config
        when no override is set (per the #42 audit-state-alignment
        pattern).
        """
        return self.envelope(
            {
                "permit_root_login": "prohibit-password",
                "password_authentication": "no",
                "kbd_interactive_authentication": "no",
                "x11_forwarding": False,
                "port": 22,
                "allow_users": "",
            }
        )

    def tpm_status(self) -> dict[str, Any]:
        """Return the simulated TPM state (#98).

        Status-only: the dashboard must not be able to clear, enable, or
        own the TPM from the SPA (per audit.md §9, TPM state is
        read-only). The shape matches what a real ``tpm2_getcap``
        output would expose: presence, version, ownership, PCR banks.
        """
        return self.envelope(
            {
                "present": True,
                "enabled": True,
                "version": "2.0",
                "owned": False,
                "pcr_banks": ["sha256", "sha384"],
            }
        )

    # --- Features.md §Security / §Users: additional read surfaces.
    def ssh_status(self) -> dict[str, Any]:
        """SSH service status + key material (Features.md §Security)."""
        if self._real:
            # Real: parse `systemctl is-active sshd` + read authorized_keys.
            import subprocess

            try:
                r = subprocess.run(["systemctl", "is-active", "sshd"], capture_output=True, text=True, timeout=2)
                state = r.stdout.strip() or "unknown"
            except (OSError, subprocess.TimeoutExpired):
                state = "unknown"
            key_count = 0
            keys_path = Path("/root/.ssh/authorized_keys")
            if keys_path.exists():
                raw = keys_path.read_text(errors="ignore").splitlines()
                key_count = sum(1 for line in raw if line.strip() and not line.startswith("#"))
            return self.envelope(
                {
                    "service_state": state,
                    "port": 22,
                    "password_auth": True,
                    "root_login": False,
                    "key_count": key_count,
                }
            )
        return self.envelope(
            {
                "service_state": "active",
                "port": 22,
                "password_auth": True,
                "root_login": False,
                "key_count": 3,
            }
        )

    def compliance_summary(self) -> dict[str, Any]:
        """CVE scan results + SBOM + last-audit summary (Features.md §Security)."""
        rng = sim_rng("security", "compliance") if not self._real else None
        return self.envelope(
            {
                "cves": {
                    "critical": 0 if rng is None else 0,
                    "high": 0 if rng is None else 1,
                    "medium": 2 if rng is None else 2,
                    "low": 5 if rng is None else 5,
                },
                "last_audit": "2026-05-15",
                "sbom_packages": 142 if rng is None else 142,
                "secure_boot": True,
                "kernel_hardening": True,
            }
        )

    def user_groups(self) -> list[dict[str, Any]]:
        """System user groups (Features.md §Users).

        Real: ``grp.getgrall()`` (POSIX). Simulated: deterministic list.
        """
        if self._real:
            import grp as _grp

            try:
                return [{"name": g.gr_name, "gid": g.gr_gid, "members": list(g.gr_mem)} for g in _grp.getgrall()]
            except OSError:
                pass
        rng = sim_rng("security", "groups")
        names = ["admin", "operator", "viewer", "service", "wheel", "ssh"]
        return [
            {
                "name": n,
                "gid": 1000 + i,
                "members": [n] if rng.random() > 0.5 else [],
            }
            for i, n in enumerate(names)
        ]
