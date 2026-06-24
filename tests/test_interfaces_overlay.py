"""Tests for interfaces commit-overlay on enable/disable (#107, audit §3).

audit.md §3 Peripherals: "Write changes as device-tree overlay /
config fragment." Previously ``POST /api/interfaces/{name}/enable``
only flipped a DB column; the live kernel/devfs was untouched. The
``commit_overlay`` HAL method now returns the would-be applied
envelope with ``applied: not self.simulated`` so the SPA can show
"would apply" vs "applied" and the audit row reflects a real state
change (#42).
"""

from __future__ import annotations


def _admin(tokens: dict[str, str]) -> dict[str, str]:
    return {"Authorization": f"Bearer {tokens['admin']}"}


def _operator(tokens: dict[str, str]) -> dict[str, str]:
    return {"Authorization": f"Bearer {tokens['operator']}"}


def test_enable_returns_overlay_envelope(client: object, tokens: dict[str, str]) -> None:
    """Enabling an interface returns the would-be applied overlay envelope."""
    resp = client.post(  # type: ignore[attr-defined]
        "/api/interfaces/i2c-1/enable",
        json={"enabled": True},
        headers=_operator(tokens),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["enabled"] is True
    overlay = body["overlay"]
    # The envelope contract (#42) — every HAL write returns simulated/applied
    # so the SPA can show "would apply" vs "applied".
    assert "simulated" in overlay
    assert "applied" in overlay
    assert overlay["enabled"] is True
    assert overlay["loaded"] is True
    assert overlay["overlay_path"].endswith("/i2c-1.dtbo")


def test_disable_returns_overlay_envelope(client: object, tokens: dict[str, str]) -> None:
    """Disabling an interface returns the removal envelope (loaded=False)."""
    # First enable so disable is a real transition.
    client.post(  # type: ignore[attr-defined]
        "/api/interfaces/uart0/enable",
        json={"enabled": True},
        headers=_operator(tokens),
    )
    resp = client.post(  # type: ignore[attr-defined]
        "/api/interfaces/uart0/enable",
        json={"enabled": False},
        headers=_operator(tokens),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["enabled"] is False
    overlay = body["overlay"]
    assert overlay["enabled"] is False
    assert overlay["loaded"] is False
    assert overlay["overlay_path"].endswith("/uart0.dtbo")


def test_enable_persists_and_audits_applied_flag(client: object, tokens: dict[str, str]) -> None:
    """Audit row records the `applied: true|false` so the action log
    reflects the actual state change (#42)."""
    client.post(  # type: ignore[attr-defined]
        "/api/interfaces/spi-0/enable",
        json={"enabled": False},
        headers=_operator(tokens),
    )
    client.post(  # type: ignore[attr-defined]
        "/api/interfaces/spi-0/enable",
        json={"enabled": True},
        headers=_operator(tokens),
    )
    audit = client.get(  # type: ignore[attr-defined]
        "/api/security/audit?action=interface_enable&limit=10",
        headers=_admin(tokens),
    )
    rows = audit.json().get("entries") or []
    targets = [r for r in rows if r["target"] == "spi-0"]
    assert targets, "expected at least one audit row for spi-0"
    # The most recent enable row must mention `applied=` so operators can
    # see whether the overlay actually committed or was simulated.
    latest_enable = next(r for r in targets if "enabled=True" in (r["new_value"] or ""))
    assert "applied=" in latest_enable["new_value"]


def test_enable_unknown_interface_returns_404(client: object, tokens: dict[str, str]) -> None:
    """Unknown interface name still 404s — the overlay commit runs *after*
    the row lookup so a typo doesn't silently fall through to a HAL call."""
    resp = client.post(  # type: ignore[attr-defined]
        "/api/interfaces/does-not-exist/enable",
        json={"enabled": True},
        headers=_operator(tokens),
    )
    assert resp.status_code == 404, resp.text
