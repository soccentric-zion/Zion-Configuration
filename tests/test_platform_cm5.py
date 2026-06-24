"""Regression test for #127 — CM5 runtime detection (req.md §5).

req.md §5 CM5-Specific I/O + audit report §5 verdict: "Runtime
detection so CM5-only and module-dependent features surface only
when present." The platform HAL at ``app/hal/platform.py`` reads
``/proc/device-tree/model`` and decides whether each CM5-only
feature (PCIe, eMMC, full GPIO banks, RTC, PMIC) is present.
This test verifies the gating logic is correct for both CM5
(detected) and non-CM5 (not detected) hardware.

The audit gates the dashboard UI on ``features.pcie``/``features.emmc``/
``features.gpio_banks``/``features.pmic`` — if the HAL reports
``True`` on non-CM5 hardware the dashboard renders panels that
fail at runtime; if it reports ``False`` on CM5 hardware the
dashboard hides panels that should be there.
"""

from __future__ import annotations

import pytest

from app.hal.platform import PlatformHal


@pytest.fixture
def cm5_hal() -> PlatformHal:
    """A real CM5 detected via the device-tree (simulated env)."""
    return PlatformHal(force_sim=True)


def test_features_present_for_simulated_cm5(cm5_hal: PlatformHal) -> None:
    """In the simulated dev machine (the test conftest pins force_sim),
    the platform HAL falls back to a CM5 simulation so the panels
    are demoable (#127). All CM5-only features must report True."""
    features = cm5_hal.features()
    assert features["pcie"] is True
    assert features["emmc"] is True
    assert features["gpio_banks"] is True
    assert features["pmic"] is True


def test_features_absent_for_non_cm5(monkeypatch: pytest.MonkeyPatch) -> None:
    """A non-CM5 device-tree model must disable CM5-only features.

    The platform HAL's ``_is_cm5`` falls back to ``self.simulated``
    on a non-CM5 device-tree (so the dashboard is still demoable
    on a dev machine). To verify the *detection* path we have to
    set ``_real=True`` so the simulated branch is also False, then
    patch ``_model()`` to return a non-CM5 string. The ``detect()``
    branch is then the only one that can return True — and it
    correctly returns False for a non-CM5 model.
    """
    import app.hal.platform as plat_mod

    monkeypatch.setattr(plat_mod, "_model", lambda: "Raspberry Pi 4 Model B Rev 1.4")
    hal = PlatformHal(force_sim=True)
    # Force the "real hardware" path so the simulated fallback in
    # ``_is_cm5`` is not consulted — that's the path the audit gates
    # the dashboard UI on.
    hal._real = True
    assert hal.simulated is False
    features = hal.features()
    assert features["pcie"] is False, f"pcie should be False on Pi4, got {features}"
    assert features["emmc"] is False
    assert features["gpio_banks"] is False
    assert features["pmic"] is False


def test_module_info_marks_non_cm5(monkeypatch: pytest.MonkeyPatch) -> None:
    """``module_info()`` must report ``is_cm5=False`` for non-CM5
    hardware; the SPA hides the CM5 panels on this branch."""
    import app.hal.platform as plat_mod

    monkeypatch.setattr(plat_mod, "_model", lambda: "Raspberry Pi 4 Model B Rev 1.4")
    hal = PlatformHal(force_sim=True)
    hal._real = True
    info = hal.module_info()
    assert info["is_cm5"] is False
    assert info["simulated"] is False
    # On a non-CM5 device the carrier is unknown, not "CM5 IO Board".
    assert info["carrier"] == "unknown"


def test_module_info_marks_cm5(monkeypatch: pytest.MonkeyPatch) -> None:
    """``module_info()`` must report ``is_cm5=True`` when the
    device-tree model is a CM5."""
    import app.hal.platform as plat_mod

    monkeypatch.setattr(plat_mod, "_model", lambda: "Raspberry Pi Compute Module 5 Rev 1.0")
    hal = PlatformHal(force_sim=True)
    hal._real = True
    info = hal.module_info()
    assert info["is_cm5"] is True
    assert info["carrier"] == "CM5 IO Board"
    assert info["variant"]["emmc_present"] is True
    assert info["variant"]["emmc_size_gb"] == 32


def test_pcie_devices_empty_on_non_cm5(monkeypatch: pytest.MonkeyPatch) -> None:
    """A non-CM5 device must report ``pcie.present=False`` and an
    empty device list — otherwise the dashboard would render a
    panel that cannot read any actual hardware."""
    import app.hal.platform as plat_mod

    monkeypatch.setattr(plat_mod, "_model", lambda: "Raspberry Pi 4 Model B Rev 1.4")
    hal = PlatformHal(force_sim=True)
    hal._real = True
    pcie = hal.pcie()
    assert pcie["present"] is False
    assert pcie["lanes"] == 0
    assert pcie["devices"] == []


def test_emmc_disabled_on_non_cm5(monkeypatch: pytest.MonkeyPatch) -> None:
    """A non-CM5 device must report ``emmc.present=False`` and
    ``enabled=False``."""
    import app.hal.platform as plat_mod

    monkeypatch.setattr(plat_mod, "_model", lambda: "Raspberry Pi 4 Model B Rev 1.4")
    hal = PlatformHal(force_sim=True)
    hal._real = True
    emmc = hal.emmc()
    assert emmc["present"] is False
    assert emmc["enabled"] is False
    assert emmc["device"] is None
    assert emmc["size_gb"] == 0
