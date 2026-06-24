"""HAL base class and deterministic simulation helpers."""

from __future__ import annotations

import math
import random
import shutil
import time
from pathlib import Path


class HalModule:
    """Common base for all hardware abstraction modules.

    Subclasses set ``name`` and implement :meth:`detect` to decide whether the
    real interface is present. ``force_sim`` (from config) overrides detection.
    The ``simulated`` flag is surfaced in every API response.
    """

    name: str = "base"

    def __init__(self, force_sim: bool = False) -> None:
        self._force_sim = force_sim
        self._real = (not force_sim) and self.detect()

    def detect(self) -> bool:
        """Return True if the real hardware interface is available."""
        return False

    @property
    def simulated(self) -> bool:
        return not self._real

    def envelope(self, data: dict[str, object]) -> dict[str, object]:
        """Tag a payload with the simulation flag."""
        return {"simulated": self.simulated, **data}


# --- simulation helpers ----------------------------------------------------


def sim_rng(*seed_parts: object) -> random.Random:
    """Deterministic RNG seeded by the given parts.

    Used so simulated topology (serials, MACs, device lists) is stable across
    calls and across processes — important for reproducible tests.
    """
    return random.Random("|".join(str(p) for p in seed_parts))


def sim_wave(period_s: float, lo: float, hi: float, phase: float = 0.0) -> float:
    """Smooth time-varying value in [lo, hi] for animated live readings."""
    t = time.time()
    frac = (math.sin(2 * math.pi * (t / period_s) + phase) + 1) / 2
    return lo + (hi - lo) * frac


def path_exists(p: str) -> bool:
    return Path(p).exists()


def has_command(cmd: str) -> bool:
    return shutil.which(cmd) is not None
