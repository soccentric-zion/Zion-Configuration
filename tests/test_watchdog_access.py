"""Regression test for watchdog access reporting (#49).

``ThermalHal.watchdog`` used to only stat the device path, so a
locked-down device (file exists but no write permission) looked
identical to a fully accessible one. Fix: actually open() the
device O_RDWR | O_NONBLOCK and report ``accessible`` + any
``access_error`` so the SPA can distinguish them.
"""

from __future__ import annotations

from app.hal.thermal import ThermalHal


def test_watchdog_reports_accessible_when_open_works(monkeypatch) -> None:
    """If /dev/watchdog opens successfully, accessible=True and no error (#49)."""
    hal = ThermalHal(force_sim=True)
    # /dev/watchdog typically does not exist in test environments, so
    # we patch the helpers directly.
    monkeypatch.setattr("app.hal.base.path_exists", lambda p: True)

    opened: list[str] = []

    class _FakeFd:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_open(path, flags):
        opened.append(path)
        return 7  # arbitrary fd

    def fake_close(fd):
        opened.append(f"close:{fd}")

    monkeypatch.setattr("os.open", fake_open)
    monkeypatch.setattr("os.close", fake_close)

    body = hal.watchdog()
    inner = body["watchdog"] if isinstance(body.get("watchdog"), dict) else body
    assert inner["present"] is True
    assert inner["accessible"] is True
    assert inner["access_error"] is None
    assert opened == ["/dev/watchdog", "close:7"]


def test_watchdog_reports_permission_error(monkeypatch) -> None:
    """If os.open raises PermissionError, accessible=False and access_error set (#49)."""
    hal = ThermalHal(force_sim=True)
    monkeypatch.setattr("app.hal.base.path_exists", lambda p: True)

    def fake_open(path, flags):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr("os.open", fake_open)

    body = hal.watchdog()
    inner = body["watchdog"] if isinstance(body.get("watchdog"), dict) else body
    assert inner["present"] is True
    assert inner["accessible"] is False
    assert inner["access_error"] is not None
    assert "Permission" in inner["access_error"]


def test_watchdog_absent_when_device_missing(monkeypatch) -> None:
    """If /dev/watchdog is missing, present=False and accessible=False (#49)."""
    hal = ThermalHal(force_sim=True)
    monkeypatch.setattr("app.hal.base.path_exists", lambda p: False)

    body = hal.watchdog()
    inner = body["watchdog"] if isinstance(body.get("watchdog"), dict) else body
    assert inner["present"] is False
    assert inner["accessible"] is False
    assert inner["access_error"] is None
    assert inner["type"] == "software"
