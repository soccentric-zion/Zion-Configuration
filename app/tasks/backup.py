"""Encrypted backup / restore of dashboard configuration.

Produces a single password-protected archive of the current configuration and
restore points. Uses Fernet (AES-128-CBC + HMAC) with a PBKDF2-HMAC-SHA256
derived key, so the round-trip is authenticated — tampering is detected on
restore. The salt is prepended to the ciphertext.
"""

from __future__ import annotations

import base64
import json
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from app.errors import ApiError

_SALT_LEN = 16
_ITERATIONS = 200_000


def _derive_key(password: str, salt: bytes) -> bytes:
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=_ITERATIONS)
    return base64.urlsafe_b64encode(kdf.derive(password.encode()))


def encrypt_backup(payload: dict[str, Any], password: str) -> bytes:
    import os

    salt = os.urandom(_SALT_LEN)
    token = Fernet(_derive_key(password, salt)).encrypt(json.dumps(payload).encode())
    return salt + token


def decrypt_backup(blob: bytes, password: str) -> dict[str, Any]:
    if len(blob) <= _SALT_LEN:
        raise ApiError("bad_backup", "Backup file is corrupt or truncated", 400)
    salt, token = blob[:_SALT_LEN], blob[_SALT_LEN:]
    try:
        data = Fernet(_derive_key(password, salt)).decrypt(token)
    except InvalidToken as exc:
        raise ApiError("bad_password", "Wrong password or corrupted backup", 400) from exc
    result: dict[str, Any] = json.loads(data.decode())
    return result
