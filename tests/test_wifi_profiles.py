"""Tests for Wi-Fi profile persistence + listing (#110, audit §2).

audit.md §2 Networking: "Wi-Fi (SSID/creds/country)." The router used
to accept only ``ssid`` and forget the connection on reboot. This
change persists the full profile in ``wifi_networks`` so the HAL can
re-apply on boot, lists profiles (without the PSK) for the operator
panel, and lets an admin delete a profile.
"""

from __future__ import annotations


def _admin(tokens: dict[str, str]) -> dict[str, str]:
    return {"Authorization": f"Bearer {tokens['admin']}"}


def _operator(tokens: dict[str, str]) -> dict[str, str]:
    return {"Authorization": f"Bearer {tokens['operator']}"}


def _viewer(tokens: dict[str, str]) -> dict[str, str]:
    return {"Authorization": f"Bearer {tokens['viewer']}"}


def test_wifi_profile_round_trip(client: object, tokens: dict[str, str]) -> None:
    """Connect persists the profile; the listing returns the SSID +
    country + EAP fields, the PSK is omitted (only ``has_psk`` flag)."""
    resp = client.post(  # type: ignore[attr-defined]
        "/api/sysadmin/wifi/connect",
        json={
            "ssid": "lab-wifi",
            "psk": "supersecret123",
            "country": "US",
            "confirm": True,
        },
        headers=_operator(tokens),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["profile"]["ssid"] == "lab-wifi"
    assert body["profile"]["country"] == "US"
    assert body["applied"] is False  # simulated

    # Listing returns the profile (without the PSK).
    listing = client.get(  # type: ignore[attr-defined]
        "/api/sysadmin/wifi/profiles",
        headers=_viewer(tokens),
    )
    assert listing.status_code == 200, listing.text
    profiles = listing.json()["profiles"]
    lab = next(p for p in profiles if p["ssid"] == "lab-wifi")
    assert lab["country"] == "US"
    assert "psk" not in lab  # never return the key
    assert lab["has_psk"] is True


def test_wifi_profile_invalid_country_rejected(client: object, tokens: dict[str, str]) -> None:
    """``country`` must be ISO 3166-1 alpha-2; lowercase or 3-letter
    codes 422 via pydantic."""
    resp = client.post(  # type: ignore[attr-defined]
        "/api/sysadmin/wifi/connect",
        json={"ssid": "x", "psk": "longenough", "country": "usa"},
        headers=_operator(tokens),
    )
    assert resp.status_code == 422, resp.text


def test_wifi_profile_psk_too_short_rejected(client: object, tokens: dict[str, str]) -> None:
    """WPA2 PSKs are 8..63 chars; 7 chars 422s."""
    resp = client.post(  # type: ignore[attr-defined]
        "/api/sysadmin/wifi/connect",
        json={"ssid": "y", "psk": "short"},
        headers=_operator(tokens),
    )
    assert resp.status_code == 422, resp.text


def test_wifi_profile_unknown_key_rejected(client: object, tokens: dict[str, str]) -> None:
    """``extra=\"forbid\"`` via _StrictModel rejects typos (e.g.
    ``passphrase`` vs ``psk``) with 422 — they would otherwise
    silently land in the DB."""
    resp = client.post(  # type: ignore[attr-defined]
        "/api/sysadmin/wifi/connect",
        json={"ssid": "z", "passphrase": "typo-not-psk"},
        headers=_operator(tokens),
    )
    assert resp.status_code == 422, resp.text


def test_wifi_profile_delete_admin_only(client: object, tokens: dict[str, str]) -> None:
    """Delete is admin-only; viewer/operator are rejected with 403."""
    # First create a profile.
    client.post(  # type: ignore[attr-defined]
        "/api/sysadmin/wifi/connect",
        json={"ssid": "delete-me", "psk": "longenough", "confirm": True},
        headers=_operator(tokens),
    )
    listing = client.get(  # type: ignore[attr-defined]
        "/api/sysadmin/wifi/profiles",
        headers=_viewer(tokens),
    ).json()
    pid = next(p["id"] for p in listing["profiles"] if p["ssid"] == "delete-me")

    # viewer/operator 403
    for role, hdr in (("viewer", _viewer), ("operator", _operator)):
        resp = client.delete(  # type: ignore[attr-defined]
            f"/api/sysadmin/wifi/profiles/{pid}",
            headers=hdr(tokens),
        )
        assert resp.status_code == 403, (role, resp.text)

    # admin succeeds
    resp = client.delete(  # type: ignore[attr-defined]
        f"/api/sysadmin/wifi/profiles/{pid}",
        headers=_admin(tokens),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["ok"] is True


def test_wifi_profile_delete_unknown_404(client: object, tokens: dict[str, str]) -> None:
    """Deleting a non-existent profile id 404s."""
    resp = client.delete(  # type: ignore[attr-defined]
        "/api/sysadmin/wifi/profiles/999999",
        headers=_admin(tokens),
    )
    assert resp.status_code == 404, resp.text


def test_wifi_connect_records_audit_with_country(client: object, tokens: dict[str, str]) -> None:
    """Audit row records the country so the action log shows what
    changed, not just that the endpoint was called (#42, #110)."""
    client.post(  # type: ignore[attr-defined]
        "/api/sysadmin/wifi/connect",
        json={"ssid": "audit-me", "psk": "longenough", "country": "DE", "confirm": True},
        headers=_operator(tokens),
    )
    audit = client.get(  # type: ignore[attr-defined]
        "/api/security/audit?action=wifi_connect&limit=20",
        headers=_admin(tokens),
    )
    rows = audit.json().get("entries") or []
    ours = [r for r in rows if r["target"] == "audit-me"]
    assert ours, "expected at least one audit row for audit-me"
    assert any("country=DE" in (r.get("new_value") or "") for r in ours)


# --- #142: confirm + redacted PSK -------------------------------------------
def test_wifi_connect_requires_confirm(client: object, tokens: dict[str, str]) -> None:
    """``confirm: true`` is required (req.md §7, #142). Without it the
    action 428s the same way the other lockout-capable endpoints do."""
    resp = client.post(  # type: ignore[attr-defined]
        "/api/sysadmin/wifi/connect",
        json={"ssid": "needs-confirm", "psk": "longenough"},
        headers=_operator(tokens),
    )
    assert resp.status_code == 428, resp.text
    assert resp.json()["error"]["code"] == "confirm_required"


def test_wifi_connect_rejects_huge_psk(client: object, tokens: dict[str, str]) -> None:
    """``psk`` is bounded to WPA2's 8..63 char range; 100-char PSK 422s."""
    resp = client.post(  # type: ignore[attr-defined]
        "/api/sysadmin/wifi/connect",
        json={"ssid": "huge-psk", "psk": "x" * 100, "confirm": True},
        headers=_operator(tokens),
    )
    assert resp.status_code == 422, resp.text


def test_wifi_connect_audits_redacted_psk(client: object, tokens: dict[str, str]) -> None:
    """The audit row carries the SSID and a redacted PSK marker
    (``***``) instead of the actual key (#142)."""
    client.post(  # type: ignore[attr-defined]
        "/api/sysadmin/wifi/connect",
        json={"ssid": "redact-me", "psk": "supersecret123", "confirm": True},
        headers=_operator(tokens),
    )
    audit = client.get(  # type: ignore[attr-defined]
        "/api/security/audit?action=wifi_connect&limit=20",
        headers=_admin(tokens),
    )
    rows = audit.json().get("entries") or []
    ours = [r for r in rows if r["target"] == "redact-me"]
    assert ours
    last = ours[0]
    assert "psk=***" in (last.get("new_value") or "")
    # The actual PSK must not appear anywhere in the row.
    assert "supersecret123" not in str(last)
