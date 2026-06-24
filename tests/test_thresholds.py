"""Tests for system threshold PUT endpoint (#106, audit §7).

audit.md §7 Health: "Apply mode: read-only (+ editable thresholds =
live)." The static ``ThresholdsConfig`` in ``app/config.py`` is the
default; the operator can override individual fields via
``PUT /api/system/thresholds`` and the values are persisted in
``system_settings_kv`` so they survive a restart (#42, #106).
"""

from __future__ import annotations


def _admin(tokens: dict[str, str]) -> dict[str, str]:
    return {"Authorization": f"Bearer {tokens['admin']}"}


def _viewer(tokens: dict[str, str]) -> dict[str, str]:
    return {"Authorization": f"Bearer {tokens['viewer']}"}


def test_thresholds_defaults_returned_when_unset(client: object, tokens: dict[str, str]) -> None:
    """A fresh device returns the static config defaults from
    ``GET /api/system/thresholds``."""
    resp = client.get(  # type: ignore[attr-defined]
        "/api/system/thresholds",
        headers=_viewer(tokens),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # All four thresholds are required and non-negative.
    assert body["cpu_temp_warn_c"] >= 0
    assert body["cpu_temp_crit_c"] >= body["cpu_temp_warn_c"]
    assert 0 <= body["disk_usage_warn_pct"] <= 100
    assert 0 <= body["mem_usage_warn_pct"] <= 100


def test_thresholds_persisted_and_used(client: object, tokens: dict[str, str]) -> None:
    """A PUT that changes a threshold persists the value in
    ``system_settings_kv`` and the next GET returns the override."""
    resp = client.put(  # type: ignore[attr-defined]
        "/api/system/thresholds",
        json={"cpu_temp_warn_c": 75.0, "cpu_temp_crit_c": 95.0},
        headers=_admin(tokens),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["thresholds"]["cpu_temp_warn_c"] == 75.0
    assert body["thresholds"]["cpu_temp_crit_c"] == 95.0
    # Read-back returns the override.
    resp = client.get(  # type: ignore[attr-defined]
        "/api/system/thresholds",
        headers=_viewer(tokens),
    )
    assert resp.json()["cpu_temp_warn_c"] == 75.0


def test_thresholds_partial_update_leaves_others(client: object, tokens: dict[str, str]) -> None:
    """A PUT with only one field set leaves the other fields at their
    previous (default) value — None means "leave unchanged"."""
    client.put(  # type: ignore[attr-defined]
        "/api/system/thresholds",
        json={"disk_usage_warn_pct": 75},
        headers=_admin(tokens),
    )
    resp = client.get(  # type: ignore[attr-defined]
        "/api/system/thresholds",
        headers=_viewer(tokens),
    )
    body = resp.json()
    assert body["disk_usage_warn_pct"] == 75
    # mem_usage_warn_pct was not in the PUT, so it stays at the default.
    assert body["mem_usage_warn_pct"] == 90  # static default


def test_thresholds_rejects_out_of_range(client: object, tokens: dict[str, str]) -> None:
    """A threshold outside its allowed range is rejected at the pydantic
    layer (422)."""
    resp = client.put(  # type: ignore[attr-defined]
        "/api/system/thresholds",
        json={"cpu_temp_warn_c": 250.0},
        headers=_admin(tokens),
    )
    assert resp.status_code == 422, resp.text
    resp = client.put(  # type: ignore[attr-defined]
        "/api/system/thresholds",
        json={"disk_usage_warn_pct": 150},
        headers=_admin(tokens),
    )
    assert resp.status_code == 422, resp.text


def test_thresholds_rejects_unknown_keys(client: object, tokens: dict[str, str]) -> None:
    """``extra="forbid"`` via _StrictModel rejects typos with 422."""
    resp = client.put(  # type: ignore[attr-defined]
        "/api/system/thresholds",
        json={"cpu_temp_warn_c": 70, "typo_field": 1},
        headers=_admin(tokens),
    )
    assert resp.status_code == 422, resp.text


def test_thresholds_audits_each_change(client: object, tokens: dict[str, str]) -> None:
    """Each updated field produces its own audit row so the action log
    surfaces what was changed, not just that the endpoint was called
    (#42)."""
    client.put(  # type: ignore[attr-defined]
        "/api/system/thresholds",
        json={"mem_usage_warn_pct": 80, "disk_usage_warn_pct": 70},
        headers=_admin(tokens),
    )
    audit = client.get(  # type: ignore[attr-defined]
        "/api/security/audit?action=threshold_update&limit=10",
        headers=_admin(tokens),
    )
    rows = audit.json().get("entries") or []
    targets = {r["target"] for r in rows}
    assert "thresholds.mem_usage_warn_pct" in targets
    assert "thresholds.disk_usage_warn_pct" in targets


def test_thresholds_requires_admin(client: object, tokens: dict[str, str]) -> None:
    """PUT is admin-only; viewer/operator are rejected with 403."""
    for role in ("viewer", "operator"):
        resp = client.put(  # type: ignore[attr-defined]
            "/api/system/thresholds",
            json={"cpu_temp_warn_c": 75.0},
            headers={"Authorization": f"Bearer {tokens[role]}"},
        )
        assert resp.status_code == 403, (role, resp.text)
