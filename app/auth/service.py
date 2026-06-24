"""User and login services: CRUD, authentication, brute-force lockout."""

from __future__ import annotations

import datetime as dt
from typing import Any

from app.auth.security import hash_password, verify_password
from app.config import get_settings
from app.db.database import get_db
from app.errors import ApiError


async def seed_default_admin() -> None:
    """Create the default admin on an empty user table (first-run only)."""
    db = get_db()
    existing = await db.fetch_one("SELECT COUNT(*) AS n FROM users")
    if existing and existing["n"] > 0:
        return
    cfg = get_settings().auth
    await db.execute(
        """
        INSERT INTO users (username, password_hash, role, enabled, must_change_pw)
        VALUES (?, ?, 'admin', 1, 1)
        """,
        (cfg.default_admin_user, hash_password(cfg.default_admin_password)),
    )


async def get_user(username: str) -> dict[str, Any] | None:
    return await get_db().fetch_one("SELECT * FROM users WHERE username = ?", (username,))


async def list_users() -> list[dict[str, Any]]:
    return await get_db().fetch_all(
        "SELECT id, username, role, enabled, must_change_pw, "
        "user_source, system_username "
        "FROM users ORDER BY username"
    )


async def create_user(username: str, password: str, role: str) -> int:
    if await get_user(username):
        raise ApiError("user_exists", f"User '{username}' already exists", 409)
    # Admin-issued passwords are temporary by convention; force the new
    # account to pick its own on first login (#45). The seeded default
    # admin does the same via seed_default_admin.
    return await get_db().execute(
        """
        INSERT INTO users (username, password_hash, role, enabled, must_change_pw)
        VALUES (?, ?, ?, 1, 1)
        """,
        (username, hash_password(password), role),
    )


async def update_user(username: str, *, role: str | None, enabled: bool | None) -> None:
    user = await get_user(username)
    if not user:
        raise ApiError("user_not_found", f"User '{username}' not found", 404)
    if role is not None:
        await get_db().execute("UPDATE users SET role = ? WHERE username = ?", (role, username))
    if enabled is not None:
        await get_db().execute("UPDATE users SET enabled = ? WHERE username = ?", (1 if enabled else 0, username))


async def delete_user(username: str) -> None:
    user = await get_user(username)
    if not user:
        raise ApiError("user_not_found", f"User '{username}' not found", 404)
    await get_db().execute("DELETE FROM users WHERE username = ?", (username,))


async def set_password(username: str, new_password: str, *, clear_force: bool = True) -> None:
    user = await get_user(username)
    if not user:
        raise ApiError("user_not_found", f"User '{username}' not found", 404)
    await get_db().execute(
        "UPDATE users SET password_hash = ?, must_change_pw = ? WHERE username = ?",
        (hash_password(new_password), 0 if clear_force else user["must_change_pw"], username),
    )


# --- lockout ---------------------------------------------------------------


def _now_utc_str() -> str:
    """Return current UTC time as a sqlite-friendly string (matches the
    `datetime('now')` default column format used elsewhere)."""
    return dt.datetime.now(tz=dt.UTC).strftime("%Y-%m-%d %H:%M:%S")


async def is_locked(username: str) -> bool:
    """True iff the user has a fixed lockout that hasn't yet expired.

    Replaces the previous sliding-window check (#54) so an attacker can't
    bypass the lockout by waiting for the window to roll — they have to
    wait out the full lockout_minutes from the most recent crossing.
    """
    row = await get_db().fetch_one(
        "SELECT locked_until FROM users WHERE username = ?",
        (username,),
    )
    if row is None:
        return False
    locked_until = row["locked_until"]
    if locked_until is None or locked_until == "":
        return False
    return locked_until > _now_utc_str()


async def _lock_user(username: str, minutes: int) -> None:
    """Set users.locked_until = now + minutes. Idempotent.

    If the username has no row in the users table (typical of an attacker
    probing for valid usernames), we INSERT a shadow row with
    ``enabled=0`` so the lock state has somewhere to live. A shadow row
    is invisible to the legitimate user list and can never be used to
    log in (no password hash), but its ``locked_until`` does pin the
    fixed lockout clock. (#54)
    """
    until = (dt.datetime.now(tz=dt.UTC) + dt.timedelta(minutes=minutes)).strftime("%Y-%m-%d %H:%M:%S")
    # UPSERT: if the row exists, update locked_until; if not, create a
    # shadow row with a dummy password hash and enabled=0 so it can
    # never authenticate.
    await get_db().execute(
        """
        INSERT INTO users (username, password_hash, role, enabled, must_change_pw, locked_until)
        VALUES (?, '!shadow', 'viewer', 0, 0, ?)
        ON CONFLICT(username) DO UPDATE SET locked_until = excluded.locked_until
        """,
        (username, until),
    )


async def _clear_lock(username: str) -> None:
    """Successful login clears the fixed lockout (and the per-attempt
    counter is no longer used, but we leave login_attempts as an audit
    trail of how the lock was triggered)."""
    await get_db().execute(
        "UPDATE users SET locked_until = NULL WHERE username = ?",
        (username,),
    )


async def _record_attempt(username: str, ip: str, success: bool) -> None:
    await get_db().execute(
        "INSERT INTO login_attempts (username, ip, success) VALUES (?, ?, ?)",
        (username, ip, 1 if success else 0),
    )


async def authenticate(username: str, password: str, ip: str) -> dict[str, Any]:
    """Verify credentials honoring lockout. Raises ApiError on any failure."""
    if await is_locked(username):
        raise ApiError(
            "account_locked",
            "Account temporarily locked due to repeated failed logins",
            423,
        )
    user = await get_user(username)
    ok = user is not None and user["enabled"] == 1 and verify_password(password, user["password_hash"])
    await _record_attempt(username, ip, ok)
    if ok:
        assert user is not None
        # Successful login: clear any prior lockout.
        await _clear_lock(username)
        return user
    # Failed login: count *recent* failures (kept for audit / future
    # analytics) and arm a fixed lockout once the threshold is crossed.
    # The fixed lockout is the actual enforcement — the count is just
    # used to decide *when* to arm it. (#54)
    cfg = get_settings().auth
    since = (dt.datetime.now(tz=dt.UTC) - dt.timedelta(minutes=cfg.lockout_minutes)).strftime("%Y-%m-%d %H:%M:%S")
    row = await get_db().fetch_one(
        """
        SELECT COUNT(*) AS n FROM login_attempts
        WHERE username = ? AND success = 0 AND ts >= ?
        """,
        (username, since),
    )
    failures = row["n"] if row else 0
    if failures >= cfg.max_failed_logins:
        await _lock_user(username, cfg.lockout_minutes)
    raise ApiError("invalid_credentials", "Invalid username or password", 401)
