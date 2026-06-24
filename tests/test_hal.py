"""HAL simulation determinism and registry behaviour."""

from __future__ import annotations

from app.hal.base import HalModule, sim_rng, sim_wave
from app.hal.registry import any_simulated, get_hal, registry, simulated_map


def test_sim_rng_is_deterministic() -> None:
    a = sim_rng("network", "eth0").random()
    b = sim_rng("network", "eth0").random()
    assert a == b
    assert sim_rng("network", "eth1").random() != a


def test_sim_wave_within_bounds() -> None:
    for _ in range(50):
        v = sim_wave(10.0, 20.0, 80.0)
        assert 20.0 <= v <= 80.0


def test_envelope_tags_simulation() -> None:
    mod = HalModule(force_sim=True)
    env = mod.envelope({"foo": 1})
    assert env["simulated"] is True
    assert env["foo"] == 1


def test_force_sim_overrides_detection() -> None:
    class Always(HalModule):
        name = "always"

        def detect(self) -> bool:
            return True

    assert Always(force_sim=True).simulated is True
    assert Always(force_sim=False).simulated is False


def test_registry_is_singleton_and_forced_simulated() -> None:
    # The test config forces simulation, so every module reports simulated.
    assert registry() is registry()
    assert all(simulated_map().values())
    assert any_simulated() is True
    assert get_hal("system").simulated is True


def test_throttling_reports_unknown_when_cpu_freq_zero(
    monkeypatch: object,
) -> None:
    """psutil.cpu_freq() can return (current=0, max=0) on idle systems or
    when the cpufreq driver is absent. The naive `cur < mx * 0.6` check
    would call 0 < 0 True and report a false-positive throttling alarm.
    Fix #26: report None / unknown instead of false-positive throttled=True.
    """
    from dataclasses import dataclass

    import psutil

    @dataclass
    class _Zero:
        current: float = 0.0
        max: float = 0.0

    monkeypatch.setattr(psutil, "cpu_freq", lambda: _Zero())  # type: ignore[attr-defined]
    thermal = get_hal("thermal")
    payload = thermal.throttling()
    assert payload["throttled"] is None
    assert payload["cpu_freq_mhz"] == 0
    assert payload["cpu_freq_max_mhz"] == 0


def test_throttling_true_when_current_below_60pct_of_max(
    monkeypatch: object,
) -> None:
    """Sanity: a real low current reading must still report throttled=True."""
    from dataclasses import dataclass

    import psutil

    @dataclass
    class _Low:
        current: float = 600.0
        max: float = 1800.0

    monkeypatch.setattr(psutil, "cpu_freq", lambda: _Low())  # type: ignore[attr-defined]
    thermal = get_hal("thermal")
    payload = thermal.throttling()
    assert payload["throttled"] is True
