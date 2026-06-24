"""Tests for the confirm-after-boot heartbeat (audit Guardrail 2, #93).

Without a heartbeat, the apply-plan watchdog's pure timer auto-rolled
back even successful reboots if the SPA didn't click "confirm" before
``armed_until`` (#93). With a heartbeat, the watchdog commits the plan
when the device has checked in healthy, and rolls back only when no
heartbeat has arrived.

The watchdog's ``_tick`` is invoked directly to avoid wall-clock waits.
"""

from __future__ import annotations

import asyncio
import time

from app.db.database import get_db
from app.tasks.apply_state import ApplyPlanWatchdog


def _h(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# --- heartbeat endpoint records a row -------------------------------------
def test_health_heartbeat_endpoint_records_last_seen(client: object, tokens: dict[str, str]) -> None:
    admin = _h(tokens["admin"])
    resp = client.post("/api/system/health/heartbeat", json={"source": "spa"}, headers=admin)  # type: ignore[attr-defined]
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    assert body["source"] == "spa"
    assert body["last_seen_at"]
    # GET returns the recorded row
    resp = client.get("/api/system/health/heartbeat", headers=admin)  # type: ignore[attr-defined]
    assert resp.status_code == 200
    assert resp.json()["recorded"] is True
    assert resp.json()["source"] == "spa"


def test_health_heartbeat_requires_admin(client: object, tokens: dict[str, str]) -> None:
    viewer = _h(tokens["viewer"])
    resp = client.post("/api/system/health/heartbeat", json={}, headers=viewer)  # type: ignore[attr-defined]
    assert resp.status_code == 403


# --- watchdog honors a fresh heartbeat ------------------------------------
def test_watchdog_commits_when_heartbeat_recent(client: object, tokens: dict[str, str]) -> None:
    """Arm a plan, record a heartbeat, run the watchdog tick — the plan
    must auto-commit, not roll back."""
    admin = _h(tokens["admin"])
    client.get("/api/sysadmin/network", headers=admin)  # type: ignore[attr-defined]

    async def _seed_record_tick() -> None:
        db = get_db()
        past = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(time.time() - 1))
        cur = await db.conn.execute(
            """INSERT INTO apply_plans (target, before_json, after_json, rollback_kind, armed_until, status)
               VALUES (?, ?, ?, ?, ?, 'armed')""",
            (
                "network:eth0",
                '{"name":"eth0","method":"dhcp","address":null,"netmask":null,"gateway":null,"dns":null,"up":true}',
                '{"name":"eth0","method":"static","address":"10.0.0.7","netmask":"255.255.255.0","gateway":null,"dns":null,"up":true}',
                "net_interfaces",
                past,
            ),
        )
        plan_id = cur.lastrowid or 0
        await db.conn.commit()
        # Mutate net_interfaces to the after state (simulate the apply).
        await db.conn.execute(
            "UPDATE net_interfaces SET method=?, address=?, netmask=? WHERE name=?",
            ("static", "10.0.0.7", "255.255.255.0", "eth0"),
        )
        await db.conn.commit()
        # Record a fresh heartbeat — the device is alive with the new config.
        from app.tasks import apply_state

        await apply_state.record_heartbeat(source="test_healthy")
        # Tick the watchdog — heartbeat_recent() should win, plan commits.
        wd = ApplyPlanWatchdog(interval_s=0.05)
        await wd._tick()  # type: ignore[attr-defined]
        cur = await db.conn.execute("SELECT status, committed_at FROM apply_plans WHERE id = ?", (plan_id,))
        row = await cur.fetchone()
        assert row is not None
        assert row[0] == "committed", f"plan should be committed, got {row[0]}"
        assert row[1] is not None

    asyncio.new_event_loop().run_until_complete(_seed_record_tick())


def test_watchdog_rolls_back_when_heartbeat_stale(client: object, tokens: dict[str, str]) -> None:
    """Arm a plan, leave the heartbeat stale (or missing), tick the
    watchdog — the plan must auto-roll-back, not commit."""
    admin = _h(tokens["admin"])
    client.get("/api/sysadmin/network", headers=admin)  # type: ignore[attr-defined]

    async def _seed_stale_tick() -> None:
        db = get_db()
        past = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(time.time() - 1))
        cur = await db.conn.execute(
            """INSERT INTO apply_plans (target, before_json, after_json, rollback_kind, armed_until, status)
               VALUES (?, ?, ?, ?, ?, 'armed')""",
            (
                "network:wlan0",
                '{"name":"wlan0","method":"dhcp","address":null,"netmask":null,"gateway":null,"dns":null,"up":true}',
                '{"name":"wlan0","method":"static","address":"1.2.3.5","netmask":"255.255.255.0","gateway":null,"dns":null,"up":true}',
                "net_interfaces",
                past,
            ),
        )
        plan_id = cur.lastrowid or 0
        await db.conn.commit()
        await db.conn.execute(
            "UPDATE net_interfaces SET method=?, address=?, netmask=? WHERE name=?",
            ("static", "1.2.3.5", "255.255.255.0", "wlan0"),
        )
        await db.conn.commit()
        # Force the heartbeat to be old enough to look "stale": write a
        # timestamp well past HEARTBEAT_STALE_S (1h) ago.
        long_ago = time.strftime(
            "%Y-%m-%dT%H:%M:%S",
            time.gmtime(time.time() - (3600 + 60)),
        )
        await db.conn.execute(
            "UPDATE system_heartbeat SET last_seen_at = ? WHERE id = 1",
            (long_ago,),
        )
        await db.conn.commit()
        # Tick the watchdog — heartbeat_recent() must be False, plan rolls back.
        wd = ApplyPlanWatchdog(interval_s=0.05)
        await wd._tick()  # type: ignore[attr-defined]
        cur = await db.conn.execute("SELECT status, rolled_back_at FROM apply_plans WHERE id = ?", (plan_id,))
        row = await cur.fetchone()
        assert row is not None
        assert row[0] == "rolled_back", f"plan should be rolled back, got {row[0]}"
        assert row[1] is not None

    asyncio.new_event_loop().run_until_complete(_seed_stale_tick())
