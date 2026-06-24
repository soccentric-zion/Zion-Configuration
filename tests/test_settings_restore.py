"""Regression test for #130 — POST /api/settings/restore/apply (audit §12).

audit.md §12 Backup/Restore: "Apply mode: ``guarded`` on restore."
The previous implementation exposed ``/api/settings/restore/preview``
(returned the payload) but had no apply path. Operators could upload
+ preview a backup, but no way to apply it through the
apply-state-guarded flow.

The fix:
- ``POST /api/settings/restore/apply`` (admin) takes
  ``{plan_id: <restore_points.id>, confirm: true,
  rollback_armed_s: 60}``.
- The endpoint arms an apply plan with ``rollback_kind="restore_apply"``
  and a snapshot of the current ``alert_rules`` so a watchdog
  expiry auto-rolls-back.
- A fresh heartbeat within the window auto-commits the plan.
"""

from __future__ import annotations

import json

from app.db.database import get_db


def _h(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _seed_restore_point(rules: list[dict]) -> int:
    """Insert a restore_points row with the given alert rules and
    return its id."""
    import asyncio

    async def _seed() -> int:
        db = get_db()
        rid = await db.execute(
            "INSERT INTO restore_points (name, payload) VALUES (?, ?)",
            ("test", json.dumps({"alert_rules": rules})),
        )
        return int(rid)

    return asyncio.new_event_loop().run_until_complete(_seed())


def _list_rules() -> list[dict]:
    import asyncio

    async def _read() -> list[dict]:
        return await get_db().fetch_all("SELECT * FROM alert_rules ORDER BY id")

    return asyncio.new_event_loop().run_until_complete(_read())


def test_restore_apply_arms_plan(client: object, tokens: dict[str, str]) -> None:
    """The apply endpoint arms an apply_plans row with
    rollback_kind="restore_apply" (#130)."""
    rid = _seed_restore_point(
        [
            {
                "name": "restored-rule",
                "metric": "cpu",
                "condition": ">",
                "threshold": 90,
                "duration_s": 30,
                "severity": "warning",
                "enabled": True,
            }
        ]
    )
    admin = _h(tokens["admin"])
    resp = client.post(  # type: ignore[attr-defined]
        "/api/settings/restore/apply",
        json={"plan_id": rid, "confirm": True, "rollback_armed_s": 30},
        headers=admin,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    assert body["applied_rule_count"] == 1
    assert body["plan_id"]
    assert body["armed_until"]
    assert body["rollback_armed_s"] == 30
    # apply_plans row exists with rollback_kind="restore_apply"
    import asyncio

    async def _row() -> dict | None:
        return await get_db().fetch_one("SELECT * FROM apply_plans WHERE id = ?", (body["plan_id"],))

    plan = asyncio.new_event_loop().run_until_complete(_row())
    assert plan is not None
    assert plan["target"] == f"restore:point:{rid}"
    assert plan["rollback_kind"] == "restore_apply"
    assert plan["status"] == "armed"


def test_restore_apply_writes_rules(client: object, tokens: dict[str, str]) -> None:
    """The apply endpoint writes the staged rules to alert_rules."""
    # Pre-existing rule that should be replaced.
    _seed_restore_point(
        [
            {
                "name": "pre-existing",
                "metric": "mem",
                "condition": "<",
                "threshold": 10,
                "duration_s": 0,
                "severity": "warning",
                "enabled": True,
            }
        ]
    )
    # Existing rule — will be wiped on apply.
    import asyncio

    async def _seed_existing() -> None:
        await get_db().execute(
            """INSERT INTO alert_rules (name, metric, condition, threshold, duration_s, severity, enabled)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            ("will-be-removed", "disk", ">", 80, 0, "warning", 1),
        )

    asyncio.new_event_loop().run_until_complete(_seed_existing())
    rid = _seed_restore_point(
        [
            {
                "name": "from-backup",
                "metric": "cpu",
                "condition": ">",
                "threshold": 95,
                "duration_s": 60,
                "severity": "critical",
                "enabled": True,
            }
        ]
    )
    admin = _h(tokens["admin"])
    resp = client.post(  # type: ignore[attr-defined]
        "/api/settings/restore/apply",
        json={"plan_id": rid, "confirm": True, "rollback_armed_s": 30},
        headers=admin,
    )
    assert resp.status_code == 200, resp.text
    rules = _list_rules()
    names = {r["name"] for r in rules}
    assert "from-backup" in names
    assert "pre-existing" not in names
    assert "will-be-removed" not in names


def test_restore_apply_requires_confirm(client: object, tokens: dict[str, str]) -> None:
    """Privileged write — confirm: true is required (428)."""
    rid = _seed_restore_point([])
    admin = _h(tokens["admin"])
    resp = client.post(  # type: ignore[attr-defined]
        "/api/settings/restore/apply",
        json={"plan_id": rid, "rollback_armed_s": 30},
        headers=admin,
    )
    assert resp.status_code == 428, resp.text


def test_restore_apply_rejects_missing_plan_id(client: object, tokens: dict[str, str]) -> None:
    """``plan_id`` is required (422)."""
    admin = _h(tokens["admin"])
    resp = client.post(  # type: ignore[attr-defined]
        "/api/settings/restore/apply",
        json={"confirm": True, "rollback_armed_s": 30},
        headers=admin,
    )
    assert resp.status_code == 422, resp.text


def test_restore_apply_rejects_unknown_restore_point(client: object, tokens: dict[str, str]) -> None:
    """An unknown ``plan_id`` is a 404 (the caller can distinguish
    "row missing" from "row exists but apply failed")."""
    admin = _h(tokens["admin"])
    resp = client.post(  # type: ignore[attr-defined]
        "/api/settings/restore/apply",
        json={"plan_id": 999999, "confirm": True, "rollback_armed_s": 30},
        headers=admin,
    )
    assert resp.status_code == 404, resp.text


def test_restore_apply_requires_admin(client: object, tokens: dict[str, str]) -> None:
    """A viewer cannot apply a restore."""
    rid = _seed_restore_point([])
    viewer = _h(tokens["viewer"])
    resp = client.post(  # type: ignore[attr-defined]
        "/api/settings/restore/apply",
        json={"plan_id": rid, "confirm": True, "rollback_armed_s": 30},
        headers=viewer,
    )
    assert resp.status_code == 403, resp.text
