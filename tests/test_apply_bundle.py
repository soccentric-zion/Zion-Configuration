"""Tests for apply-plan bundles (#99).

audit.md "Backend" section requires atomic multi-section staging.
The bundle helpers in ``app/tasks/apply_state.py`` group plans under
a shared ``bundle_id`` and ``armed_until``; the router endpoints
create / commit / roll back such bundles.
"""

from __future__ import annotations

import asyncio
import time

from app.db.database import get_db
from app.tasks.apply_state import ApplyPlanWatchdog


def _h(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_bundle_arms_multiple_plans_atomically(client: object, tokens: dict[str, str]) -> None:
    admin = _h(tokens["admin"])
    client.get("/api/sysadmin/network", headers=admin)  # type: ignore[attr-defined]  # seed
    resp = client.post(  # type: ignore[attr-defined]
        "/api/apply/bundle",
        json={
            "items": [
                {
                    "target": "network:eth0",
                    "before": {
                        "name": "eth0",
                        "method": "dhcp",
                        "address": None,
                        "netmask": None,
                        "gateway": None,
                        "dns": None,
                        "up": True,
                    },
                    "after": {
                        "name": "eth0",
                        "method": "static",
                        "address": "10.0.0.50",
                        "netmask": "255.255.255.0",
                        "gateway": None,
                        "dns": None,
                        "up": True,
                    },
                    "rollback_kind": "net_interfaces",
                },
                {
                    "target": "network:wlan0",
                    "before": {
                        "name": "wlan0",
                        "method": "dhcp",
                        "address": None,
                        "netmask": None,
                        "gateway": None,
                        "dns": None,
                        "up": True,
                    },
                    "after": {
                        "name": "wlan0",
                        "method": "static",
                        "address": "192.168.50.10",
                        "netmask": "255.255.255.0",
                        "gateway": None,
                        "dns": None,
                        "up": True,
                    },
                    "rollback_kind": "net_interfaces",
                },
            ],
            "confirm": True,
        },
        headers=admin,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    assert body["bundle_id"]
    assert len(body["plan_ids"]) == 2
    # GET bundle returns both plans
    resp = client.get(f"/api/apply/bundle/{body['bundle_id']}", headers=admin)  # type: ignore[attr-defined]
    assert resp.status_code == 200
    plans = resp.json()["plans"]
    assert len(plans) == 2
    assert {p["target"] for p in plans} == {"network:eth0", "network:wlan0"}


def test_bundle_commit_marks_all_plans_committed(client: object, tokens: dict[str, str]) -> None:
    admin = _h(tokens["admin"])
    client.get("/api/sysadmin/network", headers=admin)  # type: ignore[attr-defined]
    arm = client.post(  # type: ignore[attr-defined]
        "/api/apply/bundle",
        json={
            "items": [
                {
                    "target": "network:eth0",
                    "before": {
                        "name": "eth0",
                        "method": "dhcp",
                        "address": None,
                        "netmask": None,
                        "gateway": None,
                        "dns": None,
                        "up": True,
                    },
                    "after": {
                        "name": "eth0",
                        "method": "static",
                        "address": "10.0.0.51",
                        "netmask": "255.255.255.0",
                        "gateway": None,
                        "dns": None,
                        "up": True,
                    },
                    "rollback_kind": "net_interfaces",
                }
            ],
            "confirm": True,
        },
        headers=admin,
    )
    bid = arm.json()["bundle_id"]
    resp = client.post(  # type: ignore[attr-defined]
        f"/api/apply/bundle/{bid}/commit", json={"confirm": True}, headers=admin
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["committed"]
    # Read back — every plan in the bundle is committed
    resp = client.get(f"/api/apply/bundle/{bid}", headers=admin)  # type: ignore[attr-defined]
    statuses = {p["status"] for p in resp.json()["plans"]}
    assert statuses == {"committed"}


def test_bundle_rollback_restores_every_plan(client: object, tokens: dict[str, str]) -> None:
    admin = _h(tokens["admin"])
    client.get("/api/sysadmin/network", headers=admin)  # type: ignore[attr-defined]
    arm = client.post(  # type: ignore[attr-defined]
        "/api/apply/bundle",
        json={
            "items": [
                {
                    "target": "network:eth0",
                    "before": {
                        "name": "eth0",
                        "method": "dhcp",
                        "address": None,
                        "netmask": None,
                        "gateway": None,
                        "dns": None,
                        "up": True,
                    },
                    "after": {
                        "name": "eth0",
                        "method": "static",
                        "address": "10.0.0.52",
                        "netmask": "255.255.255.0",
                        "gateway": None,
                        "dns": None,
                        "up": True,
                    },
                    "rollback_kind": "net_interfaces",
                },
                {
                    "target": "network:wlan0",
                    "before": {
                        "name": "wlan0",
                        "method": "dhcp",
                        "address": None,
                        "netmask": None,
                        "gateway": None,
                        "dns": None,
                        "up": True,
                    },
                    "after": {
                        "name": "wlan0",
                        "method": "static",
                        "address": "192.168.50.12",
                        "netmask": "255.255.255.0",
                        "gateway": None,
                        "dns": None,
                        "up": True,
                    },
                    "rollback_kind": "net_interfaces",
                },
            ],
            "confirm": True,
        },
        headers=admin,
    )
    bid = arm.json()["bundle_id"]
    resp = client.post(  # type: ignore[attr-defined]
        f"/api/apply/bundle/{bid}/rollback", json={"confirm": True}, headers=admin
    )
    assert resp.status_code == 200, resp.text
    # Both interfaces are back to dhcp
    resp = client.get("/api/sysadmin/network", headers=admin)  # type: ignore[attr-defined]
    ifaces = {i["name"]: i for i in resp.json()["interfaces"]}
    assert ifaces["eth0"]["method"] == "dhcp"
    assert ifaces["wlan0"]["method"] == "dhcp"


def test_empty_bundle_is_rejected(client: object, tokens: dict[str, str]) -> None:
    admin = _h(tokens["admin"])
    resp = client.post(  # type: ignore[attr-defined]
        "/api/apply/bundle",
        json={"items": [], "confirm": True},
        headers=admin,
    )
    # 422 from pydantic min_length=1
    assert resp.status_code == 422


def test_bundle_requires_confirm(client: object, tokens: dict[str, str]) -> None:
    admin = _h(tokens["admin"])
    resp = client.post(  # type: ignore[attr-defined]
        "/api/apply/bundle",
        json={
            "items": [
                {
                    "target": "network:eth0",
                    "before": {
                        "name": "eth0",
                        "method": "dhcp",
                        "address": None,
                        "netmask": None,
                        "gateway": None,
                        "dns": None,
                        "up": True,
                    },
                    "after": {
                        "name": "eth0",
                        "method": "static",
                        "address": "10.0.0.53",
                        "netmask": "255.255.255.0",
                        "gateway": None,
                        "dns": None,
                        "up": True,
                    },
                    "rollback_kind": "net_interfaces",
                }
            ]
        },
        headers=admin,
    )
    assert resp.status_code == 428


def test_watchdog_rolls_back_whole_bundle_on_stale_heartbeat(client: object, tokens: dict[str, str]) -> None:
    """If the watchdog expires a bundle (heartbeat stale), every plan
    in the bundle is rolled back (#99)."""
    admin = _h(tokens["admin"])
    client.get("/api/sysadmin/network", headers=admin)  # type: ignore[attr-defined]

    async def _seed_and_tick() -> None:
        db = get_db()
        bid = "test-bundle-deadbeef"
        past = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(time.time() - 1))
        # Two plans under the same bundle, both armed and past their
        # armed_until.
        for tgt, after_addr in (("network:eth0", "10.0.0.60"), ("network:wlan0", "192.168.60.10")):
            await db.conn.execute(
                """INSERT INTO apply_plans
                   (target, before_json, after_json, rollback_kind, armed_until, status, bundle_id)
                   VALUES (?, ?, ?, ?, ?, 'armed', ?)""",
                (
                    f"network:{tgt.split(':')[1]}",
                    '{"name":"'
                    + tgt.split(":")[1]
                    + '","method":"dhcp","address":null,"netmask":null,"gateway":null,"dns":null,"up":true}',
                    '{"name":"'
                    + tgt.split(":")[1]
                    + '","method":"static","address":"'
                    + after_addr
                    + '","netmask":"255.255.255.0","gateway":null,"dns":null,"up":true}',
                    "net_interfaces",
                    past,
                    bid,
                ),
            )
        await db.conn.commit()
        await db.conn.execute(
            "UPDATE net_interfaces SET method='static',"
            " address='10.0.0.60', netmask='255.255.255.0' WHERE name='eth0'"
        )
        await db.conn.execute(
            "UPDATE net_interfaces SET method='static',"
            " address='192.168.60.10', netmask='255.255.255.0' WHERE name='wlan0'"
        )
        await db.conn.commit()
        # Push the heartbeat back so the watchdog treats this as
        # "device never came back" — same pattern as #93.
        long_ago = time.strftime(
            "%Y-%m-%dT%H:%M:%S",
            time.gmtime(time.time() - (3600 + 60)),
        )
        await db.conn.execute(
            "UPDATE system_heartbeat SET last_seen_at = ? WHERE id = 1",
            (long_ago,),
        )
        await db.conn.commit()
        wd = ApplyPlanWatchdog(interval_s=0.05)
        await wd._tick()  # type: ignore[attr-defined]
        # Both plans must be rolled back.
        cur = await db.conn.execute("SELECT target, status FROM apply_plans WHERE bundle_id = ?", (bid,))
        rows = await cur.fetchall()
        statuses = {row[0]: row[1] for row in rows}
        assert statuses == {
            "network:eth0": "rolled_back",
            "network:wlan0": "rolled_back",
        }, statuses

    asyncio.new_event_loop().run_until_complete(_seed_and_tick())
