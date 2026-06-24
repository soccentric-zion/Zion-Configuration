"""aiosqlite connection wrapper with WAL mode, migrations and helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import aiosqlite

from app.config import get_settings
from app.db.migrations import migrate


class Database:
    """Thin async wrapper around a single aiosqlite connection.

    The dashboard is a single process with modest write volume, so one shared
    connection in WAL mode is sufficient and keeps the code simple. All queries
    return ``aiosqlite.Row`` so callers can use dict-style access.
    """

    def __init__(self, path: str) -> None:
        self._path = path
        self._conn: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self._path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA foreign_keys=ON")
        await migrate(self._conn)

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("Database not connected")
        return self._conn

    async def execute(self, sql: str, params: tuple[Any, ...] = ()) -> int:
        """Run a write statement, commit, and return lastrowid."""
        cur = await self.conn.execute(sql, params)
        await self.conn.commit()
        return cur.lastrowid or 0

    async def fetch_all(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        cur = await self.conn.execute(sql, params)
        rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def fetch_one(self, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        cur = await self.conn.execute(sql, params)
        row = await cur.fetchone()
        return dict(row) if row else None

    async def db_size_bytes(self) -> int:
        p = Path(self._path)
        return p.stat().st_size if p.exists() else 0


# --- module-level singleton, wired up in app.main lifespan -----------------
_db: Database | None = None


def set_db(db: Database) -> None:
    global _db
    _db = db


def get_db() -> Database:
    """FastAPI dependency / accessor for the shared Database instance."""
    if _db is None:
        raise RuntimeError("Database not initialised")
    return _db


async def init_db() -> Database:
    db = Database(get_settings().database.path)
    await db.connect()
    set_db(db)
    return db
