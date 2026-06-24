"""Regression tests for audit/history limit caps (issue #39).

`GET /api/system/history` and `GET /api/security/audit` previously accepted
arbitrary `limit=` query values, letting a request ask for 10M rows. They
now cap at 10k and 5k respectively and return 422 on out-of-range.
"""

from __future__ import annotations


def _admin(t: dict[str, str]) -> dict[str, str]:
    return {"Authorization": f"Bearer {t['admin']}"}


def test_history_limit_rejects_huge(client: object, tokens: dict[str, str]) -> None:
    r = client.get(  # type: ignore[attr-defined]
        "/api/system/history?metric=cpu_total&limit=10000000", headers=_admin(tokens)
    )
    assert r.status_code == 422


def test_history_limit_rejects_zero(client: object, tokens: dict[str, str]) -> None:
    r = client.get(  # type: ignore[attr-defined]
        "/api/system/history?metric=cpu_total&limit=0", headers=_admin(tokens)
    )
    assert r.status_code == 422


def test_history_limit_accepts_in_range(client: object, tokens: dict[str, str]) -> None:
    r = client.get(  # type: ignore[attr-defined]
        "/api/system/history?metric=cpu_total&limit=10", headers=_admin(tokens)
    )
    assert r.status_code == 200, r.text


def test_audit_limit_rejects_huge(client: object, tokens: dict[str, str]) -> None:
    r = client.get(  # type: ignore[attr-defined]
        "/api/security/audit?limit=10000000", headers=_admin(tokens)
    )
    assert r.status_code == 422


def test_audit_limit_accepts_in_range(client: object, tokens: dict[str, str]) -> None:
    r = client.get(  # type: ignore[attr-defined]
        "/api/security/audit?limit=50", headers=_admin(tokens)
    )
    assert r.status_code == 200, r.text
