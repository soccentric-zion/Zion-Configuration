//! Shared application state extracted by handlers via axum `State`.

use std::sync::Arc;

use axum::extract::FromRef;

use webconfig_engine::alert::AlertEngine;
use webconfig_engine::audit::AuditLog;
use webconfig_engine::manifest::BoardManifest;
use webconfig_engine::schema::Schema;
use webconfig_engine::users::UserStore;

use crate::auth::LoginThrottle;
use crate::engine_bridge::EngineBridge;
use crate::systemd::SystemdClient;
use crate::update_backend::UpdateBackend;

/// The top-level state shared across the router.
#[derive(Clone)]
pub struct AppState {
    pub engine: EngineBridge,
    pub schema: Arc<Schema>,
    pub manifest: Arc<BoardManifest>,
    pub audit: Arc<AuditLog>,
    pub alert_engine: Arc<tokio::sync::Mutex<AlertEngine>>,
    pub update_backend: Arc<dyn UpdateBackend>,
    pub systemd: Arc<dyn SystemdClient>,
    pub users_path: std::path::PathBuf,
    pub login_throttle: Arc<LoginThrottle>,
    /// Caps concurrent `/events` SSE connections (each polls `journalctl` on
    /// its own tick) so the current-thread runtime and RAM budget can't be
    /// exhausted by many simultaneous streams.
    pub sse_connections: Arc<tokio::sync::Semaphore>,
}

impl AppState {
    /// Load the user store from disk (cheap; called where mutation is needed).
    pub fn load_users(&self) -> Result<UserStore, webconfig_engine::users::UserError> {
        UserStore::load(&self.users_path)
    }

    pub fn save_users(&self, store: &UserStore) -> Result<(), webconfig_engine::users::UserError> {
        store.save(&self.users_path)
    }
}

// axum FromRef impls so handlers can extract sub-pieces directly.
impl FromRef<AppState> for EngineBridge {
    fn from_ref(s: &AppState) -> Self {
        s.engine.clone()
    }
}
impl FromRef<AppState> for Arc<Schema> {
    fn from_ref(s: &AppState) -> Self {
        s.schema.clone()
    }
}
impl FromRef<AppState> for Arc<BoardManifest> {
    fn from_ref(s: &AppState) -> Self {
        s.manifest.clone()
    }
}
impl FromRef<AppState> for Arc<AuditLog> {
    fn from_ref(s: &AppState) -> Self {
        s.audit.clone()
    }
}
impl FromRef<AppState> for Arc<dyn UpdateBackend> {
    fn from_ref(s: &AppState) -> Self {
        s.update_backend.clone()
    }
}
impl FromRef<AppState> for Arc<dyn SystemdClient> {
    fn from_ref(s: &AppState) -> Self {
        s.systemd.clone()
    }
}
impl FromRef<AppState> for Arc<tokio::sync::Mutex<AlertEngine>> {
    fn from_ref(s: &AppState) -> Self {
        s.alert_engine.clone()
    }
}
impl FromRef<AppState> for Arc<LoginThrottle> {
    fn from_ref(s: &AppState) -> Self {
        s.login_throttle.clone()
    }
}
