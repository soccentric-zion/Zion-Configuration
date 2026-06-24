"""Path-traversal protection for the file manager (hard security boundary)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.errors import ApiError
from app.util.paths import safe_resolve


def test_allows_file_inside_root(sandbox: Path) -> None:
    resolved = safe_resolve(str(sandbox / "hello.txt"))
    assert resolved == (sandbox / "hello.txt").resolve()


def test_rejects_dotdot_escape(sandbox: Path) -> None:
    with pytest.raises(ApiError) as exc:
        safe_resolve(str(sandbox / ".." / ".." / "etc" / "passwd"))
    assert exc.value.status_code == 403


def test_rejects_encoded_dotdot(sandbox: Path) -> None:
    with pytest.raises(ApiError) as exc:
        safe_resolve(f"{sandbox}/..%2f..%2fetc%2fpasswd")
    assert exc.value.status_code == 403


def test_rejects_absolute_outside_allowlist() -> None:
    with pytest.raises(ApiError) as exc:
        safe_resolve("/etc/shadow")
    assert exc.value.status_code == 403


def test_rejects_nul_byte(sandbox: Path) -> None:
    with pytest.raises(ApiError) as exc:
        safe_resolve(f"{sandbox}/hello.txt\x00.png")
    assert exc.value.status_code == 400


def test_rejects_symlink_escape(sandbox: Path, tmp_path: Path) -> None:
    # A symlink inside the sandbox pointing outside must not grant access.
    secret = tmp_path / "outside_secret.txt"
    secret.write_text("top secret")
    link = sandbox / "escape_link"
    if link.exists() or link.is_symlink():
        link.unlink()
    os.symlink(secret, link)
    with pytest.raises(ApiError) as exc:
        safe_resolve(str(link))
    assert exc.value.status_code == 403
