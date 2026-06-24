"""Password hashing and JWT token helpers."""

from __future__ import annotations

import datetime as dt
from typing import Any

import bcrypt
import jwt

from app.config import get_settings


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode(), password_hash.encode())
    except (ValueError, TypeError):
        return False


def create_access_token(username: str, role: str) -> str:
    cfg = get_settings().auth
    now = dt.datetime.now(tz=dt.UTC)
    payload: dict[str, Any] = {
        "sub": username,
        "role": role,
        "iat": int(now.timestamp()),
        "exp": int((now + dt.timedelta(minutes=cfg.access_token_ttl_minutes)).timestamp()),
    }
    return jwt.encode(payload, cfg.jwt_secret, algorithm=cfg.jwt_algorithm)


def decode_token(token: str) -> dict[str, Any]:
    """Decode and verify a JWT. Raises ``jwt.PyJWTError`` on any problem."""
    cfg = get_settings().auth
    return jwt.decode(token, cfg.jwt_secret, algorithms=[cfg.jwt_algorithm])
