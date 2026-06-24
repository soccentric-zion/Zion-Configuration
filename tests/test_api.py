"""End-to-end API checks: read endpoints, the simulated flag, and auditing."""

from __future__ import annotations

import uuid

import pytest

# A representative GET from each HAL-backed router. In forced-simulation mode
# they must all answer 200 and carry the ``simulated`` envelope flag.
SIMULATED_ENDPOINTS = [
    "/api/overview/identity",
    "/api/overview/health",
    "/api/network/interfaces",
    "/api/hardware/gpio",
    "/api/hardware/sensors",
    "/api/storage/health",
    "/api/storage/mounts",
    "/api/thermal/zones",
    "/api/thermal/fans",
]


def _h(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.parametrize("path", SIMULATED_ENDPOINTS)
def test_read_endpoints_ok_and_simulated(client: object, tokens: dict[str, str], path: str) -> None:
    resp = client.get(path, headers=_h(tokens["viewer"]))  # type: ignore[attr-defined]
    assert resp.status_code == 200, f"{path} -> {resp.status_code} {resp.text}"
    assert resp.json().get("simulated") is True


def test_simulation_banner_reports_all_simulated(client: object, tokens: dict[str, str]) -> None:
    resp = client.get(  # type: ignore[attr-defined]
        "/api/system/simulation", headers=_h(tokens["viewer"])
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["any_simulated"] is True
    assert all(body["modules"].values())


def test_create_user_writes_audit_row(client: object, tokens: dict[str, str]) -> None:
    username = f"audit-{uuid.uuid4().hex[:8]}"
    create = client.post(  # type: ignore[attr-defined]
        "/api/users",
        json={"username": username, "password": "pw-123456", "role": "viewer"},
        headers=_h(tokens["admin"]),
    )
    assert create.status_code == 201, create.text

    audit = client.get(  # type: ignore[attr-defined]
        "/api/security/audit", headers=_h(tokens["admin"])
    )
    assert audit.status_code == 200
    text = audit.text
    assert username in text


def test_unknown_route_returns_spa_index(client: object) -> None:
    # The catch-all SPA fallback serves index.html for non-API unknown paths.
    resp = client.get("/some/spa/route")  # type: ignore[attr-defined]
    assert resp.status_code == 200
    assert "<!DOCTYPE html>" in resp.text or "<html" in resp.text.lower()
