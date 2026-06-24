"""Regression tests for the cloud /provisioning/endpoint fix (issue #31).

`set_endpoint` used to call `str(body)` on the entire request body, storing
the Python dict repr ("{'endpoint': 'https://...'}") in the DB. Now it
extracts `body["endpoint"]` and validates it's an http(s):// URL.
"""

from __future__ import annotations


def _admin(tokens: dict[str, str]) -> dict[str, str]:
    return {"Authorization": f"Bearer {tokens['admin']}"}


def test_set_endpoint_stores_url_not_dict_repr(client: object, tokens: dict[str, str]) -> None:
    """The URL itself (not a repr) is persisted."""
    admin = _admin(tokens)
    url = "https://cloud.example.com/api/v1"
    resp = client.post(  # type: ignore[attr-defined]
        "/api/cloud/provisioning/endpoint", json={"endpoint": url}, headers=admin
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["endpoint"] == url
    # The provisioning GET now shows the URL.
    got = client.get("/api/cloud/provisioning", headers=admin)  # type: ignore[attr-defined]
    assert got.status_code == 200
    print("DEBUG got:", got.json())
    assert got.json()["provisioning"]["endpoint"] == url
    # The repr-style string must NOT be present anywhere in the response.
    assert "{'endpoint'" not in str(got.json())


def test_set_endpoint_rejects_missing_field(client: object, tokens: dict[str, str]) -> None:
    admin = _admin(tokens)
    resp = client.post(  # type: ignore[attr-defined]
        "/api/cloud/provisioning/endpoint", json={}, headers=admin
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "missing_field"


def test_set_endpoint_rejects_non_url(client: object, tokens: dict[str, str]) -> None:
    admin = _admin(tokens)
    resp = client.post(  # type: ignore[attr-defined]
        "/api/cloud/provisioning/endpoint", json={"endpoint": "ftp://nope"}, headers=admin
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "bad_endpoint"
