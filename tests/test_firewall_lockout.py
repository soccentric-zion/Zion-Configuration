"""Tests for #148 — firewall rejects a rule that would lock out the
dashboard (Guardrail 3, audit G3-E).

A misclick that denies a protected port (e.g. 8000 = the dashboard,
22 = SSH) without an earlier allow rule for the same port is a
self-lockout. The router now pre-checks the effective ruleset and
returns 422 ``would_lock_out_dashboard`` with a clear message; the
operator can opt in with ``?force=true`` for explicit lockout intent.
"""

from __future__ import annotations


def _h(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _admin(t: dict[str, str]) -> dict[str, str]:
    return _h(t["admin"])


def test_deny_dashboard_port_returns_422(client: object, tokens: dict[str, str]) -> None:
    """Denying port 8000 (the dashboard) without a prior allow for 8000
    is rejected (#148)."""
    resp = client.post(  # type: ignore[attr-defined]
        "/api/sysadmin/firewall",
        json={"action": "deny", "port": 8000, "protocol": "tcp", "confirm": True},
        headers=_admin(tokens),
    )
    assert resp.status_code == 422, resp.text
    body = resp.json()
    err = body.get("error") or {}
    assert err.get("code") == "would_lock_out_dashboard", body
    assert err.get("detail", {}).get("port") == 8000, body


def test_deny_dashboard_port_with_force_succeeds(client: object, tokens: dict[str, str]) -> None:
    """``?force=true`` overrides the lockout check (explicit lockout intent)."""
    resp = client.post(  # type: ignore[attr-defined]
        "/api/sysadmin/firewall?force=true",
        json={"action": "deny", "port": 8000, "protocol": "tcp", "confirm": True},
        headers=_admin(tokens),
    )
    assert resp.status_code == 201, resp.text


def test_deny_ssh_port_returns_422(client: object, tokens: dict[str, str]) -> None:
    """Denying port 22 (SSH) is also a self-lockout for an admin who's
    only on SSH (no console). Same code path."""
    resp = client.post(  # type: ignore[attr-defined]
        "/api/sysadmin/firewall",
        json={"action": "deny", "port": 22, "protocol": "tcp", "confirm": True},
        headers=_admin(tokens),
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["error"]["code"] == "would_lock_out_dashboard"


def test_allow_dashboard_port_succeeds(client: object, tokens: dict[str, str]) -> None:
    """An ``allow`` rule for port 8000 is not a lockout (it's the
    opposite — it grants access)."""
    resp = client.post(  # type: ignore[attr-defined]
        "/api/sysadmin/firewall",
        json={"action": "allow", "port": 8000, "protocol": "tcp", "confirm": True},
        headers=_admin(tokens),
    )
    assert resp.status_code == 201, resp.text


def test_deny_non_protected_port_succeeds(client: object, tokens: dict[str, str]) -> None:
    """Denying a non-protected port (e.g. 8080) is fine — the check
    only fires for protected ports."""
    resp = client.post(  # type: ignore[attr-defined]
        "/api/sysadmin/firewall",
        json={"action": "deny", "port": 8080, "protocol": "tcp", "confirm": True},
        headers=_admin(tokens),
    )
    assert resp.status_code == 201, resp.text


def test_deny_dashboard_port_after_allow_succeeds(client: object, tokens: dict[str, str]) -> None:
    """If an earlier-positioned ``allow 8000`` exists, a later ``deny
    8000`` is fine — the allow shadows it. This is the legitimate way
    to first open the port and then later restrict it (e.g. for
    scheduled maintenance)."""
    admin = _admin(tokens)
    # Step 1: allow 8000 at position 1.
    r1 = client.post(  # type: ignore[attr-defined]
        "/api/sysadmin/firewall",
        json={
            "action": "allow",
            "port": 8000,
            "protocol": "tcp",
            "position": 1,
            "confirm": True,
        },
        headers=admin,
    )
    assert r1.status_code == 201, r1.text
    # Step 2: deny 8000 at position 2 — earlier allow shadows it.
    r2 = client.post(  # type: ignore[attr-defined]
        "/api/sysadmin/firewall",
        json={
            "action": "deny",
            "port": 8000,
            "protocol": "tcp",
            "position": 2,
            "confirm": True,
        },
        headers=admin,
    )
    assert r2.status_code == 201, r2.text


def test_deny_dashboard_port_after_disabled_allow_returns_422(client: object, tokens: dict[str, str]) -> None:
    """An allow rule that's been disabled does NOT shadow a later
    deny — the deny would then be the first matching rule and is a
    lockout (#148)."""
    admin = _admin(tokens)
    # Step 1: allow 8000 at position 1.
    r1 = client.post(  # type: ignore[attr-defined]
        "/api/sysadmin/firewall",
        json={
            "action": "allow",
            "port": 8000,
            "protocol": "tcp",
            "position": 1,
            "confirm": True,
        },
        headers=admin,
    )
    assert r1.status_code == 201, r1.text
    allow_id = r1.json()["id"]
    # Step 2: disable the allow.
    r2 = client.post(  # type: ignore[attr-defined]
        f"/api/sysadmin/firewall/{allow_id}/enable",
        json={"enabled": False, "confirm": True},
        headers=admin,
    )
    assert r2.status_code == 200, r2.text
    # Step 3: deny 8000 at position 2 — no shadowing allow, so lockout.
    r3 = client.post(  # type: ignore[attr-defined]
        "/api/sysadmin/firewall",
        json={
            "action": "deny",
            "port": 8000,
            "protocol": "tcp",
            "position": 2,
            "confirm": True,
        },
        headers=admin,
    )
    assert r3.status_code == 422, r3.text
    assert r3.json()["error"]["code"] == "would_lock_out_dashboard"


def test_lockout_check_still_requires_confirm(client: object, tokens: dict[str, str]) -> None:
    """The confirm guard fires before the lockout check — a misclick
    without ``confirm: true`` is rejected with 428, not 422."""
    admin = _admin(tokens)
    resp = client.post(  # type: ignore[attr-defined]
        "/api/sysadmin/firewall",
        json={"action": "deny", "port": 8000, "protocol": "tcp"},
        headers=admin,
    )
    assert resp.status_code == 428, resp.text


def test_lockout_check_still_requires_admin(client: object, tokens: dict[str, str]) -> None:
    """Viewer/operator tokens are rejected with 403 before the lockout
    check."""
    for role in ("viewer", "operator"):
        resp = client.post(  # type: ignore[attr-defined]
            "/api/sysadmin/firewall",
            json={"action": "deny", "port": 8000, "protocol": "tcp", "confirm": True},
            headers=_h(tokens[role]),
        )
        assert resp.status_code == 403, (role, resp.text)
