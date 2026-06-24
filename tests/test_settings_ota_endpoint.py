"""Regression tests for #124 — OTA endpoint URL under /api/settings/system.

audit.md §1 System + §8 OTA: the OTA endpoint URL must be surfaced
under the System panel (not only under /api/cloud/provisioning/endpoint)
and the persisted value must survive a restart.

Fix:
- ``GET /api/settings/system/ota-endpoint`` (viewer) returns the
  active URL — prefers ``system_settings_kv`` (operator override,
  per #42) and falls back to ``provisioning.endpoint`` (legacy
  lifecycle mirror).
- ``PUT /api/settings/system/ota-endpoint`` (admin + confirm) is the
  canonical write path; it writes to both ``system_settings_kv`` and
  ``provisioning.endpoint`` so the two views always agree.
- ``POST /api/cloud/provisioning/endpoint`` (legacy write) now
  mirrors the value to ``system_settings_kv`` so a write through the
  cloud endpoint is visible to the System SPA on the next read.
"""

from __future__ import annotations


def _h(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_endpoint_round_trip(client: object, tokens: dict[str, str]) -> None:
    """PUT writes the URL; GET returns the same URL (#124)."""
    admin = _h(tokens["admin"])
    url = "https://ota.example.com/v3/manifest"
    resp = client.put(  # type: ignore[attr-defined]
        "/api/settings/system/ota-endpoint",
        json={"endpoint": url, "confirm": True},
        headers=admin,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    assert body["endpoint"] == url
    assert body["applied"] is True

    listing = client.get(  # type: ignore[attr-defined]
        "/api/settings/system/ota-endpoint", headers=admin
    )
    assert listing.status_code == 200
    payload = listing.json()
    assert payload["endpoint"] == url
    assert payload["persistent"] is True


def test_endpoint_rejects_non_http_url(client: object, tokens: dict[str, str]) -> None:
    """``ftp://...`` and bare strings are 422."""
    admin = _h(tokens["admin"])
    resp = client.put(  # type: ignore[attr-defined]
        "/api/settings/system/ota-endpoint",
        json={"endpoint": "ftp://nope.example.com/feed", "confirm": True},
        headers=admin,
    )
    assert resp.status_code == 422, resp.text


def test_endpoint_rejects_empty(client: object, tokens: dict[str, str]) -> None:
    """Empty endpoint is 422 (pydantic ``min_length=1``)."""
    admin = _h(tokens["admin"])
    resp = client.put(  # type: ignore[attr-defined]
        "/api/settings/system/ota-endpoint",
        json={"endpoint": "", "confirm": True},
        headers=admin,
    )
    assert resp.status_code == 422, resp.text


def test_endpoint_requires_confirm(client: object, tokens: dict[str, str]) -> None:
    """Privileged write — ``confirm: true`` is required (428)."""
    admin = _h(tokens["admin"])
    resp = client.put(  # type: ignore[attr-defined]
        "/api/settings/system/ota-endpoint",
        json={"endpoint": "https://ota.example.com/v3"},
        headers=admin,
    )
    assert resp.status_code == 428, resp.text


def test_endpoint_rejects_extra_field(client: object, tokens: dict[str, str]) -> None:
    """``extra="forbid"`` rejects typos like ``url`` instead of ``endpoint``."""
    admin = _h(tokens["admin"])
    resp = client.put(  # type: ignore[attr-defined]
        "/api/settings/system/ota-endpoint",
        json={"url": "https://ota.example.com", "confirm": True},
        headers=admin,
    )
    assert resp.status_code == 422, resp.text


def test_endpoint_requires_admin(client: object, tokens: dict[str, str]) -> None:
    """A viewer cannot change the OTA endpoint."""
    viewer = _h(tokens["viewer"])
    resp = client.put(  # type: ignore[attr-defined]
        "/api/settings/system/ota-endpoint",
        json={"endpoint": "https://ota.example.com/v3", "confirm": True},
        headers=viewer,
    )
    assert resp.status_code == 403, resp.text


def test_endpoint_is_viewer_readable(client: object, tokens: dict[str, str]) -> None:
    """A viewer can read the OTA endpoint (no secrets in this surface)."""
    admin = _h(tokens["admin"])
    viewer = _h(tokens["viewer"])
    url = "https://ota.example.com/v3/manifest"
    client.put(  # type: ignore[attr-defined]
        "/api/settings/system/ota-endpoint",
        json={"endpoint": url, "confirm": True},
        headers=admin,
    )
    resp = client.get(  # type: ignore[attr-defined]
        "/api/settings/system/ota-endpoint", headers=viewer
    )
    assert resp.status_code == 200
    assert resp.json()["endpoint"] == url


def test_cloud_endpoint_writes_are_visible_to_settings_get(client: object, tokens: dict[str, str]) -> None:
    """Legacy cloud write mirrors to KV — System SPA reads the same value (#124)."""
    admin = _h(tokens["admin"])
    url = "https://cloud.example.com/api/ota"
    resp = client.post(  # type: ignore[attr-defined]
        "/api/cloud/provisioning/endpoint", json={"endpoint": url}, headers=admin
    )
    assert resp.status_code == 200, resp.text

    listing = client.get(  # type: ignore[attr-defined]
        "/api/settings/system/ota-endpoint", headers=admin
    )
    assert listing.status_code == 200
    assert listing.json()["endpoint"] == url


def test_settings_get_returns_none_when_unconfigured(client: object, tokens: dict[str, str]) -> None:
    """A fresh DB has no endpoint — the read returns ``endpoint: null``."""
    viewer = _h(tokens["viewer"])
    resp = client.get(  # type: ignore[attr-defined]
        "/api/settings/system/ota-endpoint", headers=viewer
    )
    assert resp.status_code == 200
    assert resp.json()["endpoint"] is None


def test_settings_put_audits_old_and_new(client: object, tokens: dict[str, str]) -> None:
    """The audit row records both old and new values (#42)."""
    admin = _h(tokens["admin"])
    first = "https://ota.example.com/v3/first"
    second = "https://ota.example.com/v3/second"
    client.put(  # type: ignore[attr-defined]
        "/api/settings/system/ota-endpoint",
        json={"endpoint": first, "confirm": True},
        headers=admin,
    )
    resp = client.put(  # type: ignore[attr-defined]
        "/api/settings/system/ota-endpoint",
        json={"endpoint": second, "confirm": True},
        headers=admin,
    )
    assert resp.status_code == 200

    audit_resp = client.get(  # type: ignore[attr-defined]
        "/api/security/audit?action=set_ota_endpoint&limit=200", headers=admin
    )
    assert audit_resp.status_code == 200
    rows = audit_resp.json()["entries"]
    second_rows = [r for r in rows if r.get("new_value") == second]
    assert len(second_rows) == 1, f"expected one audit row for {second}, got {second_rows}"
    # The second write must record old_value == first so the audit log
    # shows the transition (not just the final value) (#42).
    assert second_rows[0]["old_value"] == first
