"""Hardware Abstraction Layer.

Every HAL module follows the same contract (:class:`~app.hal.base.HalModule`):
it detects whether the real interface is present and, if not, transparently
falls back to a deterministic *simulation* implementation. The dashboard is
therefore fully functional on a dev laptop with no hardware.

The module registry (:func:`registry`) lets the API report which subsystems are
simulated so the UI can show the simulation badge.
"""

from app.hal.base import HalModule, sim_rng
from app.hal.registry import any_simulated, registry, simulated_map

__all__ = ["HalModule", "sim_rng", "registry", "any_simulated", "simulated_map"]
