"""FastAPI application: routers, websockets, static SPA, lifecycle."""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator
from pathlib import Path

import uvicorn
from fastapi import Depends, FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.auth.deps import CurrentUser, get_current_user
from app.auth.service import seed_default_admin
from app.config import get_settings
from app.db.database import Database, init_db
from app.errors import (
    ApiError,
    api_error_handler,
    http_exception_handler,
    validation_exception_handler,
)
from app.router_modules import resolve_routers
from app.tasks.alerts import engine
from app.tasks.apply_state import apply_watchdog
from app.tasks.retention import retention
from app.tasks.sampler import sampler
from app.ws.routes import router as ws_router

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


@contextlib.asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    db: Database = await init_db()
    await seed_default_admin()
    sampler.start()
    engine.start()
    retention.start()
    apply_watchdog.start()
    try:
        yield
    finally:
        # Drain any in-flight OTA install tasks so a shutdown does not
        # abandon a flash mid-write (fix #32).
        from app.tasks import ota

        await ota.drain_inflight()
        await sampler.stop()
        await engine.stop()
        await retention.stop()
        await apply_watchdog.stop()
        await db.close()


def create_app() -> FastAPI:
    app = FastAPI(
        title="Embedded Linux Device Dashboard",
        version="1.0.0",
        docs_url=None,
        redoc_url=None,
        openapi_url="/api/openapi.json",
        lifespan=lifespan,
    )

    app.add_exception_handler(ApiError, api_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, validation_exception_handler)  # type: ignore[arg-type]

    # --- dynamic router registration (Features.md §1) ---
    # Only enabled feature groups/sub-features get their routers mounted.
    # Disabled features return 404 — the SPA sees the 404 and either
    # falls back to a generic page or omits the nav entry entirely.
    for _info, router in resolve_routers():
        app.include_router(router)
    app.include_router(ws_router)

    # Protected OpenAPI docs.
    @app.get("/api/docs", include_in_schema=False)
    async def docs(_: CurrentUser = Depends(get_current_user)) -> FileResponse:
        return FileResponse(STATIC_DIR / "docs.html")

    # Static SPA.
    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

        @app.get("/", include_in_schema=False)
        async def index() -> FileResponse:
            return FileResponse(STATIC_DIR / "index.html")

        @app.get("/{full_path:path}", include_in_schema=False)
        async def spa_fallback(full_path: str) -> FileResponse:
            # Anything not matched by /api or /static falls back to the SPA so
            # client-side routes (deep links) work on refresh. The fallback must
            # never escape STATIC_DIR — Path("/foo") / "//etc/passwd" is an
            # absolute path, and Path("/foo") / "../x" walks up. Resolve and
            # require the result to be STATIC_DIR or one of its descendants.
            try:
                candidate = (STATIC_DIR / full_path.lstrip("/")).resolve(strict=False)
            except (OSError, RuntimeError):
                return FileResponse(STATIC_DIR / "index.html")
            if STATIC_DIR not in candidate.parents and candidate != STATIC_DIR:
                return FileResponse(STATIC_DIR / "index.html")
            if candidate.is_file():
                return FileResponse(candidate)
            return FileResponse(STATIC_DIR / "index.html")

    return app


app = create_app()


def main() -> None:
    cfg = get_settings().server
    uvicorn.run("app.main:app", host=cfg.host, port=cfg.port, log_level="info")


if __name__ == "__main__":
    main()
