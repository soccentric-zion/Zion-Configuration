"""Tests for #150 — id DESC tiebreak on second-granular ts.

Reference issue: #150 — add id DESC tiebreak to ota_history ORDER BY ts.

SQLite's ``datetime('now')`` is second-granular, so multiple rows written
in the same second returned out-of-order (older rows first) when only
``ORDER BY ts DESC`` was used. Adding ``id DESC`` as the secondary sort
key ensures the most recently inserted row wins on a tie.
"""

from __future__ import annotations

import asyncio


def test_ota_history_orders_by_id_desc_with_same_second(client: object, tokens: dict[str, str]) -> None:
    """Insert 5 OTA history rows in the same second; the newest is at index 0."""
    from app.db.database import get_db

    db = get_db()
    shared_ts = "2026-01-01 00:00:00"

    async def insert_all() -> None:
        for i in range(5):
            await db.execute(
                "INSERT INTO ota_history (ts, version, result, triggered_by) " "VALUES (?, ?, 'queued', 'test')",
                (shared_ts, f"v-test-{i}"),
            )

    asyncio.run(insert_all())

    r = client.get(  # type: ignore[attr-defined]
        "/api/ota/history",
        headers={"Authorization": f"Bearer {tokens['admin']}"},
    )
    assert r.status_code == 200, r.text
    history = r.json()["history"]
    # Filter to our 5 rows.
    ours = [h for h in history if h["version"].startswith("v-test-")]
    assert len(ours) == 5
    # The newest insertion (v-test-4) must be first; the oldest (v-test-0) last.
    assert ours[0]["version"] == "v-test-4"
    assert ours[1]["version"] == "v-test-3"
    assert ours[2]["version"] == "v-test-2"
    assert ours[3]["version"] == "v-test-1"
    assert ours[4]["version"] == "v-test-0"
