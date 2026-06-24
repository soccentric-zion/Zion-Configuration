"""Tests for the GET/PUT /api/security/ssh-policy endpoints (#95).

Per audit.md §9, the dashboard must expose the live sshd_config and
allow operator updates to be persisted (so the audit row corresponds to
an actual state change, #42). The write path requires ``confirm: true``
and the body model uses ``extra='forbid'`` so typos 422.
"""

from __future__ import annotations

import asyncio
from typing import Any


def _h(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# --- read: live policy -----------------------------------------------------
def test_ssh_policy_get_returns_live_config(client: object, tokens: dict[str, str]) -> None:
    viewer = _h(tokens["viewer"])
    resp = client.get("/api/security/ssh-policy", headers=viewer)  # type: ignore[attr-defined]
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "policy" in body
    assert "simulated" in body
    policy = body["policy"]
    for field in (
        "permit_root_login",
        "password_authentication",
        "kbd_interactive_authentication",
        "x11_forwarding",
        "port",
        "allow_users",
    ):
        assert field in policy, f"missing {field} in policy"


# --- write: confirm required ----------------------------------------------
def test_ssh_policy_put_requires_confirm(client: object, tokens: dict[str, str]) -> None:
    admin = _h(tokens["admin"])
    resp = client.put(  # type: ignore[attr-defined]
        "/api/security/ssh-policy",
        json={"permit_root_login": "no"},
        headers=admin,
    )
    # 428 Precondition Required
    assert resp.status_code == 428, resp.text
    assert resp.json()["error"]["code"] == "confirm_required"


# --- write: persists + audits ---------------------------------------------
def test_ssh_policy_put_persists_and_audits(client: object, tokens: dict[str, str]) -> None:
    admin = _h(tokens["admin"])
    resp = client.put(  # type: ignore[attr-defined]
        "/api/security/ssh-policy",
        json={"permit_root_login": "no", "port": 2222, "confirm": True},
        headers=admin,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    assert "permit_root_login" in body["applied"]
    assert "port" in body["applied"]

    # GET reflects the override
    resp = client.get("/api/security/ssh-policy", headers=_h(tokens["viewer"]))  # type: ignore[attr-defined]
    policy = resp.json()["policy"]
    assert policy["permit_root_login"] == "no"
    assert policy["port"] == 2222

    # Audit row was recorded
    from app.db.database import get_db

    async def _fetch_audit() -> list[dict[str, Any]]:
        db = get_db()
        rows = await db.fetch_all(
            "SELECT action, target, new_value FROM audit_log WHERE action = ? ORDER BY id DESC LIMIT 1",
            ("ssh_policy_update",),
        )
        return list(rows)

    rows = asyncio.new_event_loop().run_until_complete(_fetch_audit())
    assert rows, "expected an audit row for ssh_policy_update"
    assert rows[0]["target"] == "sshd"
    assert "permit_root_login" in (rows[0]["new_value"] or "")


# --- write: extra=forbid rejects unknown keys ----------------------------
def test_ssh_policy_put_rejects_unknown_keys(client: object, tokens: dict[str, str]) -> None:
    admin = _h(tokens["admin"])
    resp = client.put(  # type: ignore[attr-defined]
        "/api/security/ssh-policy",
        json={"permit_root_lgin": "no", "confirm": True},  # typo
        headers=admin,
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["error"]["code"] == "validation_error"


# --- write: role guard -----------------------------------------------------
def test_ssh_policy_put_requires_admin(client: object, tokens: dict[str, str]) -> None:
    viewer = _h(tokens["viewer"])
    resp = client.put(  # type: ignore[attr-defined]
        "/api/security/ssh-policy",
        json={"permit_root_login": "no", "confirm": True},
        headers=viewer,
    )
    assert resp.status_code == 403
