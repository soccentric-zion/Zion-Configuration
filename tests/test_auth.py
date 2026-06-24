"""Authentication, lockout and the role authorization matrix."""

from __future__ import annotations

from fastapi.routing import APIRoute

# POST endpoints that are intentionally reachable at viewer level: public
# login, self-service password change, read-only "actions" that mutate
# nothing (running a self-test / diagnostic / log tail), and self-service
# session registration called by the SPA after login.
_PUBLIC_OR_SELF = {
    ("POST", "/api/login"),
    ("POST", "/api/me/password"),
    ("POST", "/api/files/diagnostics/run"),
    ("POST", "/api/sessions"),
    ("POST", "/api/logs/tail"),
    ("POST", "/api/files-log/tail"),
    # Self-service session revoke: the operator can kill *their own*
    # session from /api/me/sessions without needing operator+ role.
    # The server-side filter (username = me) keeps a viewer from
    # revoking anyone else's session.
    ("DELETE", "/api/me/sessions/{session_id}"),
}


def _login(client: object, username: str, password: str) -> object:
    return client.post(  # type: ignore[attr-defined]
        "/api/login", json={"username": username, "password": password}
    )


def test_login_success_and_failure(client: object) -> None:
    assert _login(client, "admin", "admin").status_code == 200
    assert _login(client, "admin", "wrong").status_code == 401


def test_unauthenticated_is_rejected(client: object) -> None:
    resp = client.get("/api/me")  # type: ignore[attr-defined]
    assert resp.status_code == 401


def test_tampered_token_is_rejected(client: object, tokens: dict[str, str]) -> None:
    bad = tokens["admin"][:-3] + "xxx"
    resp = client.get(  # type: ignore[attr-defined]
        "/api/me", headers={"Authorization": f"Bearer {bad}"}
    )
    assert resp.status_code == 401


def test_lockout_after_repeated_failures(client: object) -> None:
    # A throwaway account: 5 bad attempts must trip the lockout (HTTP 423).
    user = "locktest"
    statuses = [_login(client, user, "nope").status_code for _ in range(6)]
    assert 423 in statuses


def test_role_matrix_no_mutating_endpoint_is_viewer_reachable(client: object) -> None:
    """Every mutating /api endpoint must require operator or admin.

    The matrix is derived by introspecting each route's dependency tree for the
    ``require_role`` guard and reading its captured ``needed`` rank, so adding a
    new write endpoint without a guard fails this test automatically.
    """
    app = client.app  # type: ignore[attr-defined]
    unguarded: list[str] = []

    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        if not route.path.startswith("/api"):
            continue
        methods = route.methods - {"GET", "HEAD", "OPTIONS"}
        if not methods:
            continue
        needed = _max_required_rank(route.dependant)
        for method in methods:
            if (method, route.path) in _PUBLIC_OR_SELF:
                continue
            if needed < 1:  # below 'operator'
                unguarded.append(f"{method} {route.path}")

    assert not unguarded, f"Mutating endpoints missing operator+ guard: {unguarded}"


def test_viewer_forbidden_on_mutating_endpoint(client: object, tokens: dict[str, str]) -> None:
    resp = client.post(  # type: ignore[attr-defined]
        "/api/users",
        json={"username": "x", "password": "y", "role": "viewer"},
        headers={"Authorization": f"Bearer {tokens['viewer']}"},
    )
    assert resp.status_code == 403


def test_deleted_user_loses_api_access_within_ttl(client: object, tokens: dict[str, str]) -> None:
    """A deleted user must lose access within the USER_STATE_TTL_S window
    (not wait for JWT expiry — fix #25)."""
    admin = {"Authorization": f"Bearer {tokens['admin']}"}
    # Create a fresh user, log in, get a token, then delete from under them.
    name = "victim"
    create = client.post(  # type: ignore[attr-defined]
        "/api/users",
        json={"username": name, "password": f"{name}-pw", "role": "viewer"},
        headers=admin,
    )
    assert create.status_code == 201, create.text
    login = client.post(  # type: ignore[attr-defined]
        "/api/login", json={"username": name, "password": f"{name}-pw"}
    )
    assert login.status_code == 200, login.text
    victim_token = login.json()["access_token"]
    victim_h = {"Authorization": f"Bearer {victim_token}"}
    # The token works once.
    me1 = client.get("/api/me", headers=victim_h)  # type: ignore[attr-defined]
    assert me1.status_code == 200
    # Now delete the user; expect the next /api/me to 401.
    delete = client.delete(f"/api/users/{name}", headers=admin)  # type: ignore[attr-defined]
    assert delete.status_code == 200
    me2 = client.get("/api/me", headers=victim_h)  # type: ignore[attr-defined]
    assert me2.status_code == 401


def test_disabled_user_loses_api_access_within_ttl(client: object, tokens: dict[str, str]) -> None:
    """Disabling a user must invalidate their token within USER_STATE_TTL_S
    (fix #25)."""
    admin = {"Authorization": f"Bearer {tokens['admin']}"}
    name = "doomed"
    client.post(  # type: ignore[attr-defined]
        "/api/users",
        json={"username": name, "password": f"{name}-pw", "role": "viewer"},
        headers=admin,
    )
    login = client.post(  # type: ignore[attr-defined]
        "/api/login", json={"username": name, "password": f"{name}-pw"}
    )
    assert login.status_code == 200
    victim_token = login.json()["access_token"]
    victim_h = {"Authorization": f"Bearer {victim_token}"}
    me1 = client.get("/api/me", headers=victim_h)  # type: ignore[attr-defined]
    assert me1.status_code == 200
    # Disable the user.
    patch = client.patch(  # type: ignore[attr-defined]
        f"/api/users/{name}", json={"enabled": False}, headers=admin
    )
    assert patch.status_code == 200
    me2 = client.get("/api/me", headers=victim_h)  # type: ignore[attr-defined]
    assert me2.status_code == 401


# --- helpers ---------------------------------------------------------------


def _max_required_rank(dependant: object) -> int:
    """Recursively find the highest role rank required by a dependency tree."""
    rank = -1
    call = getattr(dependant, "call", None)
    if call is not None and "require_role.<locals>._guard" in getattr(call, "__qualname__", ""):
        closure = dict(
            zip(
                call.__code__.co_freevars,
                [c.cell_contents for c in (call.__closure__ or ())],
                strict=False,
            )
        )
        rank = max(rank, int(closure.get("needed", -1)))
    for sub in getattr(dependant, "dependencies", []):
        rank = max(rank, _max_required_rank(sub))
    return rank
