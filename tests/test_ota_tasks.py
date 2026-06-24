"""Regression tests for the OTA task-tracking fix (issue #32).

`start_install` used to discard the asyncio.Task returned by
`asyncio.create_task`, so any unhandled exception inside `_run_install` was
silently dropped ("Task exception was never retrieved") and shutdown could
not await in-flight installs. The fix keeps a module-level set of tasks,
attaches a done-callback that logs exceptions, and exposes `drain_inflight`
for the lifespan shutdown.
"""

from __future__ import annotations

import asyncio

import pytest

from app.tasks import ota


@pytest.fixture(autouse=True)
def _clear_inflight() -> object:
    """Make sure no leftover tasks from previous tests influence these."""
    ota._inflight_installs.clear()
    yield
    # Drain anything created during the test so the loop is clean for the next.
    asyncio.run(ota.drain_inflight(timeout_s=2.0))


async def test_start_install_tracks_task(monkeypatch: pytest.MonkeyPatch) -> None:
    """The task created by start_install is retained in _inflight_installs."""

    async def fake_run_install(job_id: str, filename: str, data: bytes, username: str) -> None:
        # Long enough that the test can observe the in-flight set.
        await asyncio.sleep(0.05)

    monkeypatch.setattr(ota, "_run_install", fake_run_install)

    job_id = await ota.start_install("fw.swu", b"data", "tester")
    assert job_id
    # At least one in-flight task.
    assert any(not t.done() for t in ota._inflight_installs)

    await ota.drain_inflight(timeout_s=2.0)
    # After drain, every task is done and has been removed from the set.
    assert all(t.done() for t in ota._inflight_installs)
    assert ota._inflight_installs == set()


async def test_done_callback_logs_unhandled_exception(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A task that raises is logged through the standard logger."""

    async def exploding_install(job_id: str, filename: str, data: bytes, username: str) -> None:
        raise RuntimeError("synthetic OTA failure")

    monkeypatch.setattr(ota, "_run_install", exploding_install)

    with caplog.at_level("ERROR", logger="tasks.ota"):
        await ota.start_install("fw.swu", b"data", "tester")
        await ota.drain_inflight(timeout_s=2.0)

    # The done-callback should have fired and recorded the failure.
    assert any("synthetic OTA failure" in rec.message for rec in caplog.records)


async def test_drain_inflight_returns_quickly_when_empty() -> None:
    """drain_inflight on an empty set is a no-op (no hang)."""
    assert ota._inflight_installs == set()
    await ota.drain_inflight(timeout_s=0.5)  # would TimeoutError if it blocked
