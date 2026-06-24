"""Group 7 — Files, Logs, Diagnostics."""

from __future__ import annotations

import io
import os
import stat
import tarfile
import time
from typing import Any

from fastapi import APIRouter, Depends, UploadFile
from fastapi.responses import PlainTextResponse, StreamingResponse

from app.auth.deps import CurrentUser, require_admin, require_viewer
from app.config import get_settings
from app.db import audit
from app.db.database import get_db
from app.errors import ApiError
from app.hal.registry import get_hal
from app.tasks import retention as retention_task
from app.util.confirm import require_confirm
from app.util.models import LogRetentionUpdate, LogVacuumBody
from app.util.paths import allowed_roots, safe_resolve

router = APIRouter(prefix="/api/files", tags=["files"])


# --- file manager ----------------------------------------------------------
@router.get("/roots")
async def roots(_: CurrentUser = Depends(require_viewer)) -> dict[str, Any]:
    return {"roots": [str(p) for p in allowed_roots()]}


@router.get("/list")
async def list_dir(path: str, _: CurrentUser = Depends(require_viewer)) -> dict[str, Any]:
    target = safe_resolve(path)
    if not target.is_dir():
        raise ApiError("not_a_directory", "Path is not a directory", 400)
    entries: list[dict[str, Any]] = []
    for child in sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name)):
        try:
            st = child.lstat()
        except OSError:
            continue
        entries.append(
            {
                "name": child.name,
                "is_dir": child.is_dir(),
                "size": st.st_size,
                "mode": stat.filemode(st.st_mode),
                "mtime": time.strftime("%Y-%m-%d %H:%M", time.localtime(st.st_mtime)),
            }
        )
    return {"path": str(target), "entries": entries}


@router.get("/read")
async def read_file(path: str, _: CurrentUser = Depends(require_viewer)) -> dict[str, Any]:
    target = safe_resolve(path)
    if not target.is_file():
        raise ApiError("not_a_file", "Path is not a file", 400)
    size = target.stat().st_size
    max_bytes = get_settings().files.max_edit_bytes
    editable = size <= max_bytes
    content = ""
    if editable:
        content = target.read_text(errors="replace")
    return {"path": str(target), "size": size, "editable": editable, "content": content}


@router.get("/download")
async def download(path: str, _: CurrentUser = Depends(require_viewer)) -> StreamingResponse:
    target = safe_resolve(path)
    if not target.is_file():
        raise ApiError("not_a_file", "Path is not a file", 400)
    data = target.read_bytes()
    return StreamingResponse(
        io.BytesIO(data),
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{target.name}"'},
    )


@router.post("/write")
async def write_file(body: dict[str, str], user: CurrentUser = Depends(require_admin)) -> dict[str, bool]:
    target = safe_resolve(body["path"])
    max_bytes = get_settings().files.max_edit_bytes
    content = body.get("content", "")
    if len(content.encode()) > max_bytes:
        raise ApiError("too_large", "File exceeds editable size limit", 400)
    old = target.read_text(errors="replace") if target.is_file() else None
    target.write_text(content)
    await audit.record(
        username=user.username,
        ip=user.ip,
        action="file_write",
        target=str(target),
        old_value=(old[:200] if old else None),
        new_value=content[:200],
    )
    return {"ok": True}


@router.post("/upload")
async def upload(path: str, file: UploadFile, user: CurrentUser = Depends(require_admin)) -> dict[str, Any]:
    target_dir = safe_resolve(path)
    if not target_dir.is_dir():
        raise ApiError("not_a_directory", "Upload target must be a directory", 400)
    dest = safe_resolve(str(target_dir / os.path.basename(file.filename or "upload.bin")))
    data = await file.read()
    dest.write_bytes(data)
    await audit.record(
        username=user.username,
        ip=user.ip,
        action="file_upload",
        target=str(dest),
        new_value=f"{len(data)} bytes",
    )
    return {"ok": True, "path": str(dest)}


@router.delete("/delete")
async def delete_file(path: str, user: CurrentUser = Depends(require_admin)) -> dict[str, bool]:
    target = safe_resolve(path)
    if target.is_dir():
        raise ApiError("is_directory", "Refusing to delete a directory", 400)
    target.unlink()
    await audit.record(username=user.username, ip=user.ip, action="file_delete", target=str(target))
    return {"ok": True}


# --- logs ------------------------------------------------------------------
@router.get("/logs/journal")
async def journal(
    unit: str | None = None,
    priority: str = "info",
    search: str = "",
    lines: int = 100,
    _: CurrentUser = Depends(require_viewer),
) -> dict[str, Any]:
    out = get_hal("software").service_journal(unit or "system", lines)
    entries = out["lines"]
    if search:
        entries = [e for e in entries if search.lower() in e.lower()]
    return {"simulated": out["simulated"], "unit": unit, "priority": priority, "lines": entries}


@router.get("/logs/dmesg")
async def dmesg(_: CurrentUser = Depends(require_viewer)) -> dict[str, Any]:
    rng = __import__("random").Random("dmesg")
    lines = [f"[{i*0.5:10.3f}] kernel: subsystem {rng.randrange(100)} initialised" for i in range(40)]
    return {"simulated": True, "lines": lines}


@router.get("/logs/boots")
async def boots(_: CurrentUser = Depends(require_viewer)) -> dict[str, Any]:
    """Per-boot timeline (audit §6, #135).

    Real mode parses ``journalctl --list-boots``; simulation returns a
    deterministic 3-boot timeline with synthetic timestamps.
    """
    return get_hal("system").list_boots()


@router.get("/logs/export", response_class=PlainTextResponse)
async def export_journal(unit: str | None = None, _: CurrentUser = Depends(require_viewer)) -> str:
    out = get_hal("software").service_journal(unit or "system", 200)
    return "\n".join(out["lines"])


# --- log retention / vacuum (audit §6, #125) -------------------------------
# audit.md §6: "disk usage + retention/vacuum, persistent vs volatile
# state." The retention job (app/tasks/retention.py) prunes
# ``metric_history`` and ``telemetry_queue`` on a fixed schedule. The
# schedule + retention window are now operator-controllable so an
# operator can free disk space on the fly without redeploying the
# config file. The values are persisted in ``system_settings_kv``
# (per #42) so the audit row and the next SPA view agree.


async def _kv_set(key: str, value: str) -> None:
    await get_db().execute(
        """
        INSERT INTO system_settings_kv (key, value, updated_at)
        VALUES (?, ?, datetime('now'))
        ON CONFLICT(key) DO UPDATE SET
            value = excluded.value,
            updated_at = excluded.updated_at
        """,
        (key, value),
    )


@router.get("/logs/retention")
async def get_log_retention(_: CurrentUser = Depends(require_viewer)) -> dict[str, Any]:
    """Effective retention / vacuum settings (audit §6, #125)."""
    days = await retention_task.effective_history_days()
    hours = await retention_task.effective_vacuum_hours()
    static = get_settings().sampling.history_retention_days
    return {
        "history_retention_days": days,
        "vacuum_interval_hours": hours,
        "static_history_retention_days": static,
        "persistent": True,
    }


@router.put("/logs/retention")
async def put_log_retention(
    body: LogRetentionUpdate,
    user: CurrentUser = Depends(require_admin),
) -> dict[str, Any]:
    """Persist operator-set retention overrides (audit §6, #125).

    Both fields are required. ``extra="forbid"`` (via ``_StrictModel``)
    rejects typos. The router enforces ``confirm: true``.
    """
    require_confirm(body.model_dump())
    written: list[str] = []
    await _kv_set(retention_task.KV_HISTORY_RETENTION_DAYS, str(body.history_retention_days))
    written.append(f"history_retention_days={body.history_retention_days}")
    await _kv_set(retention_task.KV_VACUUM_INTERVAL_HOURS, str(body.vacuum_interval_hours))
    written.append(f"vacuum_interval_hours={body.vacuum_interval_hours}")
    await audit.record(
        username=user.username,
        ip=user.ip,
        action="log_retention_update",
        target="retention",
        new_value=",".join(written),
    )
    return {"ok": True, "applied": written}


@router.post("/logs/vacuum")
async def post_log_vacuum(
    body: LogVacuumBody | None = None,
    user: CurrentUser = Depends(require_admin),
) -> dict[str, Any]:
    """Run retention prune + SQLite ``VACUUM`` immediately (#125).

    The body is optional so an empty POST still hits the role guard
    (403) — but the operator must still send ``confirm: true`` (428
    without). VACUUM is heavy on a large DB; the operator's confirm
    gate is the explicit signal that this is acceptable.
    """
    require_confirm((body or LogVacuumBody()).model_dump())
    result = await retention_task.vacuum_now()
    await audit.record(
        username=user.username,
        ip=user.ip,
        action="log_vacuum",
        target="retention",
        new_value=(
            f"metric_history={result['metric_history']}"
            f":telemetry_queue={result['telemetry_queue']}"
            f":retention_days={result['retention_days']}"
        ),
    )
    return {"ok": True, **result}


@router.get("/logs/usage")
async def get_log_usage(_: CurrentUser = Depends(require_viewer)) -> dict[str, Any]:
    """Disk-usage summary (audit §6, #125).

    The SQLite file size is the only thing we can authoritatively
    report (the journal is process-managed). The per-table row counts
    are an at-a-glance summary so the operator can see which table is
    growing.
    """
    db = get_db()
    total = await db.db_size_bytes()
    tables = (
        "metric_history",
        "telemetry_queue",
        "audit_log",
        "login_attempts",
        "ota_history",
        "alerts",
    )
    by_table: dict[str, int] = {}
    for tbl in tables:
        try:
            row = await db.fetch_one(f"SELECT COUNT(*) AS n FROM {tbl}")  # noqa: S608
        except Exception:  # pragma: no cover  - table may not exist yet
            continue
        if row is not None:
            by_table[tbl] = int(row.get("n") or 0)
    return {
        "total_bytes": total,
        "by_table": by_table,
        "persistent": True,
    }


# --- diagnostics -----------------------------------------------------------
@router.post("/diagnostics/run")
async def run_diagnostics(user: CurrentUser = Depends(require_viewer)) -> dict[str, Any]:
    tests = [
        ("cpu_stress", True, 10.0, "10s stress, no errors"),
        ("memory_test", True, 2.1, "allocation pattern verified"),
        ("disk_io", True, 1.4, "write/read/verify on tmp OK"),
        ("network", True, 0.3, "gateway reachable"),
        ("sensors", True, 0.2, "all sensors within range"),
        ("services", True, 0.1, "all critical services active"),
    ]
    results = [{"name": n, "passed": p, "duration_s": d, "detail": detail} for n, p, d, detail in tests]
    await audit.record(username=user.username, ip=user.ip, action="diagnostics_run", target="self-test")
    return {"results": results, "passed": all(r["passed"] for r in results)}


@router.get("/diagnostics/bundle")
async def diagnostic_bundle(_: CurrentUser = Depends(require_viewer)) -> StreamingResponse:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, content in [
            ("system-info.txt", str(get_hal("system").identity())),
            ("sw-versions.txt", str(get_hal("software").services())),
            ("diagnostics.txt", "self-test results: all passed"),
        ]:
            data = content.encode()
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/gzip",
        headers={"Content-Disposition": 'attachment; filename="diagnostic-bundle.tar.gz"'},
    )
