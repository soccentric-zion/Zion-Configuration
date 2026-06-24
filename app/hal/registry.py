"""HAL registry: singletons + simulation status reporting."""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from app.config import get_settings
from app.hal.hardware import HardwareHal
from app.hal.interfaces import InterfacesHal
from app.hal.media import MediaHal
from app.hal.network import NetworkHal
from app.hal.platform import PlatformHal
from app.hal.security import SecurityHal
from app.hal.software import SoftwareHal
from app.hal.storage import StorageHal
from app.hal.sysadmin import SysadminHal
from app.hal.system import SystemHal
from app.hal.thermal import ThermalHal


@lru_cache(maxsize=1)
def registry() -> dict[str, Any]:
    """Instantiate all HAL modules once and return them by name."""
    force = get_settings().simulation.force
    mods: list[Any] = [
        SystemHal(force),
        NetworkHal(force),
        HardwareHal(force),
        StorageHal(force),
        ThermalHal(force),
        SoftwareHal(force),
        InterfacesHal(force),
        MediaHal(force),
        PlatformHal(force),
        SysadminHal(force),
        SecurityHal(force),
    ]
    return {m.name: m for m in mods}


def get_hal(name: str) -> Any:
    return registry()[name]


def simulated_map() -> dict[str, bool]:
    return {name: mod.simulated for name, mod in registry().items()}


def any_simulated() -> bool:
    return any(simulated_map().values())
