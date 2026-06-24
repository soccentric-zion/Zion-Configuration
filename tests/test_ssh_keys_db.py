"""SSH key management uses the DB (migration v4) so it survives a restart and
is shared across uvicorn workers. Replaces the previous module-level
_SSH_KEYS list (issue #23).
"""

from __future__ import annotations


def test_ssh_keys_seeded_in_db(client: object, tokens: dict[str, str]) -> None:
    """First call returns the seeded example key (now from DB, not module)."""
    admin = {"Authorization": f"Bearer {tokens['admin']}"}
    resp = client.get("/api/security/ssh-keys", headers=admin)  # type: ignore[attr-defined]
    assert resp.status_code == 200
    keys = resp.json()["keys"]
    # The seed is in the table; the count may include keys added by other
    # tests in the same session.
    assert any(k["comment"] == "ops@laptop" for k in keys)


def test_ssh_keys_persist_across_gets(client: object, tokens: dict[str, str]) -> None:
    """Adding a key in one request must show up on the next GET — the bug was
    that the state lived in a module-level list which died between calls
    in some test configurations."""
    admin = {"Authorization": f"Bearer {tokens['admin']}"}
    new_key = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAISecondTestKeyForFix23 test@second"
    add = client.post(  # type: ignore[attr-defined]
        "/api/security/ssh-keys", json={"key": new_key}, headers=admin
    )
    assert add.status_code == 200, add.text
    listing = client.get("/api/security/ssh-keys", headers=admin)  # type: ignore[attr-defined]
    assert listing.status_code == 200
    assert any(k["key"] == new_key for k in listing.json()["keys"])


def test_ssh_keys_duplicate_rejected(client: object, tokens: dict[str, str]) -> None:
    """Re-adding the same key returns 409 (UNIQUE constraint)."""
    admin = {"Authorization": f"Bearer {tokens['admin']}"}
    dup = client.post(  # type: ignore[attr-defined]
        "/api/security/ssh-keys",
        json={"key": "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIExampleKeyData ops@laptop"},
        headers=admin,
    )
    assert dup.status_code == 409
