//! Application dependency assembly.
//!
//! Keeping construction policy here leaves `main` responsible for process
//! lifecycle and listener setup, while feature modules receive a fully wired
//! `AppState`.

use std::path::Path;
use std::sync::Arc;

use webconfig_engine::alert::{AlertEngine, AlertHistory};
use webconfig_engine::audit::AuditLog;
use webconfig_engine::manifest::BoardManifest;
use webconfig_engine::schema::Schema;
use webconfig_engine::{ConfigStore, Sysroot};

use crate::auth::LoginThrottle;
use crate::engine_bridge::EngineBridge;
use crate::state::AppState;

/// Max concurrent `/events` SSE connections (spec §7: current-thread runtime,
/// tight RAM budget — each connection polls `journalctl` on its own tick).
pub(crate) const MAX_SSE_CONNECTIONS: usize = 8;

/// Dependencies needed by the daemon after startup.
pub(crate) struct Application {
    pub state: AppState,
    pub engine: EngineBridge,
    pub manifest: Arc<BoardManifest>,
    pub alert_engine: Arc<tokio::sync::Mutex<AlertEngine>>,
}

/// Build the application state and its long-running feature dependencies.
pub(crate) async fn build(sysroot: Sysroot) -> Application {
    let share = sysroot.share_dir();
    let webconfig_dir = sysroot.webconfig_dir();
    std::fs::create_dir_all(&webconfig_dir).ok();

    let schema = Arc::new(
        Schema::load_dir(&share.join("schemas")).unwrap_or_else(|e| {
            tracing::warn!(error = %e, "schema load failed — permissive");
            Schema::default_permissive()
        }),
    );
    let manifest = Arc::new(load_or_seed_manifest(&sysroot, &share));
    let store = ConfigStore::with_defaults(
        sysroot.clone(),
        (*schema).clone(),
        manifest.config_defaults.clone().unwrap_or_default(),
    );
    let engine = EngineBridge::new(
        store,
        sysroot.clone(),
        &manifest.peripherals,
        &manifest.wifi_radio,
    );
    let database = webconfig_dir.join("config.db");
    let audit = Arc::new(AuditLog::new(database.clone(), 256 * 1024));
    let alert_engine = Arc::new(tokio::sync::Mutex::new(AlertEngine::new()));

    let state = AppState {
        engine: engine.clone(),
        schema,
        manifest: manifest.clone(),
        audit,
        alert_engine: alert_engine.clone(),
        update_backend: crate::update_backend::make_backend(&sysroot),
        systemd: crate::systemd::make_client(&sysroot).await,
        users_path: database,
        login_throttle: Arc::new(LoginThrottle::new()),
        sse_connections: Arc::new(tokio::sync::Semaphore::new(MAX_SSE_CONNECTIONS)),
    };

    Application {
        state,
        engine,
        manifest,
        alert_engine,
    }
}

/// Manifest load order (0.6: JSON at `<share>/board-manifest.json` is the one
/// canonical BSP-installed format/path — matches `docs/board-manifest.md` and
/// the real `board-manifests/zion.json` data). The local YAML override at
/// `/etc/webconfig/board.yaml` is kept as an intentional, documented escape
/// hatch: a field technician can hand-edit a device's manifest without
/// repackaging firmware. There is no longer a `<share>/board.yaml` YAML
/// fallback — that path only existed because the Yocto recipe used to
/// (incorrectly) install YAML into the share dir; the recipe now installs
/// the canonical JSON, so that middle format is dead and has been removed.
fn load_or_seed_manifest(sysroot: &Sysroot, share: &Path) -> BoardManifest {
    let local_yaml = sysroot.join("/etc/webconfig/board.yaml");
    if let Ok(manifest) = BoardManifest::load_yaml_path(&local_yaml) {
        return manifest;
    }
    if let Ok(manifest) = BoardManifest::load(share) {
        return manifest;
    }

    let platform = std::env::var("WEBCONFIG_PLATFORM").unwrap_or_else(|_| "zion".into());
    let raw = match platform.as_str() {
        "generic" | "generic-linux" => include_str!("../../../board-manifests/generic.yaml"),
        _ => include_str!("../../../board-manifests/zion.json"),
    };
    parse_compiled_in_manifest(&platform, raw)
}

/// Parse the compiled-in fallback manifest, logging loudly on failure instead
/// of silently degrading to `BoardManifest::default()` with no trace at all
/// (Checkpoint 1.9 — the manifest drives the entire I/O/dashboard/alerts UI,
/// so a parse failure here needs the same visibility the schema loader
/// already has for the same class of failure).
fn parse_compiled_in_manifest(platform: &str, raw: &str) -> BoardManifest {
    let parsed = if platform == "generic" || platform == "generic-linux" {
        serde_yaml::from_str::<BoardManifest>(raw).map_err(|e| e.to_string())
    } else {
        serde_json::from_str::<BoardManifest>(raw).map_err(|e| e.to_string())
    };
    match parsed {
        Ok(manifest) => manifest,
        Err(error) => {
            tracing::error!(
                %error,
                %platform,
                "compiled-in fallback board manifest failed to parse — falling back to \
                 BoardManifest::default() (degraded: I/O, dashboard tiles, and default alert \
                 rules will all be empty)"
            );
            BoardManifest::default()
        }
    }
}

pub(crate) fn alert_history(engine: &EngineBridge) -> AlertHistory {
    AlertHistory::new(engine.sysroot.webconfig_dir().join("config.db"), 1000)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn malformed_zion_manifest_falls_back_to_default_not_silently() {
        // Checkpoint 1.9: a parse failure must not be indistinguishable from
        // an empty-but-valid manifest — it still degrades to the default
        // (there is no other safe choice), but the caller-visible behavior
        // (BoardManifest::default()) must be reachable and correct even when
        // the input is garbage, and the failure path must be exercised (the
        // tracing::error! itself is asserted by inspection above; this test
        // proves the function doesn't panic and still returns a usable
        // default rather than propagating the parse error).
        let manifest = parse_compiled_in_manifest("zion", "{ not valid json");
        assert_eq!(manifest.platform, "");
        assert!(manifest.peripherals.is_empty());
    }

    #[test]
    fn malformed_generic_manifest_falls_back_to_default_not_silently() {
        let manifest = parse_compiled_in_manifest("generic-linux", ":\n  not: [valid, yaml");
        assert_eq!(manifest.platform, "");
        assert!(manifest.peripherals.is_empty());
    }

    #[test]
    fn valid_zion_manifest_parses_normally() {
        let raw = include_str!("../../../board-manifests/zion.json");
        let manifest = parse_compiled_in_manifest("zion", raw);
        assert_eq!(manifest.platform, "zion");
    }
}
