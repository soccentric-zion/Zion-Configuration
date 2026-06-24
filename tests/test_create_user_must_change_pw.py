"""Regression tests for create_user must_change_pw (#45).

Admin-issued passwords used to persist indefinitely because
`service.create_user` did not set `must_change_pw=1`, and the
`UserOut` response hardcoded `must_change_password=False`.
"""

from __future__ import annotations


def _admin(tokens: dict[str, str]) -> dict[str, str]:
    return {"Authorization": f"Bearer {tokens['admin']}"}


def test_create_user_forces_password_change_on_first_login(client: object, tokens: dict[str, str]) -> None:
    h = _admin(tokens)
    # Provision a new user via the admin endpoint.
    resp = client.post(  # type: ignore[attr-defined]
        "/api/users",
        json={"username": "newuser1", "password": "initial-pw", "role": "operator"},
        headers=h,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert (
        body["must_change_password"] is True
    ), "admin-issued credentials must force a password change on first login (#45)"

    # Login with the issued password; the token response must signal the
    # forced change so the SPA can route to the change-password page.
    login = client.post(  # type: ignore[attr-defined]
        "/api/login", json={"username": "newuser1", "password": "initial-pw"}
    )
    assert login.status_code == 200, login.text
    assert login.json()["must_change_password"] is True

    # After the user changes their password, must_change_pw must clear.
    new_token = login.json()["access_token"]
    change = client.post(  # type: ignore[attr-defined]
        "/api/me/password",
        json={"old_password": "initial-pw", "new_password": "user-picked-pw"},
        headers={"Authorization": f"Bearer {new_token}"},
    )
    assert change.status_code == 200, change.text

    me = client.get("/api/me", headers={"Authorization": f"Bearer {new_token}"})  # type: ignore[attr-defined]
    assert me.json()["must_change_password"] is False
