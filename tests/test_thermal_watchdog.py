"""Regression test for #129 — PUT /api/thermal/watchdog (audit §1).

audit.md §1 System: "watchdog config" (timeout, kick interval). The
previous implementation had ``GET /api/thermal/watchdog`` (read
state) and ``POST /api/thermal/watchdog/test`` (kick the watchdog
once) but no write endpoint to configure the timeout / kick
interval / nowayout. Operators had to edit the device's watchdog
config out-of-band, leaving the audit log without a record of the
change.

The fix:
- ``PUT /api/thermal/watchdog`` (admin) accepts the
  ``WatchdogConfigUpdate`` pydantic model
  (``timeout_s``, ``kick_interval_s``, ``nowayout``).
- Values are persisted in ``system_settings_kv`` (per #42), and the
  GET endpoint overlays them on the live HAL envelope.
- Privileged write — ``confirm: true`` is required (428 without).
"""

from __future__ import annotations


def _h(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_watchdog_config_persists(client: object, tokens: dict[str, str]) -> None:
    """PUT writes the new config; the next GET reflects it (#129)."""
    admin = _h(tokens["admin"])
    resp = client.put(  # type: ignore[attr-defined]
        "/api/thermal/watchdog",
        json={
            "timeout_s": 45,
            "kick_interval_s": 15,
            "nowayout": True,
            "confirm": True,
        },
        headers=admin,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    assert "timeout_s=45" in body["applied"]
    assert "kick_interval_s=15" in body["applied"]

    listing = client.get("/api/thermal/watchdog", headers=admin)  # type: ignore[attr-defined]
    assert listing.status_code == 200
    payload = listing.json()
    assert payload["timeout_s"] == 45
    assert payload["kick_interval_s"] == 15
    assert payload["nowayout"] is True


def test_watchdog_config_rejects_zero_timeout(client: object, tokens: dict[str, str]) -> None:
    """``timeout_s`` is bounded [1, 600] — 0 is rejected with 422."""
    admin = _h(tokens["admin"])
    resp = client.put(  # type: ignore[attr-defined]
        "/api/thermal/watchdog",
        json={"timeout_s": 0, "kick_interval_s": 5, "nowayout": False, "confirm": True},
        headers=admin,
    )
    assert resp.status_code == 422, resp.text


def test_watchdog_config_rejects_oversized_timeout(client: object, tokens: dict[str, str]) -> None:
    """``timeout_s > 600`` is rejected with 422."""
    admin = _h(tokens["admin"])
    resp = client.put(  # type: ignore[attr-defined]
        "/api/thermal/watchdog",
        json={"timeout_s": 1000, "kick_interval_s": 5, "nowayout": False, "confirm": True},
        headers=admin,
    )
    assert resp.status_code == 422, resp.text


def test_watchdog_config_requires_confirm(client: object, tokens: dict[str, str]) -> None:
    """Privileged write — ``confirm: true`` is required (428)."""
    admin = _h(tokens["admin"])
    resp = client.put(  # type: ignore[attr-defined]
        "/api/thermal/watchdog",
        json={"timeout_s": 30, "kick_interval_s": 10, "nowayout": False},
        headers=admin,
    )
    assert resp.status_code == 428, resp.text


def test_watchdog_config_rejects_extra_field(client: object, tokens: dict[str, str]) -> None:
    """``extra="forbid"`` rejects typos like ``timeout`` (no ``_s``)."""
    admin = _h(tokens["admin"])
    resp = client.put(  # type: ignore[attr-defined]
        "/api/thermal/watchdog",
        json={"timeout": 30, "kick_interval_s": 10, "nowayout": False, "confirm": True},
        headers=admin,
    )
    assert resp.status_code == 422, resp.text


def test_watchdog_config_requires_admin(client: object, tokens: dict[str, str]) -> None:
    """A viewer cannot configure the watchdog."""
    viewer = _h(tokens["viewer"])
    resp = client.put(  # type: ignore[attr-defined]
        "/api/thermal/watchdog",
        json={"timeout_s": 30, "kick_interval_s": 10, "nowayout": False, "confirm": True},
        headers=viewer,
    )
    assert resp.status_code == 403, resp.text
