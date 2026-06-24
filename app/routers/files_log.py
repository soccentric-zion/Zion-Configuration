"""Application log file manager (Features.md §Logs Application / Service / Custom).

This router is for *per-app* log files (config-declared paths) and
*service* per-unit logs. The path-traversal guard is reused from
``logs_journal._safe_resolve``.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import Field

from app.auth.deps import CurrentUser, require_admin, require_viewer
from app.config import get_settings
from app.db import audit
from app.errors import ApiError
from app.routers.logs_journal import _safe_resolve
from app.util.confirm import require_confirm
from app.util.models import _StrictModel

router = APIRouter(prefix="/api/files-log", tags=["files-log"])


def _app_log_paths() -> list[str]:
    """Per-app log directories (path-traversal guarded at use-site)."""
    cfg = get_settings()
    paths: list[str] = []
    for p in cfg.files.allowed_paths:
        if Path(p).is_dir():
            paths.append(p)
    return paths


@router.get("/apps")
async def list_apps(_: CurrentUser = Depends(require_viewer)) -> dict[str, Any]:
    """List log directories the dashboard knows about."""
    out: list[dict[str, Any]] = []
    for p in _app_log_paths():
        try:
            safe = _safe_resolve(p)
        except ApiError:
            continue
        try:
            files = [f.name for f in safe.iterdir() if f.is_file() and not f.name.startswith(".")][:50]
        except OSError:
            files = []
        out.append({"path": str(safe), "files": files, "count": len(files)})
    return {"apps": out}


class _AppLogTail(_StrictModel):
    path: str = Field(min_length=1, max_length=512)
    lines: int = Field(default=200, ge=1, le=5000)


@router.post("/tail")
async def tail(body: _AppLogTail, _: CurrentUser = Depends(require_viewer)) -> dict[str, Any]:
    """Tail an application log file (path-traversal guarded)."""
    safe = _safe_resolve(body.path)
    if not safe.is_file():
        return {"path": str(safe), "lines": [], "exists": False}
    try:
        text = safe.read_text(errors="replace")
        return {"path": str(safe), "lines": text.splitlines()[-body.lines :], "exists": True}
    except OSError as exc:
        raise ApiError("read_failed", f"could not read {safe}: {exc}", 500) from exc


@router.get("/service/{unit}")
async def service_log(
    unit: str,
    lines: int = 200,
    _: CurrentUser = Depends(require_viewer),
) -> dict[str, Any]:
    """Service unit log (Features.md §Logs Service Logs)."""
    lines = max(1, min(lines, 2000))
    safe_unit = "".join(c for c in unit if c.isalnum() or c in "@._-")
    if not safe_unit or safe_unit != unit:
        raise ApiError("invalid_unit", f"unit name {unit!r} not allowed", 422)
    try:
        proc = await asyncio.create_subprocess_exec(
            "journalctl",
            "-u",
            safe_unit,
            "-n",
            str(lines),
            "--no-pager",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=2.0)  # type: ignore[arg-type]
        return {
            "unit": safe_unit,
            "lines": out.decode("utf-8", "replace").splitlines(),
            "source": "journalctl",
        }
    except (TimeoutError, FileNotFoundError, OSError):
        return {
            "unit": safe_unit,
            "lines": [f"-- No entries (simulated) for {safe_unit}"],
            "source": "simulated",
        }


@router.get("/rotate-status")
async def rotation_status(_: CurrentUser = Depends(require_viewer)) -> dict[str, Any]:
    """Log rotation status (Features.md §Logs Log Management)."""
    for candidate in ("/var/lib/logrotate/status", "/var/lib/logrotate/logrotate.status"):
        try:
            safe = _safe_resolve(candidate)
        except ApiError:
            continue
        if safe.is_file():
            try:
                rows = safe.read_text(errors="replace").splitlines()[:50]
                return {"path": str(safe), "lines": rows, "source": "logrotate"}
            except OSError:
                continue
    return {
        "path": "simulated",
        "lines": [
            '"/var/log/syslog" 2026-06-15-03:30:1',
            '"/var/log/auth.log" 2026-06-15-03:30:1',
        ],
        "source": "simulated",
    }


class _ClearJournal(_StrictModel):
    confirm: bool
    retention_days: int = Field(default=7, ge=1, le=365)


@router.post("/clear")
async def clear_journal(
    body: _ClearJournal,
    user: CurrentUser = Depends(require_admin),
) -> dict[str, Any]:
    """Vacuum the journal (Features.md §Logs Log Management)."""
    require_confirm(body.model_dump())
    try:
        proc = await asyncio.create_subprocess_exec(
            "journalctl",
            "--vacuum-time",
            f"{body.retention_days}d",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=5.0)
        return {
            "ok": True,
            "output": out.decode("utf-8", "replace"),
            "retention_days": body.retention_days,
        }
    except (TimeoutError, FileNotFoundError, OSError) as exc:
        await audit.record(
            username=user.username,
            ip=user.ip,
            action="journal_vacuum",
            target="journal",
            new_value=f"simulated:{body.retention_days}d:{exc}",
        )
        return {
            "ok": True,
            "simulated": True,
            "retention_days": body.retention_days,
        }
    finally:
        await audit.record(
            username=user.username,
            ip=user.ip,
            action="journal_vacuum",
            target="journal",
            new_value=f"{body.retention_days}d",
        )
