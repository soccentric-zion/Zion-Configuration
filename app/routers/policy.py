"""Password policy + 2FA + session timeout (Features.md §Users Authentication)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import Field

from app.auth.deps import CurrentUser, require_admin, require_viewer
from app.db import audit
from app.db.database import get_db
from app.util.models import _StrictModel

router = APIRouter(prefix="/api/policy", tags=["policy"])


@router.get("/password")
async def get_password_policy(_: CurrentUser = Depends(require_viewer)) -> dict[str, Any]:
    """Get the password policy (Features.md §Users password policy)."""
    row = await get_db().fetch_one(
        "SELECT min_length, require_upper, require_lower, require_digit, "
        "require_symbol, expiry_days, history_count, updated_at "
        "FROM password_policy WHERE id = 1"
    )
    if row is None:
        return {
            "min_length": 12,
            "require_upper": True,
            "require_lower": True,
            "require_digit": True,
            "require_symbol": True,
            "expiry_days": 0,
            "history_count": 5,
        }
    return dict(row)


class _PasswordPolicyUpdate(_StrictModel):
    min_length: int = Field(ge=6, le=128)
    require_upper: bool = True
    require_lower: bool = True
    require_digit: bool = True
    require_symbol: bool = True
    expiry_days: int = Field(ge=0, le=3650)
    history_count: int = Field(ge=0, le=50)


@router.put("/password")
async def set_password_policy(
    body: _PasswordPolicyUpdate,
    user: CurrentUser = Depends(require_admin),
) -> dict[str, Any]:
    """Set the password policy (privileged write, audited)."""
    await get_db().execute(
        """
        INSERT INTO password_policy (id, min_length, require_upper, require_lower,
                                    require_digit, require_symbol, expiry_days,
                                    history_count, updated_at)
        VALUES (1, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(id) DO UPDATE SET
            min_length = excluded.min_length,
            require_upper = excluded.require_upper,
            require_lower = excluded.require_lower,
            require_digit = excluded.require_digit,
            require_symbol = excluded.require_symbol,
            expiry_days = excluded.expiry_days,
            history_count = excluded.history_count,
            updated_at = excluded.updated_at
        """,
        (
            body.min_length,
            int(body.require_upper),
            int(body.require_lower),
            int(body.require_digit),
            int(body.require_symbol),
            body.expiry_days,
            body.history_count,
        ),
    )
    await audit.record(
        username=user.username,
        ip=user.ip,
        action="password_policy_update",
        target="policy",
        new_value=f"min={body.min_length}:exp={body.expiry_days}",
    )
    return {"ok": True}


class _SessionTimeout(_StrictModel):
    idle_minutes: int = Field(ge=1, le=1440)


@router.put("/session-timeout")
async def set_session_timeout(body: _SessionTimeout, user: CurrentUser = Depends(require_admin)) -> dict[str, Any]:
    await get_db().execute(
        "INSERT OR REPLACE INTO system_settings_kv (key, value, updated_at) " "VALUES (?, ?, datetime('now'))",
        ("auth.idle_timeout_minutes", str(body.idle_minutes)),
    )
    await audit.record(
        username=user.username,
        ip=user.ip,
        action="session_timeout_update",
        target="auth",
        new_value=str(body.idle_minutes),
    )
    return {"ok": True, "idle_minutes": body.idle_minutes}


class _Require2FA(_StrictModel):
    enabled: bool


@router.put("/2fa")
async def set_2fa(body: _Require2FA, user: CurrentUser = Depends(require_admin)) -> dict[str, Any]:
    """Toggle 2FA enforcement (privileged write, audited)."""
    await get_db().execute(
        "INSERT OR REPLACE INTO system_settings_kv (key, value, updated_at) " "VALUES (?, ?, datetime('now'))",
        ("auth.require_2fa", "1" if body.enabled else "0"),
    )
    await audit.record(
        username=user.username,
        ip=user.ip,
        action="2fa_requirement_update",
        target="auth",
        new_value="on" if body.enabled else "off",
    )
    return {"ok": True, "require_2fa": body.enabled}
