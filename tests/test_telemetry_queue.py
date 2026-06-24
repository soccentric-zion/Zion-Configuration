"""Regression tests for telemetry_queue writes (#44).

The table was created in migration v1 but never written to, so the
``GET /api/cloud/telemetry`` ``queue_depth`` was always 0 regardless
of how many samples the dashboard processed. Fix: the sampler now
INSERTs each persisted metric into telemetry_queue as well, and a
``POST /api/cloud/telemetry/flush`` endpoint marks them sent.
"""

from __future__ import annotations

from app.db.database import get_db
from app.tasks.sampler import Sampler


def _admin(tokens: dict[str, str]) -> dict[str, str]:
    return {"Authorization": f"Bearer {tokens['admin']}"}


async def test_telemetry_queue_starts_empty(client: object, tokens: dict[str, str]) -> None:
    h = _admin(tokens)
    # Drain first to get a clean baseline (the lifespan sampler may have
    # already written some rows in CI).
    db = get_db()
    await db.execute("DELETE FROM telemetry_queue")
    await db.conn.commit()

    resp = client.get("/api/cloud/telemetry", headers=h)  # type: ignore[attr-defined]
    assert resp.status_code == 200
    body = resp.json()
    assert body["queue_depth"] == 0
    assert body["sent_total"] == 0
    assert body["last_sync"] is None


async def test_sampler_writes_to_telemetry_queue(client: object, tokens: dict[str, str]) -> None:
    # The sampler is a singleton that runs in the lifespan, so we can
    # call _persist() directly to avoid racing the loop. The same rows
    # that go to metric_history must also enqueue for telemetry (#44).
    h = _admin(tokens)
    # Drain anything that the running sampler wrote (a fresh test DB has
    # nothing, but in CI the lifespan sampler may have produced a few rows
    # already — start from a clean baseline).
    db = get_db()
    await db.execute("DELETE FROM telemetry_queue")
    await db.conn.commit()

    sampler = Sampler()
    await sampler._persist({"cpu_total": 12.3, "mem_percent": 45.0, "cpu_temp_c": 50.0})  # type: ignore[attr-defined]

    resp = client.get("/api/cloud/telemetry", headers=h)  # type: ignore[attr-defined]
    assert resp.status_code == 200
    body = resp.json()
    # 4 metrics per persist call (cpu_total, mem_percent, cpu_temp_c, cpu_freq_mhz)
    assert body["queue_depth"] >= 4, f"expected queue_depth >= 4, got {body['queue_depth']}"
    assert body["last_sync"] is not None


async def test_flush_marks_queue_rows_as_sent(client: object, tokens: dict[str, str]) -> None:
    h = _admin(tokens)
    # Seed an unsent row directly to avoid racing the background sampler.
    db = get_db()
    await db.execute("DELETE FROM telemetry_queue")
    await db.conn.commit()
    await db.execute(
        "INSERT INTO telemetry_queue (metric, value, sent) VALUES (?, ?, 0)",
        ("cpu_total", 99.9),
    )
    await db.conn.commit()

    pre = client.get("/api/cloud/telemetry", headers=h)  # type: ignore[attr-defined]
    assert pre.json()["queue_depth"] == 1

    flush = client.post("/api/cloud/telemetry/flush", headers=h)  # type: ignore[attr-defined]
    assert flush.status_code == 200
    assert flush.json()["flushed"] == 1

    post = client.get("/api/cloud/telemetry", headers=h)  # type: ignore[attr-defined]
    body = post.json()
    assert body["queue_depth"] == 0
    assert body["sent_total"] == 1
