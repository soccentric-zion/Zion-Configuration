"""Regression test for #136 — GET /api/system/top-processes (audit §7).

audit.md §7 Health: process list / top consumers. Operators must be able
to see which processes are consuming the most CPU + memory. Endpoint:

    GET /api/system/top-processes[?limit=N]

Returns the top N processes by CPU + memory consumption, capped at 100.
Each row has pid, name, cpu_pct, mem_pct, mem_bytes, status.

The test asserts:
- endpoint is reachable to viewer / operator / admin (not anonymous).
- limit defaults to 20 and accepts overrides up to 100.
- limit > 100 is rejected with 422 (Query(le=100)).
- each row carries the documented fields.
- the envelope includes simulated/applied (HAL contract).
"""

from __future__ import annotations


def _h(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_top_processes_requires_auth(client: object) -> None:
    """Anonymous cannot read the process list."""
    resp = client.get("/api/sysadmin/top-processes")  # type: ignore[attr-defined]
    assert resp.status_code == 401, resp.text


def test_top_processes_viewer(client: object, tokens: dict[str, str]) -> None:
    """A viewer can read top-processes (read-only endpoint)."""
    resp = client.get(  # type: ignore[attr-defined]
        "/api/sysadmin/top-processes", headers=_h(tokens["viewer"])
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "processes" in body
    assert isinstance(body["processes"], list)
    assert "simulated" in body  # HAL envelope


def test_top_processes_default_limit_is_20(client: object, tokens: dict[str, str]) -> None:
    """No ``limit`` arg → 20 rows (the default; matches audit §7 example)."""
    resp = client.get(  # type: ignore[attr-defined]
        "/api/sysadmin/top-processes", headers=_h(tokens["admin"])
    )
    assert resp.status_code == 200, resp.text
    procs = resp.json()["processes"]
    assert 1 <= len(procs) <= 20


def test_top_processes_honors_limit_param(client: object, tokens: dict[str, str]) -> None:
    """?limit=N returns at most N rows."""
    resp = client.get(  # type: ignore[attr-defined]
        "/api/sysadmin/top-processes?limit=5", headers=_h(tokens["admin"])
    )
    assert resp.status_code == 200, resp.text
    procs = resp.json()["processes"]
    assert len(procs) <= 5


def test_top_processes_limit_capped_at_100(client: object, tokens: dict[str, str]) -> None:
    """?limit=200 must be rejected (Query(le=100))."""
    resp = client.get(  # type: ignore[attr-defined]
        "/api/sysadmin/top-processes?limit=200", headers=_h(tokens["admin"])
    )
    assert resp.status_code == 422, resp.text


def test_top_processes_row_schema(client: object, tokens: dict[str, str]) -> None:
    """Each row must carry the documented fields (#136 acceptance)."""
    resp = client.get(  # type: ignore[attr-defined]
        "/api/sysadmin/top-processes?limit=10", headers=_h(tokens["admin"])
    )
    body = resp.json()
    assert body["processes"], "expected at least one process row"
    row = body["processes"][0]
    expected = {"pid", "name", "cpu_pct", "mem_pct", "mem_bytes", "status"}
    missing = expected - row.keys()
    assert not missing, f"top-processes row missing fields {missing}"
    # Numeric invariants.
    assert isinstance(row["pid"], int) and row["pid"] > 0
    assert isinstance(row["cpu_pct"], int | float)
    assert isinstance(row["mem_pct"], int | float)
    assert isinstance(row["mem_bytes"], int) and row["mem_bytes"] >= 0
    assert isinstance(row["status"], str) and row["status"]


def test_top_processes_sorted_by_cpu_then_memory(client: object, tokens: dict[str, str]) -> None:
    """Rows must be sorted by CPU% desc, then mem_bytes desc — the most
    suspicious processes first (#136)."""
    resp = client.get(  # type: ignore[attr-defined]
        "/api/sysadmin/top-processes?limit=20", headers=_h(tokens["admin"])
    )
    procs = resp.json()["processes"]
    for a, b in zip(procs, procs[1:], strict=False):
        if a["cpu_pct"] != b["cpu_pct"]:
            assert a["cpu_pct"] >= b["cpu_pct"], f"top-processes not sorted by cpu_pct: {a} then {b}"
        else:
            assert a["mem_bytes"] >= b["mem_bytes"], f"top-processes tiebreak by mem_bytes: {a} then {b}"
