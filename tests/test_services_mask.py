"""Tests for services mask/unmask actions (#108, audit §5).

audit.md §5 Services: "mask/unmask" should be supported. Mask is
stronger than disable — a masked unit cannot be started even by an
explicit ``systemctl start``. It's required for hardening workflows
where an operator wants to make a unit unstartable, not just disabled.
"""

from __future__ import annotations


def _admin(tokens: dict[str, str]) -> dict[str, str]:
    return {"Authorization": f"Bearer {tokens['admin']}"}


def test_mask_requires_confirm(client: object, tokens: dict[str, str]) -> None:
    """``mask`` is destructive (prevents start) and requires confirm=true."""
    # No confirm → 428 (confirm_required) — same bucket as stop/disable/restart.
    resp = client.post(  # type: ignore[attr-defined]
        "/api/sysadmin/services/docker.service/mask",
        headers=_admin(tokens),
    )
    assert resp.status_code == 428, resp.text
    assert resp.json()["error"]["code"] == "confirm_required"


def test_mask_with_confirm_audits(client: object, tokens: dict[str, str]) -> None:
    """A confirmed mask returns 200, tags the unit, and produces a
    ``service_mask`` audit row."""
    resp = client.post(  # type: ignore[attr-defined]
        "/api/sysadmin/services/docker.service/mask",
        json={"confirm": True},
        headers=_admin(tokens),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "unit" in body
    assert body["unit"].get("masked") is True
    # Audit row exists
    audit = client.get(  # type: ignore[attr-defined]
        "/api/security/audit?action=service_mask&limit=5",
        headers=_admin(tokens),
    )
    rows = audit.json().get("entries") or []
    targets = {r["target"] for r in rows}
    assert "docker.service" in targets


def test_unmask_works_without_confirm(client: object, tokens: dict[str, str]) -> None:
    """``unmask`` is the inverse of mask and is *not* destructive — it
    restores the ability to start, so it doesn't require confirm."""
    # First mask so the unmask is a real transition.
    client.post(  # type: ignore[attr-defined]
        "/api/sysadmin/services/docker.service/mask",
        json={"confirm": True},
        headers=_admin(tokens),
    )
    resp = client.post(  # type: ignore[attr-defined]
        "/api/sysadmin/services/docker.service/unmask",
        headers=_admin(tokens),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["unit"].get("masked") is False


def test_mask_unknown_action_returns_400(client: object, tokens: dict[str, str]) -> None:
    """Bad action verbs are rejected at the router (400 bad_action)."""
    resp = client.post(  # type: ignore[attr-defined]
        "/api/sysadmin/services/docker.service/garbage",
        headers=_admin(tokens),
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["error"]["code"] == "bad_action"
