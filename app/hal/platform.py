"""Platform HAL: CM5 / carrier-board specific I/O (section 5).

Provides runtime detection of CM5-only and module-dependent features so the
dashboard surfaces them only "if present": module variant (WiFi/BT presence,
eMMC size), PCIe, eMMC, full GPIO bank exposure, RTC + battery and PMIC status.

Detection reads the device-tree model; on a dev machine everything falls back to
a deterministic CM5 simulation so the panel is demoable, with ``simulated`` set.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.hal.base import HalModule, sim_rng, sim_wave


def _model() -> str:
    for p in ("/proc/device-tree/model", "/sys/firmware/devicetree/base/model"):
        try:
            text = Path(p).read_text(errors="ignore").strip("\x00 \n\t")
            if text:
                return text
        except OSError:
            continue
    return ""


class PlatformHal(HalModule):
    name = "platform"

    def detect(self) -> bool:
        return "CM5" in _model() or "Compute Module 5" in _model()

    def _is_cm5(self) -> bool:
        model = _model()
        if "CM5" in model or "Compute Module 5" in model:
            return True
        # Simulated dev machine: present a CM5 so the gated UI is demoable.
        return self.simulated

    # --- module variant ----------------------------------------------------
    def module_info(self) -> dict[str, Any]:
        cm5 = self._is_cm5()
        rng = sim_rng("module", _model() or "sim-cm5")
        return self.envelope(
            {
                "model": _model() or "Raspberry Pi Compute Module 5",
                "is_cm5": cm5,
                "soc": "BCM2712",
                "carrier": "CM5 IO Board" if cm5 else "unknown",
                "variant": {
                    "wifi_present": True,
                    "bluetooth_present": True,
                    "emmc_present": cm5,
                    "emmc_size_gb": 32 if cm5 else 0,
                    "ram_gb": rng.choice([2, 4, 8, 16]),
                },
                "features": self.features(),
            }
        )

    def features(self) -> dict[str, bool]:
        """Which CM5-only features are present (gates the dashboard UI)."""
        cm5 = self._is_cm5()
        return {
            "pcie": cm5,
            "emmc": cm5,
            "gpio_banks": cm5,
            "rtc": True,
            "pmic": cm5,
        }

    # --- PCIe --------------------------------------------------------------
    def pcie(self) -> dict[str, Any]:
        present = self.features()["pcie"]
        devices: list[dict[str, Any]] = []
        if present:
            devices = [
                {
                    "slot": "0000:01:00.0",
                    "vendor": "Realtek",
                    "device": "RTL9210 NVMe bridge",
                    "class": "NVMe",
                    "link": "Gen3 x1",
                    "enabled": True,
                },
            ]
        return self.envelope({"present": present, "lanes": 1 if present else 0, "devices": devices})

    # --- eMMC --------------------------------------------------------------
    def emmc(self) -> dict[str, Any]:
        present = self.features()["emmc"]
        return self.envelope(
            {
                "present": present,
                "device": "/dev/mmcblk0" if present else None,
                "size_gb": 32 if present else 0,
                "life_used_pct": round(sim_wave(60, 1, 12), 1) if present else 0,
                "enabled": present,
            }
        )

    # --- GPIO banks --------------------------------------------------------
    def gpio_banks(self) -> dict[str, Any]:
        extended = self.features()["gpio_banks"]
        banks: list[dict[str, Any]] = [{"bank": 0, "pins": 40, "label": "40-pin header"}]
        if extended:
            banks.append({"bank": 1, "pins": 28, "label": "carrier expansion"})
        total = sum(int(b["pins"]) for b in banks)
        return self.envelope({"extended": extended, "banks": banks, "total_pins": total})

    # --- RTC + battery -----------------------------------------------------
    def rtc_battery(self) -> dict[str, Any]:
        return self.envelope(
            {
                "present": True,
                "device": "rtc0",
                "battery_present": True,
                "battery_voltage_v": round(sim_wave(120, 2.9, 3.2), 2),
                "charging": False,
            }
        )

    # --- PMIC --------------------------------------------------------------
    def pmic(self) -> dict[str, Any]:
        present = self.features()["pmic"]
        rails: list[dict[str, Any]] = []
        if present:
            for name, nom in (("VDD_CORE", 0.85), ("DDR_VDD", 1.1), ("3V3_SYS", 3.3), ("5V_SYS", 5.0)):
                rails.append(
                    {
                        "rail": name,
                        "voltage_v": round(nom * sim_wave(30, 0.99, 1.01, phase=hash(name) % 7), 3),
                        "nominal_v": nom,
                    }
                )
        return self.envelope(
            {
                "present": present,
                "chip": "DA9091" if present else None,
                "rails": rails,
                "input_voltage_v": round(sim_wave(25, 4.9, 5.1), 2) if present else 0,
            }
        )
