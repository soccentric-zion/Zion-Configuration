"""Encrypted backup round-trip and tamper/wrong-password detection."""

from __future__ import annotations

import pytest

from app.errors import ApiError
from app.tasks.backup import decrypt_backup, encrypt_backup


def test_round_trip() -> None:
    payload = {"hostname": "device-01", "rules": [1, 2, 3]}
    blob = encrypt_backup(payload, "correct horse battery staple")
    assert decrypt_backup(blob, "correct horse battery staple") == payload


def test_wrong_password_raises() -> None:
    blob = encrypt_backup({"a": 1}, "right-password")
    with pytest.raises(ApiError) as exc:
        decrypt_backup(blob, "wrong-password")
    assert exc.value.code == "bad_password"


def test_truncated_blob_raises() -> None:
    with pytest.raises(ApiError) as exc:
        decrypt_backup(b"short", "pw")
    assert exc.value.code == "bad_backup"


def test_tampered_ciphertext_raises() -> None:
    blob = bytearray(encrypt_backup({"a": 1}, "pw"))
    blob[-1] ^= 0xFF  # flip a bit in the authenticated ciphertext
    with pytest.raises(ApiError):
        decrypt_backup(bytes(blob), "pw")


def test_salt_makes_outputs_unique() -> None:
    a = encrypt_backup({"x": 1}, "pw")
    b = encrypt_backup({"x": 1}, "pw")
    assert a != b  # random salt per encryption
