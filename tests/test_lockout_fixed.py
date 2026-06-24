"""Regression test for #54 — auth lockout is fixed-until-cleared, not
a sliding window.

The previous implementation counted failures in the last ``lockout_minutes``
and refused new logins when the count crossed ``max_failed_logins``. That
meant an attacker could lock-pulse: hit the threshold, wait for the window
to roll, hit it again. The fix arms a ``users.locked_until`` column on the
crossing failure; ``is_locked()`` checks that timestamp, not the recent
count.
"""

from __future__ import annotations

import datetime as dt

from app.auth.security import hash_password
from app.auth.service import _clear_lock, _lock_user, is_locked
from app.db.database import get_db


async def _seed_user(username: str, password: str = "hunter2") -> None:
    await get_db().execute(
        "INSERT OR REPLACE INTO users "
        "(username, password_hash, role, enabled, must_change_pw) "
        "VALUES (?, ?, 'viewer', 1, 0)",
        (username, hash_password(password)),
    )


async def _set_locked_until(username: str, when: dt.datetime) -> None:
    stamp = when.strftime("%Y-%m-%d %H:%M:%S")
    await get_db().execute("UPDATE users SET locked_until = ? WHERE username = ?", (stamp, username))


async def test_is_locked_true_when_timestamp_in_future(client: object) -> None:
    await _seed_user("locky-future")
    await _set_locked_until("locky-future", dt.datetime.now(tz=dt.UTC) + dt.timedelta(minutes=10))
    assert await is_locked("locky-future") is True


async def test_is_locked_false_when_timestamp_in_past(client: object) -> None:
    await _seed_user("locky-past")
    await _set_locked_until("locky-past", dt.datetime.now(tz=dt.UTC) - dt.timedelta(seconds=5))
    assert await is_locked("locky-past") is False


async def test_is_locked_false_when_column_null(client: object) -> None:
    await _seed_user("locky-null")
    assert await is_locked("locky-null") is False


async def test_lock_user_helper_arms_and_clear_lock_clears(client: object) -> None:
    await _seed_user("locky-helper")
    await _lock_user("locky-helper", 1)
    assert await is_locked("locky-helper") is True
    await _clear_lock("locky-helper")
    assert await is_locked("locky-helper") is False


def test_lockout_persists_past_recent_failure_window(client: object) -> None:
    """End-to-end via the API: arm the lockout by failing logins, then
    verify the lock is sticky — even the *correct* password is refused
    while the lock is armed. This is the core regression for #54: a
    sliding window would let the user back in as soon as the recent
    count dropped, but a fixed lockout stays armed."""
    admin_login = client.post("/api/login", json={"username": "admin", "password": "admin"})
    assert admin_login.status_code == 200
    token = admin_login.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    user = "sticky-victim"
    create = client.post(
        "/api/users",
        json={"username": user, "password": f"{user}-pw", "role": "viewer"},
        headers=headers,
    )
    assert create.status_code == 201, create.text
    # Hammer with wrong password until 423 trips.
    last = None
    for _ in range(10):
        last = client.post("/api/login", json={"username": user, "password": "wrong"})
        if last.status_code == 423:
            break
    assert last is not None and last.status_code == 423
    # The correct password must also be refused — the lock holds.
    good = client.post("/api/login", json={"username": user, "password": f"{user}-pw"})
    assert good.status_code == 423
