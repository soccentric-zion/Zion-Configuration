"""Access Policy (Features.md §Users access policy)."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import Field

from app.auth.deps import CurrentUser, require_admin
from app.db import audit
from app.db.database import get_db
from app.errors import ApiError
from app.util.models import _StrictModel

router = APIRouter(prefix="/api/access", tags=["access"])


class _AccessPolicyUpsert(_StrictModel):
    username: str = Field(min_length=1, max_length=64)
    ip_allowlist: list[str] = Field(default_factory=list)
    # Time-based windows: list of {days:[mon..sun], start:"HH:MM", end:"HH:MM"}.
    windows: list[dict[str, Any]] = Field(default_factory=list)


def _validate_ip(ip: str) -> bool:
    """Light CIDR / single-IP validation (no real network stack call)."""
    import ipaddress

    try:
        ipaddress.ip_network(ip, strict=False)
        return True
    except ValueError:
        return False


@router.put("/policy")
async def upsert_policy(body: _AccessPolicyUpsert, user: CurrentUser = Depends(require_admin)) -> dict[str, Any]:
    """Upsert per-user access policy (privileged write, audited)."""
    bad = [ip for ip in body.ip_allowlist if not _validate_ip(ip)]
    if bad:
        raise ApiError("invalid_ip", f"invalid IPs: {', '.join(bad)}", 422)
    window_json = json.dumps(body.windows)
    await get_db().execute(
        """
        INSERT INTO access_policy (username, ip_allowlist, window_json, updated_at)
        VALUES (?, ?, ?, datetime('now'))
        ON CONFLICT(username) DO UPDATE SET
            ip_allowlist = excluded.ip_allowlist,
            window_json = excluded.window_json,
            updated_at = excluded.updated_at
        """,
        (body.username, "\n".join(body.ip_allowlist), window_json),
    )
    await audit.record(
        username=user.username,
        ip=user.ip,
        action="access_policy_upsert",
        target=body.username,
        new_value=f"ip_allowlist={len(body.ip_allowlist)}:windows={len(body.windows)}",
    )
    return {"ok": True}


@router.get("/policy")
async def list_policies(_: CurrentUser = Depends(require_admin)) -> dict[str, Any]:
    """List all per-user access policies (SPA pulls on mount)."""
    rows = await get_db().fetch_all(
        "SELECT username, ip_allowlist, window_json, updated_at FROM access_policy ORDER BY username"
    )
    policies: list[dict[str, Any]] = []
    for r in rows:
        try:
            windows = json.loads(r["window_json"] or "[]")
        except json.JSONDecodeError:
            windows = []
        policies.append(
            {
                "username": r["username"],
                "ip_allowlist": [s for s in (r["ip_allowlist"] or "").splitlines() if s],
                "windows": windows,
                "updated_at": r["updated_at"],
            }
        )
    return {"policies": policies}


@router.get("/policy/{username}")
async def get_policy(username: str, user: CurrentUser = Depends(require_admin)) -> dict[str, Any]:
    row = await get_db().fetch_one(
        "SELECT ip_allowlist, window_json, updated_at FROM access_policy WHERE username = ?",
        (username,),
    )
    if row is None:
        return {"username": username, "ip_allowlist": [], "windows": []}
    try:
        windows = json.loads(row["window_json"] or "[]")
    except json.JSONDecodeError:
        windows = []
    return {
        "username": username,
        "ip_allowlist": [s for s in (row["ip_allowlist"] or "").splitlines() if s],
        "windows": windows,
        "updated_at": row["updated_at"],
    }
