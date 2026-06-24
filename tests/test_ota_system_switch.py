"""Regression test for #128 — OTA A/B switch arms next-boot plan (Guardrail 2).

audit.md §4 Boot + §8 OTA: Apply mode: guarded + confirm-after-boot
for slot switches. The previous ``system_ab_switch`` recorded the
intent in ``ota_history`` but did NOT arm an apply plan, so a
device reboot before the operator-triggered ``/api/ota/reboot``
left the change as a dangling "pending_reboot" flag with no
rollback window.

The fix:
- ``system_ab_switch`` arms an apply plan with
  ``rollback_kind="ota_slot"`` and a snapshot of the pre-switch
  active slot. The watchdog auto-rolls back if the device doesn't
  come back healthy; a fresh heartbeat within the window commits.
"""

from __future__ import annotations

from app.db.database import get_db


def _h(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_switch_arms_next_boot_plan(client: object, tokens: dict[str, str]) -> None:
    """POST /api/ota/system/switch returns plan_id + armed_until and
    inserts an apply_plans row with rollback_kind="ota_slot" (#128)."""
    admin = _h(tokens["admin"])
    resp = client.post(  # type: ignore[attr-defined]
        "/api/ota/system/switch",
        json={"slot": "B", "confirm": True},
        headers=admin,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["requested_slot"] == "B"
    assert body["pending_reboot"] is True
    assert body["plan_id"]
    assert body["armed_until"]
    assert body["rollback_armed_s"] == 60

    import asyncio

    async def _row() -> dict | None:
        return await get_db().fetch_one("SELECT * FROM apply_plans WHERE id = ?", (body["plan_id"],))

    plan = asyncio.new_event_loop().run_until_complete(_row())
    assert plan is not None
    assert plan["rollback_kind"] == "ota_slot"
    assert plan["status"] == "armed"
    assert plan["target"] == "ota:slot:next-boot:B"


def test_switch_records_previous_slot_in_before(client: object, tokens: dict[str, str]) -> None:
    """The plan's before snapshot carries the previous active slot
    so the rollback handler can restore it."""
    admin = _h(tokens["admin"])
    # First switch.
    resp = client.post(  # type: ignore[attr-defined]
        "/api/ota/system/switch",
        json={"slot": "B", "confirm": True},
        headers=admin,
    )
    body = resp.json()
    import asyncio
    import json

    async def _row() -> dict | None:
        return await get_db().fetch_one("SELECT before_json FROM apply_plans WHERE id = ?", (body["plan_id"],))

    row = asyncio.new_event_loop().run_until_complete(_row())
    assert row is not None
    before = json.loads(row["before_json"])
    # The HAL simulated boot reports active="A", so the snapshot must
    # capture "A" so a rollback restores the device to slot A.
    assert before["target"] == "A"


def test_switch_rejects_bad_slot(client: object, tokens: dict[str, str]) -> None:
    """``slot`` is bounded to {A, B} by ``OtaSlotSwitch``; C is a 422."""
    admin = _h(tokens["admin"])
    resp = client.post(  # type: ignore[attr-defined]
        "/api/ota/system/switch",
        json={"slot": "C", "confirm": True},
        headers=admin,
    )
    assert resp.status_code == 422, resp.text


def test_switch_requires_confirm(client: object, tokens: dict[str, str]) -> None:
    """Privileged write — ``confirm: true`` is required (428)."""
    admin = _h(tokens["admin"])
    resp = client.post(  # type: ignore[attr-defined]
        "/api/ota/system/switch",
        json={"slot": "B"},
        headers=admin,
    )
    assert resp.status_code == 428, resp.text


def test_switch_requires_admin(client: object, tokens: dict[str, str]) -> None:
    """A viewer cannot switch slots."""
    viewer = _h(tokens["viewer"])
    resp = client.post(  # type: ignore[attr-defined]
        "/api/ota/system/switch",
        json={"slot": "B", "confirm": True},
        headers=viewer,
    )
    assert resp.status_code == 403, resp.text
