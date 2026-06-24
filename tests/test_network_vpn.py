"""Tests for VPN apply real wg-quick invocation (#102, audit §2).

audit.md §2 Networking: VPN client is a real wg-quick invocation
on a real device. The HAL envelopes ``applied: True`` in real mode
and ``applied: False`` in simulation so the SPA can distinguish
"would apply" from "applied" (#42). The router records the
``applied`` flag in the audit row so the action log shows what
really happened, not just what was requested (#42).
"""

from __future__ import annotations

from unittest.mock import patch


def _admin(tokens: dict[str, str]) -> dict[str, str]:
    return {"Authorization": f"Bearer {tokens['admin']}"}


def test_vpn_apply_real_invokes_wg_quick() -> None:
    """In real mode, vpn_apply shells out to ``wg-quick {action} {target}``
    and surfaces a non-zero exit as an ApiError(500, vpn_apply_failed)."""
    from app.hal.network import NetworkHal

    hal = NetworkHal(force_sim=False)
    # Force _real to True so the real branch is exercised even when
    # wg-quick / NetworkManager is missing on the test host.
    hal._real = True
    fake = type(
        "R",
        (),
        {"returncode": 0, "stdout": "ok\n", "stderr": ""},
    )()
    with patch("subprocess.run", return_value=fake) as run:
        result = hal.vpn_apply("wg0", "up")
    args, _ = run.call_args
    assert args[0] == ["wg-quick", "up", "wg0"], args
    assert result["simulated"] is False
    assert result["applied"] is True
    assert result["target"] == "wg0"
    assert result["action"] == "up"


def test_vpn_apply_real_wg_quick_failure_raises() -> None:
    """A non-zero exit from wg-quick becomes ApiError(vpn_apply_failed, 500)
    so the router can return a clear error envelope instead of a
    silent success."""
    from app.errors import ApiError
    from app.hal.network import NetworkHal

    hal = NetworkHal(force_sim=False)
    hal._real = True
    fake = type(
        "R",
        (),
        {"returncode": 1, "stdout": "", "stderr": "wg-quick: `wg0' already exists\n"},
    )()
    with patch("subprocess.run", return_value=fake):
        try:
            hal.vpn_apply("wg0", "up")
        except ApiError as exc:
            assert exc.code == "vpn_apply_failed"
            assert exc.status_code == 500
            assert "wg0" in exc.message
        else:
            raise AssertionError("expected ApiError")


def test_vpn_apply_real_wg_quick_missing_raises() -> None:
    """If ``wg-quick`` is not installed, the call fails with
    ``vpn_apply_failed`` and a 500 — the SPA should not see a fake
    success."""
    from app.errors import ApiError
    from app.hal.network import NetworkHal

    hal = NetworkHal(force_sim=False)
    hal._real = True
    with patch("subprocess.run", side_effect=FileNotFoundError("wg-quick")):
        try:
            hal.vpn_apply("wg0", "up")
        except ApiError as exc:
            assert exc.code == "vpn_apply_failed"
            assert exc.status_code == 500
        else:
            raise AssertionError("expected ApiError")


def test_vpn_apply_sim_returns_applied_false(client: object, tokens: dict[str, str]) -> None:
    """In simulation the endpoint returns ``applied: False`` and the
    audit row records the applied flag so the action log shows what
    really happened (#42)."""
    resp = client.post(  # type: ignore[attr-defined]
        "/api/network/vpn",
        json={"action": "up", "interface": "wg0"},
        headers=_admin(tokens),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["simulated"] is True
    assert body["applied"] is False
    # Audit row carries the applied flag so the operator can tell from
    # the log that the op was simulated, not actually executed.
    audit = client.get(  # type: ignore[attr-defined]
        "/api/security/audit?action=vpn_action&limit=5",
        headers=_admin(tokens),
    )
    assert audit.status_code == 200, audit.text
    rows = audit.json().get("entries") or []
    assert any(r["target"] == "wg0" and r.get("new_value") == "up:applied=False" for r in rows), rows
