"""Append-only audit trail helper.

Every state-changing endpoint records an entry here. There is intentionally no
delete path — audit entries are immutable (a hard requirement for the security
group). The compliance/audit router only ever reads and exports.
"""

from __future__ import annotations

from typing import Any

from app.db.database import get_db


async def record(
    *,
    username: str,
    ip: str,
    action: str,
    target: str | None = None,
    old_value: Any = None,
    new_value: Any = None,
) -> None:
    """Write one audit entry. Old/new values are stringified for storage."""
    await get_db().execute(
        """
        INSERT INTO audit_log (username, ip, action, target, old_value, new_value)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            username,
            ip,
            action,
            target,
            None if old_value is None else str(old_value),
            None if new_value is None else str(new_value),
        ),
    )
