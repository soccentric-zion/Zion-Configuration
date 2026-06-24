"""Journal / dmesg / syslog viewer (Features.md §Logs).

Hard requirement: all file access is path-traversal protected
(canonicalize + allowlist prefix check). The viewer is gated viewer
because logs can expose sensitive data — and the file allowlist is
configured in ``config.yaml`` under ``files.allowed_paths``.
"""

from __future__ import annotations

import asyncio
import shlex
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import Field

from app.auth.deps import CurrentUser, require_viewer
from app.config import get_settings
from app.errors import ApiError
from app.util.models import _StrictModel

router = APIRouter(prefix="/api/logs", tags=["logs"])


def _safe_resolve(path: str) -> Path:
    """Canonicalize and ensure the path is under an allowed root.

    Returns the resolved Path if it's within an allowed root;
    raises ApiError otherwise. This is the path-traversal guard
    called out in Features.md §Logs and audit Guardrail 3.
    """
    try:
        resolved = Path(path).resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        raise ApiError("invalid_path", f"could not resolve {path!r}: {exc}", 422) from exc
    allowed = get_settings().files.allowed_paths
    for root in allowed:
        try:
            r = Path(root).resolve(strict=False)
        except (OSError, RuntimeError):
            continue
        if resolved == r or r in resolved.parents:
            return resolved
    raise ApiError(
        "path_not_allowed",
        f"{path!r} is not under any allowed root",
        403,
        detail={"allowed": list(allowed)},
    )


@router.get("/journal")
async def journal(
    unit: str = "",
    priority: str = "",
    since: str = "",
    limit: int = 200,
    _: CurrentUser = Depends(require_viewer),
) -> dict[str, Any]:
    """journalctl tail (Features.md §Logs Journal).

    Real: shells out to ``journalctl --no-pager``. The output is
    short-lived and bounded by ``limit`` (max 2000 to keep response
    size sane). Simulated: returns a deterministic journal snippet.
    """
    limit = max(1, min(limit, 2000))
    args = ["journalctl", "--no-pager", "-n", str(limit), "-o", "short"]
    if unit:
        args.extend(["-u", unit])
    if priority:
        args.extend(["-p", priority])
    if since:
        args.extend(["--since", since])
    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, _ = await asyncio.wait_for(
            proc.communicate(),  # type: ignore[arg-type]
            timeout=2.0,
        )
        return {
            "lines": out.decode("utf-8", "replace").splitlines()[-limit:],
            "source": "journalctl",
        }
    except (TimeoutError, FileNotFoundError, OSError):
        # journalctl not present (simulation); return deterministic content.
        return {
            "lines": _simulated_journal(unit, priority, limit),
            "source": "simulated",
        }


def _simulated_journal(unit: str, priority: str, limit: int) -> list[str]:
    """Build a stable journal snippet for dev / CI."""
    rng_base = f"journal:{unit}:{priority}" if unit or priority else "journal:all"
    seeds = [rng_base, *([unit] if unit else []), *([priority] if priority else [])]
    seed = "|".join(seeds)
    import hashlib

    h = hashlib.sha1(seed.encode()).digest()
    out: list[str] = []
    for i in range(min(limit, 100)):
        ts = f"Jun 21 10:{i % 60:02d}:00"
        host = "dashboard"
        prio = priority or "info"
        svc = unit or "systemd"
        msg = f"line {i}: simulated journal entry for {svc}"
        out.append(f"{ts} {host} {svc}[{1000 + (h[i % 10] % 100)}]: [{prio}] {msg}")
    return out


@router.get("/dmesg")
async def dmesg(
    limit: int = 200,
    priority: str = "",
    _: CurrentUser = Depends(require_viewer),
) -> dict[str, Any]:
    """Kernel ring buffer (Features.md §Logs dmesg)."""
    limit = max(1, min(limit, 2000))
    try:
        proc = await asyncio.create_subprocess_exec(
            "dmesg",
            "--ctime",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=2.0)  # type: ignore[arg-type]
        lines = out.decode("utf-8", "replace").splitlines()[-limit:]
        if priority:
            lines = [line for line in lines if f"<{priority}>" in line]
        return {"lines": lines, "source": "dmesg"}
    except (TimeoutError, FileNotFoundError, OSError):
        return {
            "lines": [
                "[    0.000000] Linux version 6.6.0-dashboard (simulated)",
                "[    0.001000] Command line: BOOT_IMAGE=/boot/vmlinuz root=LABEL=dashboard",
                "[    1.234567] systemd[1]: Starting Journal Service...",
            ],
            "source": "simulated",
        }


@router.get("/syslog")
async def syslog(
    lines: int = 200,
    _: CurrentUser = Depends(require_viewer),
) -> dict[str, Any]:
    """Tail /var/log/syslog or /var/log/messages (path-traversal guarded)."""
    lines = max(1, min(lines, 2000))
    for candidate in ("/var/log/syslog", "/var/log/messages"):
        try:
            safe = _safe_resolve(candidate)
        except ApiError:
            continue
        if safe.is_file():
            try:
                text = safe.read_text(errors="replace")
                tail = text.splitlines()[-lines:]
                return {"lines": tail, "path": str(safe)}
            except OSError as exc:
                raise ApiError("read_failed", f"could not read {safe}: {exc}", 500) from exc
    return {
        "lines": [
            "Jun 21 10:00:00 dashboard systemd[1]: Started Dashboard (simulated).",
            "Jun 21 10:00:01 dashboard dashboard[1234]: listening on 0.0.0.0:8000",
        ],
        "path": "simulated",
    }


class _TailRequest(_StrictModel):
    path: str = Field(min_length=1, max_length=512)
    lines: int = Field(default=200, ge=1, le=5000)
    follow: bool = False


@router.post("/tail")
async def tail(body: _TailRequest, _: CurrentUser = Depends(require_viewer)) -> dict[str, Any]:
    """Tail a custom log file (path-traversal guarded)."""
    safe = _safe_resolve(body.path)
    if not safe.is_file():
        # Path passes the allowlist check but the file isn't there yet —
        # treat as empty log rather than 404 (operators create files via
        # the Files page; the log viewer should be tolerant).
        return {"path": str(safe), "lines": [], "exists": False}
    try:
        text = safe.read_text(errors="replace")
        return {"path": str(safe), "lines": text.splitlines()[-body.lines :], "exists": True}
    except OSError as exc:
        raise ApiError("read_failed", f"could not read {safe}: {exc}", 500) from exc


@router.get("/units")
async def list_units(_: CurrentUser = Depends(require_viewer)) -> dict[str, Any]:
    """List systemd units available for the journal unit filter."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "systemctl",
            "list-units",
            "--type=service",
            "--no-pager",
            "--no-legend",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=2.0)  # type: ignore[arg-type]
        rows = []
        for line in out.decode("utf-8", "replace").splitlines():
            parts = line.split()
            if parts:
                rows.append({"unit": parts[0], "state": parts[2] if len(parts) > 2 else "unknown"})
        return {"units": rows}
    except (TimeoutError, FileNotFoundError, OSError):
        return {
            "units": [
                {"unit": "dashboard.service", "state": "running"},
                {"unit": "sshd.service", "state": "running"},
                {"unit": "swupdate.service", "state": "stopped"},
            ]
        }


@router.get("/export")
async def export_logs(
    since: str = "",
    until: str = "",
    unit: str = "",
    _: CurrentUser = Depends(require_viewer),
) -> dict[str, Any]:
    """Build a log export envelope (Features.md §Logs Export).

    Real: streams a gzipped journal export to /tmp. Simulated: returns
    a synthetic envelope so the SPA can show the export path.
    """
    ts = time.strftime("%Y%m%d-%H%M%S")
    args = ["journalctl", "--no-pager", "-o", "export"]
    if since:
        args.extend(["--since", since])
    if until:
        args.extend(["--until", until])
    if unit:
        args.extend(["-u", unit])
    out_path = Path("/tmp") / f"dashboard-journal-{ts}.export.gz"
    if not get_settings().simulation.force and Path("/usr/bin/journalctl").exists():
        try:
            cmd = " ".join(shlex.quote(a) for a in args) + f" | gzip > {out_path}"
            await asyncio.create_subprocess_shell(cmd)
            return {"path": str(out_path), "applied": True, "simulated": False}
        except OSError:
            pass
    return {
        "path": str(out_path),
        "applied": False,
        "simulated": True,
        "command": " ".join(shlex.quote(a) for a in args),
    }
