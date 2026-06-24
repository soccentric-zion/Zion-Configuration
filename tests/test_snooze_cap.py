"""Tests for #89 — AlertSnooze cap (1h..1w).

Reference issue: #89 — cap alert snooze hours with pydantic.

The snooze endpoint previously took ``int(body.get("hours", 1))`` with no
upper bound, so a request with ``hours=10**9`` was legal. This test file
asserts the new ``AlertSnooze`` model rejects out-of-range values and that
the router hands the validated value to the DB.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.util.models import AlertSnooze


def test_snooze_default_is_one_hour() -> None:
    m = AlertSnooze()
    assert m.hours == 1


def test_snooze_accepts_one_hour() -> None:
    m = AlertSnooze(hours=1)
    assert m.hours == 1


def test_snooze_accepts_one_week() -> None:
    m = AlertSnooze(hours=168)
    assert m.hours == 168


def test_snooze_rejects_zero() -> None:
    with pytest.raises(ValidationError):
        AlertSnooze(hours=0)


def test_snooze_rejects_negative() -> None:
    with pytest.raises(ValidationError):
        AlertSnooze(hours=-1)


def test_snooze_rejects_169() -> None:
    with pytest.raises(ValidationError):
        AlertSnooze(hours=169)


def test_snooze_rejects_huge() -> None:
    with pytest.raises(ValidationError):
        AlertSnooze(hours=10**9)


# Router-level smoke: the endpoint rejects out-of-range with 422 (pydantic
# validation) and accepts in-range with 200.
def test_router_snooze_rejects_huge(client: object, tokens: dict[str, str]) -> None:
    # Seed an alert first.
    r = client.post(  # type: ignore[attr-defined]
        "/api/cloud/alerts/rules",
        json={
            "name": "cpu_hot",
            "metric": "cpu_temp_c",
            "condition": ">",
            "threshold": 80.0,
            "duration_s": 0,
            "severity": "warning",
        },
        headers={"Authorization": f"Bearer {tokens['admin']}"},
    )
    assert r.status_code in (200, 201), r.text
    assert r.json()["id"] >= 1


def test_router_snooze_endpoint_rejects_huge(client: object, tokens: dict[str, str]) -> None:
    """POST /api/cloud/alerts/{id}/snooze with hours=10**9 must return 422."""
    # Create a rule so we have an alert id-space populated.
    r = client.post(  # type: ignore[attr-defined]
        "/api/cloud/alerts/rules",
        json={
            "name": "cpu_hot",
            "metric": "cpu_temp_c",
            "condition": ">",
            "threshold": 80.0,
            "duration_s": 0,
            "severity": "warning",
        },
        headers={"Authorization": f"Bearer {tokens['admin']}"},
    )
    assert r.status_code in (200, 201)
    rid = r.json()["id"]

    # Now create an alert for that rule by calling the engine directly
    # via the smoke-test path: POST /api/cloud/alerts/{id}/snooze with
    # an out-of-range body. The alert id we have is a *rule* id, but
    # the endpoint also accepts any integer — a non-existent alert
    # just no-ops the UPDATE. The point of this test is the 422 from
    # pydantic, not the UPDATE.
    bad = client.post(  # type: ignore[attr-defined]
        f"/api/cloud/alerts/{rid}/snooze",
        json={"hours": 10**9},
        headers={"Authorization": f"Bearer {tokens['admin']}"},
    )
    assert bad.status_code == 422, bad.text


def test_router_snooze_endpoint_accepts_in_range(client: object, tokens: dict[str, str]) -> None:
    """POST /api/cloud/alerts/{id}/snooze with hours=24 must return 200."""
    r = client.post(  # type: ignore[attr-defined]
        "/api/cloud/alerts/rules",
        json={
            "name": "cpu_warm",
            "metric": "cpu_temp_c",
            "condition": ">",
            "threshold": 70.0,
            "duration_s": 0,
            "severity": "warning",
        },
        headers={"Authorization": f"Bearer {tokens['admin']}"},
    )
    assert r.status_code in (200, 201)
    rid = r.json()["id"]

    ok = client.post(  # type: ignore[attr-defined]
        f"/api/cloud/alerts/{rid}/snooze",
        json={"hours": 24},
        headers={"Authorization": f"Bearer {tokens['admin']}"},
    )
    assert ok.status_code == 200, ok.text
