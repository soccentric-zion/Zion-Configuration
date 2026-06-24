"""Tests for the read-only /api/security/tpm endpoint (#98).

Per audit.md §9, the TPM state is exposed for monitoring only — the
dashboard must not be able to clear, enable, or own the TPM from the
SPA. The endpoint is GET-only and viewer-readable.
"""

from __future__ import annotations


def _h(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_tpm_status_returns_documented_fields(client: object, tokens: dict[str, str]) -> None:
    viewer = _h(tokens["viewer"])
    resp = client.get("/api/security/tpm", headers=viewer)  # type: ignore[attr-defined]
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # envelope
    assert body["simulated"] is True
    # required fields
    for field in ("present", "enabled", "version", "owned", "pcr_banks"):
        assert field in body, f"missing {field} in tpm status"
    assert isinstance(body["pcr_banks"], list)
    assert body["pcr_banks"], "expected at least one PCR bank"


def test_tpm_status_does_not_require_admin(client: object, tokens: dict[str, str]) -> None:
    viewer = _h(tokens["viewer"])
    resp = client.get("/api/security/tpm", headers=viewer)  # type: ignore[attr-defined]
    assert resp.status_code == 200


def test_tpm_has_no_write_endpoint(client: object) -> None:
    """Status-only contract: no POST/PUT/PATCH/DELETE on /api/security/tpm."""
    from app.main import create_app

    app = create_app()
    for route in app.routes:
        if route.path == "/api/security/tpm":
            methods = getattr(route, "methods", set()) or set()
            assert methods == {"GET"}, f"unexpected methods {methods} on {route.path}"
