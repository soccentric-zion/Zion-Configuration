"""Regression tests for privileged-endpoint confirm guard (issue #34).

fsck, suspend, set_rtc, set_uboot_env, boot/rollback, and watchdog/test
all skip the `require_confirm` server-side check, violating the project's
"Privileged actions" policy. They must now return 428 when `confirm` is
missing or false, and 200 with the right side-effect when it is true.
"""

from __future__ import annotations

import pytest


def _admin(t: dict[str, str]) -> dict[str, str]:
    return {"Authorization": f"Bearer {t['admin']}"}


@pytest.mark.parametrize(
    "method,path,body",
    [
        ("post", "/api/storage/fsck", {"device": "/dev/mmcblk0p1"}),
        ("post", "/api/storage/power/suspend", {}),
        ("post", "/api/storage/rtc", {"time": "2026-01-01T00:00:00Z"}),
        ("post", "/api/overview/boot/uboot-env", {"key": "boot_count", "value": "0"}),
        ("post", "/api/overview/boot/rollback", {}),
        ("post", "/api/thermal/watchdog/test", {}),
    ],
)
def test_privileged_endpoint_requires_confirm(
    client: object, tokens: dict[str, str], method: str, path: str, body: dict[str, object]
) -> None:
    """No confirm -> 428."""
    resp = getattr(client, method)(  # type: ignore[attr-defined]
        path, json=body, headers=_admin(tokens)
    )
    assert resp.status_code == 428, f"{method.upper()} {path} should require confirm"
    assert resp.json()["error"]["code"] == "confirm_required"


@pytest.mark.parametrize(
    "method,path,body",
    [
        ("post", "/api/storage/fsck", {"device": "/dev/mmcblk0p1", "confirm": True}),
        ("post", "/api/storage/power/suspend", {"confirm": True}),
        ("post", "/api/storage/rtc", {"time": "2026-01-01T00:00:00Z", "confirm": True}),
        ("post", "/api/overview/boot/uboot-env", {"key": "boot_count", "value": "0", "confirm": True}),
        ("post", "/api/overview/boot/rollback", {"confirm": True}),
        ("post", "/api/thermal/watchdog/test", {"confirm": True}),
    ],
)
def test_privileged_endpoint_accepts_confirm(
    client: object, tokens: dict[str, str], method: str, path: str, body: dict[str, object]
) -> None:
    """Explicit confirm -> 200."""
    resp = getattr(client, method)(  # type: ignore[attr-defined]
        path, json=body, headers=_admin(tokens)
    )
    assert resp.status_code == 200, resp.text
