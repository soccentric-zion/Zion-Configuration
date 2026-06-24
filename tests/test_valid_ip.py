"""Regression tests for the IPv4-only valid_ip fix (issue #36)."""

from __future__ import annotations

import pytest

from app.util.validate import valid_ip


def test_valid_ip_accepts_ipv4() -> None:
    assert valid_ip("10.0.0.1") == "10.0.0.1"
    assert valid_ip("192.168.1.1") == "192.168.1.1"
    assert valid_ip("0.0.0.0") == "0.0.0.0"


def test_valid_ip_rejects_ipv6() -> None:
    """The rest of the network stack is IPv4-only; an IPv6 address must now
    be rejected to keep the validators consistent."""
    from app.errors import ApiError

    with pytest.raises(ApiError) as exc:
        valid_ip("::1", "gateway")
    assert exc.value.status_code == 400
    assert exc.value.code == "bad_ip"


def test_valid_ip_rejects_garbage() -> None:
    from app.errors import ApiError

    with pytest.raises(ApiError) as exc:
        valid_ip("not-an-ip", "address")
    assert exc.value.code == "bad_ip"


def test_valid_ip_field_in_error_message() -> None:
    """The field name surfaces in the error so the UI can show it."""
    from app.errors import ApiError

    with pytest.raises(ApiError) as exc:
        valid_ip("::1", "gateway")
    assert "gateway" in str(exc.value)
