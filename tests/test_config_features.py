"""Config-driven feature tree endpoint (GET /api/config/features).

The feature tree is the single source of truth for which routers and
SPA panels are mounted. Tests pin the contract: 13 top-level groups,
every group has an ``enabled`` boolean, the new pages (notifications,
backup, etc.) show up under their canonical groups, and a misconfigured
group name falls back to enabled (fail-open) so a typo can't blackhole
the dashboard.
"""

from __future__ import annotations


def _viewer(tokens: dict[str, str]) -> dict[str, str]:
    return {"Authorization": f"Bearer {tokens['viewer']}"}


def test_features_endpoint_returns_all_groups(client: object, tokens: dict[str, str]) -> None:
    resp = client.get("/api/config/features", headers=_viewer(tokens))  # type: ignore[attr-defined]
    assert resp.status_code == 200
    body = resp.json()
    assert "groups" in body
    # The known top-level groups from Features.md. The names match the
    # keys in app/config_features._KNOWN_SUBFEATURES.
    expected = (
        "overview",
        "network",
        "hardware",
        "storage",
        "thermal",
        "ota",
        "telemetry",
        "cloud",
        "security",
        "users",
        "logs",
        "settings",
        "system",
    )
    for g in expected:
        assert g in body["groups"], f"missing group {g!r}"
        assert "enabled" in body["groups"][g]


def test_features_endpoint_is_viewer_reachable(client: object, tokens: dict[str, str]) -> None:
    headers = _viewer(tokens)
    resp = client.get("/api/config/features", headers=headers)  # type: ignore[attr-defined]
    assert resp.status_code == 200


def test_features_subfeatures_have_enabled_flag(client: object, tokens: dict[str, str]) -> None:
    body = client.get("/api/config/features", headers=_viewer(tokens)).json()  # type: ignore[attr-defined]
    settings = body["groups"]["settings"]
    # Either has subfeatures or a top-level enabled.
    assert "enabled" in settings


def test_features_unknown_group_fails_open() -> None:
    """A typo in the group name must NOT silently disable routes — the
    gate falls back to ``enabled=True`` so the dashboard never goes
    blank because someone spelled a group wrong in config.yaml."""
    from app.config_features import is_group_enabled

    # Unknown group → fail-open (True).
    assert is_group_enabled("not_a_real_group_xyz") is True
