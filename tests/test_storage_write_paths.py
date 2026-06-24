"""Tests for the storage write/read endpoints (#113, audit §10).

audit.md §10 Storage calls for the dashboard to expose layout-change
operations (mount/unmount/partition), the read-only state of
overlayfs and LUKS, and the A/B data-slot selector — all gated by
confirm + audit. The fsck path (#111) was a stub that returned
``{"ok": True, "started": True}`` without actually invoking e2fsck;
it now delegates to the HAL which records the would-be command
(#42 audit-state alignment). Every test runs in simulation — the HAL
returns ``applied: False`` envelopes so we can assert the response
shape and the audit trail without touching real hardware.
"""

from __future__ import annotations


def _h(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# --- mount -----------------------------------------------------------------
def test_mount_requires_confirm(client: object, tokens: dict[str, str]) -> None:
    resp = client.post(  # type: ignore[attr-defined]
        "/api/storage/mount",
        json={"device": "/dev/sda1", "mountpoint": "/mnt/data", "fstype": "ext4"},
        headers=_h(tokens["admin"]),
    )
    assert resp.status_code == 428, resp.text


def test_mount_rejects_protected_path(client: object, tokens: dict[str, str]) -> None:
    """Guardrail 3: refuse to mount on /etc/ paths even with confirm."""
    resp = client.post(  # type: ignore[attr-defined]
        "/api/storage/mount",
        json={
            "device": "/dev/sda1",
            "mountpoint": "/etc/extra",
            "fstype": "ext4",
            "confirm": True,
        },
        headers=_h(tokens["admin"]),
    )
    assert resp.status_code == 403, resp.text
    assert resp.json()["error"]["code"] == "protected_path"


def test_mount_rejects_unknown_fs(client: object, tokens: dict[str, str]) -> None:
    resp = client.post(  # type: ignore[attr-defined]
        "/api/storage/mount",
        json={
            "device": "/dev/sda1",
            "mountpoint": "/mnt/data",
            "fstype": "zfs",
            "confirm": True,
        },
        headers=_h(tokens["admin"]),
    )
    assert resp.status_code == 422, resp.text


def test_mount_simulation_envelope(client: object, tokens: dict[str, str]) -> None:
    resp = client.post(  # type: ignore[attr-defined]
        "/api/storage/mount",
        json={
            "device": "/dev/sda1",
            "mountpoint": "/mnt/data",
            "fstype": "ext4",
            "options": "noatime",
            "confirm": True,
        },
        headers=_h(tokens["admin"]),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["simulated"] is True
    assert body["applied"] is False
    assert body["device"] == "/dev/sda1"
    assert body["mountpoint"] == "/mnt/data"
    assert body["fstype"] == "ext4"
    # Audit row carries the target device + mountpoint in new_value
    audit = client.get(  # type: ignore[attr-defined]
        "/api/security/audit?action=storage_mount&limit=5",
        headers=_h(tokens["admin"]),
    )
    assert audit.status_code == 200, audit.text
    rows = audit.json().get("entries") or []
    assert any(r["target"] == "/dev/sda1" and "/mnt/data" in (r["new_value"] or "") for r in rows), rows


def test_mount_requires_admin(client: object, tokens: dict[str, str]) -> None:
    for role in ("viewer", "operator"):
        resp = client.post(  # type: ignore[attr-defined]
            "/api/storage/mount",
            json={
                "device": "/dev/sda1",
                "mountpoint": "/mnt/data",
                "fstype": "ext4",
                "confirm": True,
            },
            headers=_h(tokens[role]),
        )
        assert resp.status_code == 403, (role, resp.text)


# --- unmount ---------------------------------------------------------------
def test_unmount_requires_confirm(client: object, tokens: dict[str, str]) -> None:
    resp = client.post(  # type: ignore[attr-defined]
        "/api/storage/unmount",
        json={"mountpoint": "/mnt/data"},
        headers=_h(tokens["admin"]),
    )
    assert resp.status_code == 428, resp.text


def test_unmount_simulation_envelope(client: object, tokens: dict[str, str]) -> None:
    resp = client.post(  # type: ignore[attr-defined]
        "/api/storage/unmount",
        json={"mountpoint": "/mnt/data", "confirm": True},
        headers=_h(tokens["admin"]),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["simulated"] is True
    assert body["applied"] is False
    assert body["mountpoint"] == "/mnt/data"
    assert body["command"] == "umount /mnt/data"
    audit = client.get(  # type: ignore[attr-defined]
        "/api/security/audit?action=storage_unmount&limit=5",
        headers=_h(tokens["admin"]),
    )
    assert audit.status_code == 200, audit.text
    rows = audit.json().get("entries") or []
    assert any(r["target"] == "/mnt/data" for r in rows), rows


def test_unmount_requires_admin(client: object, tokens: dict[str, str]) -> None:
    for role in ("viewer", "operator"):
        resp = client.post(  # type: ignore[attr-defined]
            "/api/storage/unmount",
            json={"mountpoint": "/mnt/data", "confirm": True},
            headers=_h(tokens[role]),
        )
        assert resp.status_code == 403, (role, resp.text)


# --- partition -------------------------------------------------------------
def test_partition_rejects_unknown_op(client: object, tokens: dict[str, str]) -> None:
    resp = client.post(  # type: ignore[attr-defined]
        "/api/storage/partition",
        json={"op": "shred", "device": "/dev/sda", "confirm": True},
        headers=_h(tokens["admin"]),
    )
    assert resp.status_code == 422, resp.text


def test_partition_create_simulation_envelope(client: object, tokens: dict[str, str]) -> None:
    resp = client.post(  # type: ignore[attr-defined]
        "/api/storage/partition",
        json={
            "op": "create",
            "device": "/dev/sda",
            "partno": 1,
            "size": "10G",
            "confirm": True,
        },
        headers=_h(tokens["admin"]),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["simulated"] is True
    assert body["applied"] is False
    assert body["op"] == "create"
    assert body["device"] == "/dev/sda"
    assert "parted" in body["command"]
    audit = client.get(  # type: ignore[attr-defined]
        "/api/security/audit?action=storage_partition_create&limit=5",
        headers=_h(tokens["admin"]),
    )
    assert audit.status_code == 200, audit.text
    rows = audit.json().get("entries") or []
    assert any(r["target"] == "/dev/sda" for r in rows), rows


def test_partition_requires_admin(client: object, tokens: dict[str, str]) -> None:
    for role in ("viewer", "operator"):
        resp = client.post(  # type: ignore[attr-defined]
            "/api/storage/partition",
            json={"op": "create", "device": "/dev/sda", "confirm": True},
            headers=_h(tokens[role]),
        )
        assert resp.status_code == 403, (role, resp.text)


# --- overlayfs (read-only) -------------------------------------------------
def test_overlayfs_viewer_readable(client: object, tokens: dict[str, str]) -> None:
    resp = client.get(  # type: ignore[attr-defined]
        "/api/storage/overlayfs",
        headers=_h(tokens["viewer"]),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["simulated"] is True
    assert isinstance(body.get("overlayfs"), list)
    # The simulated branch surfaces at least one entry so the SPA can
    # render the lower/upper/work triplet.
    if body["overlayfs"]:
        entry = body["overlayfs"][0]
        assert "lower" in entry
        assert "upper" in entry
        assert "work" in entry


# --- luks (read-only) ------------------------------------------------------
def test_luks_viewer_readable(client: object, tokens: dict[str, str]) -> None:
    resp = client.get(  # type: ignore[attr-defined]
        "/api/storage/luks",
        headers=_h(tokens["viewer"]),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["simulated"] is True
    assert isinstance(body.get("luks"), list)


# --- ab-data slot ----------------------------------------------------------
def test_ab_data_slot_rejects_unknown_slot(client: object, tokens: dict[str, str]) -> None:
    resp = client.post(  # type: ignore[attr-defined]
        "/api/storage/ab-data/slot",
        json={"slot": "C", "confirm": True},
        headers=_h(tokens["admin"]),
    )
    assert resp.status_code == 422, resp.text


def test_ab_data_slot_requires_confirm(client: object, tokens: dict[str, str]) -> None:
    resp = client.post(  # type: ignore[attr-defined]
        "/api/storage/ab-data/slot",
        json={"slot": "A"},
        headers=_h(tokens["admin"]),
    )
    assert resp.status_code == 428, resp.text


def test_ab_data_slot_simulation_envelope(client: object, tokens: dict[str, str]) -> None:
    resp = client.post(  # type: ignore[attr-defined]
        "/api/storage/ab-data/slot",
        json={"slot": "A", "confirm": True},
        headers=_h(tokens["admin"]),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["simulated"] is True
    assert body["applied"] is False
    assert body["slot"] == "A"
    assert body["command"] == "ab_data_slot A"
    audit = client.get(  # type: ignore[attr-defined]
        "/api/security/audit?action=storage_ab_data_slot&limit=5",
        headers=_h(tokens["admin"]),
    )
    assert audit.status_code == 200, audit.text
    rows = audit.json().get("entries") or []
    assert any(r["new_value"] == "A" for r in rows), rows


def test_ab_data_slot_requires_admin(client: object, tokens: dict[str, str]) -> None:
    for role in ("viewer", "operator"):
        resp = client.post(  # type: ignore[attr-defined]
            "/api/storage/ab-data/slot",
            json={"slot": "B", "confirm": True},
            headers=_h(tokens[role]),
        )
        assert resp.status_code == 403, (role, resp.text)


# --- fsck (#111 — was a stub, now a real HAL call) -------------------------
def test_fsck_requires_device(client: object, tokens: dict[str, str]) -> None:
    resp = client.post(  # type: ignore[attr-defined]
        "/api/storage/fsck",
        json={"confirm": True},
        headers=_h(tokens["admin"]),
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["error"]["code"] == "missing_device"


def test_fsck_simulation_envelope(client: object, tokens: dict[str, str]) -> None:
    """The previous stub returned ``{"ok": True, "started": True}`` —
    the new HAL call returns the simulated envelope with the would-be
    command so the SPA can show "would apply" (#42, #111)."""
    resp = client.post(  # type: ignore[attr-defined]
        "/api/storage/fsck",
        json={"device": "/dev/mmcblk0p2", "confirm": True},
        headers=_h(tokens["admin"]),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["simulated"] is True
    assert body["applied"] is False
    assert body["device"] == "/dev/mmcblk0p2"
    assert body["command"] == "e2fsck -n /dev/mmcblk0p2"
    # And the stub fields are gone.
    assert "started" not in body


def test_fsck_requires_confirm(client: object, tokens: dict[str, str]) -> None:
    resp = client.post(  # type: ignore[attr-defined]
        "/api/storage/fsck",
        json={"device": "/dev/mmcblk0p2"},
        headers=_h(tokens["admin"]),
    )
    assert resp.status_code == 428, resp.text


def test_fsck_requires_admin(client: object, tokens: dict[str, str]) -> None:
    for role in ("viewer", "operator"):
        resp = client.post(  # type: ignore[attr-defined]
            "/api/storage/fsck",
            json={"device": "/dev/mmcblk0p2", "confirm": True},
            headers=_h(tokens[role]),
        )
        assert resp.status_code == 403, (role, resp.text)
