"""Tests for the apply-plan rollback machinery (req.md: "never leave an
interface or firewall in a half-applied state").

The end-to-end flow is exercised through the ``/api/settings/network/{iface}``
endpoints. The watchdog's ``_tick`` is invoked directly to avoid
wall-clock waits.
"""

from __future__ import annotations

import time

from app.db.database import get_db
from app.tasks.apply_state import ApplyPlanWatchdog


def _h(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# --- end-to-end: set_network + commit + rollback --------------------------
def test_set_network_arms_plan_and_commit_keeps_state(client: object, tokens: dict[str, str]) -> None:
    admin = _h(tokens["admin"])
    resp = client.post(  # type: ignore[attr-defined]
        "/api/settings/network/eth0",
        json={"method": "static", "address": "10.0.0.5", "netmask": "255.255.255.0", "confirm": True},
        headers=admin,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    plan_id = body["plan_id"]
    assert body["armed_until"]
    # commit
    resp = client.post(  # type: ignore[attr-defined]
        "/api/settings/network/eth0/apply/confirm", json={"plan_id": plan_id, "confirm": True}, headers=admin
    )
    assert resp.status_code == 200
    # row still has the new config
    resp = client.get("/api/sysadmin/network", headers=admin)  # type: ignore[attr-defined]
    ifaces = {i["name"]: i for i in resp.json()["interfaces"]}
    assert ifaces["eth0"]["method"] == "static"
    assert ifaces["eth0"]["address"] == "10.0.0.5"


def test_set_network_then_rollback_restores_previous(client: object, tokens: dict[str, str]) -> None:
    admin = _h(tokens["admin"])
    # First set a static address on wlan0
    resp = client.post(  # type: ignore[attr-defined]
        "/api/settings/network/wlan0",
        json={"method": "static", "address": "192.168.99.10", "netmask": "255.255.255.0", "confirm": True},
        headers=admin,
    )
    assert resp.status_code == 200
    plan_id = resp.json()["plan_id"]
    # now rollback
    resp = client.post(  # type: ignore[attr-defined]
        "/api/settings/network/wlan0/apply/rollback", json={"plan_id": plan_id, "confirm": True}, headers=admin
    )
    assert resp.status_code == 200
    # wlan0 should be back to dhcp
    resp = client.get("/api/sysadmin/network", headers=admin)  # type: ignore[attr-defined]
    ifaces = {i["name"]: i for i in resp.json()["interfaces"]}
    assert ifaces["wlan0"]["method"] == "dhcp"


def test_set_network_validates_static_inputs(client: object, tokens: dict[str, str]) -> None:
    admin = _h(tokens["admin"])
    # Bad IP -> 422 (pydantic validation -> FastAPI's RequestValidationError handler)
    resp = client.post(  # type: ignore[attr-defined]
        "/api/settings/network/eth0",
        json={"method": "static", "address": "not-an-ip", "netmask": "255.255.255.0", "confirm": True},
        headers=admin,
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"
    # Bad netmask -> 422
    resp = client.post(  # type: ignore[attr-defined]
        "/api/settings/network/eth0",
        json={"method": "static", "address": "10.0.0.1", "netmask": "999.999.999.999", "confirm": True},
        headers=admin,
    )
    assert resp.status_code == 422
    # Bad rollback_armed_s (out of range) -> 422
    resp = client.post(  # type: ignore[attr-defined]
        "/api/settings/network/eth0",
        json={"method": "dhcp", "confirm": True, "rollback_armed_s": 100_000},
        headers=admin,
    )
    assert resp.status_code == 422
    # Static with missing address -> 400 (our own check, runs after pydantic)
    resp = client.post(  # type: ignore[attr-defined]
        "/api/settings/network/eth0",
        json={"method": "static", "netmask": "255.255.255.0", "confirm": True},
        headers=admin,
    )
    assert resp.status_code == 400


def test_set_network_double_commit_rejected(client: object, tokens: dict[str, str]) -> None:
    admin = _h(tokens["admin"])
    resp = client.post(  # type: ignore[attr-defined]
        "/api/settings/network/eth0",
        json={"method": "static", "address": "10.0.0.6", "netmask": "255.255.255.0", "confirm": True},
        headers=admin,
    )
    assert resp.status_code == 200
    plan_id = resp.json()["plan_id"]
    # first commit
    resp = client.post(  # type: ignore[attr-defined]
        "/api/settings/network/eth0/apply/confirm", json={"plan_id": plan_id, "confirm": True}, headers=admin
    )
    assert resp.status_code == 200
    # second commit -> 409
    resp = client.post(  # type: ignore[attr-defined]
        "/api/settings/network/eth0/apply/confirm", json={"plan_id": plan_id, "confirm": True}, headers=admin
    )
    assert resp.status_code == 409


# --- watchdog tick: expires an already-armed plan in the past -------------
def test_watchdog_rolls_back_expired_plans(client: object, tokens: dict[str, str]) -> None:
    """Directly insert an armed plan with armed_until in the past, mutate
    net_interfaces to its after-state, run one watchdog tick, and assert
    the row is restored and the plan is marked rolled_back."""
    import asyncio

    async def _seed_and_tick() -> None:
        db = get_db()
        past = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(time.time() - 1))
        cur = await db.conn.execute(
            """INSERT INTO apply_plans (target, before_json, after_json, rollback_kind, armed_until, status)
               VALUES (?, ?, ?, ?, ?, 'armed')""",
            (
                "network:wlan0",
                '{"name":"wlan0","method":"dhcp","address":null,"netmask":null,"gateway":null,"dns":null,"up":true}',
                '{"name":"wlan0","method":"static","address":"1.2.3.4","netmask":"255.255.255.0","gateway":null,"dns":null,"up":true}',
                "net_interfaces",
                past,
            ),
        )
        plan_id = cur.lastrowid or 0
        await db.conn.commit()
        # Simulate the apply having happened: row currently has the "after" state.
        await db.conn.execute(
            "UPDATE net_interfaces SET method=?, address=?, netmask=? WHERE name=?",
            ("static", "1.2.3.4", "255.255.255.0", "wlan0"),
        )
        await db.conn.commit()
        # Push the heartbeat back so the watchdog treats this as
        # "device never came back" and rolls back, not commits (#93).
        long_ago = time.strftime(
            "%Y-%m-%dT%H:%M:%S",
            time.gmtime(time.time() - (3600 + 60)),
        )
        await db.conn.execute(
            "UPDATE system_heartbeat SET last_seen_at = ? WHERE id = 1",
            (long_ago,),
        )
        await db.conn.commit()
        # Tick the watchdog.
        wd = ApplyPlanWatchdog(interval_s=0.05)
        await wd._tick()  # type: ignore[attr-defined]
        # Plan should be rolled back.
        cur = await db.conn.execute("SELECT status, rolled_back_at FROM apply_plans WHERE id = ?", (plan_id,))
        row = await cur.fetchone()
        assert row is not None
        assert row[0] == "rolled_back"
        assert row[1] is not None
        # Row should be restored to dhcp.
        cur = await db.conn.execute("SELECT method FROM net_interfaces WHERE name = ?", ("wlan0",))
        nrow = await cur.fetchone()
        assert nrow is not None and nrow[0] == "dhcp"

    admin = _h(tokens["admin"])
    # Ensure wlan0 row exists (sysadmin seeds it)
    client.get("/api/sysadmin/network", headers=admin)  # type: ignore[attr-defined]
    # Run in a fresh loop (DB singleton was initialized in TestClient's loop).
    asyncio.new_event_loop().run_until_complete(_seed_and_tick())
