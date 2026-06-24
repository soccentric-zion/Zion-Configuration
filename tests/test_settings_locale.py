"""Tests for #144 — PUT /api/settings/localization (audit §1).

`audit.md §1 System` says "locale" should be configurable. The
existing GET (`app/routers/settings.py:395`) returned a static list
and there was no write endpoint. These tests pin the persistence
semantics:

- PUT validates locale shape (regex + max length) → 422 on bad input
- PUT persists to ``system_settings_kv`` → next GET returns it
- PUT is admin-only → viewer/operator get 403
- PUT writes an audit row → ``set_locale`` is searchable
- The selected locale survives across GETs in the same session
  (i.e. it really hits the KV store, not a process-local variable)
"""

from __future__ import annotations


def _h(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _admin(t: dict[str, str]) -> dict[str, str]:
    return _h(t["admin"])


def _viewer(t: dict[str, str]) -> dict[str, str]:
    return _h(t["viewer"])


def _operator(t: dict[str, str]) -> dict[str, str]:
    return _h(t["operator"])


# --- GET baseline ----------------------------------------------------------
def test_get_localization_returns_known_set(client: object, tokens: dict[str, str]) -> None:
    """GET returns the known locale list and a null ``selected`` on a
    fresh install."""
    resp = client.get("/api/settings/localization", headers=_viewer(tokens))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "en" in body["languages"]
    assert "de" in body["languages"]
    assert body["selected"] is None


# --- PUT happy path -------------------------------------------------------
def test_put_localization_persists_across_reads(client: object, tokens: dict[str, str]) -> None:
    """A PUT is reflected in subsequent GETs — the value lives in the
    KV store, not in process state."""
    admin = _admin(tokens)
    r = client.put("/api/settings/localization", json={"locale": "de"}, headers=admin)
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True
    assert r.json()["locale"] == "de"

    g = client.get("/api/settings/localization", headers=_viewer(tokens))
    assert g.status_code == 200, g.text
    assert g.json()["selected"] == "de"


def test_put_localization_supports_region_suffix(client: object, tokens: dict[str, str]) -> None:
    """Region-tagged locales (``pt_BR``, ``zh_Hans_CN``) pass the regex."""
    admin = _admin(tokens)
    for loc in ("pt_BR", "en_US", "zh_CN", "de_AT"):
        r = client.put("/api/settings/localization", json={"locale": loc}, headers=admin)
        assert r.status_code == 200, (loc, r.text)
        assert r.json()["locale"] == loc


def test_put_localization_supports_unknown_locale(client: object, tokens: dict[str, str]) -> None:
    """A syntactically-valid locale that is NOT in the known set
    (e.g. ``de_AT`` before a translation ships) is still accepted —
    the SPA falls back to the closest matching known locale."""
    admin = _admin(tokens)
    r = client.put("/api/settings/localization", json={"locale": "de_AT"}, headers=admin)
    assert r.status_code == 200, r.text
    g = client.get("/api/settings/localization", headers=_viewer(tokens))
    assert g.json()["selected"] == "de_AT"
    # "de_AT" is NOT in the known list — proving the merge logic keeps
    # the operator's pick visible.
    assert "de_AT" not in g.json()["languages"]


def test_put_overwrites_previous_locale(client: object, tokens: dict[str, str]) -> None:
    """A second PUT replaces the first — no list-of-history."""
    admin = _admin(tokens)
    client.put("/api/settings/localization", json={"locale": "de"}, headers=admin)
    r = client.put("/api/settings/localization", json={"locale": "fr"}, headers=admin)
    assert r.status_code == 200, r.text
    g = client.get("/api/settings/localization", headers=_viewer(tokens))
    assert g.json()["selected"] == "fr"


# --- PUT validation --------------------------------------------------------
def test_put_rejects_uppercase_language(client: object, tokens: dict[str, str]) -> None:
    """``EN_us`` — uppercase language is rejected by the regex."""
    resp = client.put("/api/settings/localization", json={"locale": "EN"}, headers=_admin(tokens))
    assert resp.status_code == 422, resp.text


def test_put_rejects_path_traversal(client: object, tokens: dict[str, str]) -> None:
    """A locale containing ``..`` or ``/`` must 422."""
    for bad in ("../etc/passwd", "en/../", "en.", "en..US", ".en"):
        r = client.put("/api/settings/localization", json={"locale": bad}, headers=_admin(tokens))
        assert r.status_code == 422, (bad, r.text)


def test_put_rejects_too_long(client: object, tokens: dict[str, str]) -> None:
    """max_length=32 is enforced by pydantic."""
    long_locale = "a" * 64
    resp = client.put("/api/settings/localization", json={"locale": long_locale}, headers=_admin(tokens))
    assert resp.status_code == 422, resp.text


def test_put_rejects_whitespace_and_empty(client: object, tokens: dict[str, str]) -> None:
    """Whitespace and empty strings fail the regex / min_length."""
    for bad in ("", " ", "en us", "en\tus", "\n"):
        r = client.put("/api/settings/localization", json={"locale": bad}, headers=_admin(tokens))
        assert r.status_code == 422, (bad, r.text)


def test_put_rejects_extra_field(client: object, tokens: dict[str, str]) -> None:
    """``extra="forbid"`` rejects typos in the payload."""
    resp = client.put(
        "/api/settings/localization",
        json={"locale": "en", "rogue": 1},
        headers=_admin(tokens),
    )
    assert resp.status_code == 422, resp.text


# --- role guard -----------------------------------------------------------
def test_put_requires_admin(client: object, tokens: dict[str, str]) -> None:
    """Locale changes affect every session → admin only."""
    for role, hdr in (
        ("viewer", _viewer(tokens)),
        ("operator", _operator(tokens)),
    ):
        r = client.put(
            "/api/settings/localization",
            json={"locale": "en"},
            headers=hdr,
        )
        assert r.status_code == 403, (role, r.text)


# --- audit trail ----------------------------------------------------------
def test_put_writes_audit_row(client: object, tokens: dict[str, str]) -> None:
    """Each PUT produces a ``set_locale`` audit row."""
    admin = _admin(tokens)
    client.put("/api/settings/localization", json={"locale": "fr"}, headers=admin)
    audit = client.get("/api/security/audit?action=set_locale&limit=10", headers=admin).json()
    assert any(r["target"] == "system" and r["new_value"] == "fr" for r in audit["entries"]), audit
