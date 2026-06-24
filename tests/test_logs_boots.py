"""Regression test for #135 — GET /api/files/logs/boots (audit §6).

audit.md §6 Logs: per-boot list. Operators must be able to compare
logs from boot N vs boot N-1, which requires a stable list of boots
with first/last-seen timestamps.

Real mode parses ``journalctl --list-boots``; simulation returns a
deterministic 3-boot timeline. The endpoint is viewer-level (read
only).
"""

from __future__ import annotations


def _h(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_boots_requires_auth(client: object) -> None:
    """Anonymous cannot read the boot list."""
    resp = client.get("/api/files/logs/boots")  # type: ignore[attr-defined]
    assert resp.status_code == 401, resp.text


def test_boots_list_returns_at_least_one(client: object, tokens: dict[str, str]) -> None:
    """The endpoint must return at least one boot entry (#135 acceptance)."""
    resp = client.get(  # type: ignore[attr-defined]
        "/api/files/logs/boots", headers=_h(tokens["viewer"])
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "boots" in body
    assert isinstance(body["boots"], list)
    assert body["boots"], "expected at least one boot in the list"
    # The HAL envelope is required (audit / banner consistency).
    assert "simulated" in body


def test_boots_row_schema(client: object, tokens: dict[str, str]) -> None:
    """Each boot row carries index, first_seen, last_seen."""
    resp = client.get(  # type: ignore[attr-defined]
        "/api/files/logs/boots", headers=_h(tokens["admin"])
    )
    boots = resp.json()["boots"]
    for row in boots:
        assert "index" in row and isinstance(row["index"], int)
        assert "first_seen" in row and isinstance(row["first_seen"], str)
        assert "last_seen" in row and isinstance(row["last_seen"], str)
        assert row["first_seen"], "first_seen must be a non-empty timestamp"


def test_boots_sorted_most_recent_first(client: object, tokens: dict[str, str]) -> None:
    """The list is ordered most-recent-first so the SPA can render it
    as-is in a table (boot N at the top)."""
    resp = client.get(  # type: ignore[attr-defined]
        "/api/files/logs/boots", headers=_h(tokens["admin"])
    )
    boots = resp.json()["boots"]
    # In simulated mode, index 1 is the most recent and has the
    # latest last_seen.
    assert boots[0]["index"] == 1
    if len(boots) >= 2:
        # Subsequent rows have older last_seen timestamps.
        from datetime import datetime

        def _parse(ts: str) -> datetime:
            return datetime.fromisoformat(ts.replace("Z", "+00:00"))

        first_last = _parse(boots[0]["last_seen"])
        second_last = _parse(boots[1]["last_seen"])
        assert first_last >= second_last, f"boots not sorted most-recent-first: {boots[0]} then {boots[1]}"
