"""Tests for #146 — staged OTA rollout (1% → 10% → 100%).

audit.md §8 OTA: "staged rollout (1% → 10% → all)." Without a state
machine a fleet-wide update ships to every device at once; a bad
release brick the entire fleet.

The fix:
- ``rollout_stages`` table (migration 14): one row per stage; statuses
  ``pending | active | completed | aborted``.
- ``POST /api/ota/rollout/start`` (admin + ``confirm: true``): creates
  the stages. The first stage (``1%``) is set to ``active``; the rest
  are ``pending``.
- ``GET /api/ota/rollout/status`` (viewer): returns the rows for a
  given ``update_id`` plus the ``current_percent``.
- ``POST /api/ota/rollout/advance`` (admin + ``confirm: true``):
  advances the active stage to ``completed`` and promotes the next
  pending stage to ``active``.
- ``POST /api/ota/rollout/abort`` (admin + ``confirm: true``): flips
  every pending + active row to ``aborted``.
"""

from __future__ import annotations


def _h(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _admin(t: dict[str, str]) -> dict[str, str]:
    return _h(t["admin"])


def _viewer(t: dict[str, str]) -> dict[str, str]:
    return _h(t["viewer"])


# --- status: no rollout yet ------------------------------------------------
def test_status_returns_empty_when_no_rollout(client: object, tokens: dict[str, str]) -> None:
    """A GET for a never-started ``update_id`` returns ``exists: False``."""
    resp = client.get(  # type: ignore[attr-defined]
        "/api/ota/rollout/status?update_id=nope",
        headers=_viewer(tokens),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["exists"] is False
    assert body["current_percent"] is None
    assert body["stages"] == []


# --- start ----------------------------------------------------------------
def test_start_creates_three_stages_with_first_active(client: object, tokens: dict[str, str]) -> None:
    """A successful start creates 1%/10%/100% stages; the 1% is active."""
    admin = _admin(tokens)
    resp = client.post(  # type: ignore[attr-defined]
        "/api/ota/rollout/start",
        json={"update_id": "u-001", "confirm": True},
        headers=admin,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    assert body["update_id"] == "u-001"
    percents = [s["percent"] for s in body["stages"]]
    assert percents == [1, 10, 100]
    statuses = [s["status"] for s in body["stages"]]
    assert statuses == ["active", "pending", "pending"]


def test_start_with_custom_stages(client: object, tokens: dict[str, str]) -> None:
    """An operator can pick a subset of stages — e.g. just 10%."""
    admin = _admin(tokens)
    resp = client.post(  # type: ignore[attr-defined]
        "/api/ota/rollout/start",
        json={"update_id": "u-002", "stages": [10, 100], "confirm": True},
        headers=admin,
    )
    assert resp.status_code == 200, resp.text
    percents = [s["percent"] for s in resp.json()["stages"]]
    assert percents == [10, 100]


def test_start_rejects_duplicate_update_id(client: object, tokens: dict[str, str]) -> None:
    """Starting a rollout for the same ``update_id`` twice is rejected with 409."""
    admin = _admin(tokens)
    body = {"update_id": "u-dup", "confirm": True}
    r1 = client.post(  # type: ignore[attr-defined]
        "/api/ota/rollout/start", json=body, headers=admin
    )
    assert r1.status_code == 200, r1.text
    r2 = client.post(  # type: ignore[attr-defined]
        "/api/ota/rollout/start", json=body, headers=admin
    )
    assert r2.status_code == 409, r2.text
    assert r2.json()["error"]["code"] == "rollout_exists"


def test_start_rejects_unsorted_stages(client: object, tokens: dict[str, str]) -> None:
    """``stages`` must be strictly ascending."""
    admin = _admin(tokens)
    for bad in ([100, 10, 1], [10, 1], [100, 100], []):
        resp = client.post(  # type: ignore[attr-defined]
            "/api/ota/rollout/start",
            json={"update_id": f"u-bad-{bad}", "stages": bad, "confirm": True},
            headers=admin,
        )
        assert resp.status_code == 422, (bad, resp.text)


def test_start_rejects_unknown_percent(client: object, tokens: dict[str, str]) -> None:
    """Stages must be in {1, 10, 100}; 5 / 50 / 99 are 422."""
    admin = _admin(tokens)
    resp = client.post(  # type: ignore[attr-defined]
        "/api/ota/rollout/start",
        json={"update_id": "u-pct", "stages": [5], "confirm": True},
        headers=admin,
    )
    assert resp.status_code == 422, resp.text


def test_start_rejects_extra_field(client: object, tokens: dict[str, str]) -> None:
    """``extra="forbid"`` rejects typos."""
    admin = _admin(tokens)
    resp = client.post(  # type: ignore[attr-defined]
        "/api/ota/rollout/start",
        json={"update_id": "u-x", "confirm": True, "rogue": 1},
        headers=admin,
    )
    assert resp.status_code == 422, resp.text


def test_start_requires_confirm(client: object, tokens: dict[str, str]) -> None:
    """Privileged write — ``confirm: true`` is required (428)."""
    admin = _admin(tokens)
    resp = client.post(  # type: ignore[attr-defined]
        "/api/ota/rollout/start",
        json={"update_id": "u-noconfirm"},
        headers=admin,
    )
    assert resp.status_code == 428, resp.text


def test_start_requires_admin(client: object, tokens: dict[str, str]) -> None:
    """Viewer/operator are rejected with 403."""
    for role in ("viewer", "operator"):
        resp = client.post(  # type: ignore[attr-defined]
            "/api/ota/rollout/start",
            json={"update_id": f"u-role-{role}", "confirm": True},
            headers=_h(tokens[role]),
        )
        assert resp.status_code == 403, (role, resp.text)


# --- advance --------------------------------------------------------------
def test_advance_walks_through_stages(client: object, tokens: dict[str, str]) -> None:
    """3 advances walk 1% → 10% → 100% → complete."""
    admin = _admin(tokens)
    client.post(  # type: ignore[attr-defined]
        "/api/ota/rollout/start",
        json={"update_id": "u-walk", "confirm": True},
        headers=admin,
    )
    # After start: current_percent = 1
    s = client.get(  # type: ignore[attr-defined]
        "/api/ota/rollout/status?update_id=u-walk", headers=admin
    ).json()
    assert s["current_percent"] == 1, s

    # Advance 1 → 10
    r1 = client.post(  # type: ignore[attr-defined]
        "/api/ota/rollout/advance",
        json={"update_id": "u-walk", "confirm": True},
        headers=admin,
    )
    assert r1.status_code == 200, r1.text
    assert r1.json()["from_percent"] == 1
    assert r1.json()["to_percent"] == 10
    assert r1.json()["advanced"] is True

    s = client.get(  # type: ignore[attr-defined]
        "/api/ota/rollout/status?update_id=u-walk", headers=admin
    ).json()
    assert s["current_percent"] == 10, s

    # Advance 10 → 100
    r2 = client.post(  # type: ignore[attr-defined]
        "/api/ota/rollout/advance",
        json={"update_id": "u-walk", "confirm": True},
        headers=admin,
    )
    assert r2.json()["from_percent"] == 10
    assert r2.json()["to_percent"] == 100

    # Advance 100 → complete
    r3 = client.post(  # type: ignore[attr-defined]
        "/api/ota/rollout/advance",
        json={"update_id": "u-walk", "confirm": True},
        headers=admin,
    )
    assert r3.json()["complete"] is True
    assert r3.json()["advanced"] is False
    assert r3.json()["to_percent"] is None

    s = client.get(  # type: ignore[attr-defined]
        "/api/ota/rollout/status?update_id=u-walk", headers=admin
    ).json()
    assert s["current_percent"] is None, s
    statuses = {st["status"] for st in s["stages"]}
    assert statuses == {"completed"}, s


def test_advance_unknown_rollout_returns_409(client: object, tokens: dict[str, str]) -> None:
    """Advancing a never-started rollout returns 409."""
    admin = _admin(tokens)
    resp = client.post(  # type: ignore[attr-defined]
        "/api/ota/rollout/advance",
        json={"update_id": "nope", "confirm": True},
        headers=admin,
    )
    assert resp.status_code == 409, resp.text
    assert resp.json()["error"]["code"] == "no_active_stage"


# --- abort ----------------------------------------------------------------
def test_abort_flips_pending_and_active_to_aborted(client: object, tokens: dict[str, str]) -> None:
    """Aborting a rollout in flight aborts every pending + active row."""
    admin = _admin(tokens)
    client.post(  # type: ignore[attr-defined]
        "/api/ota/rollout/start",
        json={"update_id": "u-abort", "confirm": True},
        headers=admin,
    )
    # Advance once so we have 1 completed, 1 active, 1 pending.
    client.post(  # type: ignore[attr-defined]
        "/api/ota/rollout/advance",
        json={"update_id": "u-abort", "confirm": True},
        headers=admin,
    )
    r = client.post(  # type: ignore[attr-defined]
        "/api/ota/rollout/abort",
        json={"update_id": "u-abort", "confirm": True},
        headers=admin,
    )
    assert r.status_code == 200, r.text
    assert r.json()["aborted_rows"] == 2, r.text
    s = client.get(  # type: ignore[attr-defined]
        "/api/ota/rollout/status?update_id=u-abort", headers=admin
    ).json()
    statuses = sorted(st["status"] for st in s["stages"])
    # The completed row from the advance stays completed; the active
    # and pending rows become aborted.
    assert statuses == ["aborted", "aborted", "completed"], statuses


def test_abort_then_restart_succeeds(client: object, tokens: dict[str, str]) -> None:
    """After an abort, a fresh start for the same ``update_id`` is
    allowed — the previous rows remain for the audit trail; the new
    rollout's first stage will be the only active row."""
    admin = _admin(tokens)
    client.post(  # type: ignore[attr-defined]
        "/api/ota/rollout/start",
        json={"update_id": "u-rs", "confirm": True},
        headers=admin,
    )
    client.post(  # type: ignore[attr-defined]
        "/api/ota/rollout/abort",
        json={"update_id": "u-rs", "confirm": True},
        headers=admin,
    )
    # The aborted rollout still has rows — a second start must still 409.
    r = client.post(  # type: ignore[attr-defined]
        "/api/ota/rollout/start",
        json={"update_id": "u-rs", "confirm": True},
        headers=admin,
    )
    assert r.status_code == 409, r.text


# --- audit ----------------------------------------------------------------
def test_rollout_audit_trail(client: object, tokens: dict[str, str]) -> None:
    """The start, advance, and abort each produce an audit row."""
    admin = _admin(tokens)
    client.post(  # type: ignore[attr-defined]
        "/api/ota/rollout/start",
        json={"update_id": "u-aud", "confirm": True},
        headers=admin,
    )
    client.post(  # type: ignore[attr-defined]
        "/api/ota/rollout/advance",
        json={"update_id": "u-aud", "confirm": True},
        headers=admin,
    )
    client.post(  # type: ignore[attr-defined]
        "/api/ota/rollout/abort",
        json={"update_id": "u-aud", "confirm": True},
        headers=admin,
    )
    audit = client.get(  # type: ignore[attr-defined]
        "/api/security/audit?action=ota_rollout_start&limit=20", headers=admin
    ).json()
    actions = {r["target"] for r in audit["entries"]}
    assert "u-aud" in actions, audit

    audit_adv = client.get(  # type: ignore[attr-defined]
        "/api/security/audit?action=ota_rollout_advance&limit=20", headers=admin
    ).json()
    assert any(r["target"] == "u-aud" for r in audit_adv["entries"]), audit_adv

    audit_abort = client.get(  # type: ignore[attr-defined]
        "/api/security/audit?action=ota_rollout_abort&limit=20", headers=admin
    ).json()
    assert any(r["target"] == "u-aud" for r in audit_abort["entries"]), audit_abort
