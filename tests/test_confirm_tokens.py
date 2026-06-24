"""Tests for privileged-action confirmation (req.md critical requirements).

All destructive/lockout-capable endpoints must require explicit ``confirm: true``
in the request body. The SPA sends it after the user ticks a confirm box;
the backend enforces it server-side. The settings router endpoints
(``/api/settings/reboot``, ``/api/settings/shutdown``, ``/api/settings/network/{iface}``)
are covered in ``tests/test_flows.py``; this file adds the regression for the
settings.set_network confirmation and the shared ``require_confirm`` helper.
"""

from __future__ import annotations

import pytest

from app.errors import ApiError
from app.util.confirm import require_confirm


def _h(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# --- helper unit tests -----------------------------------------------------
def test_require_confirm_accepts_truthy() -> None:
    require_confirm({"confirm": True})


def test_require_confirm_accepts_one() -> None:
    require_confirm({"confirm": 1})


@pytest.mark.parametrize("body", [None, {}, {"confirm": False}, {"confirm": 0}, {"confirm": ""}])
def test_require_confirm_rejects_missing_or_false(body: object) -> None:
    with pytest.raises(ApiError) as excinfo:
        require_confirm(body)  # type: ignore[arg-type]
    assert excinfo.value.code == "confirm_required"
    assert excinfo.value.status_code == 428


# --- settings.set_network confirmation -------------------------------------
def test_set_network_requires_confirm(client: object, tokens: dict[str, str]) -> None:
    admin = _h(tokens["admin"])
    # body missing -> 428
    resp = client.post("/api/settings/network/eth0", json={}, headers=admin)  # type: ignore[attr-defined]
    assert resp.status_code == 428
    assert resp.json()["error"]["code"] == "confirm_required"
    # body with confirm: false -> 428
    resp = client.post("/api/settings/network/eth0", json={"confirm": False}, headers=admin)  # type: ignore[attr-defined]
    assert resp.status_code == 428
    # body with confirm: true -> 200, rollback plan returned
    resp = client.post("/api/settings/network/eth0", json={"confirm": True}, headers=admin)  # type: ignore[attr-defined]
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    assert resp.json()["rollback_armed_s"] == 60


def test_set_network_rejected_for_non_admin(client: object, tokens: dict[str, str]) -> None:
    resp = client.post(  # type: ignore[attr-defined]
        "/api/settings/network/eth0", json={"confirm": True}, headers=_h(tokens["operator"])
    )
    assert resp.status_code == 403
