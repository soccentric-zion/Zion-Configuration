"""Breadth smoke tests: every parameter-free GET endpoint answers happily.

These iterate the live route table so newly added read endpoints are covered
automatically, and they drive the HAL/router code paths that the targeted tests
do not, keeping the coverage gate meaningful.
"""

from __future__ import annotations

import pytest
from fastapi.routing import APIRoute

# Query parameters some GET endpoints require. Unknown params are ignored by
# FastAPI, so it is safe to send the whole bag to every endpoint.
_COMMON_PARAMS = {
    "metric": "cpu_total",
    "target": "127.0.0.1",
    "host": "127.0.0.1",
    "unit": "system",
    "limit": 5,
}


def _get_routes(app: object) -> list[str]:
    paths: list[str] = []
    for route in app.routes:  # type: ignore[attr-defined]
        if not isinstance(route, APIRoute):
            continue
        if not route.path.startswith("/api"):
            continue
        if "{" in route.path:  # skip path-parameterized routes here
            continue
        if "GET" not in route.methods:
            continue
        paths.append(route.path)
    return sorted(set(paths))


def test_all_simple_get_endpoints_ok(client: object, tokens: dict[str, str]) -> None:
    headers = {"Authorization": f"Bearer {tokens['admin']}"}
    failures: list[str] = []
    for path in _get_routes(client.app):  # type: ignore[attr-defined]
        # The file manager needs a real allowlisted path; covered separately.
        if path == "/api/files":
            continue
        resp = client.get(  # type: ignore[attr-defined]
            path, params=_COMMON_PARAMS, headers=headers
        )
        if resp.status_code >= 500:
            failures.append(f"{path} -> {resp.status_code}: {resp.text[:120]}")
    assert not failures, "Endpoints raised 5xx:\n" + "\n".join(failures)


@pytest.mark.parametrize(
    "path",
    [
        "/api/network/wifi",
        "/api/network/cellular",
        "/api/network/routing",
        "/api/network/firewall",
        "/api/hardware/pwm",
        "/api/hardware/adc",
        "/api/hardware/usb",
        "/api/hardware/i2c",
        "/api/hardware/spi",
        "/api/hardware/can",
        "/api/storage/power",
        "/api/storage/rtc",
        "/api/thermal/throttling",
        "/api/thermal/watchdog",
        "/api/cloud/twin",
        "/api/cloud/telemetry",
        "/api/security/certificates",
        "/api/security/sbom",
        "/api/security/cve",
        "/api/security/licenses",
        "/api/security/compliance",
        "/api/settings/system",
        "/api/settings/localization",
        "/api/software/ota/versions",
        "/api/software/ota/hawkbit",
    ],
)
def test_representative_reads_have_body(client: object, tokens: dict[str, str], path: str) -> None:
    resp = client.get(  # type: ignore[attr-defined]
        path, headers={"Authorization": f"Bearer {tokens['viewer']}"}
    )
    assert resp.status_code == 200, f"{path} -> {resp.status_code} {resp.text}"
    assert resp.json()
