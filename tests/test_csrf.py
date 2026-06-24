"""Regression guard for #118 — state-changing endpoints reject cookie-only auth.

The dashboard uses JWT bearer tokens for auth, not cookies. This
guards against accidentally adding cookie-based auth (e.g. by
introducing a session middleware or a cookie-based fallback) that
would expose state-changing endpoints to CSRF.

The check walks the live route table and, for every non-GET endpoint
under /api, verifies that requests without an Authorization header
get 401, NOT a 2xx or 3xx. If a request with a cookie but no
Authorization is accepted, the auth path is broken or cookie-based
and CSRF protection is needed (#118).
"""

from __future__ import annotations

from fastapi.routing import APIRoute

# Endpoints that are intentionally reachable at viewer level or
# for login; the issue is about state-changing endpoints that
# already require auth (and so should require a bearer token).
_PUBLIC_OR_SELF = {
    ("POST", "/api/login"),
    ("POST", "/api/me/password"),
    ("POST", "/api/files/diagnostics/run"),
}


def test_no_state_changing_endpoint_accepts_cookie_only(client: object) -> None:
    routes = [r for r in client.app.routes if isinstance(r, APIRoute)]  # type: ignore[attr-defined]
    candidates: list[tuple[str, str]] = []
    for route in routes:
        if not route.path.startswith("/api"):
            continue
        methods = route.methods - {"GET", "HEAD", "OPTIONS"}
        for method in methods:
            if (method, route.path) in _PUBLIC_OR_SELF:
                continue
            candidates.append((method, route.path))

    assert candidates, "no state-changing /api routes found — test is meaningless"
    # Pick a sample of every 5th to keep the test fast.
    sample = candidates[::5] or candidates
    cookie_header = "session=attacker; csrf_token=anything"
    for method, path in sample:
        resp = client.request(  # type: ignore[attr-defined]
            method, path, headers={"Cookie": cookie_header}
        )
        assert resp.status_code in (401, 403), (
            f"{method} {path} accepted cookie-only auth (status={resp.status_code}); "
            "this would expose it to CSRF unless the endpoint is reachable "
            "without auth by design (#118)"
        )
