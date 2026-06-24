"""Regression test for OTA install exception logging (#47).

The except-block in _run_install used to swallow the exception
without logging the traceback. The WS message also leaked
str(exc) which can include sensitive context. The fix calls
_log.exception() and sends a generic message to the WS channel.
"""

from __future__ import annotations

import logging

from app.tasks import ota


class _BoomError(RuntimeError):
    pass


async def test_run_install_logs_traceback_and_does_not_leak_exc_message(monkeypatch, caplog) -> None:
    """An exception inside _run_install must be logged with traceback,
    and the WS broadcast must not include str(exc) (which could leak
    secrets) (#47).
    """
    broadcasts: list[dict[str, object]] = []

    async def fake_broadcast(job_id: str, pct: int, status: str, message: str) -> None:
        broadcasts.append({"job_id": job_id, "pct": pct, "status": status, "message": message})

    async def boom(*args, **kwargs):
        raise _BoomError("secret-token-in-message")

    class _StubDB:
        async def execute(self, *args, **kwargs):
            return None

    def stub_get_db():
        return _StubDB()

    monkeypatch.setattr(ota, "_broadcast", fake_broadcast)
    monkeypatch.setattr(ota, "_try_real_swupdate", boom)
    monkeypatch.setattr(ota, "_simulate_progress", boom)
    # The function writes ota_history after the try/except; stub it out
    # so we don't need a live DB connection.
    monkeypatch.setattr(ota, "get_db", stub_get_db)

    caplog.set_level(logging.ERROR, logger="tasks.ota")
    await ota._run_install("job-test-1", "firmware.swu", b"abc", username="admin")

    # At least one broadcast must carry status="failed" and a generic
    # message that does NOT include str(exc).
    failed = [b for b in broadcasts if b["status"] == "failed"]
    assert failed, "expected a 'failed' status broadcast on exception"
    for b in failed:
        assert "secret-token-in-message" not in str(b["message"]), f"WS broadcast leaks str(exc): {b['message']!r}"
        assert b["pct"] == 0

    # And the logger must have captured an ERROR with the traceback.
    err_records = [r for r in caplog.records if r.levelno >= logging.ERROR and r.name == "tasks.ota"]
    assert err_records, "tasks.ota logger did not capture the exception"
    assert any("OTA install failed" in r.getMessage() for r in err_records), "expected log message 'OTA install failed'"
    # The traceback must be present on the log record (exc_info is set by
    # _log.exception).
    assert any(r.exc_info is not None for r in err_records), "_log.exception should attach exc_info to the log record"
