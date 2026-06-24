"""Regression test for network_link accepting non-existent interfaces (#50).

`POST /api/sysadmin/network/{name}/link` used to flip the DB row to
`up=1` even when psutil did not see the interface on the host. The
fix cross-references the live kernel view and returns 404 if the
interface is unknown, so the operator isn't told a stale link is up.
"""

from __future__ import annotations


def _admin(tokens: dict[str, str]) -> dict[str, str]:
    return {"Authorization": f"Bearer {tokens['admin']}"}


def test_network_link_404_when_interface_not_on_host(client: object, tokens: dict[str, str], monkeypatch) -> None:
    """eth0 missing from the live kernel view → 404 interface_not_present (#50)."""
    h = _admin(tokens)
    import app.routers.sysadmin as sysadmin_router

    class _FakeSa:
        def net_status(self) -> dict[str, object]:
            # Live kernel view reports no interfaces (USB NIC removed).
            return {"interfaces": []}

    monkeypatch.setattr(sysadmin_router, "_sa", lambda: _FakeSa())

    resp = client.post(  # type: ignore[attr-defined]
        "/api/sysadmin/network/eth0/link",
        json={"up": True},
        headers=h,
    )
    assert resp.status_code == 404, resp.text
    assert resp.json()["error"]["code"] == "interface_not_present"


def test_network_link_succeeds_when_interface_present(client: object, tokens: dict[str, str]) -> None:
    """eth0 is in the seed and in the simulated live view, so the call succeeds."""
    h = _admin(tokens)
    resp = client.post(  # type: ignore[attr-defined]
        "/api/sysadmin/network/eth0/link",
        json={"up": True},
        headers=h,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"name": "eth0", "up": True}
