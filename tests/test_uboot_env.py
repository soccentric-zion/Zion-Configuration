"""Regression test for #131 — U-Boot env key validation (audit §4).

audit.md Guardrail 1 (validate before writing) + audit report §4 +
CQ14 / CQ19. U-Boot env keys are a constrained alphabet; a typo
silently overwrites nothing in simulation but would brick the device
in production.

The fix:
- ``UbootEnvSet`` pydantic model in ``app/util/models.py`` constrains
  ``key`` to ``^[A-Za-z][A-Za-z0-9_]*$`` AND an allow-list of known
  keys (``boot_partion`` — a typo — is rejected with 422).
- ``value`` is bounded to 256 chars.
- The router enforces ``confirm: true``.
"""

from __future__ import annotations


def _h(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_uboot_env_rejects_unknown_key(client: object, tokens: dict[str, str]) -> None:
    """``boot_partion`` (typo) is in the legal alphabet but NOT in the
    allow-list — 422 (#131)."""
    admin = _h(tokens["admin"])
    resp = client.post(  # type: ignore[attr-defined]
        "/api/overview/boot/uboot-env",
        json={"key": "boot_partion", "value": "1", "confirm": True},
        headers=admin,
    )
    assert resp.status_code == 422, resp.text


def test_uboot_env_rejects_invalid_pattern(client: object, tokens: dict[str, str]) -> None:
    """``9boot_partition`` starts with a digit — pydantic pattern
    rejects it with 422."""
    admin = _h(tokens["admin"])
    resp = client.post(  # type: ignore[attr-defined]
        "/api/overview/boot/uboot-env",
        json={"key": "9boot_partition", "value": "1", "confirm": True},
        headers=admin,
    )
    assert resp.status_code == 422, resp.text


def test_uboot_env_accepts_valid_key(client: object, tokens: dict[str, str]) -> None:
    """A real key (``boot_partition``) is accepted, audit-logged, and
    returns 200."""
    admin = _h(tokens["admin"])
    resp = client.post(  # type: ignore[attr-defined]
        "/api/overview/boot/uboot-env",
        json={"key": "boot_partition", "value": "1", "confirm": True},
        headers=admin,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["ok"] is True


def test_uboot_env_rejects_oversized_value(client: object, tokens: dict[str, str]) -> None:
    """``value`` is bounded to 256 chars — 257 chars is rejected."""
    admin = _h(tokens["admin"])
    resp = client.post(  # type: ignore[attr-defined]
        "/api/overview/boot/uboot-env",
        json={"key": "boot_partition", "value": "x" * 257, "confirm": True},
        headers=admin,
    )
    assert resp.status_code == 422, resp.text


def test_uboot_env_rejects_missing_confirm(client: object, tokens: dict[str, str]) -> None:
    """Privileged write — ``confirm: true`` is required (428)."""
    admin = _h(tokens["admin"])
    resp = client.post(  # type: ignore[attr-defined]
        "/api/overview/boot/uboot-env",
        json={"key": "boot_partition", "value": "1"},
        headers=admin,
    )
    assert resp.status_code == 428, resp.text


def test_uboot_env_rejects_extra_field(client: object, tokens: dict[str, str]) -> None:
    """``extra="forbid"`` rejects typos like ``partition_id``."""
    admin = _h(tokens["admin"])
    resp = client.post(  # type: ignore[attr-defined]
        "/api/overview/boot/uboot-env",
        json={"key": "boot_partition", "value": "1", "confirm": True, "rogue": 1},
        headers=admin,
    )
    assert resp.status_code == 422, resp.text


def test_uboot_env_requires_admin(client: object, tokens: dict[str, str]) -> None:
    """A viewer cannot set a U-Boot env key."""
    viewer = _h(tokens["viewer"])
    resp = client.post(  # type: ignore[attr-defined]
        "/api/overview/boot/uboot-env",
        json={"key": "boot_partition", "value": "1", "confirm": True},
        headers=viewer,
    )
    assert resp.status_code == 403, resp.text
