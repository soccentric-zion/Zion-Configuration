"""Regression test for #133 — CVE acknowledge endpoint (audit §9).

audit.md §9 Security: secure-boot / keys / certs / TPM / firewall /
SSH / CVE. The CVE *list* endpoint already exists but is read-only
and operator-controllable ack state is missing — operators need a
way to mark a CVE as "acknowledged" / "mitigated" so the SPA can
filter the unacked CVEs to the top of the list.

The fix adds:
- ``POST /api/security/cve/{cve_id}/acknowledge`` (operator) with
  optional ``note``.
- ``DELETE /api/security/cve/{cve_id}/acknowledge`` (operator) to
  un-ack.
- The ``/api/security/cve`` GET response now merges the static CVE
  list with the live ack overlay (acknowledged / acknowledged_at /
  acknowledged_by / mitigation_note per row).
"""

from __future__ import annotations

import json
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_DATA = _REPO / "data"
_CVE_FILE = _DATA / "cve-scan.json"

_FIXTURE = {
    "matches": [
        {
            "id": "CVE-2024-1001",
            "package": "openssl",
            "severity": "high",
            "fixed_version": "3.0.13-1",
        },
        {
            "id": "CVE-2024-2002",
            "package": "busybox",
            "severity": "medium",
            "fixed_version": "1.36.1-5",
        },
    ]
}


def _h(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _seed_cve_file() -> None:
    """Write the cve-scan.json fixture used by the tests."""
    _DATA.mkdir(parents=True, exist_ok=True)
    _CVE_FILE.write_text(json.dumps(_FIXTURE), encoding="utf-8")


def _drop_cve_file() -> None:
    """Remove the fixture on teardown so other tests are unaffected."""
    if _CVE_FILE.exists():
        _CVE_FILE.unlink()


def test_cve_acknowledge_persists(client: object, tokens: dict[str, str]) -> None:
    """POST acknowledge must persist a row and surface in GET /cve (#133)."""
    _seed_cve_file()
    try:
        admin = _h(tokens["admin"])
        resp = client.post(  # type: ignore[attr-defined]
            "/api/security/cve/CVE-2024-1001/acknowledge",
            json={"note": "Mitigated by switching to 3.0.13+"},
            headers=admin,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["ok"] is True

        # GET /cve must show the row as acknowledged.
        listing = client.get("/api/security/cve", headers=admin)  # type: ignore[attr-defined]
        assert listing.status_code == 200, listing.text
        assert listing.json()["available"] is True
        matches = {m["cve_id"]: m for m in listing.json()["matches"]}
        assert matches["CVE-2024-1001"]["acknowledged"] is True
        assert matches["CVE-2024-1001"]["acknowledged_by"] == "admin"
        assert matches["CVE-2024-1001"]["mitigation_note"] == "Mitigated by switching to 3.0.13+"
        # The other CVE is *not* acknowledged.
        assert matches["CVE-2024-2002"]["acknowledged"] is False
    finally:
        _drop_cve_file()


def test_cve_acknowledge_is_upsert(client: object, tokens: dict[str, str]) -> None:
    """Two POSTs on the same CVE update the note / user instead of
    erroring out (#133)."""
    _seed_cve_file()
    try:
        admin = _h(tokens["admin"])
        # First ack.
        client.post(  # type: ignore[attr-defined]
            "/api/security/cve/CVE-2024-1001/acknowledge",
            json={"note": "first"},
            headers=admin,
        )
        # Second ack — same CVE, different note.
        client.post(  # type: ignore[attr-defined]
            "/api/security/cve/CVE-2024-1001/acknowledge",
            json={"note": "second"},
            headers=admin,
        )
        listing = client.get("/api/security/cve", headers=admin)  # type: ignore[attr-defined]
        m = next(r for r in listing.json()["matches"] if r["cve_id"] == "CVE-2024-1001")
        assert m["mitigation_note"] == "second"
    finally:
        _drop_cve_file()


def test_cve_unacknowledge_removes(client: object, tokens: dict[str, str]) -> None:
    """DELETE must clear the ack row and surface as unacked in GET /cve."""
    _seed_cve_file()
    try:
        admin = _h(tokens["admin"])
        client.post(  # type: ignore[attr-defined]
            "/api/security/cve/CVE-2024-1001/acknowledge",
            json={"note": "x"},
            headers=admin,
        )
        resp = client.delete(  # type: ignore[attr-defined]
            "/api/security/cve/CVE-2024-1001/acknowledge", headers=admin
        )
        assert resp.status_code == 200, resp.text
        listing = client.get("/api/security/cve", headers=admin)  # type: ignore[attr-defined]
        m = next(r for r in listing.json()["matches"] if r["cve_id"] == "CVE-2024-1001")
        assert m["acknowledged"] is False
        assert m["mitigation_note"] is None
    finally:
        _drop_cve_file()


def test_cve_unacknowledge_404_when_no_ack(client: object, tokens: dict[str, str]) -> None:
    """DELETE on an unacked CVE returns 404 (the caller knows the
    action was a no-op, not a successful clear)."""
    _seed_cve_file()
    try:
        admin = _h(tokens["admin"])
        resp = client.delete(  # type: ignore[attr-defined]
            "/api/security/cve/CVE-2024-2002/acknowledge", headers=admin
        )
        assert resp.status_code == 404, resp.text
    finally:
        _drop_cve_file()


def test_cve_acknowledge_requires_operator(client: object, tokens: dict[str, str]) -> None:
    """A viewer cannot ack a CVE."""
    _seed_cve_file()
    try:
        viewer = _h(tokens["viewer"])
        resp = client.post(  # type: ignore[attr-defined]
            "/api/security/cve/CVE-2024-1001/acknowledge",
            json={"note": "x"},
            headers=viewer,
        )
        assert resp.status_code == 403, resp.text
    finally:
        _drop_cve_file()
