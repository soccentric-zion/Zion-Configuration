"""Integration test for the alert engine against a real (temp) database."""

from __future__ import annotations

import pytest

from app.db import database
from app.db.database import Database, set_db
from app.tasks.alerts import Sample, engine


@pytest.fixture
async def temp_db(tmp_path: object) -> object:
    previous = database._db  # restore afterwards so the shared client DB is intact
    db = Database(str(tmp_path / "alerts.db"))  # type: ignore[operator]
    await db.connect()
    set_db(db)
    try:
        yield db
    finally:
        await db.close()
        database._db = previous


async def test_engine_opens_and_resolves_alert(temp_db: Database) -> None:
    # A rule that fires when cpu_temp_c > 50.
    rid = await temp_db.execute(
        """INSERT INTO alert_rules (name, metric, condition, threshold, duration_s, severity, enabled)
           VALUES ('hot', 'cpu_temp_c', '>', 50, 0, 'critical', 1)""",
    )
    # A breaching sample -> the engine must open a firing alert.
    await temp_db.execute("INSERT INTO metric_history (metric, value) VALUES ('cpu_temp_c', 90)")
    await engine.evaluate_once()
    firing = await temp_db.fetch_one("SELECT * FROM alerts WHERE rule_id = ? AND state = 'firing'", (rid,))
    assert firing is not None

    # A subsequent normal sample -> the engine resolves the open alert.
    await temp_db.execute("INSERT INTO metric_history (metric, value) VALUES ('cpu_temp_c', 10)")
    await engine.evaluate_once()
    still_firing = await temp_db.fetch_one("SELECT * FROM alerts WHERE rule_id = ? AND state = 'firing'", (rid,))
    assert still_firing is None


async def test_recent_samples_returns_more_than_60_for_long_window(
    temp_db: Database,
) -> None:
    """Fix #30: the old hardcoded LIMIT 60 capped duration-based rules at
    10 minutes. A 1-hour window must return at least window_s/interval samples
    (plus a small margin), not the 60-row cap."""
    # 120 samples in 1 hour at 30s spacing — well above the 60-row cap.
    for i in range(120):
        # epoch=1_000_000_000 + i*30, value=10
        await temp_db.execute(
            "INSERT INTO metric_history (metric, value, ts) VALUES ('long_metric', 10, datetime(?, 'unixepoch'))",
            (1_000_000_000 + i * 30,),
        )
    samples = await engine._recent_samples("long_metric", 3600)
    # Old code: 60. New code: 3600/30 + 4 = 124 (but we only have 120, so 120).
    assert len(samples) == 120, f"expected 120, got {len(samples)}"
    assert all(isinstance(s, Sample) for s in samples)
