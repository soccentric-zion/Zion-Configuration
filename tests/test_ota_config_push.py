"""Tests for the ``OtaConfigPush`` pydantic schema (#87, audit §8 / Guardrail 1).

The previous shape was a raw ``dict[str, Any]`` — anything pushed to
``POST /api/ota/config`` landed in the ``restore_points`` table
unvalidated. The new ``OtaConfigPush`` model rejects unknown keys,
non-semver ``version``, and out-of-vocabulary ``kind`` with a 422
*before* anything is persisted (CLAUDE.md #51 pattern).
"""

from __future__ import annotations


def _h(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _admin(t: dict[str, str]) -> dict[str, str]:
    return _h(t["admin"])


def test_valid_payload_persists_and_audits(client: object, tokens: dict[str, str]) -> None:
    """A well-formed payload persists to ``restore_points``, creates an
    ``ota_history`` row, and audits with kind+version in new_value."""
    resp = client.post(  # type: ignore[attr-defined]
        "/api/ota/config",
        json={
            "kind": "interface",
            "version": "2.1",
            "name": "interface-baseline",
            "payload": {"spi-0": {"mode": "master"}},
        },
        headers=_admin(tokens),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["applied"] is True
    assert "restore_point_id" in body
    assert body["name"] == "interface-baseline"
    # Audit row carries the kind+version so the action log distinguishes
    # config pushes by section (Guardrail 1 audit alignment).
    audit = client.get(  # type: ignore[attr-defined]
        "/api/security/audit?action=ota_config_push&limit=5",
        headers=_admin(tokens),
    )
    assert audit.status_code == 200, audit.text
    rows = audit.json().get("entries") or []
    assert any(
        r["target"] == "interface-baseline" and r["new_value"] == "kind=interface:version=2.1" for r in rows
    ), rows


def test_rejects_unknown_kind(client: object, tokens: dict[str, str]) -> None:
    """``kind`` must be one of device/interface/system/media — anything
    else is rejected at the pydantic layer (422)."""
    resp = client.post(  # type: ignore[attr-defined]
        "/api/ota/config",
        json={"kind": "unicorn", "version": "1.0", "payload": {}},
        headers=_admin(tokens),
    )
    assert resp.status_code == 422, resp.text


def test_rejects_non_semver_version(client: object, tokens: dict[str, str]) -> None:
    """``version`` must match ``^\\d+\\.\\d+$`` — anything else is rejected."""
    for bad in ("1", "1.0.0", "v1.0", "1.0-rc1", "abc", ""):
        resp = client.post(  # type: ignore[attr-defined]
            "/api/ota/config",
            json={"kind": "system", "version": bad, "payload": {}},
            headers=_admin(tokens),
        )
        assert resp.status_code == 422, (bad, resp.text)


def test_rejects_unknown_keys(client: object, tokens: dict[str, str]) -> None:
    """``extra='forbid'`` (via ``_StrictModel``) rejects unknown keys."""
    resp = client.post(  # type: ignore[attr-defined]
        "/api/ota/config",
        json={
            "kind": "system",
            "version": "1.0",
            "payload": {},
            "secret_admin_backdoor": True,
        },
        headers=_admin(tokens),
    )
    assert resp.status_code == 422, resp.text


def test_rejects_missing_kind_and_version(client: object, tokens: dict[str, str]) -> None:
    """Both ``kind`` and ``version`` are required."""
    resp = client.post(  # type: ignore[attr-defined]
        "/api/ota/config",
        json={"payload": {}},
        headers=_admin(tokens),
    )
    assert resp.status_code == 422, resp.text


def test_empty_body_returns_role_guard_first(client: object, tokens: dict[str, str]) -> None:
    """An empty POST still hits the role-guard 403 (per CLAUDE.md Do),
    not 422 — viewer/operator tokens get rejected before pydantic parses."""
    resp = client.post(  # type: ignore[attr-defined]
        "/api/ota/config",
        headers=_h(tokens["viewer"]),
    )
    assert resp.status_code == 403, resp.text


def test_requires_admin(client: object, tokens: dict[str, str]) -> None:
    """Viewer/operator tokens are rejected with 403."""
    for role in ("viewer", "operator"):
        resp = client.post(  # type: ignore[attr-defined]
            "/api/ota/config",
            json={"kind": "system", "version": "1.0", "payload": {}},
            headers=_h(tokens[role]),
        )
        assert resp.status_code == 403, (role, resp.text)


def test_optional_name_defaults_to_timestamp(client: object, tokens: dict[str, str]) -> None:
    """If ``name`` is omitted the task layer derives a timestamped name
    (``config-ota-<epoch>``)."""
    resp = client.post(  # type: ignore[attr-defined]
        "/api/ota/config",
        json={"kind": "media", "version": "1.0", "payload": {}},
        headers=_admin(tokens),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["name"].startswith("config-ota-"), body
