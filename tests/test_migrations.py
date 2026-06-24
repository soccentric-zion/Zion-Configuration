"""Schema migrations apply once and are idempotent on re-run."""

from __future__ import annotations

import aiosqlite
import pytest

from app.db.migrations import MIGRATIONS, migrate


@pytest.mark.asyncio
async def test_migrate_is_idempotent() -> None:
    async with aiosqlite.connect(":memory:") as conn:
        conn.row_factory = aiosqlite.Row
        latest = MIGRATIONS[-1][0]

        first = await migrate(conn)
        assert first == latest

        second = await migrate(conn)
        assert second == latest

        # schema_version must record each migration exactly once.
        cur = await conn.execute("SELECT COUNT(*) FROM schema_version")
        count = (await cur.fetchone())[0]
        assert count == len(MIGRATIONS)


@pytest.mark.asyncio
async def test_core_tables_exist() -> None:
    async with aiosqlite.connect(":memory:") as conn:
        conn.row_factory = aiosqlite.Row
        await migrate(conn)
        cur = await conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {r["name"] for r in await cur.fetchall()}
        for expected in ("users", "audit_log", "login_attempts", "alert_rules", "alerts"):
            assert expected in tables
