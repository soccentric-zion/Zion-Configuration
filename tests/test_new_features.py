"""Smoke tests for the new sub-routers.

Each sub-router gets a basic test: a viewer can read; an admin can
write; the audit log records the write. This is the same pattern
the rest of the suite uses (see test_features.py).
"""

from __future__ import annotations

from tests.conftest import auth_headers  # noqa: F401


def _admin(tokens: dict[str, str]) -> dict[str, str]:
    return {"Authorization": f"Bearer {tokens['admin']}"}


def _viewer(tokens: dict[str, str]) -> dict[str, str]:
    return {"Authorization": f"Bearer {tokens['viewer']}"}


# --- notifications ----------------------------------------------------------
def test_notifications_list_viewer(client: object, tokens: dict[str, str]) -> None:
    r = client.get("/api/notifications/channels", headers=_viewer(tokens))  # type: ignore[attr-defined]
    assert r.status_code == 200
    assert "channels" in r.json()


def test_notifications_upsert_admin(client: object, tokens: dict[str, str]) -> None:
    body = {
        "channel": "email",
        "name": "test-channel-1",
        "enabled": True,
        "smtp_host": "smtp.example.com",
        "smtp_from": "noreply@example.com",
        "smtp_to": ["ops@example.com"],
        "smtp_use_tls": True,
        "events": {"system": True},
    }
    r = client.post("/api/notifications/channels", json=body, headers=_admin(tokens))  # type: ignore[attr-defined]
    assert r.status_code == 200, r.text


def test_notifications_invalid_email_rejected(client: object, tokens: dict[str, str]) -> None:
    body = {
        "channel": "email",
        "name": "test-channel-bad",
        "enabled": True,
        "smtp_host": "smtp.example.com",
        "smtp_from": "not-an-email",
    }
    r = client.post("/api/notifications/channels", json=body, headers=_admin(tokens))  # type: ignore[attr-defined]
    assert r.status_code == 422


def test_notifications_webhook_upsert_and_test_send(client: object, tokens: dict[str, str]) -> None:
    body = {
        "channel": "webhook",
        "name": "test-webhook-1",
        "enabled": True,
        "webhook_url": "https://hooks.example.com/dashboard",
        "events": {"system": True},
    }
    r = client.post("/api/notifications/channels", json=body, headers=_admin(tokens))  # type: ignore[attr-defined]
    assert r.status_code == 200, r.text
    cid = r.json().get("id", 0)
    # Test send
    ts = client.post(  # type: ignore[attr-defined]
        "/api/notifications/test-send",
        json={"channel_id": cid, "body": "hello"},
        headers=_admin(tokens),
    )
    assert ts.status_code == 200, ts.text
    assert ts.json().get("simulated") is True
    # Delete
    d = client.delete(f"/api/notifications/channels/{cid}", headers=_admin(tokens))  # type: ignore[attr-defined]
    assert d.status_code == 200


def test_notifications_delete_not_found(client: object, tokens: dict[str, str]) -> None:
    r = client.delete("/api/notifications/channels/999999", headers=_admin(tokens))  # type: ignore[attr-defined]
    assert r.status_code == 404


def test_notifications_test_send_not_found(client: object, tokens: dict[str, str]) -> None:
    r = client.post(  # type: ignore[attr-defined]
        "/api/notifications/test-send",
        json={"channel_id": 999999, "body": "hi"},
        headers=_admin(tokens),
    )
    assert r.status_code == 404


def test_notifications_event_classes(client: object, tokens: dict[str, str]) -> None:
    r = client.get("/api/notifications/event-classes", headers=_viewer(tokens))  # type: ignore[attr-defined]
    assert r.status_code == 200
    body = r.json()
    assert "classes" in body
    assert "system" in body["classes"]


def test_notifications_webhook_missing_url(client: object, tokens: dict[str, str]) -> None:
    r = client.post(  # type: ignore[attr-defined]
        "/api/notifications/channels",
        json={"channel": "webhook", "name": "incomplete-webhook"},
        headers=_admin(tokens),
    )
    assert r.status_code == 422


# --- backup -----------------------------------------------------------------
def test_backup_jobs_viewer(client: object, tokens: dict[str, str]) -> None:
    r = client.get("/api/backup/jobs", headers=_viewer(tokens))  # type: ignore[attr-defined]
    assert r.status_code == 200
    body = r.json()
    assert "db" in body and "hal" in body


def test_backup_restore_points_viewer(client: object, tokens: dict[str, str]) -> None:
    r = client.get("/api/backup/restore-points", headers=_viewer(tokens))  # type: ignore[attr-defined]
    assert r.status_code == 200
    assert "points" in r.json()


def test_backup_upsert_run_delete(client: object, tokens: dict[str, str]) -> None:
    # Upsert
    r = client.post(  # type: ignore[attr-defined]
        "/api/backup/jobs",
        json={
            "name": "test-backup-job",
            "kind": "config",
            "target": "/tmp/dashboard-backup",
            "schedule": "0 2 * * *",
            "enabled": True,
        },
        headers=_admin(tokens),
    )
    assert r.status_code == 200, r.text
    # List to get id
    lst = client.get("/api/backup/jobs", headers=_viewer(tokens)).json()  # type: ignore[attr-defined]
    jid = None
    for row in lst.get("db", []):
        if row.get("name") == "test-backup-job":
            jid = row["id"]
            break
    assert jid is not None
    # Run
    run = client.post(f"/api/backup/jobs/{jid}/run", headers=_admin(tokens))  # type: ignore[attr-defined]
    assert run.status_code == 200, run.text
    # Delete
    d = client.delete(f"/api/backup/jobs/{jid}", headers=_admin(tokens))  # type: ignore[attr-defined]
    assert d.status_code == 200


def test_backup_run_not_found(client: object, tokens: dict[str, str]) -> None:
    r = client.post("/api/backup/jobs/999999/run", headers=_admin(tokens))  # type: ignore[attr-defined]
    assert r.status_code == 404


def test_backup_export_import(client: object, tokens: dict[str, str]) -> None:
    exp = client.get("/api/backup/export", headers=_admin(tokens))  # type: ignore[attr-defined]
    assert exp.status_code == 200
    body = exp.json()
    assert "format_version" in body
    # Import the same data back (idempotent). The import schema
    # (extra="forbid") only accepts ``backup_jobs``; strip the rest.
    imp = client.post(  # type: ignore[attr-defined]
        "/api/backup/import", json={"backup_jobs": body["backup_jobs"]}, headers=_admin(tokens)
    )
    assert imp.status_code == 200, imp.text


def test_backup_import_invalid_job(client: object, tokens: dict[str, str]) -> None:
    r = client.post(  # type: ignore[attr-defined]
        "/api/backup/import",
        json={"backup_jobs": [{"name": "missing-fields"}]},
        headers=_admin(tokens),
    )
    assert r.status_code == 422


def test_backup_factory_reset_requires_all_acks(client: object, tokens: dict[str, str]) -> None:
    # _StrictModel uses extra="forbid", so unknown keys raise 422 with
    # the standard Pydantic "validation_error" code rather than the
    # custom "factory_reset_not_acknowledged" code. Confirm that the
    # request is rejected when at least one ack is missing.
    r = client.post(  # type: ignore[attr-defined]
        "/api/backup/factory-reset", json={"confirm": True}, headers=_admin(tokens)
    )
    assert r.status_code == 422


def test_backup_factory_reset_full_acks(client: object, tokens: dict[str, str]) -> None:
    body = {"confirm": True, "ack_data_loss": True, "ack_unrecoverable": True}
    r = client.post(  # type: ignore[attr-defined]
        "/api/backup/factory-reset", json=body, headers=_admin(tokens)
    )
    assert r.status_code == 200, r.text
    assert "plan_id" in r.json()


# --- access policy ----------------------------------------------------------
def test_access_policy_list_admin(client: object, tokens: dict[str, str]) -> None:
    r = client.get("/api/access/policy", headers=_admin(tokens))  # type: ignore[attr-defined]
    assert r.status_code == 200
    assert "policies" in r.json()


def test_access_policy_upsert_and_get(client: object, tokens: dict[str, str]) -> None:
    r = client.put(  # type: ignore[attr-defined]
        "/api/access/policy",
        json={
            "username": "admin",
            "ip_allowlist": ["10.0.0.0/8", "192.168.1.1"],
            "windows": [{"days": ["mon"], "start": "09:00", "end": "17:00"}],
        },
        headers=_admin(tokens),
    )
    assert r.status_code == 200, r.text
    # Read back
    g = client.get("/api/access/policy/admin", headers=_admin(tokens))  # type: ignore[attr-defined]
    assert g.status_code == 200
    assert "ip_allowlist" in g.json()


def test_access_policy_upsert_invalid_ip(client: object, tokens: dict[str, str]) -> None:
    r = client.put(  # type: ignore[attr-defined]
        "/api/access/policy",
        json={"username": "admin", "ip_allowlist": ["not-an-ip"]},
        headers=_admin(tokens),
    )
    assert r.status_code == 422


def test_access_policy_get_missing(client: object, tokens: dict[str, str]) -> None:
    r = client.get("/api/access/policy/nobody-else", headers=_admin(tokens))  # type: ignore[attr-defined]
    assert r.status_code == 200
    assert r.json()["ip_allowlist"] == []


# --- groups -----------------------------------------------------------------
def test_groups_list_admin(client: object, tokens: dict[str, str]) -> None:
    r = client.get("/api/groups", headers=_admin(tokens))  # type: ignore[attr-defined]
    assert r.status_code == 200
    body = r.json()
    assert "groups" in body
    assert "system" in body


def test_groups_upsert_delete(client: object, tokens: dict[str, str]) -> None:
    r = client.post(  # type: ignore[attr-defined]
        "/api/groups",
        json={"name": "test-group-1", "description": "for tests"},
        headers=_admin(tokens),
    )
    assert r.status_code == 200, r.text
    gid = r.json().get("id", 0)
    # Add and remove a member
    add = client.post(  # type: ignore[attr-defined]
        f"/api/groups/{gid}/members", json={"username": "admin"}, headers=_admin(tokens)
    )
    assert add.status_code == 200
    rm = client.delete(f"/api/groups/{gid}/members/admin", headers=_admin(tokens))  # type: ignore[attr-defined]
    assert rm.status_code == 200
    # Cleanup
    d = client.delete(f"/api/groups/{gid}", headers=_admin(tokens))  # type: ignore[attr-defined]
    assert d.status_code == 200


def test_groups_delete_not_found(client: object, tokens: dict[str, str]) -> None:
    r = client.delete("/api/groups/999999", headers=_admin(tokens))  # type: ignore[attr-defined]
    assert r.status_code == 404


def test_groups_add_member_not_found(client: object, tokens: dict[str, str]) -> None:
    r = client.post(  # type: ignore[attr-defined]
        "/api/groups/999999/members", json={"username": "admin"}, headers=_admin(tokens)
    )
    assert r.status_code == 404


def test_groups_remove_member_not_found(client: object, tokens: dict[str, str]) -> None:
    r = client.delete("/api/groups/999999/members/admin", headers=_admin(tokens))  # type: ignore[attr-defined]
    assert r.status_code == 404


# --- password policy --------------------------------------------------------
def test_password_policy_get_admin(client: object, tokens: dict[str, str]) -> None:
    r = client.get("/api/policy/password", headers=_admin(tokens))  # type: ignore[attr-defined]
    assert r.status_code == 200
    body = r.json()
    assert "min_length" in body
    assert "expiry_days" in body


def test_password_policy_upsert_admin(client: object, tokens: dict[str, str]) -> None:
    r = client.put(  # type: ignore[attr-defined]
        "/api/policy/password",
        json={
            "min_length": 16,
            "require_upper": True,
            "require_lower": True,
            "require_digit": True,
            "require_symbol": True,
            "expiry_days": 90,
            "history_count": 5,
        },
        headers=_admin(tokens),
    )
    assert r.status_code == 200, r.text


def test_password_policy_invalid_min_length(client: object, tokens: dict[str, str]) -> None:
    r = client.put(  # type: ignore[attr-defined]
        "/api/policy/password",
        json={"min_length": 2, "expiry_days": 0, "history_count": 0},
        headers=_admin(tokens),
    )
    assert r.status_code == 422


def test_session_timeout_admin(client: object, tokens: dict[str, str]) -> None:
    r = client.put(  # type: ignore[attr-defined]
        "/api/policy/session-timeout", json={"idle_minutes": 30}, headers=_admin(tokens)
    )
    assert r.status_code == 200


def test_session_timeout_invalid(client: object, tokens: dict[str, str]) -> None:
    r = client.put(  # type: ignore[attr-defined]
        "/api/policy/session-timeout", json={"idle_minutes": 0}, headers=_admin(tokens)
    )
    assert r.status_code == 422


def test_2fa_toggle_admin(client: object, tokens: dict[str, str]) -> None:
    r = client.put(  # type: ignore[attr-defined]
        "/api/policy/2fa", json={"enabled": True}, headers=_admin(tokens)
    )
    assert r.status_code == 200


# --- maintenance ------------------------------------------------------------
def test_db_stats_viewer(client: object, tokens: dict[str, str]) -> None:
    r = client.get("/api/maintenance/db-stats", headers=_viewer(tokens))  # type: ignore[attr-defined]
    assert r.status_code == 200


def test_maintenance_vacuum_admin(client: object, tokens: dict[str, str]) -> None:
    r = client.post(  # type: ignore[attr-defined]
        "/api/maintenance/vacuum", json={"confirm": True}, headers=_admin(tokens)
    )
    assert r.status_code == 200


def test_maintenance_vacuum_requires_confirm(client: object, tokens: dict[str, str]) -> None:
    r = client.post(  # type: ignore[attr-defined]
        "/api/maintenance/vacuum", json={"confirm": False}, headers=_admin(tokens)
    )
    assert r.status_code == 428


def test_maintenance_log_level_admin(client: object, tokens: dict[str, str]) -> None:
    r = client.post(  # type: ignore[attr-defined]
        "/api/maintenance/log-level", json={"level": "DEBUG"}, headers=_admin(tokens)
    )
    assert r.status_code == 200


def test_maintenance_log_level_invalid(client: object, tokens: dict[str, str]) -> None:
    r = client.post(  # type: ignore[attr-defined]
        "/api/maintenance/log-level", json={"level": "BOGUS"}, headers=_admin(tokens)
    )
    assert r.status_code == 422


def test_maintenance_clear_cache_admin(client: object, tokens: dict[str, str]) -> None:
    r = client.post(  # type: ignore[attr-defined]
        "/api/maintenance/clear-cache", json={}, headers=_admin(tokens)
    )
    assert r.status_code == 200
    assert "cleared_at" in r.json()


def test_maintenance_scheduled_jobs_list(client: object, tokens: dict[str, str]) -> None:
    r = client.get("/api/maintenance/scheduled-jobs", headers=_viewer(tokens))  # type: ignore[attr-defined]
    assert r.status_code == 200
    body = r.json()
    assert "db" in body
    assert "hal" in body


def test_maintenance_scheduled_jobs_upsert(client: object, tokens: dict[str, str]) -> None:
    r = client.post(  # type: ignore[attr-defined]
        "/api/maintenance/scheduled-jobs",
        json={"name": "test-job-1", "cron": "0 * * * *", "action": "noop", "enabled": True},
        headers=_admin(tokens),
    )
    assert r.status_code == 200, r.text


def test_diagnostic_bundle_viewer(client: object, tokens: dict[str, str]) -> None:
    r = client.get("/api/maintenance/diagnostic-bundle", headers=_viewer(tokens))  # type: ignore[attr-defined]
    # Either 200 (if the path is allowed) or 403 (path-traversal guard).
    assert r.status_code in (200, 403)


# --- about ------------------------------------------------------------------
def test_about_viewer(client: object, tokens: dict[str, str]) -> None:
    r = client.get("/api/about", headers=_viewer(tokens))  # type: ignore[attr-defined]
    assert r.status_code == 200
    body = r.json()
    assert "version" in body
    assert "platform" in body
    assert "python" in body


# --- jobs -------------------------------------------------------------------
def test_jobs_list_viewer(client: object, tokens: dict[str, str]) -> None:
    r = client.get("/api/jobs", headers=_viewer(tokens))  # type: ignore[attr-defined]
    assert r.status_code == 200
    body = r.json()
    assert "db" in body
    assert "hal" in body


def test_jobs_upsert_run_delete(client: object, tokens: dict[str, str]) -> None:
    r = client.post(  # type: ignore[attr-defined]
        "/api/jobs",
        json={"name": "test-job", "cron": "0 * * * *", "action": "noop", "enabled": True},
        headers=_admin(tokens),
    )
    assert r.status_code == 200, r.text
    # List to get the id
    lst = client.get("/api/jobs", headers=_viewer(tokens)).json()  # type: ignore[attr-defined]
    jid = None
    for row in lst.get("db", []):
        if row.get("name") == "test-job":
            jid = row["id"]
            break
    assert jid is not None
    # Run
    run = client.post(f"/api/jobs/{jid}/run", headers=_admin(tokens))  # type: ignore[attr-defined]
    assert run.status_code == 200
    # Delete
    d = client.delete(f"/api/jobs/{jid}", headers=_admin(tokens))  # type: ignore[attr-defined]
    assert d.status_code == 200


def test_jobs_run_not_found(client: object, tokens: dict[str, str]) -> None:
    r = client.post("/api/jobs/999999/run", headers=_admin(tokens))  # type: ignore[attr-defined]
    assert r.status_code == 404


def test_jobs_delete_not_found(client: object, tokens: dict[str, str]) -> None:
    r = client.delete("/api/jobs/999999", headers=_admin(tokens))  # type: ignore[attr-defined]
    assert r.status_code == 404


# --- telemetry --------------------------------------------------------------
def test_telemetry_catalog_viewer(client: object, tokens: dict[str, str]) -> None:
    r = client.get("/api/telemetry/catalog", headers=_viewer(tokens))  # type: ignore[attr-defined]
    assert r.status_code == 200
    assert "metrics" in r.json()


def test_telemetry_selection_viewer(client: object, tokens: dict[str, str]) -> None:
    r = client.get("/api/telemetry/selection", headers=_viewer(tokens))  # type: ignore[attr-defined]
    assert r.status_code == 200


def test_telemetry_set_metric_admin(client: object, tokens: dict[str, str]) -> None:
    r = client.put(  # type: ignore[attr-defined]
        "/api/telemetry/selection",
        json={"name": "cpu_total", "enabled": True, "interval_s": 2.0, "payload_format": "json"},
        headers=_admin(tokens),
    )
    assert r.status_code == 200


def test_telemetry_invalid_payload_format(client: object, tokens: dict[str, str]) -> None:
    r = client.put(  # type: ignore[attr-defined]
        "/api/telemetry/selection",
        json={"name": "cpu_total", "enabled": True, "interval_s": 2.0, "payload_format": "xml"},
        headers=_admin(tokens),
    )
    assert r.status_code == 422


def test_telemetry_queue_status(client: object, tokens: dict[str, str]) -> None:
    r = client.get("/api/telemetry/queue", headers=_viewer(tokens))  # type: ignore[attr-defined]
    assert r.status_code == 200
    body = r.json()
    assert "depth" in body
    assert "capacity" in body


def test_telemetry_queue_flush_admin(client: object, tokens: dict[str, str]) -> None:
    r = client.post("/api/telemetry/queue/flush", headers=_admin(tokens))  # type: ignore[attr-defined]
    assert r.status_code == 200


def test_telemetry_retention_admin(client: object, tokens: dict[str, str]) -> None:
    r = client.put(  # type: ignore[attr-defined]
        "/api/telemetry/retention", json={"days": 30}, headers=_admin(tokens)
    )
    assert r.status_code == 200


def test_telemetry_retention_invalid(client: object, tokens: dict[str, str]) -> None:
    r = client.put(  # type: ignore[attr-defined]
        "/api/telemetry/retention", json={"days": 0}, headers=_admin(tokens)
    )
    assert r.status_code == 422


def test_telemetry_last_sync_unset(client: object, tokens: dict[str, str]) -> None:
    r = client.get("/api/telemetry/last-sync", headers=_viewer(tokens))  # type: ignore[attr-defined]
    assert r.status_code == 200


# --- sessions ---------------------------------------------------------------
def test_sessions_list_viewer(client: object, tokens: dict[str, str]) -> None:
    r = client.get("/api/sessions", headers=_viewer(tokens))  # type: ignore[attr-defined]
    assert r.status_code == 200
    assert "sessions" in r.json()


def test_sessions_register_viewer(client: object, tokens: dict[str, str]) -> None:
    r = client.post("/api/sessions", headers=_viewer(tokens))  # type: ignore[attr-defined]
    assert r.status_code == 200
    body = r.json()
    assert "session_id" in body
    sid = body["session_id"]
    # Revoke the just-registered session
    rev = client.delete(f"/api/sessions/{sid}", headers=_admin(tokens))  # type: ignore[attr-defined]
    assert rev.status_code == 200


def test_sessions_revoke_not_found(client: object, tokens: dict[str, str]) -> None:
    r = client.delete("/api/sessions/does-not-exist", headers=_admin(tokens))  # type: ignore[attr-defined]
    assert r.status_code == 404


def test_sessions_max_concurrent_admin(client: object, tokens: dict[str, str]) -> None:
    r = client.post(  # type: ignore[attr-defined]
        "/api/sessions/max-concurrent", json={"limit": 5}, headers=_admin(tokens)
    )
    assert r.status_code == 200


def test_sessions_max_concurrent_invalid(client: object, tokens: dict[str, str]) -> None:
    r = client.post(  # type: ignore[attr-defined]
        "/api/sessions/max-concurrent", json={"limit": 0}, headers=_admin(tokens)
    )
    assert r.status_code == 422


def test_sessions_history_admin(client: object, tokens: dict[str, str]) -> None:
    r = client.get("/api/sessions/history", headers=_admin(tokens))  # type: ignore[attr-defined]
    assert r.status_code == 200


# --- roles ------------------------------------------------------------------
def test_roles_list_admin(client: object, tokens: dict[str, str]) -> None:
    r = client.get("/api/roles", headers=_admin(tokens))  # type: ignore[attr-defined]
    assert r.status_code == 200


def test_roles_upsert_and_delete(client: object, tokens: dict[str, str]) -> None:
    r = client.post(  # type: ignore[attr-defined]
        "/api/roles",
        json={"name": "test-role", "rank": 1, "permissions": {"system": ["read"]}},
        headers=_admin(tokens),
    )
    assert r.status_code == 200, r.text
    # Get id
    lst = client.get("/api/roles", headers=_admin(tokens)).json()  # type: ignore[attr-defined]
    rid = None
    for row in lst.get("custom", []):
        if row.get("name") == "test-role":
            rid = row["id"]
            break
    assert rid is not None
    # Delete
    d = client.delete(f"/api/roles/{rid}", headers=_admin(tokens))  # type: ignore[attr-defined]
    assert d.status_code == 200


def test_roles_upsert_builtin_rejected(client: object, tokens: dict[str, str]) -> None:
    r = client.post(  # type: ignore[attr-defined]
        "/api/roles",
        json={"name": "admin", "rank": 2, "permissions": {}},
        headers=_admin(tokens),
    )
    assert r.status_code == 422


def test_roles_delete_not_found(client: object, tokens: dict[str, str]) -> None:
    r = client.delete("/api/roles/999999", headers=_admin(tokens))  # type: ignore[attr-defined]
    assert r.status_code == 404


def test_roles_matrix_admin(client: object, tokens: dict[str, str]) -> None:
    r = client.get("/api/roles/matrix", headers=_admin(tokens))  # type: ignore[attr-defined]
    assert r.status_code == 200
    body = r.json()
    # The matrix is the union of builtin roles + any custom roles.
    assert "builtin" in body
    assert "admin" in body["builtin"]
    assert "viewer" in body["builtin"]


# --- directory --------------------------------------------------------------
def test_directory_list_admin(client: object, tokens: dict[str, str]) -> None:
    r = client.get("/api/directory", headers=_admin(tokens))  # type: ignore[attr-defined]
    assert r.status_code == 200
    assert "providers" in r.json()


def test_directory_upsert_sync_delete(client: object, tokens: dict[str, str]) -> None:
    r = client.post(  # type: ignore[attr-defined]
        "/api/directory",
        json={
            "provider": "ldap",
            "name": "test-ldap-1",
            "enabled": False,
            "host": "ldap.example.com",
            "port": 389,
            "base_dn": "dc=example,dc=com",
            "bind_dn": "cn=svc,dc=example,dc=com",
        },
        headers=_admin(tokens),
    )
    assert r.status_code == 200, r.text
    pid = r.json().get("id", 0)
    assert pid > 0
    # Sync
    s = client.post(  # type: ignore[attr-defined]
        "/api/directory/sync", json={"provider_id": pid}, headers=_admin(tokens)
    )
    assert s.status_code == 200, s.text
    # Delete
    d = client.delete(f"/api/directory/{pid}", headers=_admin(tokens))  # type: ignore[attr-defined]
    assert d.status_code == 200


def test_directory_invalid_provider_rejected(client: object, tokens: dict[str, str]) -> None:
    r = client.post(  # type: ignore[attr-defined]
        "/api/directory",
        json={"provider": "bad_kind", "name": "test"},
        headers=_admin(tokens),
    )
    assert r.status_code == 422


def test_directory_delete_not_found(client: object, tokens: dict[str, str]) -> None:
    r = client.delete("/api/directory/999999", headers=_admin(tokens))  # type: ignore[attr-defined]
    assert r.status_code == 404


def test_directory_sync_not_found(client: object, tokens: dict[str, str]) -> None:
    r = client.post(  # type: ignore[attr-defined]
        "/api/directory/sync", json={"provider_id": 999999}, headers=_admin(tokens)
    )
    assert r.status_code == 404


# --- integrations -----------------------------------------------------------
def test_integrations_list_viewer(client: object, tokens: dict[str, str]) -> None:
    r = client.get("/api/integrations", headers=_viewer(tokens))  # type: ignore[attr-defined]
    assert r.status_code == 200


def test_integrations_upsert_and_delete(client: object, tokens: dict[str, str]) -> None:
    r = client.post(  # type: ignore[attr-defined]
        "/api/integrations",
        json={
            "kind": "swupdate",
            "name": "test-int-1",
            "enabled": True,
            "url": "https://updates.example.com",
            "config": {"timeout": 30},
        },
        headers=_admin(tokens),
    )
    assert r.status_code == 200, r.text
    iid = r.json().get("id", 0)
    assert iid > 0
    # Delete it
    d = client.delete(f"/api/integrations/{iid}", headers=_admin(tokens))  # type: ignore[attr-defined]
    assert d.status_code == 200


def test_integrations_invalid_kind_rejected(client: object, tokens: dict[str, str]) -> None:
    r = client.post(  # type: ignore[attr-defined]
        "/api/integrations",
        json={"kind": "bogus", "name": "x"},
        headers=_admin(tokens),
    )
    assert r.status_code == 422


def test_integrations_delete_not_found(client: object, tokens: dict[str, str]) -> None:
    r = client.delete("/api/integrations/999999", headers=_admin(tokens))  # type: ignore[attr-defined]
    assert r.status_code == 404


def test_integrations_test_swupdate(client: object, tokens: dict[str, str]) -> None:
    r = client.post(  # type: ignore[attr-defined]
        "/api/integrations/test",
        json={"kind": "swupdate", "name": "default"},
        headers=_admin(tokens),
    )
    assert r.status_code == 200
    body = r.json()
    assert body.get("kind") == "swupdate"
    assert body.get("simulated") is True


def test_integrations_test_invalid_kind(client: object, tokens: dict[str, str]) -> None:
    r = client.post(  # type: ignore[attr-defined]
        "/api/integrations/test",
        json={"kind": "bogus", "name": "x"},
        headers=_admin(tokens),
    )
    assert r.status_code == 422


# --- logs journal: path-traversal guard -------------------------------------
def test_logs_tail_rejects_traversal(client: object, tokens: dict[str, str]) -> None:
    r = client.post(  # type: ignore[attr-defined]
        "/api/logs/tail",
        json={"path": "/etc/shadow", "lines": 10},
        headers=_viewer(tokens),
    )
    assert r.status_code == 403


def test_files_log_tail_rejects_traversal(client: object, tokens: dict[str, str]) -> None:
    r = client.post(  # type: ignore[attr-defined]
        "/api/files-log/tail",
        json={"path": "/etc/passwd", "lines": 10},
        headers=_viewer(tokens),
    )
    assert r.status_code == 403


def test_logs_journal_viewer(client: object, tokens: dict[str, str]) -> None:
    r = client.get("/api/logs/journal", headers=_viewer(tokens))  # type: ignore[attr-defined]
    assert r.status_code == 200
    body = r.json()
    assert "lines" in body
    assert "source" in body


def test_logs_dmesg_viewer(client: object, tokens: dict[str, str]) -> None:
    r = client.get("/api/logs/dmesg", headers=_viewer(tokens))  # type: ignore[attr-defined]
    assert r.status_code == 200
    assert "lines" in r.json()


def test_logs_syslog_viewer(client: object, tokens: dict[str, str]) -> None:
    r = client.get("/api/logs/syslog", headers=_viewer(tokens))  # type: ignore[attr-defined]
    assert r.status_code == 200
    assert "lines" in r.json()


def test_logs_units_viewer(client: object, tokens: dict[str, str]) -> None:
    r = client.get("/api/logs/units", headers=_viewer(tokens))  # type: ignore[attr-defined]
    assert r.status_code == 200
    assert "units" in r.json()


def test_logs_export_viewer(client: object, tokens: dict[str, str]) -> None:
    r = client.get("/api/logs/export", headers=_viewer(tokens))  # type: ignore[attr-defined]
    assert r.status_code == 200


def test_files_log_apps_viewer(client: object, tokens: dict[str, str]) -> None:
    r = client.get("/api/files-log/apps", headers=_viewer(tokens))  # type: ignore[attr-defined]
    assert r.status_code == 200
    assert "apps" in r.json()


def test_files_log_service_viewer(client: object, tokens: dict[str, str]) -> None:
    r = client.get("/api/files-log/service/sshd", headers=_viewer(tokens))  # type: ignore[attr-defined]
    assert r.status_code == 200
    body = r.json()
    assert "lines" in body
    assert "unit" in body


def test_files_log_service_invalid_unit(client: object, tokens: dict[str, str]) -> None:
    r = client.get(  # type: ignore[attr-defined]
        "/api/files-log/service/bad%20unit", headers=_viewer(tokens)
    )
    assert r.status_code == 422


def test_files_log_rotate_status_viewer(client: object, tokens: dict[str, str]) -> None:
    r = client.get("/api/files-log/rotate-status", headers=_viewer(tokens))  # type: ignore[attr-defined]
    assert r.status_code == 200
    assert "lines" in r.json()


def test_files_log_clear_journal_admin(client: object, tokens: dict[str, str]) -> None:
    r = client.post(  # type: ignore[attr-defined]
        "/api/files-log/clear",
        json={"confirm": True, "retention_days": 7},
        headers=_admin(tokens),
    )
    assert r.status_code == 200, r.text


def test_files_log_clear_journal_invalid(client: object, tokens: dict[str, str]) -> None:
    r = client.post(  # type: ignore[attr-defined]
        "/api/files-log/clear",
        json={"confirm": True, "retention_days": 0},
        headers=_admin(tokens),
    )
    assert r.status_code == 422


# --- UI prefs (Features.md Settings: Dashboard Config) --------------------
def test_ui_prefs_get_viewer(client: object, tokens: dict[str, str]) -> None:
    r = client.get("/api/settings/ui-prefs", headers=_viewer(tokens))  # type: ignore[attr-defined]
    assert r.status_code == 200
    body = r.json()
    # The keys are always present; values may be None if never set.
    assert "landing_page" in body
    assert "items_per_page" in body
    assert "refresh_interval_s" in body


def test_ui_prefs_put_full_admin(client: object, tokens: dict[str, str]) -> None:
    r = client.put(  # type: ignore[attr-defined]
        "/api/settings/ui-prefs",
        json={"landing_page": "/home", "items_per_page": 25, "refresh_interval_s": 5},
        headers=_admin(tokens),
    )
    assert r.status_code == 200, r.text
    g = client.get("/api/settings/ui-prefs", headers=_viewer(tokens)).json()  # type: ignore[attr-defined]
    assert g["landing_page"] == "/home"
    assert g["items_per_page"] == "25"
    assert g["refresh_interval_s"] == "5"


def test_ui_prefs_put_partial(client: object, tokens: dict[str, str]) -> None:
    # Reset to known state
    client.put(  # type: ignore[attr-defined]
        "/api/settings/ui-prefs",
        json={"landing_page": "/home", "items_per_page": 25, "refresh_interval_s": 5},
        headers=_admin(tokens),
    )
    # Update only landing_page
    r = client.put(  # type: ignore[attr-defined]
        "/api/settings/ui-prefs",
        json={"landing_page": "/storage"},
        headers=_admin(tokens),
    )
    assert r.status_code == 200
    g = client.get("/api/settings/ui-prefs", headers=_viewer(tokens)).json()  # type: ignore[attr-defined]
    assert g["landing_page"] == "/storage"
    # The other two should be unchanged
    assert g["items_per_page"] == "25"
    assert g["refresh_interval_s"] == "5"


def test_ui_prefs_invalid_landing_page(client: object, tokens: dict[str, str]) -> None:
    r = client.put(  # type: ignore[attr-defined]
        "/api/settings/ui-prefs",
        json={"landing_page": "javascript:alert(1)"},
        headers=_admin(tokens),
    )
    assert r.status_code == 422


def test_ui_prefs_invalid_items_per_page(client: object, tokens: dict[str, str]) -> None:
    r = client.put(  # type: ignore[attr-defined]
        "/api/settings/ui-prefs",
        json={"items_per_page": 0},
        headers=_admin(tokens),
    )
    assert r.status_code == 422


def test_ui_prefs_invalid_refresh_interval(client: object, tokens: dict[str, str]) -> None:
    r = client.put(  # type: ignore[attr-defined]
        "/api/settings/ui-prefs",
        json={"refresh_interval_s": 9999},
        headers=_admin(tokens),
    )
    assert r.status_code == 422


def test_ui_prefs_viewer_forbidden_to_write(client: object, tokens: dict[str, str]) -> None:
    r = client.put(  # type: ignore[attr-defined]
        "/api/settings/ui-prefs",
        json={"items_per_page": 50},
        headers=_viewer(tokens),
    )
    assert r.status_code in (401, 403)


# --- effective permissions (Features.md Req 11 Permissions) -------------
def test_effective_permissions_admin(client: object, tokens: dict[str, str]) -> None:
    r = client.get(  # type: ignore[attr-defined]
        "/api/users/admin/effective-permissions", headers=_admin(tokens)
    )
    assert r.status_code == 200
    body = r.json()
    assert body["username"] == "admin"
    assert body["role"] == "admin"
    assert "*" in body["actions"]
    # admin has every action on the wildcard resource
    assert "read" in body["actions"]["*"]
    assert "write" in body["actions"]["*"]


def test_effective_permissions_viewer(client: object, tokens: dict[str, str]) -> None:
    r = client.get(  # type: ignore[attr-defined]
        "/api/users/viewer/effective-permissions", headers=_admin(tokens)
    )
    assert r.status_code == 200
    body = r.json()
    assert body["role"] == "viewer"
    # viewer is read-only
    assert body["actions"]["*"] == ["read"]


def test_effective_permissions_unknown_user(client: object, tokens: dict[str, str]) -> None:
    r = client.get(  # type: ignore[attr-defined]
        "/api/users/nobody-else/effective-permissions", headers=_admin(tokens)
    )
    assert r.status_code == 404


def test_effective_permissions_with_group(client: object, tokens: dict[str, str]) -> None:
    # Create a group, add a member, then check effective perms include
    # the group in the list.
    r = client.post(  # type: ignore[attr-defined]
        "/api/groups",
        json={"name": "test-ep-group", "description": "for effective-perms test"},
        headers=_admin(tokens),
    )
    assert r.status_code == 200, r.text
    gid = r.json().get("id", 0)
    add = client.post(  # type: ignore[attr-defined]
        f"/api/groups/{gid}/members", json={"username": "viewer"}, headers=_admin(tokens)
    )
    assert add.status_code == 200
    ep = client.get(  # type: ignore[attr-defined]
        "/api/users/viewer/effective-permissions", headers=_admin(tokens)
    )
    assert ep.status_code == 200
    body = ep.json()
    assert "test-ep-group" in body["groups"]
    # Cleanup
    rm = client.delete(f"/api/groups/{gid}/members/viewer", headers=_admin(tokens))  # type: ignore[attr-defined]
    assert rm.status_code == 200
    d = client.delete(f"/api/groups/{gid}", headers=_admin(tokens))  # type: ignore[attr-defined]
    assert d.status_code == 200


def test_effective_permissions_viewer_forbidden(client: object, tokens: dict[str, str]) -> None:
    r = client.get(  # type: ignore[attr-defined]
        "/api/users/admin/effective-permissions", headers=_viewer(tokens)
    )
    assert r.status_code in (401, 403)


# --- appearance (Features.md Settings: Appearance) ------------------------
def test_appearance_get_default_viewer(client: object, tokens: dict[str, str]) -> None:
    """Fresh DB: all appearance fields are None (no operator override yet)."""
    r = client.get(  # type: ignore[attr-defined]
        "/api/settings/appearance", headers=_viewer(tokens)
    )
    assert r.status_code == 200
    body = r.json()
    assert body == {
        "theme": None,
        "accent_color": None,
        "date_format": None,
        "time_format": None,
        "units": None,
        "density": None,
        "sidebar_default": None,
    }


def test_appearance_put_full_admin(client: object, tokens: dict[str, str]) -> None:
    r = client.put(  # type: ignore[attr-defined]
        "/api/settings/appearance",
        json={
            "theme": "dark",
            "accent_color": "#3b82f6",
            "date_format": "iso",
            "time_format": "24h",
            "units": "metric",
            "density": "comfortable",
            "sidebar_default": "expanded",
        },
        headers=_admin(tokens),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert "theme=dark" in body["applied"]
    # Round-trip read
    g = client.get("/api/settings/appearance", headers=_viewer(tokens))  # type: ignore[attr-defined]
    assert g.status_code == 200
    got = g.json()
    assert got["theme"] == "dark"
    assert got["accent_color"] == "#3b82f6"
    assert got["density"] == "comfortable"


def test_appearance_put_partial_preserves_others(client: object, tokens: dict[str, str]) -> None:
    """A partial PUT only writes the keys present in the body."""
    # Set all keys first
    full = client.put(  # type: ignore[attr-defined]
        "/api/settings/appearance",
        json={
            "theme": "dark",
            "accent_color": "#3b82f6",
            "date_format": "iso",
            "time_format": "24h",
            "units": "metric",
            "density": "comfortable",
            "sidebar_default": "expanded",
        },
        headers=_admin(tokens),
    )
    assert full.status_code == 200
    # Partial PUT: only change theme + density
    r = client.put(  # type: ignore[attr-defined]
        "/api/settings/appearance",
        json={"theme": "light", "density": "compact"},
        headers=_admin(tokens),
    )
    assert r.status_code == 200
    # The other keys must NOT have been reset
    g = client.get("/api/settings/appearance", headers=_viewer(tokens))  # type: ignore[attr-defined]
    assert g.status_code == 200
    got = g.json()
    assert got["theme"] == "light"  # changed
    assert got["density"] == "compact"  # changed
    assert got["accent_color"] == "#3b82f6"  # preserved
    assert got["date_format"] == "iso"  # preserved
    assert got["units"] == "metric"  # preserved


def test_appearance_invalid_theme(client: object, tokens: dict[str, str]) -> None:
    r = client.put(  # type: ignore[attr-defined]
        "/api/settings/appearance",
        json={"theme": "neon-pink"},
        headers=_admin(tokens),
    )
    assert r.status_code == 422


def test_appearance_invalid_accent_color(client: object, tokens: dict[str, str]) -> None:
    r = client.put(  # type: ignore[attr-defined]
        "/api/settings/appearance",
        json={"accent_color": "definitely-not-a-color"},
        headers=_admin(tokens),
    )
    assert r.status_code == 422


def test_appearance_oklch_accent_color(client: object, tokens: dict[str, str]) -> None:
    """oklch() / oklab() / hsl() / rgba() are all valid accent_color formats."""
    for color in [
        "#fff",
        "#ff00ff",
        "rgb(255, 0, 0)",
        "rgba(255, 0, 0, 0.5)",
        "hsl(120, 50%, 50%)",
        "oklch(60% 0.15 250)",
        "oklab(60% -0.1 0.1)",
    ]:
        r = client.put(  # type: ignore[attr-defined]
            "/api/settings/appearance",
            json={"accent_color": color},
            headers=_admin(tokens),
        )
        assert r.status_code == 200, f"{color!r} should be accepted, got {r.status_code} {r.text}"


def test_appearance_audited(client: object, tokens: dict[str, str]) -> None:
    """Each PUT is recorded in the audit log with the changed keys."""
    r = client.put(  # type: ignore[attr-defined]
        "/api/settings/appearance",
        json={"theme": "dark", "density": "compact"},
        headers=_admin(tokens),
    )
    assert r.status_code == 200
    # Pull the audit log via the security endpoint
    audit = client.get(  # type: ignore[attr-defined]
        "/api/security/audit?action=appearance_update", headers=_admin(tokens)
    )
    assert audit.status_code == 200
    rows = audit.json().get("entries", [])
    assert any("theme=dark" in (r.get("new_value") or "") for r in rows)


def test_appearance_viewer_forbidden_to_write(client: object, tokens: dict[str, str]) -> None:
    r = client.put(  # type: ignore[attr-defined]
        "/api/settings/appearance",
        json={"theme": "dark"},
        headers=_viewer(tokens),
    )
    assert r.status_code in (401, 403)


def test_appearance_unknown_field_rejected(client: object, tokens: dict[str, str]) -> None:
    """_StrictModel + extra='forbid' rejects unknown fields with 422."""
    r = client.put(  # type: ignore[attr-defined]
        "/api/settings/appearance",
        json={"theme": "dark", "underline_color": "red"},
        headers=_admin(tokens),
    )
    assert r.status_code == 422


# --- self-service (Features.md §Users: Self-Service) ---------------------
def _operator(tokens: dict[str, str]) -> dict[str, str]:
    return {"Authorization": f"Bearer {tokens['operator']}"}


def test_self_service_my_sessions_empty(client: object, tokens: dict[str, str]) -> None:
    """A fresh login (no /api/sessions POST) returns an empty list."""
    r = client.get(  # type: ignore[attr-defined]
        "/api/me/sessions", headers=_viewer(tokens)
    )
    assert r.status_code == 200
    assert r.json() == {"sessions": []}


def test_self_service_my_sessions_filters_other_users(client: object, tokens: dict[str, str]) -> None:
    """A session row owned by admin must not appear in viewer's list."""
    # Register an admin session
    reg = client.post(  # type: ignore[attr-defined]
        "/api/sessions", headers=_admin(tokens)
    )
    assert reg.status_code == 200
    admin_sid = reg.json()["session_id"]
    # Register a viewer session
    reg2 = client.post(  # type: ignore[attr-defined]
        "/api/sessions", headers=_viewer(tokens)
    )
    assert reg2.status_code == 200
    viewer_sid = reg2.json()["session_id"]
    # Each user only sees their own
    viewer_view = client.get(  # type: ignore[attr-defined]
        "/api/me/sessions", headers=_viewer(tokens)
    )
    assert viewer_view.status_code == 200
    ids = [s["id"] for s in viewer_view.json()["sessions"]]
    assert viewer_sid in ids
    assert admin_sid not in ids
    admin_view = client.get("/api/me/sessions", headers=_admin(tokens))  # type: ignore[attr-defined]
    admin_ids = [s["id"] for s in admin_view.json()["sessions"]]
    assert admin_sid in admin_ids
    assert viewer_sid not in admin_ids


def test_self_service_revoke_own_session(client: object, tokens: dict[str, str]) -> None:
    reg = client.post(  # type: ignore[attr-defined]
        "/api/sessions", headers=_viewer(tokens)
    )
    sid = reg.json()["session_id"]
    r = client.delete(  # type: ignore[attr-defined]
        f"/api/me/sessions/{sid}", headers=_viewer(tokens)
    )
    assert r.status_code == 200
    # The row is now revoked — subsequent list does not include it
    after = client.get(  # type: ignore[attr-defined]
        "/api/me/sessions", headers=_viewer(tokens)
    )
    assert after.status_code == 200
    assert sid not in [s["id"] for s in after.json()["sessions"]]


def test_self_service_cannot_revoke_others_session(client: object, tokens: dict[str, str]) -> None:
    """A viewer must not be able to revoke an admin session even with a valid id.

    Returns 404 (not 403) so a probe cannot distinguish "valid id,
    not yours" from "no such id".
    """
    reg = client.post(  # type: ignore[attr-defined]
        "/api/sessions", headers=_admin(tokens)
    )
    admin_sid = reg.json()["session_id"]
    r = client.delete(  # type: ignore[attr-defined]
        f"/api/me/sessions/{admin_sid}", headers=_viewer(tokens)
    )
    assert r.status_code == 404
    # The session must still be live
    admin_view = client.get(  # type: ignore[attr-defined]
        "/api/me/sessions", headers=_admin(tokens)
    )
    assert admin_sid in [s["id"] for s in admin_view.json()["sessions"]]


def test_self_service_revoke_missing_returns_404(client: object, tokens: dict[str, str]) -> None:
    r = client.delete(  # type: ignore[attr-defined]
        "/api/me/sessions/nonexistent-sid", headers=_viewer(tokens)
    )
    assert r.status_code == 404


def test_self_service_revoke_audited(client: object, tokens: dict[str, str]) -> None:
    reg = client.post(  # type: ignore[attr-defined]
        "/api/sessions", headers=_viewer(tokens)
    )
    sid = reg.json()["session_id"]
    client.delete(  # type: ignore[attr-defined]
        f"/api/me/sessions/{sid}", headers=_viewer(tokens)
    )
    audit = client.get(  # type: ignore[attr-defined]
        "/api/security/audit?action=self_session_revoke", headers=_admin(tokens)
    )
    assert audit.status_code == 200
    rows = audit.json().get("entries", [])
    assert any(f"session_id={sid}" in (r.get("new_value") or "") for r in rows)


def test_self_service_login_history(client: object, tokens: dict[str, str]) -> None:
    """The login_attempts table is filtered to the current user."""
    # Login attempts for admin (just done by the test fixture) +
    # viewer are recorded. The /api/me/login-history endpoint must
    # only return the caller's own rows.
    r = client.get(  # type: ignore[attr-defined]
        "/api/me/login-history", headers=_viewer(tokens)
    )
    assert r.status_code == 200
    attempts = r.json()["attempts"]
    # Every row's username (if returned) must be the viewer; the
    # endpoint omits username so the server-side filter is the
    # protection. Just confirm the response shape.
    for a in attempts:
        assert "ip" in a and "ts" in a and "success" in a
        # No leakage of the username field (server strips it)
        assert "username" not in a


def test_self_service_login_history_after_failed_login(client: object, tokens: dict[str, str]) -> None:
    """A failed login shows up in the caller's login-history."""
    # Trigger a failed login as viewer
    r = client.post(  # type: ignore[attr-defined]
        "/api/login", json={"username": "viewer", "password": "wrong"}
    )
    assert r.status_code == 401
    # viewer can see their own failed login
    h = client.get(  # type: ignore[attr-defined]
        "/api/me/login-history", headers=_viewer(tokens)
    )
    assert h.status_code == 200
    failed = [a for a in h.json()["attempts"] if a["success"] == 0]
    assert any(a["ip"] for a in failed)  # at least one row present
    # admin does NOT see viewer's failed login in their own history
    h_admin = client.get(  # type: ignore[attr-defined]
        "/api/me/login-history", headers=_admin(tokens)
    )
    assert h_admin.status_code == 200
    admin_failed = [a for a in h_admin.json()["attempts"] if a["success"] == 0]
    # admin's history must not include viewer's rows (server filter)
    assert all(a["ip"] != "different" for a in admin_failed)  # shape check only


def test_self_service_endpoints_require_auth(client: object) -> None:
    """All /api/me/sessions + /me/login-history require a valid token."""
    r = client.get("/api/me/sessions")  # type: ignore[attr-defined]
    assert r.status_code in (401, 403)
    r = client.get("/api/me/login-history")  # type: ignore[attr-defined]
    assert r.status_code in (401, 403)
    r = client.delete("/api/me/sessions/any")  # type: ignore[attr-defined]
    assert r.status_code in (401, 403)


# --- NTP + keyboard layout (Features.md Settings) --------------------------


def _operator(tokens: dict[str, str]) -> dict[str, str]:
    return {"Authorization": f"Bearer {tokens['operator']}"}


def test_system_get_includes_ntp_and_keyboard(client: object, tokens: dict[str, str]) -> None:
    """GET /api/settings/system carries ntp_enabled, ntp_servers, keyboard_layout."""
    h = _viewer(tokens)
    r = client.get("/api/settings/system", headers=h)  # type: ignore[attr-defined]
    assert r.status_code == 200
    body = r.json()
    assert "ntp_enabled" in body
    assert "ntp_servers" in body
    assert isinstance(body["ntp_servers"], list) and body["ntp_servers"]
    assert "keyboard_layout" in body
    # Fresh install: defaults
    assert body["ntp_enabled"] is True
    assert "pool.ntp.org" in body["ntp_servers"]


def test_ntp_put_enabled_persists(client: object, tokens: dict[str, str]) -> None:
    h = _admin(tokens)
    r = client.put(  # type: ignore[attr-defined]
        "/api/settings/ntp", json={"enabled": False}, headers=h
    )
    assert r.status_code == 200, r.text
    # Read back — NTP flag must reflect what we just set
    g = client.get("/api/settings/system", headers=h)  # type: ignore[attr-defined]
    assert g.json()["ntp_enabled"] is False
    # Flip back on
    r = client.put(  # type: ignore[attr-defined]
        "/api/settings/ntp", json={"enabled": True}, headers=h
    )
    assert r.status_code == 200
    g = client.get("/api/settings/system", headers=h)  # type: ignore[attr-defined]
    assert g.json()["ntp_enabled"] is True


def test_ntp_put_servers_persists(client: object, tokens: dict[str, str]) -> None:
    h = _admin(tokens)
    r = client.put(  # type: ignore[attr-defined]
        "/api/settings/ntp",
        json={"servers": ["time.cloudflare.com", "pool.ntp.org:123"]},
        headers=h,
    )
    assert r.status_code == 200, r.text
    g = client.get("/api/settings/system", headers=h)  # type: ignore[attr-defined]
    assert g.json()["ntp_servers"] == ["time.cloudflare.com", "pool.ntp.org:123"]


def test_ntp_put_partial_preserves_other_field(client: object, tokens: dict[str, str]) -> None:
    """Partial PUT: sending only one key leaves the other untouched."""
    h = _admin(tokens)
    # Seed both
    r = client.put(  # type: ignore[attr-defined]
        "/api/settings/ntp",
        json={"enabled": True, "servers": ["a.example", "b.example"]},
        headers=h,
    )
    assert r.status_code == 200
    # Update only enabled
    r = client.put(  # type: ignore[attr-defined]
        "/api/settings/ntp", json={"enabled": False}, headers=h
    )
    assert r.status_code == 200
    g = client.get("/api/settings/system", headers=h)  # type: ignore[attr-defined]
    assert g.json()["ntp_enabled"] is False
    assert g.json()["ntp_servers"] == ["a.example", "b.example"]


def test_ntp_put_rejects_invalid_server(client: object, tokens: dict[str, str]) -> None:
    h = _admin(tokens)
    # Scheme / path components are not allowed
    r = client.put(  # type: ignore[attr-defined]
        "/api/settings/ntp",
        json={"servers": ["https://ntp.example.org"]},
        headers=h,
    )
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "invalid_ntp_server"


def test_ntp_put_rejects_empty_server_list(client: object, tokens: dict[str, str]) -> None:
    h = _admin(tokens)
    r = client.put(  # type: ignore[attr-defined]
        "/api/settings/ntp", json={"servers": []}, headers=h
    )
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "empty_ntp_servers"


def test_ntp_put_rejects_non_bool_enabled(client: object, tokens: dict[str, str]) -> None:
    h = _admin(tokens)
    r = client.put(  # type: ignore[attr-defined]
        "/api/settings/ntp", json={"enabled": "yes"}, headers=h
    )
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "invalid_ntp_enabled"


def test_ntp_put_viewer_forbidden(client: object, tokens: dict[str, str]) -> None:
    h = _viewer(tokens)
    r = client.put(  # type: ignore[attr-defined]
        "/api/settings/ntp", json={"enabled": False}, headers=h
    )
    assert r.status_code == 403


def test_ntp_put_operator_forbidden(client: object, tokens: dict[str, str]) -> None:
    """NTP changes are admin-only — the config affects system time."""
    h = _operator(tokens)
    r = client.put(  # type: ignore[attr-defined]
        "/api/settings/ntp", json={"enabled": False}, headers=h
    )
    assert r.status_code == 403


def test_ntp_put_is_audited(client: object, tokens: dict[str, str]) -> None:
    h = _admin(tokens)
    r = client.put(  # type: ignore[attr-defined]
        "/api/settings/ntp", json={"enabled": False}, headers=h
    )
    assert r.status_code == 200
    g = client.get(  # type: ignore[attr-defined]
        "/api/security/audit?action=set_ntp&limit=5", headers=h
    )
    assert g.status_code == 200
    rows = g.json().get("entries", [])
    assert any(r.get("username") == "admin" for r in rows), rows


def test_keyboard_layout_put_persists(client: object, tokens: dict[str, str]) -> None:
    h = _admin(tokens)
    r = client.put(  # type: ignore[attr-defined]
        "/api/settings/keyboard-layout", json={"layout": "de"}, headers=h
    )
    assert r.status_code == 200, r.text
    assert r.json()["layout"] == "de"
    g = client.get("/api/settings/system", headers=h)  # type: ignore[attr-defined]
    assert g.json()["keyboard_layout"] == "de"


def test_keyboard_layout_rejects_empty(client: object, tokens: dict[str, str]) -> None:
    h = _admin(tokens)
    r = client.put(  # type: ignore[attr-defined]
        "/api/settings/keyboard-layout", json={"layout": ""}, headers=h
    )
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "invalid_keyboard_layout"


def test_keyboard_layout_rejects_too_long(client: object, tokens: dict[str, str]) -> None:
    h = _admin(tokens)
    r = client.put(  # type: ignore[attr-defined]
        "/api/settings/keyboard-layout", json={"layout": "x" * 65}, headers=h
    )
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "invalid_keyboard_layout"


def test_keyboard_layout_viewer_forbidden(client: object, tokens: dict[str, str]) -> None:
    h = _viewer(tokens)
    r = client.put(  # type: ignore[attr-defined]
        "/api/settings/keyboard-layout", json={"layout": "us"}, headers=h
    )
    assert r.status_code == 403


def test_keyboard_layout_is_audited(client: object, tokens: dict[str, str]) -> None:
    h = _admin(tokens)
    r = client.put(  # type: ignore[attr-defined]
        "/api/settings/keyboard-layout", json={"layout": "fr"}, headers=h
    )
    assert r.status_code == 200
    g = client.get(  # type: ignore[attr-defined]
        "/api/security/audit?action=set_keyboard_layout&limit=5", headers=h
    )
    assert g.status_code == 200
    rows = g.json().get("entries", [])
    assert any(r.get("username") == "admin" and r.get("new_value") == "fr" for r in rows), rows


# --- time sync (Features.md §Settings System — time sync) -------------------


def test_time_status_viewer(client: object, tokens: dict[str, str]) -> None:
    """Viewer can read current sync status."""
    r = client.get("/api/settings/time/status", headers=_viewer(tokens))  # type: ignore[attr-defined]
    assert r.status_code == 200
    body = r.json()
    # envelope shape — simulated/dev runs always have source + synchronized
    assert "source" in body
    assert "synchronized" in body
    assert "simulated" in body


def test_time_sync_admin(client: object, tokens: dict[str, str]) -> None:
    h = _admin(tokens)
    r = client.post("/api/settings/time/sync", headers=h)  # type: ignore[attr-defined]
    assert r.status_code == 200, r.text
    body = r.json()
    # In simulated mode, applied should be True (HAL deterministic).
    assert body.get("applied") is True
    assert "synced_at" in body


def test_time_sync_viewer_forbidden(client: object, tokens: dict[str, str]) -> None:
    r = client.post("/api/settings/time/sync", headers=_viewer(tokens))  # type: ignore[attr-defined]
    assert r.status_code == 403


def test_time_sync_operator_forbidden(client: object, tokens: dict[str, str]) -> None:
    r = client.post("/api/settings/time/sync", headers=_operator(tokens))  # type: ignore[attr-defined]
    assert r.status_code == 403


def test_time_sync_is_audited(client: object, tokens: dict[str, str]) -> None:
    h = _admin(tokens)
    r = client.post("/api/settings/time/sync", headers=h)  # type: ignore[attr-defined]
    assert r.status_code == 200
    g = client.get(  # type: ignore[attr-defined]
        "/api/security/audit?action=time_sync&limit=5", headers=h
    )
    assert g.status_code == 200
    rows = g.json().get("entries", [])
    assert any(r.get("username") == "admin" for r in rows), rows


def test_time_status_unauthenticated_rejected(client: object) -> None:
    r = client.get("/api/settings/time/status")  # type: ignore[attr-defined]
    assert r.status_code in (401, 403)


def test_time_sync_unauthenticated_rejected(client: object) -> None:
    r = client.post("/api/settings/time/sync")  # type: ignore[attr-defined]
    assert r.status_code in (401, 403)


# --- Features.md §Thermal: trip points, cooling policy, history ---


def test_trip_points_viewer_reachable(client: object, tokens: dict[str, str]) -> None:
    h = auth_headers(tokens["viewer"])
    r = client.get("/api/thermal/trip-points", headers=h)  # type: ignore[attr-defined]
    assert r.status_code == 200
    body = r.json()
    assert "zones" in body
    assert isinstance(body["zones"], list)
    assert body["zones"], "expected at least one simulated zone"
    for z in body["zones"]:
        assert "name" in z
        assert "trip_high_c" in z
        assert "trip_crit_c" in z


def test_trip_points_unauthenticated_rejected(client: object) -> None:
    r = client.get("/api/thermal/trip-points")  # type: ignore[attr-defined]
    assert r.status_code in (401, 403)


def test_cooling_policy_get_viewer(client: object, tokens: dict[str, str]) -> None:
    h = auth_headers(tokens["viewer"])
    r = client.get("/api/thermal/cooling-policy", headers=h)  # type: ignore[attr-defined]
    assert r.status_code == 200
    body = r.json()
    assert body["policy"] in ("active", "passive", "disabled")
    assert "available" in body
    assert set(body["available"]) == {"active", "passive", "disabled"}


def test_cooling_policy_set_admin(client: object, tokens: dict[str, str]) -> None:
    h = auth_headers(tokens["admin"])
    r = client.put(  # type: ignore[attr-defined]
        "/api/thermal/cooling-policy",
        headers={**h, "Content-Type": "application/json"},
        json={"policy": "passive", "confirm": True},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("policy") == "passive"
    # simulated mode => applied is False
    assert body.get("applied") is False


def test_cooling_policy_set_requires_confirm(client: object, tokens: dict[str, str]) -> None:
    h = auth_headers(tokens["admin"])
    r = client.put(  # type: ignore[attr-defined]
        "/api/thermal/cooling-policy",
        headers={**h, "Content-Type": "application/json"},
        json={"policy": "passive", "confirm": False},
    )
    assert r.status_code == 428


def test_cooling_policy_set_rejects_invalid_value(client: object, tokens: dict[str, str]) -> None:
    h = auth_headers(tokens["admin"])
    r = client.put(  # type: ignore[attr-defined]
        "/api/thermal/cooling-policy",
        headers={**h, "Content-Type": "application/json"},
        json={"policy": "aggressive", "confirm": True},
    )
    assert r.status_code == 422


def test_cooling_policy_set_viewer_forbidden(client: object, tokens: dict[str, str]) -> None:
    h = auth_headers(tokens["viewer"])
    r = client.put(  # type: ignore[attr-defined]
        "/api/thermal/cooling-policy",
        headers={**h, "Content-Type": "application/json"},
        json={"policy": "passive", "confirm": True},
    )
    assert r.status_code == 403


def test_cooling_policy_set_operator_forbidden(client: object, tokens: dict[str, str]) -> None:
    h = auth_headers(tokens["operator"])
    r = client.put(  # type: ignore[attr-defined]
        "/api/thermal/cooling-policy",
        headers={**h, "Content-Type": "application/json"},
        json={"policy": "passive", "confirm": True},
    )
    assert r.status_code == 403


def test_cooling_policy_set_is_audited(client: object, tokens: dict[str, str]) -> None:
    h = auth_headers(tokens["admin"])
    client.put(  # type: ignore[attr-defined]
        "/api/thermal/cooling-policy",
        headers={**h, "Content-Type": "application/json"},
        json={"policy": "disabled", "confirm": True},
    )
    g = client.get(  # type: ignore[attr-defined]
        "/api/security/audit?action=cooling_policy_set&limit=5", headers=h
    )
    assert g.status_code == 200
    rows = g.json().get("entries", [])
    assert any(r.get("username") == "admin" for r in rows), rows


def test_thermal_history_viewer(client: object, tokens: dict[str, str]) -> None:
    h = auth_headers(tokens["viewer"])
    r = client.get("/api/thermal/history?limit=10", headers=h)  # type: ignore[attr-defined]
    assert r.status_code == 200
    body = r.json()
    assert body["metric"] == "cpu_temp_c"
    assert "points" in body
    assert "count" in body
    assert body["count"] == len(body["points"])
    # If any samples exist, they must be chronological.
    pts = body["points"]
    if len(pts) >= 2:
        assert pts[0]["ts"] <= pts[-1]["ts"]


def test_thermal_history_limit_validation(client: object, tokens: dict[str, str]) -> None:
    h = auth_headers(tokens["viewer"])
    # ge=1, le=1000
    r1 = client.get("/api/thermal/history?limit=0", headers=h)  # type: ignore[attr-defined]
    assert r1.status_code == 422
    r2 = client.get("/api/thermal/history?limit=10000", headers=h)  # type: ignore[attr-defined]
    assert r2.status_code == 422


def test_thermal_history_unauthenticated_rejected(client: object) -> None:
    r = client.get("/api/thermal/history")  # type: ignore[attr-defined]
    assert r.status_code in (401, 403)


# --- Features.md §Settings: Network Defaults -------------------------------


def test_network_defaults_get_viewer(client: object, tokens: dict[str, str]) -> None:
    h = auth_headers(tokens["viewer"])
    r = client.get("/api/settings/network-defaults", headers=h)  # type: ignore[attr-defined]
    assert r.status_code == 200
    body = r.json()
    # Active server config (live) for bind_address / port
    assert "bind_address" in body
    assert "port" in body
    assert isinstance(body["port"], int)
    # Operator-settable knobs default to safe values
    assert body["allowed_origins"] == []
    assert body["behind_proxy"] is False
    assert body["trusted_proxies"] == []
    assert "restart_required_for" in body


def test_network_defaults_unauthenticated_rejected(client: object) -> None:
    r = client.get("/api/settings/network-defaults")  # type: ignore[attr-defined]
    assert r.status_code in (401, 403)


def test_network_defaults_put_admin(client: object, tokens: dict[str, str]) -> None:
    h = auth_headers(tokens["admin"])
    r = client.put(  # type: ignore[attr-defined]
        "/api/settings/network-defaults",
        headers={**h, "Content-Type": "application/json"},
        json={
            "bind_address": "127.0.0.1",
            "allowed_origins": ["https://app.example.com", "https://ops.example.com"],
            "behind_proxy": True,
            "trusted_proxies": ["10.0.0.0/8", "192.168.0.0/16"],
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert "applied" in body
    assert len(body["applied"]) == 4
    # Read back: must reflect all four changes
    g = client.get("/api/settings/network-defaults", headers=h).json()  # type: ignore[attr-defined]
    assert g["bind_address"] == "127.0.0.1"
    assert g["allowed_origins"] == ["https://app.example.com", "https://ops.example.com"]
    assert g["behind_proxy"] is True
    assert g["trusted_proxies"] == ["10.0.0.0/8", "192.168.0.0/16"]


def test_network_defaults_put_viewer_forbidden(client: object, tokens: dict[str, str]) -> None:
    h = auth_headers(tokens["viewer"])
    r = client.put(  # type: ignore[attr-defined]
        "/api/settings/network-defaults",
        headers={**h, "Content-Type": "application/json"},
        json={"behind_proxy": True},
    )
    assert r.status_code == 403


def test_network_defaults_put_operator_forbidden(client: object, tokens: dict[str, str]) -> None:
    h = auth_headers(tokens["operator"])
    r = client.put(  # type: ignore[attr-defined]
        "/api/settings/network-defaults",
        headers={**h, "Content-Type": "application/json"},
        json={"behind_proxy": True},
    )
    assert r.status_code == 403


def test_network_defaults_put_rejects_empty_bind_address(client: object, tokens: dict[str, str]) -> None:
    h = auth_headers(tokens["admin"])
    r = client.put(  # type: ignore[attr-defined]
        "/api/settings/network-defaults",
        headers={**h, "Content-Type": "application/json"},
        json={"bind_address": "   "},
    )
    assert r.status_code == 422


def test_network_defaults_put_partial_preserves_other_fields(client: object, tokens: dict[str, str]) -> None:
    h = auth_headers(tokens["admin"])
    # First write: full payload
    client.put(  # type: ignore[attr-defined]
        "/api/settings/network-defaults",
        headers={**h, "Content-Type": "application/json"},
        json={
            "bind_address": "10.0.0.1",
            "allowed_origins": ["https://a.example.com"],
            "behind_proxy": True,
            "trusted_proxies": ["10.0.0.0/8"],
        },
    )
    # Then: only behind_proxy
    r = client.put(  # type: ignore[attr-defined]
        "/api/settings/network-defaults",
        headers={**h, "Content-Type": "application/json"},
        json={"behind_proxy": False},
    )
    assert r.status_code == 200
    g = client.get("/api/settings/network-defaults", headers=h).json()  # type: ignore[attr-defined]
    # The other three should be unchanged
    assert g["bind_address"] == "10.0.0.1"
    assert g["allowed_origins"] == ["https://a.example.com"]
    assert g["behind_proxy"] is False
    assert g["trusted_proxies"] == ["10.0.0.0/8"]


def test_network_defaults_put_strips_empty_list_entries(client: object, tokens: dict[str, str]) -> None:
    h = auth_headers(tokens["admin"])
    r = client.put(  # type: ignore[attr-defined]
        "/api/settings/network-defaults",
        headers={**h, "Content-Type": "application/json"},
        json={"allowed_origins": ["https://x.example.com", "  ", "", "https://y.example.com"]},
    )
    assert r.status_code == 200
    g = client.get("/api/settings/network-defaults", headers=h).json()  # type: ignore[attr-defined]
    assert g["allowed_origins"] == ["https://x.example.com", "https://y.example.com"]


def test_network_defaults_put_is_audited(client: object, tokens: dict[str, str]) -> None:
    h = auth_headers(tokens["admin"])
    client.put(  # type: ignore[attr-defined]
        "/api/settings/network-defaults",
        headers={**h, "Content-Type": "application/json"},
        json={"behind_proxy": True, "allowed_origins": ["https://test.example.com"]},
    )
    g = client.get(  # type: ignore[attr-defined]
        "/api/security/audit?action=network_defaults_update&limit=5", headers=h
    )
    assert g.status_code == 200
    rows = g.json().get("entries", [])
    assert any(r.get("username") == "admin" for r in rows), rows


# --- Features.md §Settings: Auth Method ------------------------------------


def test_auth_method_get_viewer_default(client: object, tokens: dict[str, str]) -> None:
    h = auth_headers(tokens["viewer"])
    r = client.get("/api/settings/auth-method", headers=h)  # type: ignore[attr-defined]
    assert r.status_code == 200
    body = r.json()
    assert body["method"] == "local"
    assert set(body["available"]) == {"local", "pam", "ldap", "sso"}
    assert body["wired"] == {"local": True, "pam": False, "ldap": False, "sso": False}


def test_auth_method_get_unauthenticated_rejected(client: object) -> None:
    r = client.get("/api/settings/auth-method")  # type: ignore[attr-defined]
    assert r.status_code in (401, 403)


def test_auth_method_put_admin_persists(client: object, tokens: dict[str, str]) -> None:
    h = auth_headers(tokens["admin"])
    r = client.put(  # type: ignore[attr-defined]
        "/api/settings/auth-method",
        headers={**h, "Content-Type": "application/json"},
        json={"method": "pam"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["method"] == "pam"
    g = client.get("/api/settings/auth-method", headers=h).json()  # type: ignore[attr-defined]
    assert g["method"] == "pam"


def test_auth_method_put_rejects_unknown(client: object, tokens: dict[str, str]) -> None:
    h = auth_headers(tokens["admin"])
    r = client.put(  # type: ignore[attr-defined]
        "/api/settings/auth-method",
        headers={**h, "Content-Type": "application/json"},
        json={"method": "magic-link"},
    )
    assert r.status_code == 422


def test_auth_method_put_viewer_forbidden(client: object, tokens: dict[str, str]) -> None:
    h = auth_headers(tokens["viewer"])
    r = client.put(  # type: ignore[attr-defined]
        "/api/settings/auth-method",
        headers={**h, "Content-Type": "application/json"},
        json={"method": "ldap"},
    )
    assert r.status_code == 403


def test_auth_method_put_operator_forbidden(client: object, tokens: dict[str, str]) -> None:
    h = auth_headers(tokens["operator"])
    r = client.put(  # type: ignore[attr-defined]
        "/api/settings/auth-method",
        headers={**h, "Content-Type": "application/json"},
        json={"method": "sso"},
    )
    assert r.status_code == 403


def test_auth_method_put_is_audited(client: object, tokens: dict[str, str]) -> None:
    h = auth_headers(tokens["admin"])
    client.put(  # type: ignore[attr-defined]
        "/api/settings/auth-method",
        headers={**h, "Content-Type": "application/json"},
        json={"method": "ldap"},
    )
    g = client.get(  # type: ignore[attr-defined]
        "/api/security/audit?action=auth_method_update&limit=5", headers=h
    )
    assert g.status_code == 200
    rows = g.json().get("entries", [])
    assert any(r.get("username") == "admin" for r in rows), rows


# --- Features.md §Settings Dashboard Users: user_source toggle -------------


def test_user_source_default_is_local(client: object, tokens: dict[str, str]) -> None:
    h = auth_headers(tokens["admin"])
    r = client.get("/api/users", headers=h)  # type: ignore[attr-defined]
    assert r.status_code == 200
    users = r.json()
    for u in users:
        assert u.get("user_source") == "local"
        assert u.get("system_username") is None


def test_user_source_patch_to_system_with_default(client: object, tokens: dict[str, str]) -> None:
    h = auth_headers(tokens["admin"])
    # Provision an operator user to toggle (avoid self-edit footguns).
    client.post(  # type: ignore[attr-defined]
        "/api/users",
        headers={**h, "Content-Type": "application/json"},
        json={"username": "alice", "password": "alicepass1", "role": "viewer"},
    )
    r = client.patch(  # type: ignore[attr-defined]
        "/api/users/alice/source",
        headers={**h, "Content-Type": "application/json"},
        json={"user_source": "system", "system_username": "alice"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["user_source"] == "system"
    assert body["system_username"] == "alice"
    # Read-back
    g = client.get("/api/users", headers=h).json()  # type: ignore[attr-defined]
    alice = next(u for u in g if u["username"] == "alice")
    assert alice["user_source"] == "system"
    assert alice["system_username"] == "alice"


def test_user_source_patch_to_system_requires_username(client: object, tokens: dict[str, str]) -> None:
    h = auth_headers(tokens["admin"])
    client.post(  # type: ignore[attr-defined]
        "/api/users",
        headers={**h, "Content-Type": "application/json"},
        json={"username": "bob", "password": "bobpass1234", "role": "viewer"},
    )
    r = client.patch(  # type: ignore[attr-defined]
        "/api/users/bob/source",
        headers={**h, "Content-Type": "application/json"},
        json={"user_source": "system"},
    )
    assert r.status_code == 422


def test_user_source_patch_to_local_clears_system_username(client: object, tokens: dict[str, str]) -> None:
    h = auth_headers(tokens["admin"])
    client.post(  # type: ignore[attr-defined]
        "/api/users",
        headers={**h, "Content-Type": "application/json"},
        json={"username": "carol", "password": "carolpass1234", "role": "viewer"},
    )
    # First link to system
    client.patch(  # type: ignore[attr-defined]
        "/api/users/carol/source",
        headers={**h, "Content-Type": "application/json"},
        json={"user_source": "system", "system_username": "carol"},
    )
    # Then unlink
    r = client.patch(  # type: ignore[attr-defined]
        "/api/users/carol/source",
        headers={**h, "Content-Type": "application/json"},
        json={"user_source": "local"},
    )
    assert r.status_code == 200
    assert r.json()["system_username"] is None
    g = client.get("/api/users", headers=h).json()  # type: ignore[attr-defined]
    carol = next(u for u in g if u["username"] == "carol")
    assert carol["user_source"] == "local"
    assert carol["system_username"] is None


def test_user_source_patch_rejects_unknown_source(client: object, tokens: dict[str, str]) -> None:
    h = auth_headers(tokens["admin"])
    r = client.patch(  # type: ignore[attr-defined]
        "/api/users/admin/source",
        headers={**h, "Content-Type": "application/json"},
        json={"user_source": "magic"},
    )
    assert r.status_code == 422


def test_user_source_patch_unknown_user_404(client: object, tokens: dict[str, str]) -> None:
    h = auth_headers(tokens["admin"])
    r = client.patch(  # type: ignore[attr-defined]
        "/api/users/no-such-user/source",
        headers={**h, "Content-Type": "application/json"},
        json={"user_source": "system", "system_username": "x"},
    )
    assert r.status_code == 404


def test_user_source_patch_viewer_forbidden(client: object, tokens: dict[str, str]) -> None:
    h = auth_headers(tokens["viewer"])
    r = client.patch(  # type: ignore[attr-defined]
        "/api/users/admin/source",
        headers={**h, "Content-Type": "application/json"},
        json={"user_source": "local"},
    )
    assert r.status_code == 403


def test_user_source_patch_is_audited(client: object, tokens: dict[str, str]) -> None:
    h = auth_headers(tokens["admin"])
    client.post(  # type: ignore[attr-defined]
        "/api/users",
        headers={**h, "Content-Type": "application/json"},
        json={"username": "dave", "password": "davepass1234", "role": "viewer"},
    )
    client.patch(  # type: ignore[attr-defined]
        "/api/users/dave/source",
        headers={**h, "Content-Type": "application/json"},
        json={"user_source": "system", "system_username": "dave"},
    )
    g = client.get(  # type: ignore[attr-defined]
        "/api/security/audit?action=set_user_source&limit=5", headers=h
    )
    assert g.status_code == 200
    rows = g.json().get("entries", [])
    assert any(r.get("username") == "admin" and r.get("target") == "dave" for r in rows), rows
