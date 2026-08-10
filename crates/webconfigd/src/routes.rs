//! Router assembly (spec §3: HTTP layer — TLS, auth/sessions/CSRF, static
//! assets, JSON REST, one SSE endpoint).

use axum::middleware::{from_fn, from_fn_with_state};
use axum::routing::{delete, get, post, put};
use axum::Router;
use tower_http::limit::RequestBodyLimitLayer;
use tower_http::services::{ServeDir, ServeFile};
use tower_sessions::cookie::SameSite;
use tower_sessions::{MemoryStore, SessionManagerLayer};

/// Per-route request-body caps (spec §2 RAM budget). Sized generously above
/// any legitimate payload for that route while still rejecting an
/// arbitrarily large body before it's fully buffered (Checkpoint 1.8) — none
/// of these routes accept large binary uploads today (`/updates/install`
/// still takes a JSON `bundle_path` reference, not raw bytes; see Checkpoint
/// 1.4), so a modest JSON/PEM-sized cap is correct for all of them.
const TLS_UPLOAD_LIMIT: usize = 64 * 1024; // cert + key PEM
const CERT_UPLOAD_LIMIT: usize = 64 * 1024; // one PEM cert
const BACKUP_SECRET_LIMIT: usize = 4 * 1024; // {"passphrase": "..."}
const RESTORE_LIMIT: usize = 8 * 1024 * 1024; // a full config backup (JSON or encrypted envelope)
const INSTALL_LIMIT: usize = 4 * 1024; // {"bundle_path": "..."} — a path string only, for now
                                       // A real OS bundle upload (Checkpoint 1.4) — streamed to disk chunk-by-chunk
                                       // in the handler, so this cap is a hard ceiling against a runaway upload,
                                       // not a memory-buffering limit (matches updates::MAX_UPLOAD_BUNDLE_BYTES).
const UPLOAD_BUNDLE_LIMIT: usize = 512 * 1024 * 1024;

use crate::api::{
    alerts, io, logs, meta, monitoring, network, security, services, system, updates,
};
use crate::auth;
use crate::csrf;
use crate::sse;
use crate::state::AppState;

/// Build the application router. `static_dir` is the on-disk frontend assets
/// directory (served by the daemon, executing entirely in the browser, spec §2).
pub fn build_router(state: AppState, static_dir: std::path::PathBuf) -> Router {
    let session_layer = SessionManagerLayer::new(MemoryStore::default())
        // The daemon serves TLS only (server.rs), so the session cookie is
        // always carried over HTTPS — mark it Secure so browsers never emit it
        // over plain HTTP (spec §4.8: sessions httpOnly + Secure).
        .with_secure(true)
        .with_same_site(SameSite::Lax)
        .with_http_only(true)
        .with_path("/".to_string());

    // Public auth routes — usable before login / during first boot (spec §4.8).
    let auth_routes = Router::new()
        .route("/auth/first-boot", post(auth::first_boot))
        .route("/auth/login", post(auth::login))
        .route("/auth/logout", post(auth::logout))
        .route("/auth/whoami", get(auth::whoami));

    // Protected API: require_auth (outer) + CSRF (inner, mutating only).
    let protected = Router::new()
        // Meta (spec §3): board manifest + per-section schema descriptor.
        .route("/manifest", get(meta::manifest))
        .route("/schemas/:section", get(meta::schema_section))
        // System (§4.1)
        .route("/system", get(system::get).put(system::put))
        .route("/system/apply", post(system::apply))
        .route("/system/info", get(system::info))
        .route("/system/reboot", post(system::reboot))
        .route("/system/shutdown", post(system::shutdown))
        .route("/system/factory-reset", post(system::factory_reset))
        .route("/system/root-password", post(system::root_password))
        .route(
            "/system/users",
            get(system::list_users).post(system::create_user),
        )
        .route(
            "/system/groups",
            get(system::list_groups).post(system::create_group),
        )
        .route("/system/groups/:name", delete(system::delete_group))
        .route(
            "/system/groups/:group/members/:user",
            put(system::add_group_member).delete(system::remove_group_member),
        )
        .route("/system/users/:name", delete(system::delete_user))
        .route("/system/users/:name/role", put(system::set_role))
        .route("/system/users/:name/password", put(system::change_password))
        // Networking (§4.2)
        .route("/network", get(network::get).put(network::put))
        .route("/network/apply", post(network::apply))
        .route("/network/confirm", post(network::confirm))
        .route("/network/rollback", post(network::rollback))
        .route("/network/scan", get(network::scan))
        .route(
            "/network/connectivity-test",
            post(network::connectivity_test),
        )
        // Services & Apps (§4.3)
        .route("/services", get(services::list))
        .route("/services/health", get(services::health_strip))
        .route("/services/:unit/start", post(services::start))
        .route("/services/:unit/stop", post(services::stop))
        .route("/services/:unit/restart", post(services::restart))
        .route("/services/:unit/enabled", put(services::set_enabled))
        .route("/services/:unit/journal", get(services::journal))
        .route(
            "/services/mqtt",
            get(services::get_mqtt).put(services::put_mqtt),
        )
        .route("/services/mqtt/test", post(services::test_mqtt))
        .route("/schemas", get(services::schemas))
        .route(
            "/apps/:id/settings",
            get(services::get_app_settings).put(services::put_app_settings),
        )
        // Updates (§4.4)
        .route("/updates/slots", get(updates::slots))
        .route(
            "/updates/install",
            post(updates::install).layer(RequestBodyLimitLayer::new(INSTALL_LIMIT)),
        )
        .route(
            "/updates/upload",
            post(updates::upload).layer(RequestBodyLimitLayer::new(UPLOAD_BUNDLE_LIMIT)),
        )
        .route("/updates/confirm", post(updates::confirm))
        .route("/updates/reboot-to-apply", post(updates::reboot_to_apply))
        .route("/updates/ota", get(updates::get_ota).put(updates::put_ota))
        .route("/updates/ota/check", get(updates::ota_check))
        .route(
            "/updates/backup/config",
            get(updates::get_backup_config).put(updates::put_backup_config),
        )
        .route("/updates/backup/check", get(updates::backup_check))
        .route(
            "/updates/backup",
            get(updates::backup)
                .post(updates::backup_encrypted)
                .layer(RequestBodyLimitLayer::new(BACKUP_SECRET_LIMIT)),
        )
        .route(
            "/updates/restore",
            post(updates::restore).layer(RequestBodyLimitLayer::new(RESTORE_LIMIT)),
        )
        .route("/updates/diagnostics", get(updates::diagnostics))
        .route("/updates/factory-reset", post(updates::factory_reset))
        // Monitoring (§4.5)
        .route("/monitoring/vitals", get(monitoring::vitals))
        .route("/monitoring/emmc/:dev", get(monitoring::emmc_wear))
        .route("/monitoring/tiles", get(monitoring::tiles))
        .route(
            "/monitoring/telemetry",
            get(monitoring::get_telemetry).put(monitoring::put_telemetry),
        )
        // Logs (§4.6)
        .route("/logs", get(logs::read))
        .route("/logs/sources", get(logs::sources))
        .route("/logs/config", get(logs::get_config).put(logs::put_config))
        .route("/logs/audit", get(logs::audit_log))
        .route("/logs/download", get(logs::download))
        // Alerts (§4.7)
        .route("/alerts", get(alerts::get).put(alerts::put))
        .route("/alerts/history", get(alerts::history))
        // Security (§4.8)
        .route("/security", get(security::get).put(security::put))
        .route("/security/apply", post(security::apply))
        .route("/security/wireguard/pubkey", get(security::wg_pubkey))
        .route("/security/csr", post(security::generate_csr))
        .route(
            "/security/tls",
            post(security::upload_tls_identity).layer(RequestBodyLimitLayer::new(TLS_UPLOAD_LIMIT)),
        )
        .route("/security/exposure", get(security::exposure))
        .route("/security/trust", get(security::trust))
        .route(
            "/security/certs",
            get(security::certs)
                .post(security::upload_cert)
                .layer(RequestBodyLimitLayer::new(CERT_UPLOAD_LIMIT)),
        )
        .route("/security/certs/:id", delete(security::delete_cert))
        // I/O (§4.9)
        .route("/io", get(io::get).put(io::put))
        .route("/io/usb", get(io::usb_devices))
        .route("/io/pci", get(io::pci_devices))
        .route("/io/i2c", get(io::i2c_devices))
        .route("/io/spi", get(io::spi_devices))
        .route("/io/can/apply", post(io::apply_can))
        .route("/io/serial/apply", post(io::apply_serial))
        .route("/io/gpio/:name/toggle", post(io::toggle_gpio))
        .route("/io/i2c/scan", post(io::i2c_scan))
        .route("/io/mcu", get(io::mcu))
        .route("/io/mcu/firmware", post(io::mcu_firmware))
        .route("/io/platform-panel", get(io::platform_panel))
        // SSE (§3/§4.6)
        .route("/events", get(sse::events))
        .layer(from_fn_with_state(state.clone(), csrf::csrf_guard))
        .layer(from_fn_with_state(state.clone(), auth::require_auth));

    let api = auth_routes.merge(protected);

    // Static frontend: served by the daemon, no server-side rendering (spec §2).
    let index = static_dir.join("index.html");
    let static_svc = ServeDir::new(&static_dir).fallback(ServeFile::new(index));

    Router::new()
        .nest("/api/v1", api)
        .fallback_service(static_svc)
        .layer(session_layer)
        // HTTP→HTTPS redirect is handled by the plaintext listener in
        // server::serve (spec §4.8); the cookie policy above is shared.
        .layer(from_fn(http_to_https_hint))
        .with_state(state)
}

/// A middleware that sets a `Strict-Transport-Security` header on every
/// response (spec §4.8). The actual HTTP→HTTPS redirect lives in the plaintext
/// listener wiring in `server::serve`; this layer only adds HSTS.
async fn http_to_https_hint(
    req: axum::extract::Request,
    next: axum::middleware::Next,
) -> axum::response::Response {
    use axum::http::header;
    let mut resp = next.run(req).await;
    // Strict-Transport-Security on all responses (spec §4.8). The actual
    // HTTP→HTTPS redirect lives in the plaintext listener in server::serve.
    resp.headers_mut().insert(
        header::STRICT_TRANSPORT_SECURITY,
        axum::http::HeaderValue::from_static("max-age=31536000"),
    );
    resp
}
