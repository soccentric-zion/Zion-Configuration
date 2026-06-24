"""Regression tests for SSH key format validation (issue #38).

Previously `add_ssh_key` only checked ``len(parts) >= 2``, so a string like
``rm -rf /`` (3 parts) was accepted and stored as the user's SSH key.
Now the validator enforces an allow-list of key types and a base64-shaped
blob. Real ed25519 keys still work (existing tests cover that).
"""

from __future__ import annotations


def _admin(t: dict[str, str]) -> dict[str, str]:
    return {"Authorization": f"Bearer {t['admin']}"}


def test_add_ssh_key_rejects_garbage(client: object, tokens: dict[str, str]) -> None:
    """Arbitrary command-like strings are rejected with 400 bad_key_type."""
    r = client.post(  # type: ignore[attr-defined]
        "/api/security/ssh-keys", json={"key": "rm -rf /"}, headers=_admin(tokens)
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "bad_key_type"


def test_add_ssh_key_rejects_unknown_type(client: object, tokens: dict[str, str]) -> None:
    """A bogus type prefix is rejected even with a base64-shaped blob."""
    r = client.post(  # type: ignore[attr-defined]
        "/api/security/ssh-keys",
        json={"key": "ssh-bogus AAAAC3NzaC1lZDI1NTE5AAAAITestKeyDataForFlow test@flow"},
        headers=_admin(tokens),
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "bad_key_type"


def test_add_ssh_key_rejects_non_base64_blob(client: object, tokens: dict[str, str]) -> None:
    """A real type with a non-base64 blob is rejected."""
    r = client.post(  # type: ignore[attr-defined]
        "/api/security/ssh-keys",
        json={"key": "ssh-ed25519 not!base64! at-all"},
        headers=_admin(tokens),
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "bad_key_blob"


def test_add_ssh_key_rejects_short_blob(client: object, tokens: dict[str, str]) -> None:
    """A real type with a too-short blob is rejected."""
    r = client.post(  # type: ignore[attr-defined]
        "/api/security/ssh-keys",
        json={"key": "ssh-ed25519 AAAAAAAA short@blob"},
        headers=_admin(tokens),
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "bad_key_blob"


def test_add_ssh_key_rejects_single_token(client: object, tokens: dict[str, str]) -> None:
    """A key without type + blob is rejected (bad_key, not bad_key_type)."""
    r = client.post(  # type: ignore[attr-defined]
        "/api/security/ssh-keys", json={"key": "ssh-rsa"}, headers=_admin(tokens)
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "bad_key"


def test_add_ssh_key_accepts_rsa(client: object, tokens: dict[str, str]) -> None:
    """ssh-rsa with a valid blob is still accepted."""
    r = client.post(  # type: ignore[attr-defined]
        "/api/security/ssh-keys",
        json={"key": "ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAAAQQDExampleKeyDataForRsaTest user@host"},
        headers=_admin(tokens),
    )
    assert r.status_code == 200, r.text
