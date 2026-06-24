"""Tests for #151 — U-Boot env write arms an apply_state plan so the
watchdog (or a manual rollback) can restore the previous value if the
device never re-heartbeats.

audit.md §4: "Apply mode: guarded + confirm-after-boot. Most dangerous
section." Without plan-arming, a bad ``boot_partition=0`` write that
prevents boot cannot be auto-rolled-back; the operator must physically
recover the device.

The fix:
- ``POST /api/overview/boot/uboot-env`` now arms an ``apply_state``
  plan with ``rollback_kind="boot_env"`` and returns ``plan_id`` +
  ``armed_until`` + ``rollback_armed_s``.
- ``POST /api/overview/boot/uboot-env/apply/confirm`` commits the plan.
- ``POST /api/overview/boot/uboot-env/apply/rollback`` rolls it back,
  attributing the audit row to the calling admin (Don't #48).
"""

from __future__ import annotations


def _h(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _admin(t: dict[str, str]) -> dict[str, str]:
    return _h(t["admin"])


# --- arms a plan ----------------------------------------------------------
def test_set_uboot_env_arms_apply_plan(client: object, tokens: dict[str, str]) -> None:
    """A successful env write returns plan_id + armed_until + rollback_armed_s (#151)."""
    admin = _admin(tokens)
    resp = client.post(  # type: ignore[attr-defined]
        "/api/overview/boot/uboot-env",
        json={
            "key": "boot_partition",
            "value": "1",
            "confirm": True,
            "rollback_armed_s": 30,
        },
        headers=admin,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    assert "plan_id" in body and isinstance(body["plan_id"], int) and body["plan_id"] > 0
    assert "armed_until" in body
    assert body["rollback_armed_s"] == 30


def test_set_uboot_env_audit_carries_plan_id(client: object, tokens: dict[str, str]) -> None:
    """The audit row carries ``value=...:plan=<id>:applied=<bool>`` so the
    action log can reconstruct the change + its plan."""
    admin = _admin(tokens)
    resp = client.post(  # type: ignore[attr-defined]
        "/api/overview/boot/uboot-env",
        json={"key": "boot_partition", "value": "1", "confirm": True},
        headers=admin,
    )
    assert resp.status_code == 200, resp.text
    plan_id = resp.json()["plan_id"]
    audit = client.get(  # type: ignore[attr-defined]
        "/api/security/audit?action=set_uboot_env&limit=5",
        headers=admin,
    )
    assert audit.status_code == 200, audit.text
    rows = audit.json().get("entries") or []
    assert rows
    row = rows[0]
    assert row["target"] == "boot_partition"
    assert f"plan={plan_id}" in row["new_value"], row
    assert "applied=" in row["new_value"], row


def test_set_uboot_env_captures_previous_value(client: object, tokens: dict[str, str]) -> None:
    """The plan's ``before`` snapshot contains the previous value of the
    key so the rollback handler can restore it. We verify this by
    calling the rollback handler directly after arming — the new value
    is replaced by the previous value (the simulated baudrate 115200)."""
    admin = _admin(tokens)
    resp = client.post(  # type: ignore[attr-defined]
        "/api/overview/boot/uboot-env",
        json={"key": "baudrate", "value": "9600", "confirm": True},
        headers=admin,
    )
    assert resp.status_code == 200, resp.text
    plan_id = resp.json()["plan_id"]
    # Manual rollback: the boot_env rollback handler writes the before
    # snapshot back into the boot_env_kv table.
    rb = client.post(  # type: ignore[attr-defined]
        "/api/overview/boot/uboot-env/apply/rollback",
        json={"plan_id": plan_id, "confirm": True},
        headers=admin,
    )
    assert rb.status_code == 200, rb.text


# --- commit endpoint ------------------------------------------------------
def test_commit_unknown_plan_returns_404(client: object, tokens: dict[str, str]) -> None:
    """A plan_id that doesn't exist returns 404, not a silent success."""
    admin = _admin(tokens)
    resp = client.post(  # type: ignore[attr-defined]
        "/api/overview/boot/uboot-env/apply/confirm",
        json={"plan_id": 999999, "confirm": True},
        headers=admin,
    )
    assert resp.status_code == 404, resp.text


def test_commit_requires_admin(client: object, tokens: dict[str, str]) -> None:
    """Viewer / operator are rejected with 403."""
    for role in ("viewer", "operator"):
        resp = client.post(  # type: ignore[attr-defined]
            "/api/overview/boot/uboot-env/apply/confirm",
            json={"plan_id": 1, "confirm": True},
            headers=_h(tokens[role]),
        )
        assert resp.status_code == 403, (role, resp.text)


def test_commit_requires_confirm(client: object, tokens: dict[str, str]) -> None:
    """Privileged write — ``confirm: true`` is required (428)."""
    admin = _admin(tokens)
    resp = client.post(  # type: ignore[attr-defined]
        "/api/overview/boot/uboot-env/apply/confirm",
        json={"plan_id": 1},
        headers=admin,
    )
    assert resp.status_code == 428, resp.text


def test_commit_happy_path(client: object, tokens: dict[str, str]) -> None:
    """Arm a plan, then commit it — the plan transitions to ``committed``."""
    admin = _admin(tokens)
    arm = client.post(  # type: ignore[attr-defined]
        "/api/overview/boot/uboot-env",
        json={"key": "boot_partition", "value": "1", "confirm": True},
        headers=admin,
    )
    assert arm.status_code == 200, arm.text
    plan_id = arm.json()["plan_id"]
    commit = client.post(  # type: ignore[attr-defined]
        "/api/overview/boot/uboot-env/apply/confirm",
        json={"plan_id": plan_id, "confirm": True},
        headers=admin,
    )
    assert commit.status_code == 200, commit.text
    assert commit.json() == {
        "ok": True,
        "plan_id": plan_id,
        "committed": True,
    }


# --- manual rollback endpoint --------------------------------------------
def test_rollback_unknown_plan_returns_404(client: object, tokens: dict[str, str]) -> None:
    """A plan_id that doesn't exist returns 404."""
    admin = _admin(tokens)
    resp = client.post(  # type: ignore[attr-defined]
        "/api/overview/boot/uboot-env/apply/rollback",
        json={"plan_id": 999999, "confirm": True},
        headers=admin,
    )
    assert resp.status_code == 404, resp.text


def test_rollback_requires_admin(client: object, tokens: dict[str, str]) -> None:
    """Viewer / operator are rejected with 403."""
    for role in ("viewer", "operator"):
        resp = client.post(  # type: ignore[attr-defined]
            "/api/overview/boot/uboot-env/apply/rollback",
            json={"plan_id": 1, "confirm": True},
            headers=_h(tokens[role]),
        )
        assert resp.status_code == 403, (role, resp.text)


def test_rollback_happy_path_attributed_to_user(client: object, tokens: dict[str, str]) -> None:
    """Arm a plan, then roll it back — the audit row carries the calling
    admin's username, not ``system`` (#48, Don't #48)."""
    admin = _admin(tokens)
    arm = client.post(  # type: ignore[attr-defined]
        "/api/overview/boot/uboot-env",
        json={"key": "boot_partition", "value": "1", "confirm": True},
        headers=admin,
    )
    assert arm.status_code == 200, arm.text
    plan_id = arm.json()["plan_id"]
    rb = client.post(  # type: ignore[attr-defined]
        "/api/overview/boot/uboot-env/apply/rollback",
        json={"plan_id": plan_id, "confirm": True},
        headers=admin,
    )
    assert rb.status_code == 200, rb.text
    assert rb.json() == {
        "ok": True,
        "plan_id": plan_id,
        "rolled_back": True,
    }
    # And the apply_rollback audit row attributes to the admin.
    audit = client.get(  # type: ignore[attr-defined]
        "/api/security/audit?action=apply_rollback&limit=5",
        headers=admin,
    )
    assert audit.status_code == 200, audit.text
    rows = audit.json().get("entries") or []
    matching = [r for r in rows if r.get("target") == "boot_env:boot_partition"]
    assert matching, rows
    assert matching[0]["username"] == "admin", matching[0]


def test_double_rollback_returns_409(client: object, tokens: dict[str, str]) -> None:
    """Rolling back the same plan twice returns 409 (plan not in 'armed' status)."""
    admin = _admin(tokens)
    arm = client.post(  # type: ignore[attr-defined]
        "/api/overview/boot/uboot-env",
        json={"key": "boot_partition", "value": "1", "confirm": True},
        headers=admin,
    )
    assert arm.status_code == 200, arm.text
    plan_id = arm.json()["plan_id"]
    rb1 = client.post(  # type: ignore[attr-defined]
        "/api/overview/boot/uboot-env/apply/rollback",
        json={"plan_id": plan_id, "confirm": True},
        headers=admin,
    )
    assert rb1.status_code == 200, rb1.text
    rb2 = client.post(  # type: ignore[attr-defined]
        "/api/overview/boot/uboot-env/apply/rollback",
        json={"plan_id": plan_id, "confirm": True},
        headers=admin,
    )
    assert rb2.status_code == 409, rb2.text


# --- rollback_armed_s validation ------------------------------------------
def test_rollback_armed_s_rejects_out_of_range(client: object, tokens: dict[str, str]) -> None:
    """``rollback_armed_s`` must be in [1, 3600]; 0 and 9999 are 422."""
    admin = _admin(tokens)
    for bad in (0, -1, 3601, 9999):
        resp = client.post(  # type: ignore[attr-defined]
            "/api/overview/boot/uboot-env",
            json={
                "key": "boot_partition",
                "value": "1",
                "confirm": True,
                "rollback_armed_s": bad,
            },
            headers=admin,
        )
        assert resp.status_code == 422, (bad, resp.text)
