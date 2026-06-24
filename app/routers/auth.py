"""Auth + user management endpoints."""

from __future__ import annotations

import json
from typing import Any, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import field_validator

from app.auth import service
from app.auth.deps import (
    CurrentUser,
    _invalidate_user_state,
    get_current_user,
    require_admin,
)
from app.auth.models import (
    LoginRequest,
    PasswordChange,
    PasswordReset,
    TokenResponse,
    UserCreate,
    UserOut,
    UserUpdate,
)
from app.auth.security import create_access_token
from app.db import audit
from app.db.database import get_db
from app.errors import ApiError
from app.util.models import _StrictModel

router = APIRouter(prefix="/api", tags=["auth"])


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest, request: Request) -> TokenResponse:
    user = await service.authenticate(body.username, body.password, _client_ip(request))
    token = create_access_token(user["username"], user["role"])
    await audit.record(username=user["username"], ip=_client_ip(request), action="login", target=user["username"])
    return TokenResponse(
        access_token=token,
        role=user["role"],
        username=user["username"],
        must_change_password=bool(user["must_change_pw"]),
    )


@router.get("/me", response_model=UserOut)
async def me(user: CurrentUser = Depends(get_current_user)) -> UserOut:
    record = await service.get_user(user.username)
    if not record:
        raise ApiError("user_not_found", "User no longer exists", 404)
    return UserOut(
        id=record["id"],
        username=record["username"],
        role=record["role"],
        enabled=bool(record["enabled"]),
        must_change_password=bool(record["must_change_pw"]),
    )


@router.post("/me/password")
async def change_password(body: PasswordChange, user: CurrentUser = Depends(get_current_user)) -> dict[str, bool]:
    record = await service.get_user(user.username)
    if not record:
        raise ApiError("user_not_found", "User no longer exists", 404)
    from app.auth.security import verify_password

    if not verify_password(body.old_password, record["password_hash"]):
        raise ApiError("invalid_credentials", "Current password is incorrect", 401)
    await service.set_password(user.username, body.new_password)
    await audit.record(username=user.username, ip=user.ip, action="change_password", target=user.username)
    return {"ok": True}


# --- user management (admin) ----------------------------------------------


@router.get("/users", response_model=list[UserOut])
async def list_users(_: CurrentUser = Depends(require_admin)) -> list[UserOut]:
    rows = await service.list_users()
    return [
        UserOut(
            id=r["id"],
            username=r["username"],
            role=r["role"],
            enabled=bool(r["enabled"]),
            must_change_password=bool(r["must_change_pw"]),
            user_source=r.get("user_source") or "local",
            system_username=r.get("system_username"),
        )
        for r in rows
    ]


@router.post("/users", response_model=UserOut, status_code=201)
async def create_user(body: UserCreate, user: CurrentUser = Depends(require_admin)) -> UserOut:
    await service.create_user(body.username, body.password, body.role)
    await audit.record(
        username=user.username,
        ip=user.ip,
        action="create_user",
        target=body.username,
        new_value=body.role,
    )
    # Read the persisted row back so must_change_password reflects the
    # actual DB value (create_user sets it to 1, per #45) instead of a
    # hardcoded False.
    record = await service.get_user(body.username)
    assert record is not None
    return UserOut(
        id=record["id"],
        username=record["username"],
        role=record["role"],
        enabled=bool(record["enabled"]),
        must_change_password=bool(record["must_change_pw"]),
        user_source=record.get("user_source") or "local",
        system_username=record.get("system_username"),
    )


@router.patch("/users/{username}")
async def update_user(username: str, body: UserUpdate, user: CurrentUser = Depends(require_admin)) -> dict[str, bool]:
    await service.update_user(username, role=body.role, enabled=body.enabled)
    _invalidate_user_state(username)
    await audit.record(
        username=user.username,
        ip=user.ip,
        action="update_user",
        target=username,
        new_value=body.model_dump(exclude_none=True),
    )
    return {"ok": True}


# --- per-user source toggle (Features.md §Settings Dashboard Users) --------
# A user row can be either ``local`` (dashboard-managed credentials) or
# ``system`` (PAM-mapped; ``system_username`` is the OS account). The
# actual PAM bind is a follow-up; today this just records the link so
# the SPA can show it and backup/restore picks it up. Switching a user
# to ``system`` requires a non-empty ``system_username`` so we never
# store a row that points at nothing.
class _UserSourceUpdate(_StrictModel):
    user_source: Literal["local", "system"]
    system_username: str | None = None

    @field_validator("system_username")
    @classmethod
    def _system_username_when_system(cls, v: str | None, info: Any) -> str | None:
        # Cross-field check (method is "after" so user_source is set).
        # We accept the values as-is, then re-validate in the router
        # once we know ``user_source`` — pydantic v2 has no first-class
        # cross-field @field_validator when one of the fields is a
        # Literal that we also need to inspect, so the router performs
        # the rule. This validator only strips/limits the string.
        if v is None:
            return v
        v = v.strip()
        if not v:
            return None
        if len(v) > 64:
            raise ValueError("system_username must be <= 64 chars")
        return v


@router.patch("/users/{username}/source")
async def set_user_source(
    username: str,
    body: _UserSourceUpdate,
    user: CurrentUser = Depends(require_admin),
) -> dict[str, Any]:
    """Link/unlink a dashboard user to a system account (Features.md §Settings Dashboard Users)."""
    user_row = await service.get_user(username)
    if not user_row:
        raise ApiError("user_not_found", f"User '{username}' not found", 404)
    # System-mapped rows must reference a real system_username; local
    # rows must clear it so a future re-link starts from a clean state.
    if body.user_source == "system" and not body.system_username:
        raise ApiError(
            "system_username_required",
            "system_username is required when user_source is 'system'",
            422,
        )
    final_system_username = body.system_username if body.user_source == "system" else None
    await get_db().execute(
        "UPDATE users SET user_source = ?, system_username = ? WHERE username = ?",
        (body.user_source, final_system_username, username),
    )
    _invalidate_user_state(username)
    await audit.record(
        username=user.username,
        ip=user.ip,
        action="set_user_source",
        target=username,
        new_value=f"source={body.user_source}:system={final_system_username}",
    )
    return {"ok": True, "user_source": body.user_source, "system_username": final_system_username}


@router.post("/users/{username}/password")
async def reset_password(
    username: str, body: PasswordReset, user: CurrentUser = Depends(require_admin)
) -> dict[str, bool]:
    await service.set_password(username, body.new_password)
    await audit.record(username=user.username, ip=user.ip, action="reset_password", target=username)
    return {"ok": True}


@router.delete("/users/{username}")
async def delete_user(username: str, user: CurrentUser = Depends(require_admin)) -> dict[str, bool]:
    if username == user.username:
        raise ApiError("cannot_delete_self", "You cannot delete your own account", 400)
    await service.delete_user(username)
    _invalidate_user_state(username)
    await audit.record(username=user.username, ip=user.ip, action="delete_user", target=username)
    return {"ok": True}


# --- self-service (Features.md §Users: Self-Service) -----------------------
# The operator's own profile / sessions / login history. Distinct from
# the admin ``/api/users`` surface — these are scoped to the
# *current* user, with no privilege to see or change other accounts.


@router.get("/me/sessions")
async def my_sessions(user: CurrentUser = Depends(get_current_user)) -> dict[str, Any]:
    """Active sessions belonging to the current user (Features.md §Users).

    Self-service: every operator can see which devices / IPs they
    are logged in from. Distinct from ``GET /api/sessions`` (admin)
    which lists *all* sessions. Filtered server-side by
    ``username = me`` so a viewer can never see another user's row.
    """
    rows = await get_db().fetch_all(
        "SELECT id, ip, user_agent, issued_at, last_seen_at, expires_at "
        "FROM sessions WHERE username = ? AND revoked = 0 "
        "ORDER BY issued_at DESC LIMIT 50",
        (user.username,),
    )
    return {"sessions": [dict(r) for r in rows]}


@router.delete("/me/sessions/{session_id}")
async def revoke_my_session(session_id: str, user: CurrentUser = Depends(get_current_user)) -> dict[str, Any]:
    """Revoke one of the current user's own sessions.

    Self-service remote-logout: the operator notices an unfamiliar
    IP / device in ``GET /api/me/sessions`` and clicks "Revoke".
    Server-side filter (``username = me``) prevents a viewer from
    revoking anyone else's session even by guessing the id.
    """
    row = await get_db().fetch_one("SELECT username FROM sessions WHERE id = ?", (session_id,))
    if row is None:
        raise ApiError("session_not_found", f"session {session_id!r} not found", 404)
    if row["username"] != user.username:
        # Don't disclose the existence of another user's session —
        # return 404, not 403, so a probe gets the same answer as a
        # missing id.
        raise ApiError("session_not_found", f"session {session_id!r} not found", 404)
    await get_db().execute("UPDATE sessions SET revoked = 1 WHERE id = ?", (session_id,))
    await audit.record(
        username=user.username,
        ip=user.ip,
        action="self_session_revoke",
        target=user.username,
        new_value=f"session_id={session_id}",
    )
    return {"ok": True, "session_id": session_id, "revoked": True}


@router.get("/me/login-history")
async def my_login_history(user: CurrentUser = Depends(get_current_user)) -> dict[str, Any]:
    """Last 50 login attempts for the current user (Features.md §Users).

    Includes both successful and failed attempts so the operator can
    spot a brute-force probe. Filtered server-side by ``username = me``
    so a viewer can never see another user's row. The audit log
    (``/api/security/audit?user_filter=me``) carries the privileged
    actions separately.
    """
    rows = await get_db().fetch_all(
        "SELECT ts, ip, success FROM login_attempts " "WHERE username = ? ORDER BY ts DESC, id DESC LIMIT 50",
        (user.username,),
    )
    return {"attempts": [dict(r) for r in rows]}


# --- effective permissions (#152, Features.md Req 11 Permissions) ----------
# Per-user view: the role from the users table + the union of any
# group memberships. The SPA uses this to render the "what can this
# user do?" panel under the user-edit form.
@router.get("/users/{username}/effective-permissions")
async def effective_permissions(username: str, _: CurrentUser = Depends(require_admin)) -> dict[str, object]:
    """Resolved per-user permission set (Features.md Req 11).

    Returns ``role`` (the user's role from the ``users`` table),
    ``groups`` (the list of dashboard groups the user belongs to),
    and ``actions`` (the union of role- and group-derived actions
    per resource: ``{resource: [read, write, execute]}``).
    """
    user_row = await service.get_user(username)
    if user_row is None:
        raise ApiError("user_not_found", f"user {username!r} not found", 404)
    role = user_row["role"]

    # Built-in role defaults match the matrix in app/routers/roles.py.
    # Keep these in sync if a new role is added.
    role_actions: dict[str, list[str]] = {}
    if role == "admin":
        role_actions = {"*": ["read", "write", "execute"]}
    elif role == "operator":
        role_actions = {"*": ["read", "write"]}
    elif role == "viewer":
        role_actions = {"*": ["read"]}

    # Pull the user's group memberships; group rows may carry
    # ``permissions_json`` for fine-grained resource overrides.
    rows = await get_db().fetch_all(
        "SELECT g.name, g.permissions FROM groups g "
        "JOIN group_memberships m ON m.group_id = g.id "
        "WHERE m.username = ?",
        (username,),
    )
    group_names: list[str] = []
    group_actions: dict[str, list[str]] = {}
    for r in rows:
        group_names.append(r["name"])
        try:
            perms = json.loads(r["permissions"] or "{}")
        except (json.JSONDecodeError, TypeError):
            perms = {}
        if isinstance(perms, dict):
            for res, acts in perms.items():
                if not isinstance(acts, list):
                    continue
                # Union, not replace — multiple groups may grant the
                # same resource a different action.
                cur = set(group_actions.get(res, []))
                cur.update(str(a) for a in acts)
                group_actions[res] = sorted(cur)

    # Compose: role actions are the base, group actions add.
    actions: dict[str, list[str]] = {}
    base = role_actions.get("*", [])
    for res in {"*", *group_actions.keys()}:
        acts = set(base)
        if res in group_actions:
            acts.update(group_actions[res])
        actions[res] = sorted(acts)
    return {
        "username": username,
        "role": role,
        "groups": group_names,
        "actions": actions,
    }
