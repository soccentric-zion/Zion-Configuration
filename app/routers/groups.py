"""Groups (Features.md §Users Groups)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import Field

from app.auth.deps import CurrentUser, require_admin
from app.db import audit
from app.db.database import get_db
from app.errors import ApiError
from app.hal.registry import get_hal
from app.util.models import _StrictModel

router = APIRouter(prefix="/api/groups", tags=["groups"])


@router.get("")
async def list_groups(_: CurrentUser = Depends(require_admin)) -> dict[str, Any]:
    """List dashboard groups + system groups from the security HAL.

    Each dashboard group entry also carries the username list from
    ``group_memberships`` so the SPA can render add/remove controls
    without a second round-trip.
    """
    rows = await get_db().fetch_all("SELECT id, name, description, parent_id, created_at FROM groups ORDER BY name")
    out: list[dict[str, Any]] = []
    for r in rows:
        d = dict(r)
        members = await get_db().fetch_all(
            "SELECT username FROM group_memberships WHERE group_id = ? ORDER BY username",
            (d["id"],),
        )
        d["members"] = [m["username"] for m in members]
        out.append(d)
    sys_groups = get_hal("security").user_groups()
    return {"groups": out, "system": sys_groups}


class _GroupUpsert(_StrictModel):
    name: str = Field(min_length=1, max_length=64)
    description: str = Field(default="", max_length=512)
    parent_id: int | None = None


@router.post("")
async def upsert_group(body: _GroupUpsert, user: CurrentUser = Depends(require_admin)) -> dict[str, Any]:
    """Create or update a group (privileged write, audited)."""
    cur = await get_db().execute(
        """
        INSERT INTO groups (name, description, parent_id)
        VALUES (?, ?, ?)
        ON CONFLICT(name) DO UPDATE SET
            description = excluded.description,
            parent_id = excluded.parent_id
        """,
        (body.name, body.description, body.parent_id),
    )
    await audit.record(
        username=user.username,
        ip=user.ip,
        action="group_upsert",
        target=body.name,
        new_value=f"parent={body.parent_id}",
    )
    return {"ok": True, "id": cur}


@router.delete("/{group_id}")
async def delete_group(group_id: int, user: CurrentUser = Depends(require_admin)) -> dict[str, Any]:
    row = await get_db().fetch_one("SELECT name FROM groups WHERE id = ?", (group_id,))
    if row is None:
        raise ApiError("group_not_found", f"group {group_id} not found", 404)
    await get_db().execute("DELETE FROM groups WHERE id = ?", (group_id,))
    await audit.record(
        username=user.username,
        ip=user.ip,
        action="group_delete",
        target=row["name"],
    )
    return {"ok": True, "deleted": group_id}


class _GroupMember(_StrictModel):
    username: str = Field(min_length=1, max_length=64)


@router.post("/{group_id}/members")
async def add_member(group_id: int, body: _GroupMember, user: CurrentUser = Depends(require_admin)) -> dict[str, Any]:
    row = await get_db().fetch_one("SELECT name FROM groups WHERE id = ?", (group_id,))
    if row is None:
        raise ApiError("group_not_found", f"group {group_id} not found", 404)
    await get_db().execute(
        "INSERT OR IGNORE INTO group_memberships (group_id, username) VALUES (?, ?)",
        (group_id, body.username),
    )
    await audit.record(
        username=user.username,
        ip=user.ip,
        action="group_add_member",
        target=f"{row['name']}/{body.username}",
    )
    return {"ok": True}


@router.delete("/{group_id}/members/{username}")
async def remove_member(
    group_id: int,
    username: str,
    user: CurrentUser = Depends(require_admin),
) -> dict[str, Any]:
    row = await get_db().fetch_one("SELECT name FROM groups WHERE id = ?", (group_id,))
    if row is None:
        raise ApiError("group_not_found", f"group {group_id} not found", 404)
    await get_db().execute(
        "DELETE FROM group_memberships WHERE group_id = ? AND username = ?",
        (group_id, username),
    )
    await audit.record(
        username=user.username,
        ip=user.ip,
        action="group_remove_member",
        target=f"{row['name']}/{username}",
    )
    return {"ok": True}
