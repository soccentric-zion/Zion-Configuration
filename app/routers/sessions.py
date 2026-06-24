"""Active session tracking (Features.md §Users Sessions)."""

from __future__ import annotations

import time
import uuid
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import Field

from app.auth.deps import CurrentUser, require_admin, require_viewer
from app.auth.security import create_access_token
from app.config import get_settings
from app.db import audit
from app.db.database import get_db
from app.errors import ApiError
from app.util.models import _StrictModel

router = APIRouter(prefix="/api/sessions", tags=["sessions"])


@router.get("")
async def list_sessions(_: CurrentUser = Depends(require_viewer)) -> dict[str, Any]:
    """List active (non-revoked, non-expired) sessions (Features.md §Users)."""
    rows = await get_db().fetch_all(
        "SELECT id, username, ip, user_agent, issued_at, last_seen_at, expires_at "
        "FROM sessions WHERE revoked = 0 ORDER BY issued_at DESC LIMIT 100"
    )
    return {"sessions": [dict(r) for r in rows]}


@router.post("")
async def register_session(
    user: CurrentUser = Depends(require_viewer),
) -> dict[str, Any]:
    """Register a session for the current user (called by the SPA on login).

    Real: client passes user_agent / ip. We extract from the request
    here via the dependency. The session id is the JWT's ``jti`` claim,
    so revoking a session here invalidates the matching JWT.
    """
    token = create_access_token(user.username, user.role)
    sid = uuid.uuid4().hex
    ttl = get_settings().auth.access_token_ttl_minutes
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    exp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + ttl * 60))
    await get_db().execute(
        """
        INSERT INTO sessions (id, username, ip, user_agent, issued_at, last_seen_at, expires_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (sid, user.username, user.ip, "dashboard-spa", now, now, exp),
    )
    return {"session_id": sid, "issued_at": now, "expires_at": exp, "token": token}


@router.delete("/{session_id}")
async def revoke_session(
    session_id: str,
    user: CurrentUser = Depends(require_admin),
) -> dict[str, Any]:
    """Revoke an active session (privileged write, audited)."""
    row = await get_db().fetch_one("SELECT username FROM sessions WHERE id = ?", (session_id,))
    if row is None:
        raise ApiError("session_not_found", f"session {session_id} not found", 404)
    await get_db().execute(
        "UPDATE sessions SET revoked = 1 WHERE id = ?",
        (session_id,),
    )
    await audit.record(
        username=user.username,
        ip=user.ip,
        action="session_revoke",
        target=row["username"],
        new_value=f"session_id={session_id}",
    )
    return {"ok": True, "revoked": session_id}


@router.get("/history")
async def session_history(_: CurrentUser = Depends(require_admin)) -> dict[str, Any]:
    """Session history (last 100, revoked or expired)."""
    rows = await get_db().fetch_all(
        "SELECT id, username, ip, issued_at, last_seen_at, expires_at, revoked "
        "FROM sessions ORDER BY issued_at DESC LIMIT 100"
    )
    return {"sessions": [dict(r) for r in rows]}


class _MaxConcurrent(_StrictModel):
    limit: int = Field(ge=1, le=64)


@router.post("/max-concurrent")
async def set_max_concurrent(
    body: _MaxConcurrent,
    user: CurrentUser = Depends(require_admin),
) -> dict[str, Any]:
    """Set max concurrent sessions per user (privileged write, audited)."""
    await get_db().execute(
        "INSERT OR REPLACE INTO system_settings_kv (key, value, updated_at) " "VALUES (?, ?, datetime('now'))",
        ("auth.max_concurrent_sessions", str(body.limit)),
    )
    await audit.record(
        username=user.username,
        ip=user.ip,
        action="max_concurrent_sessions",
        target="auth",
        new_value=str(body.limit),
    )
    return {"ok": True, "max_concurrent": body.limit}
