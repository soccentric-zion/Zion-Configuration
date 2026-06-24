"""Input validation helpers for privileged system-admin actions.

Network and firewall changes can lock out the host, so inputs are validated
before anything is applied. These raise :class:`ApiError` (400) on bad input.
"""

from __future__ import annotations

import ipaddress

from app.errors import ApiError


def valid_ip(value: str, field: str = "address") -> str:
    """Validate an IPv4 address.

    The rest of the network stack (netmask validator, pydantic
    ``StaticNetworkConfig``) is IPv4-only — the original ``valid_ip`` here
    also accepted IPv6, which let a caller pass an ``::1`` address with an
    ``/24`` netmask and the inconsistency wasn't caught. Now the validator
    matches the rest of the layer and only IPv4 is accepted (#36).
    """
    try:
        ipaddress.IPv4Address(value)
    except (ipaddress.AddressValueError, ValueError) as exc:
        raise ApiError("bad_ip", f"Invalid IPv4 address for '{field}': {value}", 400) from exc
    return value


def valid_netmask(value: str) -> str:
    try:
        # Accept dotted-quad masks and refuse anything non-contiguous.
        ipaddress.ip_network(f"0.0.0.0/{value}", strict=False)
    except ValueError as exc:
        raise ApiError("bad_netmask", f"Invalid netmask: {value}", 400) from exc
    return value


def valid_port(value: int, field: str = "port") -> int:
    if not 1 <= int(value) <= 65535:
        raise ApiError("bad_port", f"'{field}' must be between 1 and 65535", 400)
    return int(value)


def valid_protocol(value: str) -> str:
    proto = str(value).lower()
    if proto not in {"tcp", "udp", "any"}:
        raise ApiError("bad_protocol", "protocol must be tcp, udp or any", 400)
    return proto
