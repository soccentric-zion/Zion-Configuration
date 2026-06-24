"""FastAPI dependencies: current-user extraction and role guards."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import jwt
from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.auth.security import decode_token
from app.errors import ApiError

_bearer = HTTPBearer(auto_error=False)

ROLE_RANK = {"viewer": 0, "operator": 1, "admin": 2}

# In-memory cache of (username -> (role, enabled, expires_at_monotonic)).
# Hot-path auth validation must not hit the DB on every request, but a deleted
# or disabled user must lose API access within seconds (not minutes). 10s TTL
# is short enough to revoke quickly and long enough to absorb burst traffic.
_USER_STATE_TTL_S = 10.0
_user_state_cache: dict[str, tuple[str, bool, float]] = {}


def _cached_user_state(username: str) -> tuple[str, bool] | None:
    now = time.monotonic()
    entry = _user_state_cache.get(username)
    if entry is None:
        return None
    role, enabled, expires_at = entry
    if expires_at <= now:
        _user_state_cache.pop(username, None)
        return None
    return role, enabled


def _set_user_state(username: str, role: str, enabled: bool) -> None:
    _user_state_cache[username] = (
        role,
        enabled,
        time.monotonic() + _USER_STATE_TTL_S,
    )


def _invalidate_user_state(username: str) -> None:
    """Drop any cached state for a user (called on update/delete)."""
    _user_state_cache.pop(username, None)


@dataclass
class CurrentUser:
    username: str
    role: str
    ip: str


async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> CurrentUser:
    if credentials is None or not credentials.credentials:
        raise ApiError("not_authenticated", "Authentication required", 401)
    try:
        payload = decode_token(credentials.credentials)
    except jwt.PyJWTError as exc:
        raise ApiError("invalid_token", "Invalid or expired token", 401) from exc
    username = str(payload["sub"])
    claim_role = str(payload["role"])

    # Verify the user still exists, is enabled, and the role claim matches the
    # DB (a deleted or demoted user must lose access within USER_STATE_TTL_S).
    cached = _cached_user_state(username)
    if cached is None:
        # Lazy import avoids a circular dep at module load.
        from app.auth.service import get_user

        row = await get_user(username)
        if row is None:
            raise ApiError("user_not_found", "User no longer exists", 401)
        if row["enabled"] != 1:
            raise ApiError("user_disabled", "User account is disabled", 401)
        _set_user_state(username, str(row["role"]), bool(row["enabled"]))
        db_role = str(row["role"])
        enabled = bool(row["enabled"])
    else:
        db_role, enabled = cached
    if not enabled:
        raise ApiError("user_disabled", "User account is disabled", 401)
    if db_role != claim_role:
        # The role changed after the JWT was issued — treat as unauthorised.
        raise ApiError("role_changed", "User role has changed; please re-login", 401)

    ip = request.client.host if request.client else "unknown"
    return CurrentUser(username=username, role=db_role, ip=ip)


def require_role(minimum: str) -> Callable[[CurrentUser], Awaitable[CurrentUser]]:
    """Dependency factory enforcing a minimum role (viewer<operator<admin)."""
    needed = ROLE_RANK[minimum]

    async def _guard(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if ROLE_RANK.get(user.role, -1) < needed:
            raise ApiError(
                "forbidden",
                f"Requires '{minimum}' role or higher",
                403,
            )
        return user

    return _guard


# Convenience guards
require_viewer = require_role("viewer")
require_operator = require_role("operator")
require_admin = require_role("admin")


def user_from_token(token: str, ip: str) -> CurrentUser:
    """Validate a raw token (used for WebSocket query-param auth)."""
    try:
        payload = decode_token(token)
    except jwt.PyJWTError as exc:
        raise ApiError("invalid_token", "Invalid or expired token", 401) from exc
    return CurrentUser(username=str(payload["sub"]), role=str(payload["role"]), ip=ip)
