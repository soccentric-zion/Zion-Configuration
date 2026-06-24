"""Pydantic models for auth requests/responses."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Role = Literal["admin", "operator", "viewer"]
UserSource = Literal["local", "system"]


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: Role
    username: str
    must_change_password: bool


class UserOut(BaseModel):
    id: int
    username: str
    role: Role
    enabled: bool
    must_change_password: bool
    # Features.md §Settings Dashboard Users — per-row "where do
    # credentials live?" toggle. "local" = dashboard-managed,
    # "system" = PAM-mapped; dashboard-local is the safe default.
    user_source: UserSource = "local"
    system_username: str | None = None


class UserCreate(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=4, max_length=256)
    role: Role = "viewer"


class UserUpdate(BaseModel):
    role: Role | None = None
    enabled: bool | None = None


class PasswordChange(BaseModel):
    old_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=4, max_length=256)


class PasswordReset(BaseModel):
    new_password: str = Field(min_length=4, max_length=256)
