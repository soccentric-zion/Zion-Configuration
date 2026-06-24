"""Write-path flows across the major routers (state changes + audit + HAL)."""

from __future__ import annotations

import io
from pathlib import Path

import pytest


def _h(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# --- cloud: alert rules + alert lifecycle ----------------------------------


def test_alert_rule_create_list_delete(client: object, tokens: dict[str, str]) -> None:
    op = _h(tokens["operator"])
    created = client.post(  # type: ignore[attr-defined]
        "/api/cloud/alerts/rules",
        json={"name": "hot-cpu", "metric": "cpu_temp_c", "condition": ">", "threshold": 85},
        headers=op,
    )
    assert created.status_code == 201, created.text
    rid = created.json()["id"]

    rules = client.get("/api/cloud/alerts/rules", headers=op).json()["rules"]  # type: ignore[attr-defined]
    assert any(r["id"] == rid for r in rules)

    # Delete requires body-based confirm (#37).
    no_confirm = client.delete(f"/api/cloud/alerts/rules/{rid}", headers=op)  # type: ignore[attr-defined]
    assert no_confirm.status_code == 428
    assert no_confirm.json()["error"]["code"] == "confirm_required"

    import json as _json

    deleted = client.request(  # type: ignore[attr-defined]
        "DELETE",
        f"/api/cloud/alerts/rules/{rid}",
        content=_json.dumps({"confirm": True}),
        headers={**op, "Content-Type": "application/json"},
    )
    assert deleted.status_code == 200, deleted.text


def test_alert_rule_bad_condition_rejected(client: object, tokens: dict[str, str]) -> None:
    resp = client.post(  # type: ignore[attr-defined]
        "/api/cloud/alerts/rules",
        json={"name": "x", "metric": "cpu_total", "condition": "!!", "threshold": 1},
        headers=_h(tokens["operator"]),
    )
    assert resp.status_code == 400


def test_provisioning_advances_and_claims_token(client: object, tokens: dict[str, str]) -> None:
    admin = _h(tokens["admin"])
    advanced = client.post("/api/cloud/provisioning/advance", headers=admin)  # type: ignore[attr-defined]
    assert advanced.status_code == 200
    assert advanced.json()["state"] == "claimed"

    token = client.post("/api/cloud/provisioning/claim-token", headers=admin)  # type: ignore[attr-defined]
    assert token.status_code == 200
    assert token.json()["claim_token"]


# --- settings: backup round-trip via the API + restore points --------------


def test_backup_download_and_restore_preview(client: object, tokens: dict[str, str]) -> None:
    admin = _h(tokens["admin"])
    blob = client.post(  # type: ignore[attr-defined]
        "/api/settings/backup", json={"password": "secret123"}, headers=admin
    )
    assert blob.status_code == 200
    data = blob.content
    assert data

    preview = client.post(  # type: ignore[attr-defined]
        "/api/settings/restore/preview?password=secret123",
        files={"file": ("dashboard-backup.enc", io.BytesIO(data), "application/octet-stream")},
        headers=admin,
    )
    assert preview.status_code == 200
    assert preview.json()["valid"] is True


def test_backup_weak_password_rejected(client: object, tokens: dict[str, str]) -> None:
    resp = client.post(  # type: ignore[attr-defined]
        "/api/settings/backup", json={"password": "x"}, headers=_h(tokens["admin"])
    )
    assert resp.status_code == 400


def test_restore_point_create_and_delete(client: object, tokens: dict[str, str]) -> None:
    admin = _h(tokens["admin"])
    created = client.post(  # type: ignore[attr-defined]
        "/api/settings/restore-points", json={"name": "before-update"}, headers=admin
    )
    assert created.status_code == 200
    rid = created.json()["id"]
    listing = client.get("/api/settings/restore-points", headers=admin).json()  # type: ignore[attr-defined]
    assert any(rp["id"] == rid for rp in listing["restore_points"])
    deleted = client.delete(f"/api/settings/restore-points/{rid}", headers=admin)  # type: ignore[attr-defined]
    assert deleted.status_code == 200


def test_reboot_and_shutdown_are_admin_actions(client: object, tokens: dict[str, str]) -> None:
    admin = _h(tokens["admin"])
    # confirm: true is required (428 without it)
    assert client.post("/api/settings/reboot", json={"confirm": True}, headers=admin).status_code == 200  # type: ignore[attr-defined]
    assert client.post("/api/settings/reboot", headers=admin).status_code == 428  # type: ignore[attr-defined]
    assert client.post("/api/settings/shutdown", json={"confirm": True}, headers=admin).status_code == 200  # type: ignore[attr-defined]


# --- security: ssh key add/remove ------------------------------------------


def test_ssh_key_add_and_remove(client: object, tokens: dict[str, str]) -> None:
    admin = _h(tokens["admin"])
    key = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAITestKeyDataForFlow test@flow"
    added = client.post(  # type: ignore[attr-defined]
        "/api/security/ssh-keys", json={"key": key}, headers=admin
    )
    assert added.status_code == 200
    fp = added.json()["fingerprint"]
    removed = client.delete(f"/api/security/ssh-keys/{fp.replace('/', '%2F')}", headers=admin)  # type: ignore[attr-defined]
    assert removed.status_code == 200


def test_audit_export_is_csv(client: object, tokens: dict[str, str]) -> None:
    resp = client.get(  # type: ignore[attr-defined]
        "/api/security/audit/export", headers=_h(tokens["admin"])
    )
    assert resp.status_code == 200
    assert "ts,username,ip,action" in resp.text


# --- software: service + container actions ---------------------------------


def test_service_action_and_journal(client: object, tokens: dict[str, str]) -> None:
    op = _h(tokens["operator"])
    services = client.get("/api/software/services", headers=op).json()  # type: ignore[attr-defined]
    assert "services" in services or services
    action = client.post("/api/software/services/ssh/restart", headers=op)  # type: ignore[attr-defined]
    assert action.status_code == 200
    journal = client.get("/api/software/services/ssh/journal", headers=op)  # type: ignore[attr-defined]
    assert journal.status_code == 200


def test_container_actions(client: object, tokens: dict[str, str]) -> None:
    op = _h(tokens["operator"])
    assert client.get("/api/software/containers", headers=op).status_code == 200  # type: ignore[attr-defined]
    pull = client.post(  # type: ignore[attr-defined]
        "/api/software/containers/pull", json={"image": "nginx:latest"}, headers=_h(tokens["admin"])
    )
    assert pull.status_code == 200


# --- files: full CRUD inside the sandbox -----------------------------------


def test_file_manager_crud(client: object, tokens: dict[str, str], sandbox: Path) -> None:
    admin = _h(tokens["admin"])

    listing = client.get(  # type: ignore[attr-defined]
        "/api/files/list", params={"path": str(sandbox)}, headers=_h(tokens["viewer"])
    )
    assert listing.status_code == 200

    target = sandbox / "note.txt"
    written = client.post(  # type: ignore[attr-defined]
        "/api/files/write",
        json={"path": str(target), "content": "hello from test"},
        headers=admin,
    )
    assert written.status_code == 200, written.text

    read = client.get(  # type: ignore[attr-defined]
        "/api/files/read", params={"path": str(target)}, headers=admin
    )
    assert read.status_code == 200
    assert "hello from test" in read.json().get("content", "")

    deleted = client.delete(  # type: ignore[attr-defined]
        "/api/files/delete", params={"path": str(target)}, headers=admin
    )
    assert deleted.status_code == 200


def test_file_outside_sandbox_is_forbidden(client: object, tokens: dict[str, str]) -> None:
    resp = client.get(  # type: ignore[attr-defined]
        "/api/files/read", params={"path": "/etc/passwd"}, headers=_h(tokens["admin"])
    )
    assert resp.status_code == 403


# --- diagnostics ------------------------------------------------------------


def test_diagnostics_run(client: object, tokens: dict[str, str]) -> None:
    resp = client.post(  # type: ignore[attr-defined]
        "/api/files/diagnostics/run", headers=_h(tokens["viewer"])
    )
    assert resp.status_code == 200
    assert resp.json()["passed"] is True


@pytest.mark.parametrize("role", ["viewer", "operator"])
def test_admin_only_endpoints_reject_lower_roles(client: object, tokens: dict[str, str], role: str) -> None:
    resp = client.post(  # type: ignore[attr-defined]
        "/api/settings/reboot", headers=_h(tokens[role])
    )
    assert resp.status_code == 403


# --- user management lifecycle ---------------------------------------------


def test_user_create_update_reset_delete(client: object, tokens: dict[str, str]) -> None:
    admin = _h(tokens["admin"])
    name = "lifecycle-user"

    created = client.post(  # type: ignore[attr-defined]
        "/api/users",
        json={"username": name, "password": "init-pass", "role": "viewer"},
        headers=admin,
    )
    assert created.status_code == 201, created.text

    updated = client.patch(  # type: ignore[attr-defined]
        f"/api/users/{name}", json={"role": "operator", "enabled": True}, headers=admin
    )
    assert updated.status_code == 200

    reset = client.post(  # type: ignore[attr-defined]
        f"/api/users/{name}/password", json={"new_password": "new-pass-123"}, headers=admin
    )
    assert reset.status_code == 200
    # The new password must authenticate at the new (operator) role.
    relog = client.post(  # type: ignore[attr-defined]
        "/api/login", json={"username": name, "password": "new-pass-123"}
    )
    assert relog.status_code == 200
    assert relog.json()["role"] == "operator"

    deleted = client.delete(f"/api/users/{name}", headers=admin)  # type: ignore[attr-defined]
    assert deleted.status_code == 200


def test_cannot_delete_self(client: object, tokens: dict[str, str]) -> None:
    resp = client.delete("/api/users/admin", headers=_h(tokens["admin"]))  # type: ignore[attr-defined]
    assert resp.status_code == 400


def test_container_logs_and_action(client: object, tokens: dict[str, str]) -> None:
    logs = client.get(  # type: ignore[attr-defined]
        "/api/software/containers/edge-broker/logs", headers=_h(tokens["viewer"])
    )
    assert logs.status_code == 200
    action = client.post(  # type: ignore[attr-defined]
        "/api/software/containers/edge-broker/restart", headers=_h(tokens["admin"])
    )
    assert action.status_code == 200


def test_bad_service_action_rejected(client: object, tokens: dict[str, str]) -> None:
    resp = client.post(  # type: ignore[attr-defined]
        "/api/software/services/ssh/frobnicate", headers=_h(tokens["operator"])
    )
    assert resp.status_code == 400
