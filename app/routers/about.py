"""Dashboard about (Features.md §Settings About)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from app.auth.deps import CurrentUser, require_viewer
from app.config import get_active_platform
from app.hal.registry import get_hal

router = APIRouter(prefix="/api/about", tags=["about"])


@router.get("")
async def about(_: CurrentUser = Depends(require_viewer)) -> dict[str, Any]:
    """Dashboard version + build metadata + open-source attribution."""
    plat = get_active_platform()
    hal = get_hal("system").dashboard_about()
    return {
        "version": "1.0.0",
        "platform": {"name": plat.name, "codename": plat.codename, "description": plat.description},
        "build_date": hal.get("build_date"),
        "license": hal.get("license"),
        "python": hal.get("python"),
        "open_source_attributions": hal.get("open_source_attributions", []),
        "simulated": hal.get("simulated", True),
    }
