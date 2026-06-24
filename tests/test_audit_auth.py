"""Regression test for #137 — auth audit rows must record the username in target.

audit.md Guardrail 1 requires every state-changing action to be auditable
and reconstructable. ``/api/login`` used to record ``target="session"``,
making the audit log unsearchable by username. The fix is to set
``target=user["username"]`` (and to expose ``target`` as a filter on
``/api/security/audit`` so consumers can find all events for a user).

The test below covers:

1. The ``login`` audit row's ``target`` is the logged-in username (not a
   generic "session" string).
2. The audit endpoint supports ``?target=`` and returns matching rows.
3. The existing ``change_password`` and ``create_user`` audit rows
   continue to use the username as ``target`` (regression guard for
   #42, the audit-state-alignment series).
"""

from __future__ import annotations


def _h(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_login_audit_target_is_username(client: object) -> None:
    """A successful login writes a row with ``target == username`` (#137).

    A bad login still goes through ``authenticate``; check both the
    happy path and an invalid-password attempt (which the audit log
    does not write today, so just check the happy-path row).
    """
    # Log in as the seeded default admin.
    resp = client.post("/api/login", json={"username": "admin", "password": "admin"})
    assert resp.status_code == 200, resp.text
    token = resp.json()["access_token"]
    # Query the audit log for action=login and find the latest row.
    audit = client.get("/api/security/audit?action=login&limit=10", headers=_h(token))
    assert audit.status_code == 200, audit.text
    entries = audit.json()["entries"]
    assert entries, "no login audit row was written"
    latest = entries[0]
    assert latest["action"] == "login"
    # The fix: target must be the username, not a generic "session" string.
    assert latest["target"] == "admin", (
        f"login audit target must be the username (got {latest['target']!r} — "
        "audit log is unsearchable by username, #137)"
    )


def test_audit_supports_target_filter(client: object, tokens: dict[str, str]) -> None:
    """The audit endpoint must accept ``?target=`` and return rows
    where ``target`` matches (#137)."""
    admin = _h(tokens["admin"])
    # Create a user — the create_user audit row has target=new-username.
    new_username = "audit-target-user"
    resp = client.post(
        "/api/users",
        json={"username": new_username, "password": "audit-target-pw", "role": "viewer"},
        headers=admin,
    )
    assert resp.status_code == 201, resp.text
    # Query the audit log by target.
    audit = client.get(f"/api/security/audit?target={new_username}&limit=10", headers=admin)
    assert audit.status_code == 200, audit.text
    entries = audit.json()["entries"]
    assert entries, f"no audit rows for target={new_username!r}"
    # Every row returned must match the target.
    assert all(e["target"] == new_username for e in entries)
    # Sanity: at least one of them is the create_user event.
    assert any(e["action"] == "create_user" for e in entries)


def test_change_password_audit_target_is_username(client: object, tokens: dict[str, str]) -> None:
    """``change_password`` must audit with ``target == username`` (#42, #137)."""
    # Use the operator user (we have their password from the fixture).
    operator = _h(tokens["operator"])
    resp = client.post(
        "/api/me/password",
        json={"old_password": "operator-pw", "new_password": "operator-new-pw"},
        headers=operator,
    )
    assert resp.status_code == 200, resp.text
    # Query audit by target.
    audit = client.get(
        "/api/security/audit?target=operator&action=change_password&limit=10",
        headers=_h(tokens["admin"]),
    )
    assert audit.status_code == 200, audit.text
    entries = audit.json()["entries"]
    assert entries, "no change_password audit row for operator"
    assert all(e["target"] == "operator" for e in entries)
