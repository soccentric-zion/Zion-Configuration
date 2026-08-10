//! Canonical config store (spec §3.2).
//!
//! A single versioned config tree persisted in SQLite at
//! `<data>/webconfig/config.db`. The tree is serialized as JSON inside one
//! transactional row so the public typed API remains stable while SQLite is
//! the authoritative database. Existing `config.json` files are imported once
//! on first open and are never written again.
//!
//! The store is synchronous and blocking-friendly: the HTTP layer runs it on a
//! blocking task (spec §3, applier trait). It performs no network or D-Bus I/O.

use std::path::PathBuf;

use rusqlite::{params, Connection, OptionalExtension};
use serde::{Deserialize, Serialize};
use thiserror::Error;

use crate::schema::Schema;
use crate::sysroot::Sysroot;
use crate::CONFIG_SCHEMA_VERSION;

#[derive(Debug, Error)]
pub enum StoreError {
    #[error("config file not found at {0}")]
    NotFound(PathBuf),
    #[error("config serialization error: {0}")]
    Parse(#[from] serde_json::Error),
    #[error("sqlite error: {0}")]
    Sqlite(#[from] rusqlite::Error),
    #[error("schema validation failed: {0}")]
    Validation(String),
    #[error("io error: {0}")]
    Io(#[from] std::io::Error),
    #[error("schema version mismatch: file={file} engine={engine}")]
    Version { file: u32, engine: u32 },
    #[error("no migration registered for schema version {0}")]
    Migration(u32),
    #[error("schema error: {0}")]
    Schema(#[from] crate::schema::SchemaError),
}

/// The persisted wrapper: schema version + the tree itself.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ConfigFile {
    pub schema_version: u32,
    #[serde(default)]
    pub config: Config,
}

/// The root config tree. Sections mirror the nine functional areas (spec §4).
/// Schema-driven app settings and alert packs live as `serde_json::Value` so
/// new settings can be dropped in as schema files with zero code change
/// (spec §3, schema-driven rendering everywhere).
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct Config {
    #[serde(default)]
    pub system: system::SystemConfig,
    #[serde(default)]
    pub network: network::NetworkConfig,
    #[serde(default)]
    pub services: services::ServicesConfig,
    #[serde(default)]
    pub updates: updates::UpdatesConfig,
    #[serde(default)]
    pub monitoring: monitoring::MonitoringConfig,
    #[serde(default)]
    pub logs: logs::LogsConfig,
    #[serde(default)]
    pub alerts: alerts::AlertsConfig,
    #[serde(default)]
    pub security: security::SecurityConfig,
    #[serde(default)]
    pub io: io::IoConfig,
    /// Schema-driven application settings keyed by app id. The shape of each
    /// value is governed by a schema file in `/usr/share/webconfig/schemas/`.
    #[serde(default)]
    pub app_settings: serde_json::Map<String, serde_json::Value>,
}

impl ConfigFile {
    /// A fresh default config for first boot.
    pub fn new() -> Self {
        Self {
            schema_version: CONFIG_SCHEMA_VERSION,
            config: Config::default(),
        }
    }
}

impl Default for ConfigFile {
    fn default() -> Self {
        Self::new()
    }
}

/// The canonical config store: load / commit / backup, schema-validated.
#[derive(Debug)]
pub struct ConfigStore {
    sysroot: Sysroot,
    schema: Schema,
    defaults: Config,
}

impl ConfigStore {
    /// Open the store for `sysroot`, validating commits against `schema`.
    pub fn new(sysroot: Sysroot, schema: Schema) -> Self {
        Self {
            sysroot,
            schema,
            defaults: Config::default(),
        }
    }

    /// Open the store with board YAML values used only when SQLite has no live
    /// row yet. Once seeded, the database is authoritative.
    pub fn with_defaults(sysroot: Sysroot, schema: Schema, defaults: Config) -> Self {
        Self {
            sysroot,
            schema,
            defaults,
        }
    }

    fn path(&self) -> PathBuf {
        self.sysroot.webconfig_dir().join("config.db")
    }

    fn legacy_path(&self) -> PathBuf {
        self.sysroot.webconfig_dir().join("config.json")
    }

    /// Load the config tree, or return a fresh default on first boot.
    /// A backup of the previous file is *not* touched on load.
    pub fn load(&self) -> Result<ConfigFile, StoreError> {
        let path = self.path();
        std::fs::create_dir_all(self.sysroot.webconfig_dir())?;
        let conn = Connection::open(&path)?;
        self.initialize(&conn)?;
        let row: Option<(u32, String)> = conn
            .query_row(
                "SELECT schema_version, config_json FROM config_state WHERE id = 1",
                [],
                |row| Ok((row.get(0)?, row.get(1)?)),
            )
            .optional()?;
        let Some((schema_version, raw)) = row else {
            let file = self.import_or_default()?;
            self.write_connection(&conn, &file)?;
            return Ok(file);
        };
        let file = ConfigFile {
            schema_version,
            config: serde_json::from_str(&raw)?,
        };
        if file.schema_version > CONFIG_SCHEMA_VERSION {
            // Newer file than this engine — refuse rather than silently drop.
            return Err(StoreError::Version {
                file: file.schema_version,
                engine: CONFIG_SCHEMA_VERSION,
            });
        }
        Self::migrate(file)
    }

    /// Migrate a config tree loaded at `file.schema_version` forward to
    /// `CONFIG_SCHEMA_VERSION`, one version step at a time. Add a new match
    /// arm here (and bump `CONFIG_SCHEMA_VERSION` in `lib.rs`) whenever a
    /// schema change needs real data transformation beyond serde's
    /// `#[serde(default)]` tolerance — e.g. renaming or restructuring a field
    /// rather than just adding one. Currently a no-op: v1 is the only schema
    /// version ever persisted, and every field addition so far has been
    /// purely additive (new fields default via serde, nothing to transform).
    /// The lint allows below are scaffold-only: with a single catch-all arm
    /// the loop is trivially one pass, but clippy would otherwise block
    /// landing this extension point until a real migration arm exists.
    #[allow(
        clippy::never_loop,
        clippy::match_single_binding,
        unreachable_code,
        unused_mut
    )]
    fn migrate(mut file: ConfigFile) -> Result<ConfigFile, StoreError> {
        while file.schema_version < CONFIG_SCHEMA_VERSION {
            file = match file.schema_version {
                // 1 => migrate_v1_to_v2(file),
                other => return Err(StoreError::Migration(other)),
            };
        }
        Ok(file)
    }

    /// Validate `file` against the schema, then atomically write it to disk
    /// and refresh the `.bak` backup. Returns the persisted tree.
    pub fn commit(&self, file: &ConfigFile) -> Result<(), StoreError> {
        self.schema.validate(&file.config)?;

        let dir = self.sysroot.webconfig_dir();
        std::fs::create_dir_all(&dir)?;
        let conn = Connection::open(self.path())?;
        self.initialize(&conn)?;
        self.write_connection(&conn, file)?;
        tracing::info!("config committed to SQLite");
        Ok(())
    }

    fn initialize(&self, conn: &Connection) -> Result<(), StoreError> {
        conn.execute_batch(
            "PRAGMA journal_mode = WAL;
             PRAGMA foreign_keys = ON;
             CREATE TABLE IF NOT EXISTS config_state (
                 id INTEGER PRIMARY KEY CHECK (id = 1),
                 schema_version INTEGER NOT NULL,
                 config_json TEXT NOT NULL,
                 updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
             );
             CREATE TABLE IF NOT EXISTS config_state_backup (
                 id INTEGER PRIMARY KEY CHECK (id = 1),
                 schema_version INTEGER NOT NULL,
                 config_json TEXT NOT NULL,
                 updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
             );",
        )?;
        Ok(())
    }

    fn write_connection(&self, conn: &Connection, file: &ConfigFile) -> Result<(), StoreError> {
        let raw = serde_json::to_string(&file.config)?;
        // Atomic commit (spec §3 "atomic write + backup copy"): in one
        // transaction, snapshot the *previous* committed config into the
        // backup table, then upsert the new config. If anything fails the
        // transaction is rolled back and the live row is untouched.
        conn.execute_batch("BEGIN")?;
        let result: Result<(), rusqlite::Error> = (|| {
            conn.execute(
                "INSERT OR REPLACE INTO config_state_backup
                     (id, schema_version, config_json, updated_at)
                 SELECT id, schema_version, config_json, updated_at
                 FROM config_state WHERE id = 1",
                [],
            )?;
            conn.execute(
                "INSERT INTO config_state (id, schema_version, config_json) VALUES (1, ?1, ?2)
                 ON CONFLICT(id) DO UPDATE SET schema_version = excluded.schema_version,
                 config_json = excluded.config_json, updated_at = CURRENT_TIMESTAMP",
                params![file.schema_version, raw],
            )?;
            Ok(())
        })();
        match result {
            Ok(()) => {
                conn.execute_batch("COMMIT")?;
            }
            Err(e) => {
                let _ = conn.execute_batch("ROLLBACK");
                return Err(StoreError::Sqlite(e));
            }
        }
        Ok(())
    }

    fn import_or_default(&self) -> Result<ConfigFile, StoreError> {
        let legacy = self.legacy_path();
        if legacy.exists() {
            let raw = std::fs::read_to_string(&legacy)?;
            tracing::info!(path = %legacy.display(), "importing legacy JSON config into SQLite");
            return Ok(serde_json::from_str(&raw)?);
        }
        tracing::info!("no live config found — seeding defaults from board YAML layer");
        Ok(ConfigFile {
            schema_version: CONFIG_SCHEMA_VERSION,
            config: self.defaults.clone(),
        })
    }

    /// Load → mutate → commit in one call. The closure receives a mutable
    /// config tree; if it returns `Ok`, the result is validated and persisted.
    pub fn update<F>(&self, f: F) -> Result<ConfigFile, StoreError>
    where
        F: FnOnce(&mut Config) -> Result<(), StoreError>,
    {
        let mut file = self.load()?;
        f(&mut file.config)?;
        self.commit(&file)?;
        Ok(file)
    }

    /// Path on disk (for diagnostics / backup download).
    pub fn file_path(&self) -> PathBuf {
        self.path()
    }

    /// Load the previous committed config (the backup snapshot), or `None` if
    /// only one commit has ever happened (no prior state to snapshot). Used for
    /// disaster recovery / rollback-to-last-known-good (spec §3 backup copy).
    pub fn load_backup(&self) -> Result<Option<ConfigFile>, StoreError> {
        let path = self.path();
        if !path.exists() {
            return Ok(None);
        }
        let conn = Connection::open(&path)?;
        self.initialize(&conn)?;
        let row: Option<(u32, String)> = conn
            .query_row(
                "SELECT schema_version, config_json FROM config_state_backup WHERE id = 1",
                [],
                |row| Ok((row.get(0)?, row.get(1)?)),
            )
            .optional()?;
        match row {
            None => Ok(None),
            Some((schema_version, raw)) => Ok(Some(ConfigFile {
                schema_version,
                config: serde_json::from_str(&raw)?,
            })),
        }
    }
}

// ---------------------------------------------------------------------------
// Section models. Kept small and typed; schema-driven app settings live as
// `serde_json::Value` above. Adding a new *typed* field is a schema-version
// bump; adding a new schema-driven app setting is not.
// ---------------------------------------------------------------------------

pub mod system {
    use serde::{Deserialize, Serialize};

    #[derive(Debug, Clone, Default, Serialize, Deserialize)]
    pub struct SystemConfig {
        #[serde(default)]
        pub hostname: Option<String>,
        /// Free-text asset tag / site label — config store only, never written
        /// to the OS hostname (spec §4.1).
        #[serde(default)]
        pub asset_tag: Option<String>,
        #[serde(default)]
        pub hardware_revision: Option<String>,
        #[serde(default)]
        pub serial_number: Option<String>,
        #[serde(default)]
        pub time: TimeConfig,
        #[serde(default)]
        pub ssh: SshConfig,
        #[serde(default)]
        pub ntp_servers: Vec<String>,
    }

    #[derive(Debug, Clone, Default, Serialize, Deserialize)]
    pub struct TimeConfig {
        #[serde(default)]
        pub timezone: Option<String>,
        #[serde(default)]
        pub ntp_enabled: bool,
        /// Precision Time Protocol synchronization. NTP and PTP are mutually
        /// exclusive; legacy configs keep NTP as the default.
        #[serde(default)]
        pub ptp_enabled: bool,
        #[serde(default)]
        pub ptp_interface: Option<String>,
        #[serde(default)]
        pub ptp_domain: Option<u8>,
        /// GPS/GNSS time synchronization through gpsd/chrony.
        #[serde(default)]
        pub gps_enabled: bool,
        #[serde(default)]
        pub gps_device: Option<String>,
        #[serde(default)]
        pub gps_pps_device: Option<String>,
        /// Manual date/time for air-gapped sites (ISO-8601 local). Applied via
        /// `timedatectl set-time` (spec §4.1).
        #[serde(default)]
        pub manual_datetime: Option<String>,
    }

    #[derive(Debug, Clone, Default, Serialize, Deserialize)]
    pub struct SshConfig {
        #[serde(default)]
        pub sshd_enabled: bool,
        #[serde(default)]
        pub serial_getty_enabled: bool,
        #[serde(default)]
        pub authorized_keys: String,
    }
}

pub mod network {
    use serde::{Deserialize, Serialize};

    #[derive(Debug, Clone, Default, Serialize, Deserialize)]
    pub struct NetworkConfig {
        #[serde(default)]
        pub interfaces: Vec<Interface>,
        #[serde(default)]
        pub wifi: Vec<WifiNetwork>,
        #[serde(default)]
        pub regulatory_domain: Option<String>,
        #[serde(default)]
        pub dns_servers: Vec<String>,
        #[serde(default)]
        pub firewall: FirewallConfig,
        /// Commit/confirm rollback window in seconds (spec §4.2 flagship
        /// feature). Default 90.
        #[serde(default = "default_confirm_window")]
        pub confirm_window_secs: u64,
        /// AP provisioning mode trigger (spec §4.2).
        #[serde(default)]
        pub ap_mode: ApMode,
        /// [v2] schema slots — designed now, not implemented (spec §4.2).
        #[serde(default)]
        pub vlan: Vec<serde_json::Value>,
        #[serde(default)]
        pub bridges: Vec<serde_json::Value>,
        #[serde(default)]
        pub cellular: Vec<serde_json::Value>,
        /// [v2] 5G modem profile (spec §4.2 explicitly lists
        /// "cellular/ModemManager" as a deferred v2 design-only slot). No
        /// ModemManager/mmcli applier exists — demoted to the same untyped
        /// placeholder treatment as vlan/bridges/cellular above (Checkpoint
        /// 1.3) rather than the fully-typed `FiveGConfig` this used to be,
        /// which read as an active v1 feature it never was: `PUT /network`
        /// accepted and stored it, but no applier ever acted on it.
        #[serde(default)]
        pub cellular_5g: serde_json::Value,
    }

    fn default_confirm_window() -> u64 {
        90
    }

    #[derive(Debug, Clone, Default, Serialize, Deserialize)]
    pub struct Interface {
        pub name: String,
        /// Optional locally assigned MAC address, applied by systemd-networkd.
        #[serde(default)]
        pub mac_address: Option<String>,
        #[serde(default)]
        pub enabled: bool,
        #[serde(default = "default_dhcp")]
        pub dhcp: bool,
        #[serde(default)]
        pub address: Option<String>,
        #[serde(default)]
        pub prefix: Option<u8>,
        #[serde(default)]
        pub gateway: Option<String>,
        #[serde(default)]
        pub mtu: Option<u32>,
        /// Per-interface DNS (spec §4.2). Empty = use global list.
        #[serde(default)]
        pub dns: Vec<String>,
        /// Firewall services open on this interface (spec §4.2 service-based
        /// firewall). Values are service ids from the allowlist.
        #[serde(default)]
        pub open_services: Vec<String>,
        /// Additional explicitly opened ports for this interface.
        #[serde(default)]
        pub open_ports: Vec<OpenPort>,
    }

    #[derive(Debug, Clone, Serialize, Deserialize)]
    pub struct OpenPort {
        pub port: u16,
        /// `tcp` or `udp`.
        pub protocol: String,
    }

    impl Default for OpenPort {
        fn default() -> Self {
            Self {
                port: 0,
                protocol: "tcp".into(),
            }
        }
    }

    fn default_dhcp() -> bool {
        true
    }

    #[derive(Debug, Clone, Default, Serialize, Deserialize)]
    pub struct WifiNetwork {
        pub ssid: String,
        #[serde(default)]
        pub psk: Option<String>,
        #[serde(default)]
        pub priority: i32,
        #[serde(default)]
        pub hidden: bool,
    }

    #[derive(Debug, Clone, Serialize, Deserialize)]
    pub struct FirewallConfig {
        #[serde(default = "default_firewall_enabled")]
        pub enabled: bool,
        /// Services globally open regardless of interface binding.
        #[serde(default)]
        pub default_open: Vec<String>,
        /// Additional explicitly opened ports on every enabled interface.
        #[serde(default)]
        pub default_open_ports: Vec<OpenPort>,
    }

    fn default_firewall_enabled() -> bool {
        true
    }

    // Manual Default so `Config::default()` (the first-boot seed) ships the
    // firewall ON — spec §4.2: "Default: everything closed except the UI." The
    // derived Default would give `enabled: false`, contradicting the serde
    // default and leaving the device unprotected out of the box.
    impl Default for FirewallConfig {
        fn default() -> Self {
            Self {
                enabled: true,
                default_open: Vec::new(),
                default_open_ports: Vec::new(),
            }
        }
    }

    #[derive(Debug, Clone, Default, Serialize, Deserialize)]
    pub struct ApMode {
        #[serde(default)]
        pub enabled: bool,
        /// `no-config` or `gpio-button` (spec §4.2).
        #[serde(default = "default_ap_trigger")]
        pub trigger: String,
        #[serde(default = "default_ap_ssid")]
        pub ssid: String,
        #[serde(default = "default_ap_passphrase")]
        pub passphrase: String,
    }

    fn default_ap_trigger() -> String {
        "no-config".to_string()
    }
    fn default_ap_ssid() -> String {
        "SoCcentric-Setup".to_string()
    }
    fn default_ap_passphrase() -> String {
        // AP must be open-ish but spec mandates WPA; installer sets a real one.
        "change-me-now".to_string()
    }
}

pub mod services {
    use serde::{Deserialize, Serialize};

    #[derive(Debug, Clone, Default, Serialize, Deserialize)]
    pub struct ServicesConfig {
        #[serde(default)]
        pub mqtt: MqttConfig,
        /// Per-service log level overrides (keyed by unit name) where the
        /// service schema declares one (spec §4.3 / §4.6).
        #[serde(default)]
        pub log_levels: std::collections::BTreeMap<String, String>,
    }

    #[derive(Debug, Clone, Default, Serialize, Deserialize)]
    pub struct MqttConfig {
        #[serde(default)]
        pub enabled: bool,
        #[serde(default)]
        pub host: Option<String>,
        #[serde(default)]
        pub port: Option<u16>,
        #[serde(default)]
        pub tls: bool,
        #[serde(default)]
        pub username: Option<String>,
        #[serde(default)]
        pub password: Option<String>,
        /// Client cert id from the Security cert store, or None.
        #[serde(default)]
        pub client_cert_id: Option<String>,
        #[serde(default)]
        pub client_id: Option<String>,
        #[serde(default)]
        pub base_topic: Option<String>,
    }
}

pub mod updates {
    use serde::{Deserialize, Serialize};

    #[derive(Debug, Clone, Default, Serialize, Deserialize)]
    pub struct UpdatesConfig {
        #[serde(default)]
        pub ota: OtaConfig,
        #[serde(default)]
        pub backup: BackupConfig,
        /// Whether HTTP→HTTPS redirect is on (mirrors security; kept here for
        /// the Updates page convenience).
        #[serde(default)]
        pub https_redirect: bool,
    }

    #[derive(Debug, Clone, Default, Serialize, Deserialize)]
    pub struct OtaConfig {
        #[serde(default)]
        pub enabled: bool,
        #[serde(default)]
        pub server_url: Option<String>,
        /// v1: notify-only (spec §4.4). [v2] auto-apply + maintenance windows.
        #[serde(default)]
        pub auto_apply: bool,
        #[serde(default)]
        pub maintenance_window: Option<serde_json::Value>,
    }

    #[derive(Debug, Clone, Default, Serialize, Deserialize)]
    pub struct BackupConfig {
        #[serde(default)]
        pub enabled: bool,
        #[serde(default)]
        pub server_url: Option<String>,
        #[serde(default = "default_feature_enabled")]
        pub backup_enabled: bool,
        #[serde(default = "default_feature_enabled")]
        pub restore_enabled: bool,
    }

    fn default_feature_enabled() -> bool {
        true
    }
}

pub mod monitoring {
    use serde::{Deserialize, Serialize};

    #[derive(Debug, Clone, Default, Serialize, Deserialize)]
    pub struct MonitoringConfig {
        /// Dashboard poll interval in seconds while the page is open (spec §4.5).
        #[serde(default = "default_poll_interval_secs")]
        pub poll_interval_secs: u64,
        #[serde(default)]
        pub telemetry: TelemetryConfig,
    }

    #[derive(Debug, Clone, Default, Serialize, Deserialize)]
    pub struct TelemetryConfig {
        #[serde(default)]
        pub enabled: bool,
        #[serde(default)]
        pub server_url: String,
        #[serde(default)]
        pub username: String,
        #[serde(default)]
        pub password: String,
        #[serde(default = "default_publish_interval_secs")]
        pub publish_interval_secs: u64,
        #[serde(default)]
        pub include_diagnostics: bool,
    }

    fn default_poll_interval_secs() -> u64 {
        3
    }

    fn default_publish_interval_secs() -> u64 {
        30
    }
}

pub mod logs {
    use serde::{Deserialize, Serialize};

    #[derive(Debug, Clone, Default, Serialize, Deserialize)]
    pub struct LogsConfig {
        /// `volatile` (default) or `persistent` (spec §4.6).
        #[serde(default = "default_storage")]
        pub storage: String,
        /// Max journal size in bytes when persistent (eMMC wear, spec §4.6).
        #[serde(default = "default_max_size")]
        pub max_size_bytes: u64,
        /// Maximum retention duration in days. Zero leaves journald's default.
        #[serde(default = "default_retention_days")]
        pub retention_days: u32,
        #[serde(default)]
        pub remote_syslog: Option<RemoteSyslog>,
    }

    fn default_storage() -> String {
        "volatile".to_string()
    }
    fn default_max_size() -> u64 {
        64 * 1024 * 1024
    }
    fn default_retention_days() -> u32 {
        30
    }

    #[derive(Debug, Clone, Default, Serialize, Deserialize)]
    pub struct RemoteSyslog {
        #[serde(default)]
        pub enabled: bool,
        pub host: String,
        pub port: u16,
        /// `udp` or `tcp` (spec §4.6 — one destination).
        pub protocol: String,
    }
}

pub mod alerts {
    use serde::{Deserialize, Serialize};

    fn default_enabled() -> bool {
        true
    }

    #[derive(Debug, Clone, Serialize, Deserialize)]
    pub struct AlertsConfig {
        /// Global alert-engine switch (spec §4.7). Defaults to **true** so the
        /// shipped per-platform default packs (disk-full, watchdog-reset,
        /// Zion throttling) protect the device out of the box; an operator
        /// can turn the whole engine off here. The daemon's alert timer honors
        /// this — when false it emits clears for any firing rules and skips
        /// evaluation. (Previously this field defaulted to false AND was
        /// ignored, so the toggle was a no-op and default packs only fired by
        /// accident because the timer never checked it.)
        #[serde(default = "default_enabled")]
        pub enabled: bool,
        #[serde(default)]
        pub rules: Vec<AlertRule>,
        /// Webhook POST targets (spec §4.7).
        #[serde(default)]
        pub webhooks: Vec<String>,
        /// MQTT event topic base (spec §4.7).
        #[serde(default)]
        pub mqtt_event_topic: Option<String>,
    }

    #[derive(Debug, Clone, Default, Serialize, Deserialize)]
    pub struct AlertRule {
        pub id: String,
        #[serde(default = "default_enabled")]
        pub enabled: bool,
        pub metric: String,
        /// `>`, `<`, `==`, `!=`.
        pub comparison: String,
        pub threshold: f64,
        /// Duration in seconds the condition must hold to fire (anti-flap,
        /// spec §4.7).
        #[serde(default)]
        pub duration_secs: u64,
        /// `webhook`, `mqtt`, `ui` (always UI + event log).
        #[serde(default)]
        pub actions: Vec<String>,
    }

    // Manual Default so `Config::default()` (the first-boot seed) enables the
    // alert engine — matching the serde default of `default_enabled` above.
    // The derived Default would give `enabled: false`, contradicting the
    // out-of-box protection intent.
    impl Default for AlertsConfig {
        fn default() -> Self {
            Self {
                enabled: true,
                rules: Vec::new(),
                webhooks: Vec::new(),
                mqtt_event_topic: None,
            }
        }
    }
}

pub mod security {
    use serde::{Deserialize, Serialize};

    #[derive(Debug, Clone, Default, Serialize, Deserialize)]
    pub struct SecurityConfig {
        #[serde(default = "default_idle")]
        pub session_idle_timeout_secs: u64,
        /// Plain HTTP admin opt-in (spec §4.8; default off).
        #[serde(default)]
        pub allow_plain_http: bool,
        #[serde(default)]
        pub wireguard: WireGuardConfig,
        /// Cert ids present in the device cert store (metadata only here;
        /// the certs themselves live on the data partition).
        #[serde(default)]
        pub certificates: Vec<CertMeta>,
    }

    fn default_idle() -> u64 {
        900
    }

    #[derive(Debug, Clone, Default, Serialize, Deserialize)]
    pub struct WireGuardConfig {
        #[serde(default)]
        pub enabled: bool,
        /// Listen port.
        #[serde(default)]
        pub port: u16,
        /// Peers (spec §4.8).
        #[serde(default)]
        pub peers: Vec<WgPeer>,
    }

    #[derive(Debug, Clone, Default, Serialize, Deserialize)]
    pub struct WgPeer {
        pub public_key: String,
        #[serde(default)]
        pub allowed_ips: String,
        #[serde(default)]
        pub endpoint: Option<String>,
    }

    #[derive(Debug, Clone, Default, Serialize, Deserialize)]
    pub struct CertMeta {
        pub id: String,
        pub kind: String, // "ca" | "client"
        pub subject: String,
        pub not_after: String, // ISO-8601
    }

    // Re-impl default idle timeout at the section level via a helper, since
    // serde `default=` can't call the function above without it being in scope
    // for the field. We expose a typed accessor instead.
    impl SecurityConfig {
        pub fn idle_timeout_secs(&self) -> u64 {
            if self.session_idle_timeout_secs == 0 {
                default_idle()
            } else {
                self.session_idle_timeout_secs
            }
        }
    }
}

pub mod io {
    use serde::{Deserialize, Serialize};

    #[derive(Debug, Clone, Default, Serialize, Deserialize)]
    pub struct IoConfig {
        #[serde(default)]
        pub serial: Vec<SerialPort>,
        #[serde(default = "default_enabled")]
        pub serial_enabled: bool,
        #[serde(default)]
        pub can: Vec<CanInterface>,
        #[serde(default = "default_enabled")]
        pub can_enabled: bool,
        #[serde(default)]
        pub gpio: Vec<GpioPin>,
        #[serde(default = "default_enabled")]
        pub gpio_enabled: bool,
        #[serde(default)]
        pub cameras: Vec<CameraConfig>,
        #[serde(default = "default_enabled")]
        pub cameras_enabled: bool,
        #[serde(default)]
        pub mcu: Option<McuConfig>,
        #[serde(default)]
        pub board_leds: BoardLedConfig,
    }

    #[derive(Debug, Clone, Default, Serialize, Deserialize)]
    pub struct BoardLedConfig {
        #[serde(default = "default_enabled")]
        pub enabled: bool,
        #[serde(default)]
        pub system_led: Option<String>,
        #[serde(default)]
        pub os_led: Option<String>,
        #[serde(default)]
        pub hardware_led: Option<String>,
        #[serde(default)]
        pub app: AppLedConfig,
    }

    #[derive(Debug, Clone, Default, Serialize, Deserialize)]
    pub struct AppLedConfig {
        #[serde(default)]
        pub enabled: bool,
        #[serde(default)]
        pub led: Option<String>,
        #[serde(default)]
        pub service: Option<String>,
    }

    fn default_enabled() -> bool {
        true
    }

    #[derive(Debug, Clone, Default, Serialize, Deserialize)]
    pub struct SerialPort {
        pub device: String,
        #[serde(default = "default_enabled")]
        pub enabled: bool,
        #[serde(default)]
        pub baud: u32,
        #[serde(default)]
        pub mode: String, // "rs232" | "rs485"
        /// Ownership assignment (spec §4.9): console | customer | modbus | unassigned.
        #[serde(default = "default_owner")]
        pub owner: String,
    }

    fn default_owner() -> String {
        "unassigned".to_string()
    }

    #[derive(Debug, Clone, Default, Serialize, Deserialize)]
    pub struct CanInterface {
        pub name: String,
        #[serde(default = "default_enabled")]
        pub enabled: bool,
        #[serde(default)]
        pub bitrate: u32,
        #[serde(default)]
        pub fd: bool,
        #[serde(default)]
        pub data_bitrate: u32,
        #[serde(default)]
        pub listen_only: bool,
        #[serde(default)]
        pub restart_ms: u32,
        #[serde(default)]
        pub up_on_boot: bool,
    }

    #[derive(Debug, Clone, Default, Serialize, Deserialize)]
    pub struct GpioPin {
        pub name: String,
        #[serde(default = "default_enabled")]
        pub enabled: bool,
        #[serde(default)]
        pub direction: String, // "in" | "out"
        #[serde(default)]
        pub active_low: bool,
        #[serde(default)]
        pub initial_state: Option<bool>,
    }

    #[derive(Debug, Clone, Default, Serialize, Deserialize)]
    pub struct CameraConfig {
        pub name: String,
        #[serde(default = "default_enabled")]
        pub enabled: bool,
    }

    #[derive(Debug, Clone, Default, Serialize, Deserialize)]
    pub struct McuConfig {
        pub kind: String, // "stm32" | "picow"
        #[serde(default = "default_enabled")]
        pub enabled: bool,
        #[serde(default)]
        pub firmware_version: Option<String>,
        #[serde(default)]
        pub settings: serde_json::Map<String, serde_json::Value>,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::schema::Schema;

    #[test]
    fn commit_and_load_roundtrip() {
        let dir = tempfile::tempdir().unwrap();
        let sys = Sysroot::at(dir.path());
        let schema = Schema::default_permissive();
        let store = ConfigStore::new(sys, schema);

        let mut file = store.load().unwrap();
        file.config.system.hostname = Some("zion-01".into());
        store.commit(&file).unwrap();

        let reloaded = ConfigStore::new(Sysroot::at(dir.path()), Schema::default_permissive())
            .load()
            .unwrap();
        assert_eq!(
            reloaded.config.system.hostname.as_deref(),
            Some("zion-01")
        );
    }

    #[test]
    fn migrate_is_a_no_op_at_current_schema_version() {
        let file = ConfigFile {
            schema_version: CONFIG_SCHEMA_VERSION,
            config: Config::default(),
        };
        let migrated = ConfigStore::migrate(file).unwrap();
        assert_eq!(migrated.schema_version, CONFIG_SCHEMA_VERSION);
    }

    #[test]
    fn migrate_errors_on_an_older_version_with_no_registered_step() {
        // No migration is registered below CONFIG_SCHEMA_VERSION (== 1) yet —
        // a file claiming an older version has no path forward and must
        // error, not silently pass through as if nothing changed.
        let file = ConfigFile {
            schema_version: 0,
            config: Config::default(),
        };
        let err = ConfigStore::migrate(file).unwrap_err();
        assert!(matches!(err, StoreError::Migration(0)));
    }

    #[test]
    fn commit_is_transactional_in_sqlite() {
        let dir = tempfile::tempdir().unwrap();
        let sys = Sysroot::at(dir.path());
        let store = ConfigStore::new(sys, Schema::default_permissive());

        let mut file = store.load().unwrap();
        file.config.system.hostname = Some("first".into());
        store.commit(&file).unwrap();

        file.config.system.hostname = Some("second".into());
        store.commit(&file).unwrap();

        let conn = rusqlite::Connection::open(dir.path().join("data/webconfig/config.db")).unwrap();
        let cur: String = conn
            .query_row(
                "SELECT config_json FROM config_state WHERE id = 1",
                [],
                |row| row.get(0),
            )
            .unwrap();
        assert!(cur.contains("second"));
    }

    #[test]
    fn update_helper_validates_and_persists() {
        let dir = tempfile::tempdir().unwrap();
        let store = ConfigStore::new(Sysroot::at(dir.path()), Schema::default_permissive());
        let out = store
            .update(|c| {
                c.system.hostname = Some("z".into());
                Ok(())
            })
            .unwrap();
        assert_eq!(out.config.system.hostname.as_deref(), Some("z"));
    }

    #[test]
    fn commit_snapshots_previous_state_into_backup_row() {
        // spec §3: each commit keeps a backup copy of the *previous* state.
        let dir = tempfile::tempdir().unwrap();
        let sys = Sysroot::at(dir.path());
        let store = ConfigStore::new(sys, Schema::default_permissive());

        // `load()` seeds the defaults (writes id=1); no prior state exists yet,
        // so no backup row is created during seeding.
        let mut file = store.load().unwrap();
        assert!(store.load_backup().unwrap().is_none());

        // First explicit commit ("first"): the backup row snapshots the
        // previous (seeded-default) state, whose hostname is None.
        file.config.system.hostname = Some("first".into());
        store.commit(&file).unwrap();
        let backup0 = store.load_backup().unwrap().expect("backup row present");
        assert!(backup0.config.system.hostname.is_none());

        // Second commit ("second"): backup now holds "first".
        file.config.system.hostname = Some("second".into());
        store.commit(&file).unwrap();
        let backup1 = store.load_backup().unwrap().expect("backup row present");
        assert_eq!(backup1.config.system.hostname.as_deref(), Some("first"));

        // The live row holds the latest ("second") state.
        assert_eq!(
            store.load().unwrap().config.system.hostname.as_deref(),
            Some("second")
        );

        // Third commit ("third"): backup now holds "second".
        file.config.system.hostname = Some("third".into());
        store.commit(&file).unwrap();
        let backup2 = store.load_backup().unwrap().expect("backup row present");
        assert_eq!(backup2.config.system.hostname.as_deref(), Some("second"));
        assert_eq!(
            store.load().unwrap().config.system.hostname.as_deref(),
            Some("third")
        );
    }
}
