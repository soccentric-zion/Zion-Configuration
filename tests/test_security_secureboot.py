"""Tests for the read-only /api/security/secure-boot, /fuses, /keys/enrolled
endpoints (audit §4, §9, #94).

The acceptance criteria are deliberately narrow:

- Each endpoint returns 200 with the expected shape.
- Each endpoint is viewer-readable (no admin-only lockout).
- No POST/PUT/DELETE handler exists for any of them (status-only).
"""

from __future__ import annotations


def _h(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# --- secure-boot -----------------------------------------------------------
def test_secure_boot_returns_status_envelope(client: object, tokens: dict[str, str]) -> None:
    viewer = _h(tokens["viewer"])
    resp = client.get("/api/security/secure-boot", headers=viewer)  # type: ignore[attr-defined]
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # envelope
    assert body["simulated"] is True
    # status fields
    assert "enabled" in body
    assert "mode" in body
    assert "vendor_keys" in body
    assert isinstance(body["vendor_keys"], int)


# --- fuses -----------------------------------------------------------------
def test_fuses_returns_enumeration(client: object, tokens: dict[str, str]) -> None:
    viewer = _h(tokens["viewer"])
    resp = client.get("/api/security/fuses", headers=viewer)  # type: ignore[attr-defined]
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["simulated"] is True
    assert "banks" in body
    assert isinstance(body["banks"], list)
    assert body["banks"], "expected at least one fuse bank"
    bank = body["banks"][0]
    assert "name" in bank
    assert "fuses" in bank
    assert bank["fuses"]
    fuse = bank["fuses"][0]
    # Each fuse entry carries offset/name/locked/value.
    for field in ("offset", "name", "locked", "value"):
        assert field in fuse


# --- enrolled keys ---------------------------------------------------------
def test_enrolled_keys_returns_list(client: object, tokens: dict[str, str]) -> None:
    viewer = _h(tokens["viewer"])
    resp = client.get("/api/security/keys/enrolled", headers=viewer)  # type: ignore[attr-defined]
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["simulated"] is True
    assert "keys" in body
    assert isinstance(body["keys"], list)
    assert body["keys"]
    key = body["keys"][0]
    for field in ("key_id", "fingerprint", "enrolled_at", "owner"):
        assert field in key


# --- role guard: viewer is enough -----------------------------------------
def test_status_endpoints_do_not_require_admin(client: object, tokens: dict[str, str]) -> None:
    viewer = _h(tokens["viewer"])
    for path in ("/api/security/secure-boot", "/api/security/fuses", "/api/security/keys/enrolled"):
        resp = client.get(path, headers=viewer)  # type: ignore[attr-defined]
        assert resp.status_code == 200, (path, resp.text)


# --- no write path exists (status-only contract) ---------------------------
def test_no_write_endpoint_for_status_only_surfaces(client: object) -> None:
    """Per audit.md §9, key enrollment is status-only. There must be no
    POST/PUT/PATCH/DELETE handler for these surfaces — only GET."""
    # Importing the router after the app is built lets us read the
    # already-registered routes without spinning up a request.
    from app.main import create_app

    app = create_app()
    paths = {r.path for r in app.routes}
    for surface in ("/api/security/secure-boot", "/api/security/fuses", "/api/security/keys/enrolled"):
        assert surface in paths, f"missing GET route {surface}"
    # A POST/PUT/DELETE on these paths must not exist.
    from fastapi import APIRouter  # noqa: F401  (sanity)

    for route in app.routes:
        if route.path in {
            "/api/security/secure-boot",
            "/api/security/fuses",
            "/api/security/keys/enrolled",
        }:
            methods = getattr(route, "methods", set()) or set()
            assert methods == {"GET"}, f"unexpected methods {methods} on {route.path}"
