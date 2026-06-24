"""Shared helpers for destructive/privileged endpoints.

Destructive actions (firewall changes, interface down, port close, service
stop, network reconfig, reboot, shutdown, factory reset, OTA switch/rollback)
require explicit ``confirm: true`` in the request body. The SPA sends it after
the user ticks a confirm box; the backend enforces it server-side. The 428
status ("Precondition Required") matches the existing pattern in
``app/routers/sysadmin.py``.
"""

from __future__ import annotations

from typing import Any

from app.errors import ApiError


def require_confirm(body: dict[str, Any] | None) -> None:
    """Raise 428 if the body does not carry an explicit ``confirm: true``."""
    if not body or not body.get("confirm"):
        raise ApiError(
            "confirm_required",
            "This lockout-capable action requires explicit confirmation",
            428,
        )
