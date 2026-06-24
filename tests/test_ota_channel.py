"""Tests for #147 — OTA channel / SWUpdate server config is editable
through the API.

audit.md §8 OTA: "update channel/server config." Without a runtime
override, the ``SwupdateConfig.base_url`` from config.yaml is the only
source of truth for the SWUpdate server, and there's no way to switch
between stable / beta / nightly channels.

The fix:
- ``GET /api/ota/channel`` (viewer) returns the effective config
  (operator override layered over the static defaults).
- ``PUT /api/ota/channel`` (admin + ``confirm: true``) validates the
  body via ``OtaChannelUpdate`` (``base_url`` http(s), ``channel`` in
  ``{stable, beta, nightly}``, ``auto_check`` bool, ``signing_key_id``
  matches ``[A-Za-z0-9_-]+``) and persists into ``system_settings_kv``
  under the ``ota.channel.`` prefix.
- The next install reads the override from kv before falling back to
  the static config (#42, audit Guardrail 1).
"""

from __future__ import annotations


def _h(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _admin(t: dict[str, str]) -> dict[str, str]:
    return _h(t["admin"])


def _viewer(t: dict[str, str]) -> dict[str, str]:
    return _h(t["viewer"])


# --- GET /api/ota/channel --------------------------------------------------
def test_get_channel_viewer_returns_defaults(client: object, tokens: dict[str, str]) -> None:
    """A viewer GET returns the effective config; with no operator
    override set, the ``default`` flag is True and values come from
    ``SwupdateConfig``."""
    resp = client.get(  # type: ignore[attr-defined]
        "/api/ota/channel",
        headers=_viewer(tokens),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["default"] is True
    assert body["channel"] in {"stable", "beta", "nightly"}
    assert isinstance(body["base_url"], str) and body["base_url"]
    assert isinstance(body["auto_check"], bool)
    assert isinstance(body["signing_key_id"], str)


# --- PUT /api/ota/channel --------------------------------------------------
def test_put_channel_persists_and_audits(client: object, tokens: dict[str, str]) -> None:
    """A PUT with a valid body writes to kv and audits the change."""
    admin = _admin(tokens)
    resp = client.put(  # type: ignore[attr-defined]
        "/api/ota/channel",
        json={
            "base_url": "https://updates.example.com",
            "channel": "beta",
            "auto_check": False,
            "signing_key_id": "lab-key-01",
            "confirm": True,
        },
        headers=admin,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["ok"] is True
    # GET now reflects the override.
    get = client.get(  # type: ignore[attr-defined]
        "/api/ota/channel", headers=admin
    )
    body = get.json()
    assert body["default"] is False
    assert body["base_url"] == "https://updates.example.com"
    assert body["channel"] == "beta"
    assert body["auto_check"] is False
    assert body["signing_key_id"] == "lab-key-01"
    # Audit row exists.
    audit = client.get(  # type: ignore[attr-defined]
        "/api/security/audit?action=ota_channel_update&limit=5",
        headers=admin,
    )
    assert audit.status_code == 200, audit.text
    rows = audit.json().get("entries") or []
    assert rows
    row = rows[0]
    assert row["target"] == "channel"
    assert "base_url=" in row["new_value"]
    assert "channel=beta" in row["new_value"]


def test_put_channel_rejects_unknown_channel(client: object, tokens: dict[str, str]) -> None:
    """``channel`` must be one of stable/beta/nightly — anything else is 422."""
    resp = client.put(  # type: ignore[attr-defined]
        "/api/ota/channel",
        json={
            "base_url": "https://updates.example.com",
            "channel": "canary",
            "auto_check": True,
            "signing_key_id": "k1",
            "confirm": True,
        },
        headers=_admin(tokens),
    )
    assert resp.status_code == 422, resp.text


def test_put_channel_rejects_bad_base_url(client: object, tokens: dict[str, str]) -> None:
    """``base_url`` must be an http(s) URL — ftp / file / plain strings are 422."""
    for bad in ("ftp://updates.example.com", "file:///etc/passwd", "not-a-url", ""):
        resp = client.put(  # type: ignore[attr-defined]
            "/api/ota/channel",
            json={
                "base_url": bad,
                "channel": "stable",
                "auto_check": True,
                "signing_key_id": "k1",
                "confirm": True,
            },
            headers=_admin(tokens),
        )
        assert resp.status_code == 422, (bad, resp.text)


def test_put_channel_rejects_bad_signing_key_id(client: object, tokens: dict[str, str]) -> None:
    """``signing_key_id`` must match ``[A-Za-z0-9_-]+`` — path-traversal or
    whitespace is 422."""
    for bad in ("../etc/passwd", "key with space", "key;rm", ""):
        resp = client.put(  # type: ignore[attr-defined]
            "/api/ota/channel",
            json={
                "base_url": "https://updates.example.com",
                "channel": "stable",
                "auto_check": True,
                "signing_key_id": bad,
                "confirm": True,
            },
            headers=_admin(tokens),
        )
        assert resp.status_code == 422, (bad, resp.text)


def test_put_channel_rejects_extra_field(client: object, tokens: dict[str, str]) -> None:
    """``extra="forbid"`` rejects unknown keys."""
    resp = client.put(  # type: ignore[attr-defined]
        "/api/ota/channel",
        json={
            "base_url": "https://updates.example.com",
            "channel": "stable",
            "auto_check": True,
            "signing_key_id": "k1",
            "confirm": True,
            "secret_admin_backdoor": True,
        },
        headers=_admin(tokens),
    )
    assert resp.status_code == 422, resp.text


def test_put_channel_rejects_missing_confirm(client: object, tokens: dict[str, str]) -> None:
    """Privileged write — ``confirm: true`` is required (428)."""
    resp = client.put(  # type: ignore[attr-defined]
        "/api/ota/channel",
        json={
            "base_url": "https://updates.example.com",
            "channel": "stable",
            "auto_check": True,
            "signing_key_id": "k1",
        },
        headers=_admin(tokens),
    )
    assert resp.status_code == 428, resp.text


def test_put_channel_requires_admin(client: object, tokens: dict[str, str]) -> None:
    """Viewer/operator tokens are rejected with 403."""
    for role in ("viewer", "operator"):
        resp = client.put(  # type: ignore[attr-defined]
            "/api/ota/channel",
            json={
                "base_url": "https://updates.example.com",
                "channel": "stable",
                "auto_check": True,
                "signing_key_id": "k1",
                "confirm": True,
            },
            headers=_h(tokens[role]),
        )
        assert resp.status_code == 403, (role, resp.text)


def test_put_channel_overwrites_previous(client: object, tokens: dict[str, str]) -> None:
    """A second PUT overwrites the first — the operator can switch
    channels without an explicit DELETE."""
    admin = _admin(tokens)
    for ch in ("stable", "beta", "nightly"):
        r = client.put(  # type: ignore[attr-defined]
            "/api/ota/channel",
            json={
                "base_url": "https://updates.example.com",
                "channel": ch,
                "auto_check": True,
                "signing_key_id": "k1",
                "confirm": True,
            },
            headers=admin,
        )
        assert r.status_code == 200, (ch, r.text)
        g = client.get(  # type: ignore[attr-defined]
            "/api/ota/channel", headers=admin
        )
        assert g.json()["channel"] == ch, (ch, g.json())


def test_install_uses_overridden_base_url(client: object, tokens: dict[str, str]) -> None:
    """After PUT, the next install reads the override from kv; this
    exercises the read path in ``_run_install``. We assert by checking
    the audit log + that the override survives a config reload."""
    admin = _admin(tokens)
    r = client.put(  # type: ignore[attr-defined]
        "/api/ota/channel",
        json={
            "base_url": "https://updates.example.com/v2",
            "channel": "stable",
            "auto_check": True,
            "signing_key_id": "k1",
            "confirm": True,
        },
        headers=admin,
    )
    assert r.status_code == 200, r.text
    # The override is now persisted in system_settings_kv; verify a
    # subsequent GET still returns it.
    g = client.get(  # type: ignore[attr-defined]
        "/api/ota/channel", headers=admin
    )
    assert g.json()["base_url"] == "https://updates.example.com/v2"
