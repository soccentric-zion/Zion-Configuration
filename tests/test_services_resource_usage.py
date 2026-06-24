"""Tests for per-service-unit resource usage (#100, audit §5).

audit.md §5 requires ``cpu_pct`` and ``mem_bytes`` per active systemd
unit so operators can see which service is consuming CPU/memory.
The HAL populates these on each ``services()`` call; in simulation
mode they are deterministic per-unit so the SPA renders consistent
rows across reloads.
"""

from __future__ import annotations


def _h(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_active_unit_has_cpu_and_mem(client: object, tokens: dict[str, str]) -> None:
    """Every active unit in the services list carries ``cpu_pct`` and
    ``mem_bytes`` (#100)."""
    resp = client.get(  # type: ignore[attr-defined]
        "/api/sysadmin/services",
        headers=_h(tokens["viewer"]),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    units = body["units"]
    assert units, "services fixture should expose some units"
    for u in units:
        assert "cpu_pct" in u, f"unit {u['name']} missing cpu_pct: {u}"
        assert "mem_bytes" in u, f"unit {u['name']} missing mem_bytes: {u}"
        if u.get("active") == "active":
            # Active units must report a CPU reading (>=0) and a memory
            # footprint (>0) so the SPA can sort / highlight them.
            assert u["cpu_pct"] >= 0, u
            assert u["mem_bytes"] > 0, u
        else:
            # Inactive units must explicitly show 0/0 — never the
            # previous reading of a stopped neighbour.
            assert u["cpu_pct"] == 0.0, u
            assert u["mem_bytes"] == 0, u


def test_cpu_pct_is_stable_across_reads(client: object, tokens: dict[str, str]) -> None:
    """In simulation, two consecutive reads of the same active unit
    produce the same ``cpu_pct`` (deterministic per-unit seed)."""
    headers = _h(tokens["viewer"])
    first = client.get("/api/sysadmin/services", headers=headers).json()  # type: ignore[attr-defined]
    second = client.get("/api/sysadmin/services", headers=headers).json()  # type: ignore[attr-defined]
    by_name_1 = {u["name"]: u for u in first["units"]}
    by_name_2 = {u["name"]: u for u in second["units"]}
    for name, u1 in by_name_1.items():
        if u1.get("active") == "active":
            assert by_name_2[name]["cpu_pct"] == u1["cpu_pct"], name
            assert by_name_2[name]["mem_bytes"] == u1["mem_bytes"], name


def test_stopped_unit_reports_zero(client: object, tokens: dict[str, str]) -> None:
    """Stopping an active unit must drop its cpu/mem to 0/0 on the next
    read — operators should not see a stale reading from a unit that is
    no longer running (#100)."""
    admin = _h(tokens["admin"])
    # docker.service is active by default and is not on the protected
    # list (dashboard, ssh, NetworkManager, chronyd are), so it is
    # safe to stop for the test.
    resp = client.post(  # type: ignore[attr-defined]
        "/api/sysadmin/services/docker.service/stop",
        json={"confirm": True},
        headers=admin,
    )
    assert resp.status_code == 200, resp.text
    resp = client.get(  # type: ignore[attr-defined]
        "/api/sysadmin/services",
        headers=_h(tokens["viewer"]),
    )
    body = resp.json()
    docker = next(u for u in body["units"] if u["name"] == "docker.service")
    assert docker["active"] != "active"
    assert docker["cpu_pct"] == 0.0
    assert docker["mem_bytes"] == 0
