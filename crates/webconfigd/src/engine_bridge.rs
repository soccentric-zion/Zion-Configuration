//! Engine bridge (spec §3 + §4.2 flagship commit/confirm rollback).
//!
//! The async HTTP layer communicates with the (synchronous) config engine over
//! this bridge. Applier execution runs on a blocking task; the bridge owns the
//! applier set and the rollback state for the networking commit/confirm flow.
//!
//! Commit/confirm (spec §4.2): when an applier returns
//! `ApplyOutcome::Provisional`, the caller starts a tokio timer for
//! `confirm_window_secs`. If the user reconfirms in time → `confirm()`. If the
//! timer fires first → `timeout_rollback()` calls the applier's `rollback()`.

use std::sync::Arc;

use tokio::sync::Mutex;
use tokio::time::Duration;

use webconfig_engine::appliers;
use webconfig_engine::{Applier, ApplyOutcome, Config, ConfigStore, Sysroot};

use crate::error::{ApiError, ApiResult};

/// A pending provisional apply awaiting commit/confirm.
#[derive(Debug)]
pub struct PendingConfirm {
    pub applier_id: String,
    pub window_secs: u64,
    pub started_at: std::time::Instant,
}

/// Manages the single in-flight provisional apply + its rollback deadline.
#[derive(Debug, Default)]
pub struct ConfirmManager {
    pending: Mutex<Option<PendingConfirm>>,
}

impl ConfirmManager {
    pub fn new() -> Self {
        Self::default()
    }

    pub async fn set(&self, p: PendingConfirm) {
        *self.pending.lock().await = Some(p);
    }

    /// Confirm the pending change — clears the pending state (the apply stands).
    pub async fn confirm(&self) -> Option<PendingConfirm> {
        self.pending.lock().await.take()
    }

    /// True if a provisional apply is awaiting confirmation.
    pub async fn pending(&self) -> Option<PendingConfirm> {
        self.pending.lock().await.as_ref().map(|p| PendingConfirm {
            applier_id: p.applier_id.clone(),
            window_secs: p.window_secs,
            started_at: p.started_at,
        })
    }
}

/// The bridge between the async HTTP layer and the sync engine/appliers.
#[derive(Clone)]
pub struct EngineBridge {
    pub store: Arc<ConfigStore>,
    pub sysroot: Arc<Sysroot>,
    appliers: Arc<Vec<Box<dyn Applier>>>,
    pub confirm: Arc<ConfirmManager>,
}

impl EngineBridge {
    /// `manifest_peripherals`/`wifi_radio` are passed straight through to
    /// `appliers::all()` — only `GpioApplier`/`ApApplier` respectively need
    /// them, to resolve manifest-driven device names and radio parameters.
    pub fn new(
        store: ConfigStore,
        sysroot: Sysroot,
        manifest_peripherals: &[webconfig_engine::manifest::Peripheral],
        wifi_radio: &webconfig_engine::manifest::WifiRadio,
    ) -> Self {
        Self {
            store: Arc::new(store),
            sysroot: Arc::new(sysroot),
            appliers: Arc::new(appliers::all(manifest_peripherals, wifi_radio)),
            confirm: Arc::new(ConfirmManager::new()),
        }
    }

    /// Load the current config tree (cheap; runs on the async task).
    pub fn load(&self) -> ApiResult<Config> {
        Ok(self.store.load()?.config)
    }

    /// Mutate + validate + persist (cheap; sync file IO).
    pub fn update<F>(&self, f: F) -> ApiResult<Config>
    where
        F: FnOnce(&mut Config) -> Result<(), webconfig_engine::StoreError> + Send + 'static,
        Config: Send,
    {
        let file = self.store.update(f)?;
        Ok(file.config)
    }

    fn find(&self, id: &str) -> Option<&dyn Applier> {
        self.appliers.iter().find(|a| a.id() == id).map(|b| &**b)
    }

    /// Run `validate` for one applier on a blocking task.
    pub async fn validate(&self, applier_id: &str) -> ApiResult<()> {
        let sys = self.sysroot.clone();
        let cfg = self.load();
        let applier_id = applier_id.to_string();
        let appliers = self.appliers.clone();
        tokio::task::spawn_blocking(move || {
            let cfg = cfg?;
            let a = appliers
                .iter()
                .find(|x| x.id() == applier_id)
                .ok_or(ApiError::NotFound)?;
            a.validate(&sys, &cfg).map_err(Into::into)
        })
        .await
        .map_err(|_| ApiError::Internal)?
    }

    /// Run `apply` for one applier on a blocking task. If the outcome is
    /// provisional, registers the pending confirm window.
    pub async fn apply(&self, applier_id: &str) -> ApiResult<ApplyOutcome> {
        let sys = self.sysroot.clone();
        let applier_id = applier_id.to_string();
        let appliers = self.appliers.clone();
        let store = self.store.clone();
        let applier_id_for_task = applier_id.clone();
        let outcome = tokio::task::spawn_blocking(move || -> Result<ApplyOutcome, ApiError> {
            let cfg = store.load()?.config;
            let a = appliers
                .iter()
                .find(|x| x.id() == applier_id_for_task)
                .ok_or(ApiError::NotFound)?;
            a.apply(&sys, &cfg).map_err(Into::into)
        })
        .await
        .map_err(|_| ApiError::Internal)??;

        if let ApplyOutcome::Provisional {
            confirm_window_secs,
        } = &outcome
        {
            self.confirm
                .set(PendingConfirm {
                    applier_id: applier_id.clone(),
                    window_secs: *confirm_window_secs,
                    started_at: std::time::Instant::now(),
                })
                .await;
        }
        Ok(outcome)
    }

    /// Run `rollback` for one applier on a blocking task (spec §3/§4.2).
    pub async fn rollback(&self, applier_id: &str) -> ApiResult<()> {
        let sys = self.sysroot.clone();
        let applier_id = applier_id.to_string();
        let appliers = self.appliers.clone();
        tokio::task::spawn_blocking(move || {
            let a = appliers
                .iter()
                .find(|x| x.id() == applier_id)
                .ok_or(ApiError::NotFound)?;
            a.rollback(&sys).map_err(Into::into)
        })
        .await
        .map_err(|_| ApiError::Internal)?
    }

    /// Confirm the pending networking change (spec §4.2).
    pub async fn confirm_pending(&self) -> ApiResult<bool> {
        Ok(self.confirm.confirm().await.is_some())
    }

    /// Drive the commit/confirm timer: if the pending apply is past its
    /// window, roll it back. Returns true if a rollback was performed. Called
    /// by a periodic task in main.rs (spec §4.2: tokio timer task).
    pub async fn tick_confirm(&self) -> bool {
        let Some(p) = self.confirm.pending().await else {
            return false;
        };
        let elapsed = p.started_at.elapsed();
        if elapsed >= Duration::from_secs(p.window_secs) {
            tracing::warn!(
                applier = %p.applier_id,
                "commit/confirm window expired — rolling back"
            );
            let _ = self.confirm.confirm().await; // consume
            let id = p.applier_id.clone();
            if let Err(e) = self.rollback(&id).await {
                tracing::error!(applier = %id, error = %e, "rollback failed");
            }
            return true;
        }
        false
    }

    /// Wait for the confirm window to elapse, then roll back if still pending.
    /// Used by the dedicated timer task (spec §4.2).
    pub async fn run_confirm_timer(self: Arc<Self>) {
        loop {
            tokio::time::sleep(Duration::from_secs(1)).await;
            self.tick_confirm().await;
        }
    }

    /// Convenience for handlers that need to know an applier exists.
    pub fn has_applier(&self, id: &str) -> bool {
        self.find(id).is_some()
    }
}
