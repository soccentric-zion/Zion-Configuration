"""Custom roles + role-permission matrix (Features.md §Users Roles/Permissions)."""

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

router = APIRouter(prefix="/api/roles", tags=["roles"])

# Built-in roles come from the auth module; custom roles are persisted.
_BUILTIN_ROLES: tuple[tuple[str, int], ...] = (
    ("admin", 2),
    ("operator", 1),
    ("viewer", 0),
)


@router.get("")
async def list_roles(_: CurrentUser = Depends(require_admin)) -> dict[str, Any]:
    """List built-in + custom roles."""
    rows = await get_db().fetch_all(
        "SELECT id, name, rank, permissions, builtin FROM custom_roles ORDER BY rank DESC, name"
    )
    custom = []
    for r in rows:
        d = dict(r)
        try:
            d["permissions"] = json.loads(d["permissions"] or "{}")
        except json.JSONDecodeError:
            d["permissions"] = {}
        custom.append(d)
    return {
        "builtin": [{"name": n, "rank": r, "builtin": True} for n, r in _BUILTIN_ROLES],
        "custom": custom,
    }


class _RoleUpsert(_StrictModel):
    name: str = Field(min_length=1, max_length=64)
    rank: int = Field(ge=0, le=10)
    permissions: dict[str, list[str]] = Field(default_factory=dict)


@router.post("")
async def upsert_role(body: _RoleUpsert, user: CurrentUser = Depends(require_admin)) -> dict[str, Any]:
    """Create or update a custom role (privileged write, audited)."""
    if body.name in {n for n, _ in _BUILTIN_ROLES}:
        raise ApiError("builtin_role", f"{body.name!r} is a built-in role and cannot be redefined", 422)
    cur = await get_db().execute(
        """
        INSERT INTO custom_roles (name, rank, permissions, builtin)
        VALUES (?, ?, ?, 0)
        ON CONFLICT(name) DO UPDATE SET
            rank = excluded.rank,
            permissions = excluded.permissions
        """,
        (body.name, body.rank, json.dumps(body.permissions)),
    )
    await audit.record(
        username=user.username,
        ip=user.ip,
        action="role_upsert",
        target=body.name,
        new_value=f"rank={body.rank}:perms={len(body.permissions)}",
    )
    return {"ok": True, "id": cur}


@router.delete("/{role_id}")
async def delete_role(role_id: int, user: CurrentUser = Depends(require_admin)) -> dict[str, Any]:
    row = await get_db().fetch_one(
        "SELECT name, builtin FROM custom_roles WHERE id = ?",
        (role_id,),
    )
    if row is None:
        raise ApiError("role_not_found", f"role {role_id} not found", 404)
    if row["builtin"]:
        raise ApiError("builtin_role", "built-in roles cannot be deleted", 422)
    await get_db().execute("DELETE FROM custom_roles WHERE id = ?", (role_id,))
    await audit.record(
        username=user.username,
        ip=user.ip,
        action="role_delete",
        target=row["name"],
    )
    return {"ok": True, "deleted": role_id}


@router.get("/matrix")
async def permission_matrix(_: CurrentUser = Depends(require_admin)) -> dict[str, Any]:
    """Role × resource × action matrix for the SPA."""
    resources = (
        "system",
        "network",
        "hardware",
        "storage",
        "thermal",
        "ota",
        "telemetry",
        "cloud",
        "security",
        "users",
        "logs",
        "settings",
    )
    actions = ("read", "write", "execute")
    rows = await get_db().fetch_all("SELECT name, rank, permissions FROM custom_roles ORDER BY rank DESC")
    custom = {}
    for r in rows:
        try:
            perms = json.loads(r["permissions"] or "{}")
        except json.JSONDecodeError:
            perms = {}
        custom[r["name"]] = perms
    # Built-in roles have implicit permissions baked into the role guards;
    # the SPA shows them as "all" for admin, "system/network/..." for operator,
    # "read-only" for viewer.
    builtin = {
        "admin": {res: list(actions) for res in resources},
        "operator": {res: ["read", "write"] for res in resources},
        "viewer": {res: ["read"] for res in resources},
    }
    return {"resources": list(resources), "actions": list(actions), "builtin": builtin, "custom": custom}
