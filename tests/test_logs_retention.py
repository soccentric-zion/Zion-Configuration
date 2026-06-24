"""Regression test for #125 — PUT /api/files/logs/retention + vacuum (audit §6).

audit.md §6: "disk usage + retention/vacuum, persistent vs volatile
state." The previous implementation hard-coded the retention window
in the static YAML config — operators could not adjust it on the
fly to free disk space.

The fix:
- ``PUT /api/files/logs/retention`` (admin) accepts
  ``LogRetentionUpdate`` and persists the values in
  ``system_settings_kv`` (per #42).
- ``POST /api/files/logs/vacuum`` (admin + confirm) runs the
  retention prune + SQLite ``VACUUM`` immediately.
- ``GET /api/files/logs/usage`` (viewer) reports total bytes and
  per-table row counts.
- The retention task in ``app/tasks/retention.py`` consults the
  override (else falls back to the static config).
"""

from __future__ import annotations


def _h(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_log_retention_persists(client: object, tokens: dict[str, str]) -> None:
    """PUT writes the new retention; the next GET reflects it (#125)."""
    admin = _h(tokens["admin"])
    resp = client.put(  # type: ignore[attr-defined]
        "/api/files/logs/retention",
        json={"history_retention_days": 7, "vacuum_interval_hours": 12, "confirm": True},
        headers=admin,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    assert "history_retention_days=7" in body["applied"]
    assert "vacuum_interval_hours=12" in body["applied"]

    listing = client.get("/api/files/logs/retention", headers=admin)  # type: ignore[attr-defined]
    assert listing.status_code == 200
    payload = listing.json()
    assert payload["history_retention_days"] == 7
    assert payload["vacuum_interval_hours"] == 12


def test_log_retention_rejects_oversized_days(client: object, tokens: dict[str, str]) -> None:
    """``history_retention_days`` is bounded [1, 3650]; 4000 is 422."""
    admin = _h(tokens["admin"])
    resp = client.put(  # type: ignore[attr-defined]
        "/api/files/logs/retention",
        json={"history_retention_days": 4000, "vacuum_interval_hours": 12, "confirm": True},
        headers=admin,
    )
    assert resp.status_code == 422, resp.text


def test_log_retention_rejects_zero_days(client: object, tokens: dict[str, str]) -> None:
    """``history_retention_days`` < 1 is 422 (would delete every row)."""
    admin = _h(tokens["admin"])
    resp = client.put(  # type: ignore[attr-defined]
        "/api/files/logs/retention",
        json={"history_retention_days": 0, "vacuum_interval_hours": 12, "confirm": True},
        headers=admin,
    )
    assert resp.status_code == 422, resp.text


def test_log_retention_rejects_oversized_hours(client: object, tokens: dict[str, str]) -> None:
    """``vacuum_interval_hours`` is bounded [1, 168]; 200 is 422."""
    admin = _h(tokens["admin"])
    resp = client.put(  # type: ignore[attr-defined]
        "/api/files/logs/retention",
        json={"history_retention_days": 7, "vacuum_interval_hours": 200, "confirm": True},
        headers=admin,
    )
    assert resp.status_code == 422, resp.text


def test_log_retention_requires_confirm(client: object, tokens: dict[str, str]) -> None:
    """Privileged write — ``confirm: true`` is required (428)."""
    admin = _h(tokens["admin"])
    resp = client.put(  # type: ignore[attr-defined]
        "/api/files/logs/retention",
        json={"history_retention_days": 7, "vacuum_interval_hours": 12},
        headers=admin,
    )
    assert resp.status_code == 428, resp.text


def test_log_retention_rejects_extra_field(client: object, tokens: dict[str, str]) -> None:
    """``extra="forbid"`` rejects typos like ``days`` (no
    ``history_`` prefix)."""
    admin = _h(tokens["admin"])
    resp = client.put(  # type: ignore[attr-defined]
        "/api/files/logs/retention",
        json={"days": 7, "vacuum_interval_hours": 12, "confirm": True},
        headers=admin,
    )
    assert resp.status_code == 422, resp.text


def test_log_retention_requires_admin(client: object, tokens: dict[str, str]) -> None:
    """A viewer cannot change retention."""
    viewer = _h(tokens["viewer"])
    resp = client.put(  # type: ignore[attr-defined]
        "/api/files/logs/retention",
        json={"history_retention_days": 7, "vacuum_interval_hours": 12, "confirm": True},
        headers=viewer,
    )
    assert resp.status_code == 403, resp.text


def test_log_vacuum_requires_confirm(client: object, tokens: dict[str, str]) -> None:
    """VACUUM is heavy — ``confirm: true`` is required (428)."""
    admin = _h(tokens["admin"])
    resp = client.post(  # type: ignore[attr-defined]
        "/api/files/logs/vacuum",
        json={},
        headers=admin,
    )
    assert resp.status_code == 428, resp.text


def test_log_vacuum_runs_with_confirm(client: object, tokens: dict[str, str]) -> None:
    """VACUUM with confirm runs immediately and reports rows pruned."""
    admin = _h(tokens["admin"])
    resp = client.post(  # type: ignore[attr-defined]
        "/api/files/logs/vacuum",
        json={"confirm": True},
        headers=admin,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    assert "metric_history" in body
    assert "telemetry_queue" in body
    assert "retention_days" in body


def test_log_vacuum_requires_admin(client: object, tokens: dict[str, str]) -> None:
    """A viewer cannot trigger vacuum."""
    viewer = _h(tokens["viewer"])
    resp = client.post(  # type: ignore[attr-defined]
        "/api/files/logs/vacuum",
        json={"confirm": True},
        headers=viewer,
    )
    assert resp.status_code == 403, resp.text


def test_log_usage_returns_totals(client: object, tokens: dict[str, str]) -> None:
    """GET /api/files/logs/usage returns a total + by_table dict (#125)."""
    admin = _h(tokens["admin"])
    resp = client.get("/api/files/logs/usage", headers=admin)  # type: ignore[attr-defined]
    assert resp.status_code == 200
    body = resp.json()
    assert "total_bytes" in body
    assert body["persistent"] is True
    assert isinstance(body["by_table"], dict)
    # Some tables we expect to see in the by_table summary.
    assert "metric_history" in body["by_table"]
    assert "audit_log" in body["by_table"]


def test_log_usage_is_viewer_readable(client: object, tokens: dict[str, str]) -> None:
    """A viewer can read disk usage (no secrets in this surface)."""
    viewer = _h(tokens["viewer"])
    resp = client.get("/api/files/logs/usage", headers=viewer)  # type: ignore[attr-defined]
    assert resp.status_code == 200
