"""Regression test for #134 — factory-reset arms an apply plan (audit §13).

audit.md §13 Factory Reset requires the operation to use Apply-mode
(guarded) with an explicit confirm. The previous implementation just
wiped the tables directly without arming an apply plan, leaving no
rollback window and breaking the symmetry with the other apply-mode
endpoints (set_network, ota_switch).

The fix arms a plan with rollback_kind="factory_reset" before the
wipe; the existing rollback handler in
``app/tasks/apply_state.py::_rollback_factory_reset`` restores the
pre-reset ``system_settings_kv`` rows.
"""

from __future__ import annotations

import asyncio
import platform


def _h(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_factory_reset_arms_apply_plan(client: object, tokens: dict[str, str]) -> None:
    """factory_reset must return ``plan_id`` + ``armed_until`` (#134)."""
    admin = _h(tokens["admin"])
    hostname = platform.node()
    resp = client.post(  # type: ignore[attr-defined]
        "/api/settings/factory-reset",
        json={"confirm_hostname": hostname},
        headers=admin,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # New in #134: the response must surface the apply-plan fields.
    assert "plan_id" in body, "factory_reset must arm an apply plan"
    assert isinstance(body["plan_id"], int)
    assert "armed_until" in body
    assert "rollback_armed_s" in body


def test_factory_reset_persists_armed_plan_row(client: object, tokens: dict[str, str]) -> None:
    """The apply-plan row must be persisted so a watchdog can find it.

    Look up the plan row directly from the DB to verify the
    rollback_kind + status + target fields match the audit §13
    contract.
    """
    admin = _h(tokens["admin"])
    resp = client.post(  # type: ignore[attr-defined]
        "/api/settings/factory-reset",
        json={"confirm_hostname": platform.node()},
        headers=admin,
    )
    assert resp.status_code == 200, resp.text
    plan_id = resp.json()["plan_id"]
    # Look up the plan row directly from the DB.
    from app.db.database import get_db

    db = get_db()

    async def lookup() -> object:
        return await db.fetch_one("SELECT * FROM apply_plans WHERE id = ?", (plan_id,))

    row = asyncio.run(lookup())
    assert row is not None, "apply plan row must be persisted for the rollback watchdog"
    assert row["target"] == "system:factory-reset"
    assert row["rollback_kind"] == "factory_reset"
    assert row["status"] == "armed"


def test_factory_reset_rollback_transitions_status(client: object, tokens: dict[str, str]) -> None:
    """The factory-reset plan can be rolled back and the plan row
    transitions to ``rolled_back`` (#134)."""
    admin = _h(tokens["admin"])
    resp = client.post(  # type: ignore[attr-defined]
        "/api/settings/factory-reset",
        json={"confirm_hostname": platform.node()},
        headers=admin,
    )
    assert resp.status_code == 200, resp.text
    plan_id = resp.json()["plan_id"]

    from app.db.database import get_db
    from app.tasks import apply_state

    db = get_db()

    async def do_rollback() -> object:
        return await apply_state.rollback(plan_id, reason="manual", username="admin")

    asyncio.run(do_rollback())

    async def lookup() -> object:
        return await db.fetch_one("SELECT status FROM apply_plans WHERE id = ?", (plan_id,))

    row = asyncio.run(lookup())
    assert row is not None
    assert row["status"] == "rolled_back", "factory_reset rollback must transition the plan to 'rolled_back' (#134)"


def test_factory_reset_requires_admin(client: object, tokens: dict[str, str]) -> None:
    """A viewer / operator cannot trigger a factory-reset."""
    viewer = _h(tokens["viewer"])
    resp = client.post(  # type: ignore[attr-defined]
        "/api/settings/factory-reset",
        json={"confirm_hostname": platform.node()},
        headers=viewer,
    )
    assert resp.status_code == 403, resp.text
