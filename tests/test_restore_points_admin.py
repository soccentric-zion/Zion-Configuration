"""Regression tests for #123 — restore-points write/delete admin-only (audit §12).

audit.md §12 Backup/Restore: "must be governed (audit, role guard)."
The previous implementation guarded POST and DELETE with
``require_admin``, but the role matrix is auto-derived from the live
route table — adding any role guard here later is a footgun. This
test pins the matrix explicitly so a refactor that drops the guard
fails CI.

Acceptance (per #123):
- DELETE /api/settings/restore-points/{id} with viewer → 403.
- DELETE /api/settings/restore-points/{id} with operator → 403.
- DELETE /api/settings/restore-points/{id} with admin → 200 + audit.
- POST (create) is also admin-only.
- GET (list) is viewer-readable.
"""

from __future__ import annotations


def _h(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _seed_restore_point(client: object, admin: dict[str, str]) -> int:
    """Create a restore_points row via the admin POST and return its id."""
    resp = client.post(  # type: ignore[attr-defined]
        "/api/settings/restore-points", json={"name": "rp-test"}, headers=admin
    )
    assert resp.status_code == 200, resp.text
    return int(resp.json()["id"])


def test_delete_requires_admin_for_viewer(client: object, tokens: dict[str, str]) -> None:
    """Viewer DELETE → 403 (#123)."""
    admin = _h(tokens["admin"])
    rid = _seed_restore_point(client, admin)
    viewer = _h(tokens["viewer"])
    resp = client.delete(  # type: ignore[attr-defined]
        f"/api/settings/restore-points/{rid}", headers=viewer
    )
    assert resp.status_code == 403, resp.text


def test_delete_requires_admin_for_operator(client: object, tokens: dict[str, str]) -> None:
    """Operator DELETE → 403 (#123) — only admin may delete."""
    admin = _h(tokens["admin"])
    rid = _seed_restore_point(client, admin)
    operator = _h(tokens["operator"])
    resp = client.delete(  # type: ignore[attr-defined]
        f"/api/settings/restore-points/{rid}", headers=operator
    )
    assert resp.status_code == 403, resp.text


def test_delete_admin_succeeds_and_audits(client: object, tokens: dict[str, str]) -> None:
    """Admin DELETE → 200, the row is gone, and an audit row is recorded (#123, #42)."""
    admin = _h(tokens["admin"])
    rid = _seed_restore_point(client, admin)
    resp = client.delete(  # type: ignore[attr-defined]
        f"/api/settings/restore-points/{rid}", headers=admin
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body == {"ok": True}

    # Verify the row is gone.
    listing = client.get(  # type: ignore[attr-defined]
        "/api/settings/restore-points", headers=admin
    )
    assert listing.status_code == 200
    ids = [int(r["id"]) for r in listing.json()["restore_points"]]
    assert rid not in ids

    # Verify the audit row was recorded (#42).
    audit_resp = client.get(  # type: ignore[attr-defined]
        "/api/security/audit?action=restore_point_delete&limit=200", headers=admin
    )
    assert audit_resp.status_code == 200
    rows = audit_resp.json()["entries"]
    matches = [r for r in rows if r.get("target") == str(rid)]
    assert len(matches) == 1, f"expected one restore_point_delete audit row for {rid}"


def test_create_requires_admin(client: object, tokens: dict[str, str]) -> None:
    """Both viewer and operator POST → 403 — only admin may create (#123)."""
    viewer = _h(tokens["viewer"])
    operator = _h(tokens["operator"])
    for headers in (viewer, operator):
        resp = client.post(  # type: ignore[attr-defined]
            "/api/settings/restore-points",
            json={"name": "rp-should-fail"},
            headers=headers,
        )
        assert resp.status_code == 403, resp.text


def test_get_is_viewer_readable(client: object, tokens: dict[str, str]) -> None:
    """Viewer GET (list) succeeds (#123 — read is operator-visible)."""
    viewer = _h(tokens["viewer"])
    resp = client.get(  # type: ignore[attr-defined]
        "/api/settings/restore-points", headers=viewer
    )
    assert resp.status_code == 200
    assert "restore_points" in resp.json()
