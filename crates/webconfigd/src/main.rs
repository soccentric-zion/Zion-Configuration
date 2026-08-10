//! webconfigd — on-device WebConfig daemon (spec §2: exactly one daemon, tokio
//! `current_thread` runtime, non-root, TLS, rustls, no OpenSSL).
//!
//! Layers (spec §3): HTTP (here) → config engine (webconfig-engine) → platform
//! (board manifest). The async HTTP layer talks to the synchronous engine over
//! the [`engine_bridge`] and runs appliers on blocking tasks.

#![forbid(unsafe_code)]

mod alert_dispatch;
mod api;
mod auth;
mod bootstrap;
mod csrf;
mod engine_bridge;
mod error;
mod jobs;
mod routes;
mod server;
mod sse;
mod state;
mod systemd;
mod tls;
mod update_backend;

#[cfg(test)]
mod tests;

use std::sync::Arc;
use tracing_subscriber::EnvFilter;
use webconfig_engine::Sysroot;

/// One daemon, current-thread runtime (spec §2).
#[tokio::main(flavor = "current_thread")]
async fn main() -> anyhow::Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter(EnvFilter::from_default_env())
        .compact()
        .init();

    let sysroot = resolve_sysroot();
    let application = bootstrap::build(sysroot).await;
    let bootstrap::Application {
        state,
        engine,
        manifest,
        alert_engine,
    } = application;

    let static_dir = resolve_static_dir();
    let app = routes::build_router(state, static_dir);

    // Commit/confirm rollback timer (spec §4.2 flagship feature).
    tokio::spawn(Arc::new(engine.clone()).run_confirm_timer());

    // Alert evaluation timer (spec §4.7): rule evaluation on the same data the
    // dashboard reads, on a timer inside the daemon — no extra process.
    tokio::spawn(jobs::run_alert_timer(
        engine.clone(),
        manifest.clone(),
        alert_engine.clone(),
    ));

    let webconfig_dir = engine.sysroot.webconfig_dir();
    server::serve(app, engine, &webconfig_dir, &manifest).await
}

/// Resolve the sysroot from `WEBCONFIG_SYSROOT` (dev/CI fake sysroot) or `/`.
fn resolve_sysroot() -> Sysroot {
    match std::env::var("WEBCONFIG_SYSROOT") {
        Ok(dir) => Sysroot::at(dir),
        Err(_) => Sysroot::new(),
    }
}

/// Resolve the frontend static dir from `WEBCONFIG_STATIC_DIR`, else the
/// install default `/usr/share/webconfig/static`, else `frontend/` (dev).
fn resolve_static_dir() -> std::path::PathBuf {
    if let Ok(dir) = std::env::var("WEBCONFIG_STATIC_DIR") {
        return std::path::PathBuf::from(dir);
    }
    let prod = std::path::PathBuf::from("/usr/share/webconfig/static");
    if prod.exists() {
        return prod;
    }
    std::path::PathBuf::from("frontend")
}
