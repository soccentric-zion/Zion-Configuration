"""Top-level ``/api/config/*`` endpoints (Features.md §1).

The feature tree endpoint lives at ``/api/config/features`` per the
spec, not under ``/api/system``. Kept separate from ``app/routers/meta.py``
(mounted at ``/api/system``) so the canonical URL is preserved without
shifting the rest of the meta surface.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from app.auth.deps import CurrentUser, require_viewer
from app.config_features import get_feature_tree

router = APIRouter(prefix="/api/config", tags=["config"])


@router.get("/features")
async def config_features(_: CurrentUser = Depends(require_viewer)) -> dict[str, Any]:
    """Resolved per-sub-feature tree (Features.md §1).

    The SPA fetches this once on shell init and uses it to (a) build the
    sidebar, (b) gate the router, and (c) hide panels within a page.
    Viewer-readable because every page already trusts the SPA to render;
    the backend is the real gate (see ``app/router_modules.py``).
    """
    return get_feature_tree()
