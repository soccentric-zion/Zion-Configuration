"""SQLite schema migrations.

Migrations are plain SQL statements keyed by a monotonically increasing
version. They are applied inside a transaction and are idempotent: the
``schema_version`` table records the highest applied version, so re-running
``migrate`` on an up-to-date database is a no-op. This is exercised by the
``test_migrations_idempotent`` test.
"""

from __future__ import annotations

import aiosqlite

# Each entry: (version, SQL). Append new migrations; never edit historical ones.
MIGRATIONS: list[tuple[int, str]] = [
    (
        1,
        """
        CREATE TABLE IF NOT EXISTS users (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            username      TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            role          TEXT NOT NULL CHECK (role IN ('admin','operator','viewer')),
            enabled       INTEGER NOT NULL DEFAULT 1,
            must_change_pw INTEGER NOT NULL DEFAULT 0,
            created_at    TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS audit_log (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            ts         TEXT NOT NULL DEFAULT (datetime('now')),
            username   TEXT NOT NULL,
            ip         TEXT NOT NULL,
            action     TEXT NOT NULL,
            target     TEXT,
            old_value  TEXT,
            new_value  TEXT
        );

        CREATE TABLE IF NOT EXISTS login_attempts (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            ts        TEXT NOT NULL DEFAULT (datetime('now')),
            username  TEXT NOT NULL,
            ip        TEXT NOT NULL,
            success   INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS metric_history (
            id      INTEGER PRIMARY KEY AUTOINCREMENT,
            ts      TEXT NOT NULL DEFAULT (datetime('now')),
            metric  TEXT NOT NULL,
            value   REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_metric_history_metric_ts
            ON metric_history(metric, ts);

        CREATE TABLE IF NOT EXISTS alerts (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            rule_id     INTEGER,
            metric      TEXT NOT NULL,
            severity    TEXT NOT NULL,
            state       TEXT NOT NULL DEFAULT 'firing',
            message     TEXT NOT NULL,
            fired_at    TEXT NOT NULL DEFAULT (datetime('now')),
            resolved_at TEXT,
            acked_at    TEXT,
            acked_by    TEXT,
            snooze_until TEXT
        );

        CREATE TABLE IF NOT EXISTS alert_rules (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            name      TEXT NOT NULL,
            metric    TEXT NOT NULL,
            condition TEXT NOT NULL CHECK (condition IN ('>','<','==','rate')),
            threshold REAL NOT NULL,
            duration_s INTEGER NOT NULL DEFAULT 0,
            severity  TEXT NOT NULL DEFAULT 'warning',
            enabled   INTEGER NOT NULL DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS ota_history (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            ts        TEXT NOT NULL DEFAULT (datetime('now')),
            version   TEXT NOT NULL,
            result    TEXT NOT NULL,
            duration_s REAL,
            triggered_by TEXT
        );

        CREATE TABLE IF NOT EXISTS restore_points (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            ts        TEXT NOT NULL DEFAULT (datetime('now')),
            name      TEXT NOT NULL,
            payload   TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS telemetry_queue (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            ts        TEXT NOT NULL DEFAULT (datetime('now')),
            metric    TEXT NOT NULL,
            value     REAL NOT NULL,
            sent      INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS provisioning (
            id        INTEGER PRIMARY KEY CHECK (id = 1),
            state     TEXT NOT NULL DEFAULT 'unprovisioned',
            endpoint  TEXT,
            claim_token TEXT,
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        """,
    ),
    (
        2,
        """
        -- Hardware interface configuration + enable/disable (section 1).
        CREATE TABLE IF NOT EXISTS hw_interfaces (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            name       TEXT NOT NULL UNIQUE,
            kind       TEXT NOT NULL,
            enabled    INTEGER NOT NULL DEFAULT 1,
            config     TEXT NOT NULL DEFAULT '{}',
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        -- Declarative attached-device layer (section 6).
        CREATE TABLE IF NOT EXISTS devices (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            name         TEXT NOT NULL,
            kind         TEXT NOT NULL,
            interface    TEXT NOT NULL,
            address      TEXT,
            params       TEXT NOT NULL DEFAULT '{}',
            register_map TEXT NOT NULL DEFAULT '[]',
            enabled      INTEGER NOT NULL DEFAULT 1,
            created_at   TEXT NOT NULL DEFAULT (datetime('now'))
        );

        -- System administration: firewall rules (section 7).
        CREATE TABLE IF NOT EXISTS firewall_rules (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            action      TEXT NOT NULL CHECK (action IN ('allow','deny')),
            port        INTEGER NOT NULL,
            protocol    TEXT NOT NULL DEFAULT 'tcp',
            source      TEXT NOT NULL DEFAULT 'any',
            destination TEXT NOT NULL DEFAULT 'any',
            enabled     INTEGER NOT NULL DEFAULT 1,
            position    INTEGER NOT NULL DEFAULT 0,
            created_at  TEXT NOT NULL DEFAULT (datetime('now'))
        );

        -- System administration: network interface configuration (section 7).
        CREATE TABLE IF NOT EXISTS net_interfaces (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            name       TEXT NOT NULL UNIQUE,
            method     TEXT NOT NULL DEFAULT 'dhcp' CHECK (method IN ('dhcp','static')),
            address    TEXT,
            netmask    TEXT,
            gateway    TEXT,
            dns        TEXT,
            up         INTEGER NOT NULL DEFAULT 1,
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        -- SoC media/codec/compute blocks + CSI/DSI (sections 2 & 3).
        CREATE TABLE IF NOT EXISTS media_blocks (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            name       TEXT NOT NULL UNIQUE,
            kind       TEXT NOT NULL,
            enabled    INTEGER NOT NULL DEFAULT 1,
            config     TEXT NOT NULL DEFAULT '{}',
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        """,
    ),
    (
        3,
        """
        -- Apply plans: short-lived records of state changes that arm a
        -- rollback if the SPA does not confirm the change within the
        -- window. Used by set_network (req.md: "Apply changes in a way
        -- consistent with the app's existing rollback/error-handling
        -- behavior; never leave an interface or firewall in a
        -- half-applied state.").
        CREATE TABLE IF NOT EXISTS apply_plans (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            target        TEXT NOT NULL,           -- "network:eth0" etc.
            before_json   TEXT NOT NULL,           -- snapshot pre-apply
            after_json    TEXT NOT NULL,           -- snapshot to restore on rollback
            rollback_kind TEXT NOT NULL,           -- "net_interfaces"
            armed_until   TEXT NOT NULL,           -- ISO8601
            status        TEXT NOT NULL DEFAULT 'armed' -- armed|committed|rolled_back|expired
                          CHECK (status IN ('armed','committed','rolled_back','expired')),
            created_at    TEXT NOT NULL DEFAULT (datetime('now')),
            committed_at  TEXT,
            rolled_back_at TEXT
        );
        CREATE INDEX IF NOT EXISTS ix_apply_plans_status_armed
            ON apply_plans (status, armed_until);
        """,
    ),
    (
        4,
        """
        -- SSH authorized_keys (section 8) — replaces the module-level
        -- _SSH_KEYS list in app/routers/security.py so the state is shared
        -- across all uvicorn workers and survives a restart.
        CREATE TABLE IF NOT EXISTS ssh_keys (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            comment    TEXT NOT NULL,
            key        TEXT NOT NULL UNIQUE,
            added_at   TEXT NOT NULL DEFAULT (datetime('now'))
        );
        """,
    ),
    (
        5,
        """
        -- system_settings_kv: persists operator-chosen hostname/timezone so
        -- set_hostname / set_timezone are not silent no-ops. The SPA's
        -- /api/settings/system read falls back to platform.node() /
        -- time.tzname when no override is present, so a fresh device still
        -- shows its real values. (#42)
        CREATE TABLE IF NOT EXISTS system_settings_kv (
            key        TEXT PRIMARY KEY,
            value      TEXT NOT NULL,
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        """,
    ),
    (
        6,
        """
        -- auth lockout: turn the sliding window in auth.is_locked into a
        -- fixed lockout. On the failure that crosses max_failed_logins,
        -- set users.locked_until = now + lockout_minutes. authenticate()
        -- refuses even valid logins until that time (#54). A NULL value
        -- means "not currently locked".
        ALTER TABLE users ADD COLUMN locked_until TEXT;
        """,
    ),
    (
        7,
        """
        -- system_heartbeat: a single-row table recording the last time a
        -- caller hit POST /api/system/health/heartbeat. The apply-plan
        -- watchdog consults this to decide between auto-commit (recent
        -- heartbeat, device is healthy with the new config) and
        -- auto-rollback (stale heartbeat, reboot/boot change never came
        -- back) (#93). A single row with id=1 keeps the read/write path
        -- trivial; a "ping" updates last_seen_at.
        CREATE TABLE IF NOT EXISTS system_heartbeat (
            id           INTEGER PRIMARY KEY CHECK (id = 1),
            last_seen_at TEXT NOT NULL,
            source       TEXT
        );
        INSERT OR IGNORE INTO system_heartbeat (id, last_seen_at, source)
        VALUES (1, datetime('now'), 'init');
        """,
    ),
    (
        8,
        """
        -- apply_plans: add bundle_id for atomic multi-section staging
        -- (#99). audit.md "Backend" section requires that first-boot
        -- provisioning can stage several changes (network, hostname,
        -- timezone, …) and apply them as a single atomic unit with one
        -- rollback point. The column is NULL for single-section plans
        -- and a UUID-shaped string for bundled plans. An index on
        -- (bundle_id, status) lets the watchdog and the
        -- /api/apply/bundle endpoints find every plan in a bundle
        -- atomically.
        ALTER TABLE apply_plans ADD COLUMN bundle_id TEXT;
        CREATE INDEX IF NOT EXISTS ix_apply_plans_bundle_id
            ON apply_plans (bundle_id, status);
        """,
    ),
    (
        9,
        """
        -- wifi_networks: operator-entered Wi-Fi profiles (#110,
        -- audit §2). The dashboard can scan + connect, but the
        -- credentials need to persist across reboots so the HAL can
        -- re-apply the connection on boot. Columns mirror the
        -- req.md "Wi-Fi (SSID/creds/country)" set:
        --   ssid     — network name (unique per profile)
        --   psk      — pre-shared key (WPA2-Personal); NULL for open
        --   country  — ISO 3166-1 alpha-2 regulatory domain (e.g. US)
        --   eap      — EAP method for 802.1X (NULL = PSK/open)
        --   identity — EAP identity (NULL for PSK/open)
        --   priority — connection priority (lower = preferred)
        -- PSK is stored as opaque text. A real deployment should use
        -- the platform keyring (e.g. NetworkManager's secret-store) —
        -- this column is the persistence stub the HAL can read.
        CREATE TABLE IF NOT EXISTS wifi_networks (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            ssid        TEXT NOT NULL,
            psk         TEXT,
            country     TEXT,
            eap         TEXT,
            identity    TEXT,
            priority    INTEGER NOT NULL DEFAULT 0,
            created_at  TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at  TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(ssid)
        );
        """,
    ),
    (
        10,
        """
        -- cve_acknowledged: operator ack/mitigation state for a CVE
        -- match (audit §9, #133). The CVE *list* is sourced from
        -- data/cve-scan.json (read-only) — this table adds the
        -- operator-controllable ack overlay. The
        -- /api/security/cve endpoint merges the static list with
        -- the live ack state.
        --
        --   cve_id           — the CVE ID (e.g. CVE-2024-12345)
        --   acknowledged_at  — set on POST acknowledge
        --   acknowledged_by  — username of the operator
        --   mitigation_note  — free-text note (NULL on initial ack)
        --
        -- A single row per cve_id; DELETE un-acks (rolls back to
        -- "unacknowledged"). No hard delete; the audit_log carries
        -- the action record forever.
        CREATE TABLE IF NOT EXISTS cve_acknowledged (
            cve_id           TEXT PRIMARY KEY,
            acknowledged_at  TEXT NOT NULL DEFAULT (datetime('now')),
            acknowledged_by  TEXT NOT NULL,
            mitigation_note  TEXT
        );
        """,
    ),
    (
        11,
        """
        -- signing_keys: operator-uploaded public keys for OTA bundle
        -- signature verification (audit §8 "signed update chain", #120).
        -- The install path requires the upload to carry an ed25519 /
        -- rsa-pss signature over a manifest that includes the SHA256
        -- of the bundle; the public key is looked up by id and the
        -- signature is verified before the install runs.
        --
        --   key_id        — short operator-chosen identifier
        --   alg           — "ed25519" or "rsa-pss" (matches OtaSignature)
        --   public_key    — base64-encoded raw key bytes (ed25519: 32
        --                   bytes; rsa-pss: PEM SubjectPublicKeyInfo)
        --   label         — human-readable label for the UI
        --   created_at    — set on INSERT
        --
        -- A single row per key_id (PRIMARY KEY); DELETE removes the
        -- key. The audit_log carries the action record forever so the
        -- who/when of any key removal is recoverable.
        CREATE TABLE IF NOT EXISTS signing_keys (
            key_id      TEXT PRIMARY KEY,
            alg         TEXT NOT NULL CHECK (alg IN ('ed25519', 'rsa-pss')),
            public_key  TEXT NOT NULL,
            label       TEXT,
            created_at  TEXT NOT NULL DEFAULT (datetime('now'))
        );
        """,
    ),
    (
        12,
        """
        -- pending_overlays: next-boot staging table (audit §3, §4, §10, #114).
        -- The audit requires that hardware-touching writes can be staged
        -- for "apply on next boot" instead of going live immediately. The
        -- write goes to a row here; a boot-time hook (POST
        -- /api/apply/pending/commit) applies every `armed` row to the
        -- live table in one transaction and marks it `committed`.
        --
        --   target       — surface name, e.g. "iface:i2c-1", "media:csi0",
        --                  "device:42", "service:ssh"
        --   kind         — "interface_enable" | "media_enable" |
        --                  "device_enable" | "service_enable" |
        --                  "service_disable" — used by the commit hook
        --                  to route to the right live table
        --   payload_json — JSON-encoded change body
        --   status       — armed | committed | discarded
        --   armed_at     — staged
        --   committed_at — applied on boot
        --
        -- The audit_log carries the staging + commit actions forever; the
        -- pending_overlays row is mutable (status transitions) but the
        -- who/when is in the audit_log, not here.
        CREATE TABLE IF NOT EXISTS pending_overlays (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            target        TEXT NOT NULL,
            kind          TEXT NOT NULL CHECK (kind IN (
                'interface_enable', 'media_enable', 'device_enable',
                'service_enable', 'service_disable'
            )),
            payload_json  TEXT NOT NULL DEFAULT '{}',
            status        TEXT NOT NULL DEFAULT 'armed'
                          CHECK (status IN ('armed', 'committed', 'discarded')),
            armed_at      TEXT NOT NULL DEFAULT (datetime('now')),
            committed_at  TEXT,
            armed_by      TEXT
        );
        CREATE INDEX IF NOT EXISTS ix_pending_overlays_status
            ON pending_overlays (status);
        """,
    ),
    (
        13,
        """
        -- boot_env_kv: durable store for the last-applied U-Boot env value
        -- per key (#151, audit §4 Guardrail 2). The apply_state rollback
        -- handler for `rollback_kind="boot_env"` writes the previous
        -- value back into this table so the action log can show the
        -- restored state; the live read path (HAL `boot()["uboot_env"]`)
        -- exposes the most recent value to the SPA.
        --
        -- The audit_log carries the who/when for each staged write; this
        -- table is just the current persisted value, mutable by the
        -- rollback handler only.
        CREATE TABLE IF NOT EXISTS boot_env_kv (
            key        TEXT PRIMARY KEY,
            value      TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        """,
    ),
    (
        14,
        """
        -- rollout_stages: staged-rollout state machine for OTA updates
        -- (#146, audit §8 "staged rollout (1% → 10% → all)"). One row
        -- per stage of an in-flight rollout; the active row carries the
        -- current cohort percent + status. ``update_id`` groups the
        -- rows so an operator can see which update they belong to.
        --
        -- The audit_log carries the who/when for the start/advance/abort
        -- actions; this table is the mutable per-stage state.
        CREATE TABLE IF NOT EXISTS rollout_stages (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            update_id     TEXT NOT NULL,
            percent       INTEGER NOT NULL CHECK (percent IN (1, 10, 100)),
            status        TEXT NOT NULL DEFAULT 'pending'
                          CHECK (status IN ('pending', 'active', 'completed', 'aborted')),
            started_at    TEXT,
            completed_at  TEXT,
            created_at    TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS ix_rollout_stages_update
            ON rollout_stages (update_id);
        CREATE INDEX IF NOT EXISTS ix_rollout_stages_status
            ON rollout_stages (status);
        """,
    ),
    (
        15,
        """
        -- notifications: SMTP / webhook channels and per-event toggles
        -- (Features.md §Settings). Channel is one of: email, webhook, toast.
        -- Per-event enabled flag drives whether the alert engine calls the
        -- channel for a given event class (system, security, network, ota).
        CREATE TABLE IF NOT EXISTS notifications (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            channel      TEXT NOT NULL CHECK (channel IN ('email', 'webhook', 'toast')),
            name         TEXT NOT NULL,
            enabled      INTEGER NOT NULL DEFAULT 1,
            config_json  TEXT NOT NULL DEFAULT '{}',
            events_json  TEXT NOT NULL DEFAULT '{}',
            created_at   TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at   TEXT NOT NULL DEFAULT (datetime('now'))
        );
        """,
    ),
    (
        16,
        """
        -- backup_jobs: scheduled + on-demand backups (Features.md §Settings).
        -- target is a path or a remote spec (nfs, smb, usb); kind is one of
        -- config, full, factory. status follows the apply_state pattern.
        CREATE TABLE IF NOT EXISTS backup_jobs (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            name         TEXT NOT NULL,
            kind         TEXT NOT NULL CHECK (kind IN ('config', 'full', 'factory')),
            target       TEXT NOT NULL,
            schedule     TEXT NOT NULL DEFAULT '',
            enabled      INTEGER NOT NULL DEFAULT 1,
            last_run_at  TEXT,
            last_status  TEXT,
            created_at   TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at   TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS backup_restore_points (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id       INTEGER REFERENCES backup_jobs(id) ON DELETE CASCADE,
            label        TEXT NOT NULL,
            path         TEXT NOT NULL,
            size_bytes   INTEGER NOT NULL DEFAULT 0,
            checksum     TEXT NOT NULL DEFAULT '',
            created_at   TEXT NOT NULL DEFAULT (datetime('now'))
        );
        """,
    ),
    (
        17,
        """
        -- custom_roles: extend the built-in admin/operator/viewer with
        -- configurable roles (Features.md §Users). Permissions is a JSON
        -- map of resource -> [actions] (e.g. {"network": ["read", "write"]}).
        CREATE TABLE IF NOT EXISTS custom_roles (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            name         TEXT NOT NULL UNIQUE,
            rank         INTEGER NOT NULL DEFAULT 0,
            permissions  TEXT NOT NULL DEFAULT '{}',
            builtin      INTEGER NOT NULL DEFAULT 0,
            created_at   TEXT NOT NULL DEFAULT (datetime('now'))
        );
        -- groups: named permission sets (Features.md §Users).
        CREATE TABLE IF NOT EXISTS groups (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            name         TEXT NOT NULL UNIQUE,
            description  TEXT NOT NULL DEFAULT '',
            parent_id    INTEGER REFERENCES groups(id) ON DELETE SET NULL,
            created_at   TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS group_memberships (
            group_id     INTEGER NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
            username     TEXT NOT NULL,
            PRIMARY KEY (group_id, username)
        );
        """,
    ),
    (
        18,
        """
        -- sessions: active dashboard sessions (Features.md §Users).
        -- The JWT lives in the browser; this table tracks the server-side
        -- shadow (ip, last activity, expiry) so the operator can revoke.
        CREATE TABLE IF NOT EXISTS sessions (
            id           TEXT PRIMARY KEY,
            username     TEXT NOT NULL,
            ip           TEXT NOT NULL DEFAULT '',
            user_agent   TEXT NOT NULL DEFAULT '',
            issued_at    TEXT NOT NULL DEFAULT (datetime('now')),
            last_seen_at TEXT NOT NULL DEFAULT (datetime('now')),
            expires_at   TEXT NOT NULL,
            revoked      INTEGER NOT NULL DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS ix_sessions_user
            ON sessions (username);
        -- access_policy: per-user IP allowlist + time window (Features.md §Users).
        CREATE TABLE IF NOT EXISTS access_policy (
            username     TEXT PRIMARY KEY,
            ip_allowlist TEXT NOT NULL DEFAULT '',
            window_json  TEXT NOT NULL DEFAULT '{}',
            updated_at   TEXT NOT NULL DEFAULT (datetime('now'))
        );
        """,
    ),
    (
        19,
        """
        -- password_policy: org-wide password rules (Features.md §Users).
        -- Single-row table (singleton) — UPSERT keeps it at id=1.
        CREATE TABLE IF NOT EXISTS password_policy (
            id                INTEGER PRIMARY KEY CHECK (id = 1),
            min_length        INTEGER NOT NULL DEFAULT 12,
            require_upper     INTEGER NOT NULL DEFAULT 1,
            require_lower     INTEGER NOT NULL DEFAULT 1,
            require_digit     INTEGER NOT NULL DEFAULT 1,
            require_symbol    INTEGER NOT NULL DEFAULT 1,
            expiry_days       INTEGER NOT NULL DEFAULT 0,
            history_count     INTEGER NOT NULL DEFAULT 5,
            updated_at        TEXT NOT NULL DEFAULT (datetime('now'))
        );
        INSERT OR IGNORE INTO password_policy (id) VALUES (1);
        """,
    ),
    (
        20,
        """
        -- telemetry_metrics: per-metric enable/send-interval selection
        -- (Features.md §Telemetry). The metric catalog is in code; this
        -- table holds the operator's per-metric overrides.
        CREATE TABLE IF NOT EXISTS telemetry_metrics (
            metric         TEXT PRIMARY KEY,
            enabled        INTEGER NOT NULL DEFAULT 1,
            interval_s     REAL NOT NULL DEFAULT 30.0,
            payload_format TEXT NOT NULL DEFAULT 'json',
            updated_at     TEXT NOT NULL DEFAULT (datetime('now'))
        );
        -- store_forward_queue: offline queue depth + flush state
        -- (Features.md §Telemetry). The Sampler fills this when offline;
        -- the flusher drains it on reconnect.
        CREATE TABLE IF NOT EXISTS store_forward_queue (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            metric       TEXT NOT NULL,
            value_json   TEXT NOT NULL,
            queued_at    TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS ix_sfq_queued_at
            ON store_forward_queue (queued_at);
        """,
    ),
    (
        21,
        """
        -- integrations: dashboard-level integration endpoints
        -- (Features.md §Settings). One row per integration (swupdate,
        -- cloud, telemetry, coprocessor); config_json holds the per-
        -- integration settings.
        CREATE TABLE IF NOT EXISTS integrations (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            kind         TEXT NOT NULL CHECK (kind IN ('swupdate', 'cloud', 'telemetry', 'coprocessor')),
            name         TEXT NOT NULL,
            config_json  TEXT NOT NULL DEFAULT '{}',
            enabled      INTEGER NOT NULL DEFAULT 1,
            created_at   TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at   TEXT NOT NULL DEFAULT (datetime('now'))
        );
        -- directory_config: LDAP/AD/SSO providers (Features.md §Users).
        CREATE TABLE IF NOT EXISTS directory_config (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            provider     TEXT NOT NULL CHECK (provider IN ('ldap', 'active_directory', 'sso_oauth', 'sso_saml')),
            name         TEXT NOT NULL,
            config_json  TEXT NOT NULL DEFAULT '{}',
            enabled      INTEGER NOT NULL DEFAULT 0,
            last_sync_at TEXT,
            created_at   TEXT NOT NULL DEFAULT (datetime('now'))
        );
        -- scheduled_jobs: dashboard-level scheduled tasks (Features.md §Maintenance).
        CREATE TABLE IF NOT EXISTS scheduled_jobs (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            name         TEXT NOT NULL,
            cron         TEXT NOT NULL,
            action       TEXT NOT NULL,
            enabled      INTEGER NOT NULL DEFAULT 1,
            last_run_at  TEXT,
            last_status  TEXT,
            created_at   TEXT NOT NULL DEFAULT (datetime('now'))
        );
        -- maintenance_log: rotation, vacuum, diagnostic-bundle events.
        CREATE TABLE IF NOT EXISTS maintenance_log (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            action       TEXT NOT NULL,
            detail       TEXT NOT NULL DEFAULT '',
            actor        TEXT NOT NULL DEFAULT 'system',
            ts           TEXT NOT NULL DEFAULT (datetime('now'))
        );
        """,
    ),
    (
        22,
        """
        -- dashboard_about: dashboard version + build metadata cache
        -- (Features.md §Settings). Single-row (id=1), populated at startup.
        CREATE TABLE IF NOT EXISTS dashboard_about (
            id          INTEGER PRIMARY KEY CHECK (id = 1),
            version     TEXT NOT NULL DEFAULT '',
            platform    TEXT NOT NULL DEFAULT '',
            build_date  TEXT NOT NULL DEFAULT (datetime('now')),
            license     TEXT NOT NULL DEFAULT 'Apache-2.0'
        );
        INSERT OR IGNORE INTO dashboard_about (id) VALUES (1);
        """,
    ),
    (
        23,
        """
        -- Backfill: add the UNIQUE constraints the new sub-routers depend
        -- on for their ON CONFLICT(name) DO UPDATE upsert pattern. The
        -- original tables were created without them, so the unique
        -- indexes must be added separately to preserve existing data
        -- (SQLite raises an error if you try to ALTER TABLE ADD UNIQUE
        -- and the column has duplicates — DROP+recreate is the safer
        -- path).
        -- Notifications: each (name) is unique.
        CREATE UNIQUE INDEX IF NOT EXISTS idx_notifications_name
            ON notifications(name);
        -- Custom roles: each (name) is unique.
        CREATE UNIQUE INDEX IF NOT EXISTS idx_custom_roles_name
            ON custom_roles(name);
        -- Directory providers: each (name) is unique.
        CREATE UNIQUE INDEX IF NOT EXISTS idx_directory_config_name
            ON directory_config(name);
        -- Integrations: each (name) is unique.
        CREATE UNIQUE INDEX IF NOT EXISTS idx_integrations_name
            ON integrations(name);
        -- Backup jobs: each (name) is unique.
        CREATE UNIQUE INDEX IF NOT EXISTS idx_backup_jobs_name
            ON backup_jobs(name);
        -- Groups: each (name) is unique.
        CREATE UNIQUE INDEX IF NOT EXISTS idx_groups_name
            ON groups(name);
        -- Scheduled jobs: each (name) is unique.
        CREATE UNIQUE INDEX IF NOT EXISTS idx_scheduled_jobs_name
            ON scheduled_jobs(name);
        -- Telemetry metrics: each (metric) is unique.
        CREATE UNIQUE INDEX IF NOT EXISTS idx_telemetry_metrics_metric
            ON telemetry_metrics(metric);
        """,
    ),
    (
        24,
        """
        -- groups.permissions: per-group, per-resource action list
        -- (Features.md Req 11 "Permissions", effective-permissions viewer
        -- in app/routers/auth.py). The column is a JSON string so the
        -- effective-permissions endpoint can union across group memberships
        -- without an extra join table. Empty/missing = no extra grants
        -- beyond the user's built-in role. Numbered 24 (not 16) because
        -- version 16 is already taken by the backup_jobs migration and
        -- a duplicate-key 16 was silently skipped by migrate() — the
        -- groups table is created in migration 17, so this ALTER must
        -- run after it.
        ALTER TABLE groups ADD COLUMN permissions TEXT NOT NULL DEFAULT '{}';
        """,
    ),
    (
        25,
        """
        -- users.user_source: per-user account type
        -- (Features.md §Settings Dashboard Users). One of
        -- 'local' (dashboard-managed credentials) or 'system'
        -- (PAM-mapped). New users default to 'local' so the
        -- dashboard-local behavior is preserved for existing
        -- installs. The mode is a column, not a global flag,
        -- so a single install can mix the two.
        ALTER TABLE users ADD COLUMN user_source TEXT NOT NULL
            DEFAULT 'local' CHECK (user_source IN ('local','system'));
        ALTER TABLE users ADD COLUMN system_username TEXT;
        """,
    ),
]


async def migrate(conn: aiosqlite.Connection) -> int:
    """Apply all pending migrations. Returns the resulting schema version."""
    await conn.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)")
    cur = await conn.execute("SELECT MAX(version) FROM schema_version")
    row = await cur.fetchone()
    current = row[0] if row and row[0] is not None else 0

    for version, sql in MIGRATIONS:
        if version > current:
            await conn.executescript(sql)
            await conn.execute("INSERT INTO schema_version (version) VALUES (?)", (version,))
            current = version
    await conn.commit()
    return current
