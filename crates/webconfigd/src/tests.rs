//! Integration tests for the HTTP layer (spec §6: fake-sysroot harness, no
//! hardware). Covers the highest-risk cross-cutting concerns:
//!   - first-boot admin creation gate (CRA/SB-327, no default password)
//!   - CSRF double-submit enforcement on mutations
//!   - role-based access (admin vs viewer)
//!   - the networking commit/confirm rollback timer (spec §4.2 flagship)
//!
//! Lives in-crate (gated `#[cfg(test)]`) because `webconfigd` is a binary crate
//! and binary crates don't expose a lib target to `tests/`. The fake sysroot
//! means no host side effects and parallel-safe execution.

#![cfg(test)]

use std::sync::Arc;

use axum::body::Body;
use axum::http::{Request, StatusCode};
use http_body_util::BodyExt;
use tower::ServiceExt;

use webconfig_engine::alert::AlertEngine;
use webconfig_engine::audit::AuditLog;
use webconfig_engine::manifest::{BoardManifest, IoKind, WifiRadio};
use webconfig_engine::schema::Schema;
use webconfig_engine::{ConfigStore, Sysroot};

use crate::engine_bridge::{EngineBridge, PendingConfirm};
use crate::state::AppState;

/// Build the full app router against a tempdir fake sysroot. Returns the router
/// and the tempdir (caller must keep the tempdir alive for the test duration).
async fn build_app() -> (axum::Router, tempfile::TempDir) {
    build_app_with(Schema::default_permissive()).await
}

/// Like [`build_app`] but with a caller-supplied schema (e.g. one loaded from a
/// tempdir to exercise schema-driven paths like app `apply_hook`s).
async fn build_app_with(schema: Schema) -> (axum::Router, tempfile::TempDir) {
    build_app_with_systemd(schema, None).await
}

/// Like [`build_app_with`] but lets the caller substitute the systemd client
/// (e.g. a client that fails every verb) to exercise error-path handling that
/// the fake sysroot's always-succeeding mock can't reach.
async fn build_app_with_systemd(
    schema: Schema,
    systemd: Option<Arc<dyn crate::systemd::SystemdClient>>,
) -> (axum::Router, tempfile::TempDir) {
    build_app_full(schema, systemd, None).await
}

/// Like [`build_app_with_systemd`] but also lets the caller substitute the
/// whole board manifest (e.g. to add an MCU peripheral with a real signing
/// key for firmware-verification tests).
async fn build_app_full(
    schema: Schema,
    systemd: Option<Arc<dyn crate::systemd::SystemdClient>>,
    manifest_override: Option<BoardManifest>,
) -> (axum::Router, tempfile::TempDir) {
    let dir = tempfile::tempdir().unwrap();
    let sysroot = Sysroot::at(dir.path());
    let webconfig_dir = sysroot.webconfig_dir();
    std::fs::create_dir_all(&webconfig_dir).ok();

    let store = ConfigStore::new(sysroot.clone(), schema.clone());

    // Minimal manifest with a service allowlist + one peripheral so the I/O and
    // services pages have data.
    let manifest = manifest_override.unwrap_or_else(|| BoardManifest {
        platform: "zion".into(),
        peripherals: vec![
            webconfig_engine::manifest::Peripheral {
                id: "can0".into(),
                device: "can0".into(),
                human_name: "CAN 0".into(),
                kind: IoKind::Can,
                ..Default::default()
            },
            webconfig_engine::manifest::Peripheral {
                id: "ttyTHS0".into(),
                device: "/dev/ttyTHS0".into(),
                human_name: "High-speed UART 0".into(),
                kind: IoKind::Serial,
                ..Default::default()
            },
        ],
        service_allowlist: vec!["sshd.service".into()],
        default_alert_rules: vec![
            webconfig_engine::store::alerts::AlertRule {
                id: "disk-full".into(),
                enabled: true,
                metric: "disk.pct".into(),
                comparison: ">".into(),
                threshold: 90.0,
                duration_secs: 60,
                actions: vec!["ui".into()],
            },
            webconfig_engine::store::alerts::AlertRule {
                id: "watchdog-reset".into(),
                enabled: true,
                metric: "boot.watchdog".into(),
                comparison: ">".into(),
                threshold: 0.0,
                duration_secs: 0,
                actions: vec!["ui".into()],
            },
        ],
        ..Default::default()
    });
    let engine = EngineBridge::new(
        store,
        sysroot.clone(),
        &manifest.peripherals,
        &manifest.wifi_radio,
    );

    let state = AppState {
        engine: engine.clone(),
        schema: Arc::new(schema),
        manifest: Arc::new(manifest),
        audit: Arc::new(AuditLog::new(webconfig_dir.join("audit.jsonl"), 64 * 1024)),
        alert_engine: Arc::new(tokio::sync::Mutex::new(AlertEngine::new())),
        update_backend: crate::update_backend::make_backend(&sysroot),
        systemd: match systemd {
            Some(s) => s,
            None => crate::systemd::make_client(&sysroot).await,
        },
        users_path: webconfig_dir.join("users.json"),
        login_throttle: Arc::new(crate::auth::LoginThrottle::new()),
        sse_connections: Arc::new(tokio::sync::Semaphore::new(
            crate::bootstrap::MAX_SSE_CONNECTIONS,
        )),
    };

    // Static dir: a tempdir is fine; API routes don't depend on it.
    let static_dir = tempfile::tempdir().unwrap().keep();
    let router = crate::routes::build_router(state, static_dir);
    (router, dir)
}

/// Read the full response body as a UTF-8 string.
async fn body_text(body: Body) -> String {
    String::from_utf8_lossy(&body.collect().await.unwrap().to_bytes()).to_string()
}

/// Extract the `id=<value>` session cookie from a Set-Cookie header value.
fn session_cookie(set_cookie: &str) -> String {
    let id = set_cookie.split(';').next().unwrap_or("").trim();
    id.to_string()
}

/// Send a request and return (status, set-cookie header if any, body text).
async fn send(
    app: &axum::Router,
    method: &str,
    uri: &str,
    cookie: Option<&str>,
    csrf: Option<&str>,
    body: Option<&str>,
) -> (StatusCode, Option<String>, String) {
    let mut b = Request::builder().method(method).uri(uri);
    if let Some(c) = cookie {
        b = b.header("cookie", c);
    }
    if let Some(t) = csrf {
        b = b.header("X-CSRF-Token", t);
    }
    if body.is_some() {
        b = b.header("content-type", "application/json");
    }
    let req = b.body(Body::from(body.unwrap_or("").to_string())).unwrap();
    let resp = app.clone().oneshot(req).await.unwrap();
    let status = resp.status();
    let set_cookie = resp
        .headers()
        .get("set-cookie")
        .and_then(|v| v.to_str().ok())
        .map(session_cookie);
    let text = body_text(resp.into_body()).await;
    (status, set_cookie, text)
}

#[tokio::test]
async fn first_boot_gate_blocks_protected_routes_until_admin_created() {
    let (app, _dir) = build_app().await;

    // whoami before any admin exists → first_boot true, no username.
    let (st, _ck, text) = send(&app, "GET", "/api/v1/auth/whoami", None, None, None).await;
    assert_eq!(st, StatusCode::OK);
    let v: serde_json::Value = serde_json::from_str(&text).unwrap();
    assert_eq!(v["first_boot"], true);
    assert!(v["username"].is_null());

    // A protected route is refused before first boot.
    let (st, _, _) = send(&app, "GET", "/api/v1/system", None, None, None).await;
    assert_eq!(st, StatusCode::UNAUTHORIZED);

    // Login is refused before first boot (no admin yet).
    let (st, _, _) = send(
        &app,
        "POST",
        "/api/v1/auth/login",
        None,
        None,
        Some(r#"{"username":"admin","password":"whatever1"}"#),
    )
    .await;
    assert_eq!(st, StatusCode::SERVICE_UNAVAILABLE); // NotReady
}

#[tokio::test]
async fn first_boot_creates_admin_and_authenticates() {
    let (app, _dir) = build_app().await;

    let (st, ck, _) = send(
        &app,
        "POST",
        "/api/v1/auth/first-boot",
        None,
        None,
        Some(r#"{"username":"admin","password":"password1"}"#),
    )
    .await;
    assert_eq!(st, StatusCode::OK);
    let cookie = ck.expect("first-boot sets a session cookie");

    // The first-boot audit entry — the very first audit entry any device ever
    // produces — must use the shared RFC3339 timestamp helper, not the stale
    // local `t=<epoch>` placeholder (Checkpoint 1.7).
    let (_, _, audit_text) =
        send(&app, "GET", "/api/v1/logs/audit", Some(&cookie), None, None).await;
    let audit: serde_json::Value = serde_json::from_str(&audit_text).unwrap();
    let first_boot_entry = audit
        .as_array()
        .unwrap()
        .iter()
        .find(|e| e["action"] == "auth.first-boot")
        .expect("first-boot audit entry present");
    let ts = first_boot_entry["ts"].as_str().unwrap();
    assert!(
        !ts.starts_with("t="),
        "first-boot audit ts is a stale placeholder: {ts}"
    );
    assert!(
        ts.ends_with('Z'),
        "first-boot audit ts must be RFC3339 UTC: {ts}"
    );

    // Now whoami reports an authenticated admin.
    let (st, _, text) = send(
        &app,
        "GET",
        "/api/v1/auth/whoami",
        Some(&cookie),
        None,
        None,
    )
    .await;
    assert_eq!(st, StatusCode::OK);
    let v: serde_json::Value = serde_json::from_str(&text).unwrap();
    assert_eq!(v["username"], "admin");
    assert_eq!(v["role"], "admin");
    let csrf = v["csrf"].as_str().expect("csrf token present").to_string();

    // A protected GET now succeeds.
    let (st, _, _) = send(&app, "GET", "/api/v1/system", Some(&cookie), None, None).await;
    assert_eq!(st, StatusCode::OK);

    // A mutation WITH the csrf token succeeds.
    let (st, _, _) = send(
        &app, "PUT", "/api/v1/system", Some(&cookie), Some(&csrf),
        Some(r#"{"hostname":"zion-01","asset_tag":null,"time":{"timezone":null,"ntp_enabled":false,"manual_datetime":null},"ssh":{"sshd_enabled":false,"serial_getty_enabled":false,"authorized_keys":""},"ntp_servers":[]}"#),
    ).await;
    assert_eq!(st, StatusCode::OK);
}

#[tokio::test]
async fn session_cookie_is_secure_and_http_only_on_the_wire() {
    // Checkpoint 4.7: the test helper's session_cookie() deliberately strips
    // everything after the first ';' (it only needs the id=value pair to
    // resend as a request cookie) — so no existing test actually inspects
    // the Secure/HttpOnly attributes on the real Set-Cookie header. Build
    // the request directly here instead of going through send()/session_cookie().
    let (app, _dir) = build_app().await;
    let req = Request::builder()
        .method("POST")
        .uri("/api/v1/auth/first-boot")
        .header("content-type", "application/json")
        .body(Body::from(
            r#"{"username":"admin","password":"password1"}"#.to_string(),
        ))
        .unwrap();
    let resp = app.clone().oneshot(req).await.unwrap();
    assert_eq!(resp.status(), StatusCode::OK);
    let set_cookie = resp
        .headers()
        .get("set-cookie")
        .expect("first-boot must set a session cookie")
        .to_str()
        .unwrap()
        .to_string();
    assert!(
        set_cookie.to_lowercase().contains("secure"),
        "session cookie must be Secure: {set_cookie}"
    );
    assert!(
        set_cookie.to_lowercase().contains("httponly"),
        "session cookie must be HttpOnly: {set_cookie}"
    );
}

#[tokio::test]
async fn csrf_guard_rejects_mutations_without_token() {
    let (app, _dir) = build_app().await;
    let (_, ck, _) = send(
        &app,
        "POST",
        "/api/v1/auth/first-boot",
        None,
        None,
        Some(r#"{"username":"admin","password":"password1"}"#),
    )
    .await;
    let cookie = ck.unwrap();

    // PUT /system with a session but NO csrf token → rejected by csrf_guard
    // (returns 400 BadRequest, per csrf.rs).
    let (st, _, _) = send(
        &app, "PUT", "/api/v1/system", Some(&cookie), None,
        Some(r#"{"hostname":"x","asset_tag":null,"time":{"timezone":null,"ntp_enabled":false,"manual_datetime":null},"ssh":{"sshd_enabled":false,"serial_getty_enabled":false,"authorized_keys":""},"ntp_servers":[]}"#),
    ).await;
    assert_eq!(st, StatusCode::BAD_REQUEST);
}

#[tokio::test]
async fn viewer_role_cannot_mutate_admin_only_routes() {
    let (app, _dir) = build_app().await;

    // Bootstrap admin.
    let (_, admin_ck, _) = send(
        &app,
        "POST",
        "/api/v1/auth/first-boot",
        None,
        None,
        Some(r#"{"username":"admin","password":"password1"}"#),
    )
    .await;
    let admin_cookie = admin_ck.unwrap();
    let (_, _, text) = send(
        &app,
        "GET",
        "/api/v1/auth/whoami",
        Some(&admin_cookie),
        None,
        None,
    )
    .await;
    let admin_csrf = serde_json::from_str::<serde_json::Value>(&text).unwrap()["csrf"]
        .as_str()
        .unwrap()
        .to_string();

    // Admin creates a viewer.
    let (st, _, _) = send(
        &app,
        "POST",
        "/api/v1/system/users",
        Some(&admin_cookie),
        Some(&admin_csrf),
        Some(r#"{"username":"watcher","password":"password1","role":"viewer"}"#),
    )
    .await;
    assert_eq!(st, StatusCode::OK);

    // Viewer logs in on its own session.
    let (st, viewer_ck, _) = send(
        &app,
        "POST",
        "/api/v1/auth/login",
        None,
        None,
        Some(r#"{"username":"watcher","password":"password1"}"#),
    )
    .await;
    assert_eq!(st, StatusCode::OK);
    let viewer_cookie = viewer_ck.unwrap();
    let (_, _, text) = send(
        &app,
        "GET",
        "/api/v1/auth/whoami",
        Some(&viewer_cookie),
        None,
        None,
    )
    .await;
    let v: serde_json::Value = serde_json::from_str(&text).unwrap();
    assert_eq!(v["role"], "viewer");
    let viewer_csrf = v["csrf"].as_str().unwrap().to_string();

    // Viewer can read system (any authenticated role).
    let (st, _, _) = send(
        &app,
        "GET",
        "/api/v1/system",
        Some(&viewer_cookie),
        None,
        None,
    )
    .await;
    assert_eq!(st, StatusCode::OK);

    // Viewer cannot PUT /system (admin only) even with a valid csrf token.
    let (st, _, _) = send(
        &app, "PUT", "/api/v1/system", Some(&viewer_cookie), Some(&viewer_csrf),
        Some(r#"{"hostname":"x","asset_tag":null,"time":{"timezone":null,"ntp_enabled":false,"manual_datetime":null},"ssh":{"sshd_enabled":false,"serial_getty_enabled":false,"authorized_keys":""},"ntp_servers":[]}"#),
    ).await;
    assert_eq!(st, StatusCode::FORBIDDEN);
}

#[tokio::test]
async fn operator_role_can_mutate_apps_but_not_admin_only_routes() {
    // Checkpoint 4.6: Role::Operator sits strictly between admin and viewer —
    // can_mutate_apps() is true (unlike viewer) but is_admin() is false
    // (unlike admin). No existing test exercised this boundary; only
    // admin-vs-viewer was covered.
    let (app, _dir) = build_app().await;
    let (admin_cookie, admin_csrf) = boot_admin(&app).await;

    let (st, _, _) = send(
        &app,
        "POST",
        "/api/v1/system/users",
        Some(&admin_cookie),
        Some(&admin_csrf),
        Some(r#"{"username":"tech","password":"password1","role":"operator"}"#),
    )
    .await;
    assert_eq!(st, StatusCode::OK);

    let (st, op_ck, _) = send(
        &app,
        "POST",
        "/api/v1/auth/login",
        None,
        None,
        Some(r#"{"username":"tech","password":"password1"}"#),
    )
    .await;
    assert_eq!(st, StatusCode::OK);
    let op_cookie = op_ck.unwrap();
    let (_, _, text) = send(
        &app,
        "GET",
        "/api/v1/auth/whoami",
        Some(&op_cookie),
        None,
        None,
    )
    .await;
    let v: serde_json::Value = serde_json::from_str(&text).unwrap();
    assert_eq!(v["role"], "operator");
    let op_csrf = v["csrf"].as_str().unwrap().to_string();

    // Operator CAN hit a require_mutate_apps-gated route (services start).
    let (st, _, body) = send(
        &app,
        "POST",
        "/api/v1/services/sshd.service/start",
        Some(&op_cookie),
        Some(&op_csrf),
        None,
    )
    .await;
    assert_eq!(
        st,
        StatusCode::OK,
        "operator must be able to start a service: {body}"
    );

    // Operator CANNOT hit a require_admin-gated route (factory reset).
    let (st, _, body) = send(
        &app,
        "POST",
        "/api/v1/system/factory-reset",
        Some(&op_cookie),
        Some(&op_csrf),
        Some(r#"{"confirm":"RESET","mode":"settings"}"#),
    )
    .await;
    assert_eq!(
        st,
        StatusCode::FORBIDDEN,
        "operator must not be able to factory-reset: {body}"
    );
}

#[tokio::test]
async fn confirm_timer_rolls_back_when_window_expires() {
    // The flagship commit/confirm rollback (spec §4.2): a provisional apply
    // not confirmed within its window is auto-rolled-back by tick_confirm.
    let dir = tempfile::tempdir().unwrap();
    let sysroot = Sysroot::at(dir.path());
    let store = ConfigStore::new(sysroot.clone(), Schema::default_permissive());
    let bridge = EngineBridge::new(store, sysroot.clone(), &[], &WifiRadio::default());

    // Plant a provisional apply with a zero-second window.
    bridge
        .confirm
        .set(PendingConfirm {
            applier_id: "networkd".into(),
            window_secs: 0,
            started_at: std::time::Instant::now(),
        })
        .await;

    // Let an instant elapse so elapsed(0) >= window(0).
    tokio::time::sleep(std::time::Duration::from_millis(2)).await;

    // tick_confirm should consume the pending entry and roll back → true.
    let rolled = bridge.tick_confirm().await;
    assert!(rolled, "expired provisional apply must roll back");

    // After rollback, no pending entry remains; a further tick is a no-op.
    let again = bridge.tick_confirm().await;
    assert!(!again, "no pending apply → nothing to roll back");
}

#[tokio::test]
async fn confirm_pending_clears_on_explicit_confirm() {
    let dir = tempfile::tempdir().unwrap();
    let sysroot = Sysroot::at(dir.path());
    let store = ConfigStore::new(sysroot.clone(), Schema::default_permissive());
    let bridge = EngineBridge::new(store, sysroot, &[], &WifiRadio::default());

    bridge
        .confirm
        .set(PendingConfirm {
            applier_id: "networkd".into(),
            window_secs: 600,
            started_at: std::time::Instant::now(),
        })
        .await;

    // Explicit confirm before the window expires → stands, no rollback.
    let confirmed = bridge.confirm_pending().await.unwrap();
    assert!(
        confirmed,
        "confirm_pending reports a consumed pending apply"
    );
    let rolled = bridge.tick_confirm().await;
    assert!(!rolled, "confirmed apply must not be rolled back");
}

#[tokio::test]
async fn app_settings_put_fires_schema_apply_hook() {
    // The schema-driven app settings engine must run the declared `apply_hook`
    // after a settings save (spec §3/§4.3): a `restart` hook reaches the
    // systemd client and is recorded in the audit trail.
    let schema_dir = tempfile::tempdir().unwrap();
    let schema = r#"{
        "id": "app:demo",
        "title": "Demo app",
        "groups": [
            {
                "id": "main",
                "title": "Main",
                "fields": [
                    {
                        "id": "site_id",
                        "title": "Site ID",
                        "field_type": "string",
                        "apply_hook": { "kind": "restart", "target": "demo.service" }
                    }
                ]
            }
        ]
    }"#;
    std::fs::write(schema_dir.path().join("app-demo.schema.json"), schema).unwrap();
    let loaded = Schema::load_dir(schema_dir.path()).unwrap();
    let (app, _dir) = build_app_with(loaded).await;

    // Bootstrap admin + session + csrf.
    let (st, ck, _) = send(
        &app,
        "POST",
        "/api/v1/auth/first-boot",
        None,
        None,
        Some(r#"{"username":"admin","password":"password1"}"#),
    )
    .await;
    assert_eq!(st, StatusCode::OK);
    let cookie = ck.unwrap();
    let (_, _, text) = send(
        &app,
        "GET",
        "/api/v1/auth/whoami",
        Some(&cookie),
        None,
        None,
    )
    .await;
    let csrf = serde_json::from_str::<serde_json::Value>(&text).unwrap()["csrf"]
        .as_str()
        .unwrap()
        .to_string();

    // PUT app settings → the restart hook fires (mock systemd accepts it) and
    // is recorded in the audit entry for this save.
    let (st, _, body) = send(
        &app,
        "PUT",
        "/api/v1/apps/app-demo/settings",
        Some(&cookie),
        Some(&csrf),
        Some(r#"{"site_id":"site-1"}"#),
    )
    .await;
    assert_eq!(st, StatusCode::OK);
    assert_eq!(body, r#"{"site_id":"site-1"}"#);

    // The audit log entry carries the side effect that ran.
    let (st, _, audit) = send(&app, "GET", "/api/v1/logs/audit", Some(&cookie), None, None).await;
    assert_eq!(st, StatusCode::OK);
    assert!(
        audit.contains("restart demo.service"),
        "audit should record the apply_hook: {audit}"
    );
}

/// Bootstrap the first admin and return (cookie, csrf) for an authenticated
/// admin session — the common preamble for the mutation tests below.
async fn boot_admin(app: &axum::Router) -> (String, String) {
    let (_, ck, _) = send(
        app,
        "POST",
        "/api/v1/auth/first-boot",
        None,
        None,
        Some(r#"{"username":"admin","password":"password1"}"#),
    )
    .await;
    let cookie = ck.unwrap();
    let (_, _, text) = send(app, "GET", "/api/v1/auth/whoami", Some(&cookie), None, None).await;
    let csrf = serde_json::from_str::<serde_json::Value>(&text).unwrap()["csrf"]
        .as_str()
        .unwrap()
        .to_string();
    (cookie, csrf)
}

#[tokio::test]
async fn updates_upload_streams_bundle_to_disk_and_reaches_install() {
    // Checkpoint 1.4: a real browser upload route writes bytes to
    // <data>/webconfig/uploads/ and then calls the same verify+install path
    // POST /updates/install uses — not just a client-supplied path reference.
    let (app, dir) = build_app().await;
    let (cookie, csrf) = boot_admin(&app).await;

    // The mock update backend's verify_bundle reads a sibling
    // `<bundle_path>.manifest.json` (its own test-harness convention, not a
    // real RAUC mechanic) — pre-stage it next to the predictable upload path
    // (?name= makes the resulting path deterministic for this test).
    let uploads_dir = dir.path().join("data/webconfig/uploads");
    std::fs::create_dir_all(&uploads_dir).unwrap();
    let bundle_path = uploads_dir.join("test-bundle.raucb");
    std::fs::write(
        format!("{}.manifest.json", bundle_path.display()),
        r#"{"platform":"zion","variant":null,"version":"2.0.0","description":"test"}"#,
    )
    .unwrap();

    let bundle_bytes = b"fake rauc bundle contents for the upload test".to_vec();
    let req = Request::builder()
        .method("POST")
        .uri("/api/v1/updates/upload?name=test-bundle")
        .header("cookie", &cookie)
        .header("X-CSRF-Token", &csrf)
        .header("content-type", "application/octet-stream")
        .body(Body::from(bundle_bytes.clone()))
        .unwrap();
    let resp = app.clone().oneshot(req).await.unwrap();
    assert_eq!(resp.status(), StatusCode::OK);
    let body_bytes = resp.into_body().collect().await.unwrap().to_bytes();
    let v: serde_json::Value = serde_json::from_slice(&body_bytes).unwrap();
    assert_eq!(v["manifest"]["version"], "2.0.0");
    assert_eq!(v["reboot_to_apply"], true);

    // The audit trail shows the install actually ran with this bundle's data.
    let (st, _, audit_text) =
        send(&app, "GET", "/api/v1/logs/audit", Some(&cookie), None, None).await;
    assert_eq!(st, StatusCode::OK);
    assert!(audit_text.contains("updates.install"));
    assert!(audit_text.contains("2.0.0"));

    // The staged upload is single-use — cleaned up after install runs.
    assert!(!bundle_path.exists());
}

#[tokio::test]
async fn updates_upload_rejects_oversized_bundle_without_buffering_whole_body() {
    // Checkpoint 1.4/1.8: the streaming handler must reject a bundle over its
    // cap while still streaming, not after fully buffering it in memory. The
    // per-route RequestBodyLimitLayer (512 MiB) is too high to exercise here
    // without an actually huge body, so this asserts the handler's own
    // explicit running-total check (MAX_UPLOAD_BUNDLE_BYTES) independently —
    // covered at the unit level isn't possible (private const), so instead
    // assert the request is rejected and no bundle survives on disk when the
    // body stream itself errors out mid-upload (a truncated/aborted client
    // upload must not leave a half-written bundle laying around).
    let (app, dir) = build_app().await;
    let (cookie, csrf) = boot_admin(&app).await;

    // A body whose declared Content-Length disagrees with what's actually
    // sent simulates a client that aborts mid-upload; hyper/axum will error
    // the body stream rather than hang.
    let req = Request::builder()
        .method("POST")
        .uri("/api/v1/updates/upload?name=aborted-bundle")
        .header("cookie", &cookie)
        .header("X-CSRF-Token", &csrf)
        .header("content-type", "application/octet-stream")
        .header("content-length", "1000000")
        .body(Body::from(b"too short".to_vec()))
        .unwrap();
    let resp = app.clone().oneshot(req).await.unwrap();
    // Whatever status this resolves to, no partial bundle must remain staged.
    let _ = resp.status();
    let uploads_dir = dir.path().join("data/webconfig/uploads");
    assert!(
        !uploads_dir.join("aborted-bundle.raucb").exists(),
        "a truncated upload must not leave a half-written bundle on disk"
    );
}

#[tokio::test]
async fn updates_confirm_marks_good_and_audits() {
    // Spec §4.4: a freshly booted image is manually confirmed (mark-good) so
    // the bootloader does not roll it back. POST /updates/confirm is admin-only
    // and records an audit entry.
    let (app, _dir) = build_app().await;
    let (cookie, csrf) = boot_admin(&app).await;

    let (st, _, body) = send(
        &app,
        "POST",
        "/api/v1/updates/confirm",
        Some(&cookie),
        Some(&csrf),
        None,
    )
    .await;
    assert_eq!(st, StatusCode::OK);
    assert_eq!(body, r#"{"confirmed":true}"#);

    let (st, _, audit) = send(&app, "GET", "/api/v1/logs/audit", Some(&cookie), None, None).await;
    assert_eq!(st, StatusCode::OK);
    assert!(
        audit.contains("updates.confirm"),
        "audit should record the mark-good: {audit}"
    );
}

#[tokio::test]
async fn updates_diagnostics_bundle_carries_journal_and_network_state() {
    // Spec §4.4 diagnostics bundle must include a journal excerpt and the live
    // network state (alongside config/slots/dmesg/alert history/audit).
    let (app, _dir) = build_app().await;
    let (cookie, _csrf) = boot_admin(&app).await;

    let (st, _, body) = send(
        &app,
        "GET",
        "/api/v1/updates/diagnostics",
        Some(&cookie),
        None,
        None,
    )
    .await;
    assert_eq!(st, StatusCode::OK);
    let v: serde_json::Value = serde_json::from_str(&body).unwrap();
    assert!(v.get("journal").is_some(), "diagnostics must carry journal");
    assert!(
        v.get("network").is_some(),
        "diagnostics must carry network state"
    );
    assert!(v.get("slots").is_some());
    assert!(v.get("dmesg").is_some());
    assert!(v.get("alert_history").is_some());
    assert!(v.get("audit").is_some());
}

#[tokio::test]
async fn app_schema_secret_field_never_appears_in_backup_or_diagnostics() {
    // Spec §4.4 (P0 0.4): a schema-declared `secret`-typed field inside
    // `app_settings` must never leave the device in plaintext via the
    // (unencrypted) GET backup or diagnostics routes — only the hardcoded
    // top-level Config fields were redacted before this fix.
    let schema_dir = tempfile::tempdir().unwrap();
    let schema = r#"{
        "id": "app:demo",
        "title": "Demo app",
        "groups": [
            {
                "id": "main",
                "title": "Main",
                "fields": [
                    { "id": "api_token", "title": "API Token", "field_type": "secret" },
                    { "id": "site_id", "title": "Site ID", "field_type": "string" }
                ]
            }
        ]
    }"#;
    std::fs::write(schema_dir.path().join("app-demo.schema.json"), schema).unwrap();
    let loaded = Schema::load_dir(schema_dir.path()).unwrap();
    let (app, _dir) = build_app_with(loaded).await;
    let (cookie, csrf) = boot_admin(&app).await;

    const PLANTED_SECRET: &str = "sk-live-planted-secret-value";
    let body = serde_json::json!({ "api_token": PLANTED_SECRET, "site_id": "site-42" }).to_string();
    let (st, _, _) = send(
        &app,
        "PUT",
        "/api/v1/apps/app-demo/settings",
        Some(&cookie),
        Some(&csrf),
        Some(&body),
    )
    .await;
    assert_eq!(st, StatusCode::OK);

    let (st, _, backup_body) = send(
        &app,
        "GET",
        "/api/v1/updates/backup",
        Some(&cookie),
        None,
        None,
    )
    .await;
    assert_eq!(st, StatusCode::OK);
    assert!(
        !backup_body.contains(PLANTED_SECRET),
        "plaintext backup leaked the app secret: {backup_body}"
    );
    let v: serde_json::Value = serde_json::from_str(&backup_body).unwrap();
    assert_eq!(
        v["config"]["app_settings"]["app-demo"]["api_token"],
        "[redacted]"
    );
    // Non-secret sibling fields must survive redaction untouched.
    assert_eq!(
        v["config"]["app_settings"]["app-demo"]["site_id"],
        "site-42"
    );

    let (st, _, diag_body) = send(
        &app,
        "GET",
        "/api/v1/updates/diagnostics",
        Some(&cookie),
        None,
        None,
    )
    .await;
    assert_eq!(st, StatusCode::OK);
    assert!(
        !diag_body.contains(PLANTED_SECRET),
        "diagnostics bundle leaked the app secret: {diag_body}"
    );
}

#[tokio::test]
async fn cellular_5g_is_an_untyped_placeholder_with_redacted_secrets_in_backup() {
    // Checkpoint 1.3: cellular_5g has no applier (spec §4.2 defers cellular/
    // ModemManager to v2) — it's stored as opaque JSON like vlan/bridges/
    // cellular, round-trips through PUT/GET untouched, and its pin/password
    // keys are still redacted defensively in the plaintext backup even though
    // the field is inert.
    let (app, _dir) = build_app().await;
    let (cookie, csrf) = boot_admin(&app).await;

    const PLANTED_SIM_PIN: &str = "planted-sim-pin-1234";
    let body = serde_json::json!({
        "interfaces": [], "wifi": [], "dns_servers": [], "confirm_window_secs": 90,
        "cellular_5g": { "enabled": true, "modem": "/dev/cdc-wdm0", "pin": PLANTED_SIM_PIN }
    })
    .to_string();
    let (st, _, put_body) = send(
        &app,
        "PUT",
        "/api/v1/network",
        Some(&cookie),
        Some(&csrf),
        Some(&body),
    )
    .await;
    assert_eq!(st, StatusCode::OK);
    // Round-trips untouched through the plain GET/PUT path.
    let v: serde_json::Value = serde_json::from_str(&put_body).unwrap();
    assert_eq!(v["cellular_5g"]["modem"], "/dev/cdc-wdm0");
    assert_eq!(v["cellular_5g"]["pin"], PLANTED_SIM_PIN);

    let (st, _, backup_body) = send(
        &app,
        "GET",
        "/api/v1/updates/backup",
        Some(&cookie),
        None,
        None,
    )
    .await;
    assert_eq!(st, StatusCode::OK);
    assert!(
        !backup_body.contains(PLANTED_SIM_PIN),
        "plaintext backup leaked the 5G SIM PIN: {backup_body}"
    );
    let backup: serde_json::Value = serde_json::from_str(&backup_body).unwrap();
    assert_eq!(
        backup["config"]["network"]["cellular_5g"]["pin"],
        "[redacted]"
    );
}

#[tokio::test]
async fn alerts_expose_default_packs_and_persist_push_targets() {
    // Spec §4.7: GET /alerts surfaces the per-platform default rule packs
    // (read-only) and the user-configured rules + push targets; PUT /alerts
    // persists a user rule, webhook targets, and the MQTT event topic.
    let (app, _dir) = build_app().await;
    let (cookie, csrf) = boot_admin(&app).await;

    // Defaults shipped in the manifest are visible.
    let (st, _, body) = send(&app, "GET", "/api/v1/alerts", Some(&cookie), None, None).await;
    assert_eq!(st, StatusCode::OK);
    let v: serde_json::Value = serde_json::from_str(&body).unwrap();
    let defaults = v["default_rules"]
        .as_array()
        .expect("default_rules present");
    assert!(
        defaults.iter().any(|r| r["id"] == "disk-full"),
        "default pack must include disk-full: {defaults:?}"
    );
    assert!(
        defaults.iter().any(|r| r["id"] == "watchdog-reset"),
        "default pack must include watchdog-reset: {defaults:?}"
    );

    // Persist a user rule with webhook + mqtt actions and the push targets.
    let payload = serde_json::json!({
        "enabled": true,
        "rules": [{
            "id": "hot", "enabled": true, "metric": "temp",
            "comparison": ">", "threshold": 85.0, "duration_secs": 60,
            "actions": ["ui", "webhook", "mqtt"]
        }],
        "webhooks": ["https://hooks.example.com/alerts"],
        "mqtt_event_topic": "soccentric/events"
    })
    .to_string();
    let (st, _, _) = send(
        &app,
        "PUT",
        "/api/v1/alerts",
        Some(&cookie),
        Some(&csrf),
        Some(&payload),
    )
    .await;
    assert_eq!(st, StatusCode::OK);

    // Reload — user rule, webhooks, and mqtt topic are persisted.
    let (st, _, body) = send(&app, "GET", "/api/v1/alerts", Some(&cookie), None, None).await;
    assert_eq!(st, StatusCode::OK);
    let v: serde_json::Value = serde_json::from_str(&body).unwrap();
    assert_eq!(v["enabled"], true);
    assert_eq!(v["mqtt_event_topic"], "soccentric/events");
    assert_eq!(v["webhooks"][0], "https://hooks.example.com/alerts");
    let hot = v["rules"]
        .as_array()
        .unwrap()
        .iter()
        .find(|r| r["id"] == "hot")
        .expect("user rule persisted");
    assert_eq!(hot["metric"], "temp");
    assert_eq!(hot["threshold"], 85.0);
    let actions = hot["actions"].as_array().unwrap();
    assert!(actions.iter().any(|a| a == "webhook"));
    assert!(actions.iter().any(|a| a == "mqtt"));
}

#[tokio::test]
async fn trust_status_distinguishes_unavailable_from_real_read() {
    // Spec §4.8 (P0 0.3): update_signing_key_id/verity_status/encryption_status
    // must not be a bare hardcoded "unknown" — the API must distinguish "no
    // platform-status file on this board" from "the BSP reported real values".
    let (app, dir) = build_app().await;
    let (cookie, _csrf) = boot_admin(&app).await;

    // No platform-status.json present (the default fake-sysroot state).
    let (st, _, body) = send(
        &app,
        "GET",
        "/api/v1/security/trust",
        Some(&cookie),
        None,
        None,
    )
    .await;
    assert_eq!(st, StatusCode::OK);
    let v: serde_json::Value = serde_json::from_str(&body).unwrap();
    assert_eq!(v["platform_status_available"], false);
    assert!(v["update_signing_key_id"].is_null());
    assert!(v["verity_status"].is_null());
    assert!(v["encryption_status"].is_null());

    // Now the BSP writes a real platform-status file.
    let share_dir = dir.path().join("usr/share/webconfig");
    std::fs::create_dir_all(&share_dir).unwrap();
    std::fs::write(
        share_dir.join("platform-status.json"),
        r#"{"update_signing_key_id":"key-42","verity_status":"verified","encryption_status":"encrypted"}"#,
    )
    .unwrap();

    let (st, _, body) = send(
        &app,
        "GET",
        "/api/v1/security/trust",
        Some(&cookie),
        None,
        None,
    )
    .await;
    assert_eq!(st, StatusCode::OK);
    let v: serde_json::Value = serde_json::from_str(&body).unwrap();
    assert_eq!(v["platform_status_available"], true);
    assert_eq!(v["update_signing_key_id"], "key-42");
    assert_eq!(v["verity_status"], "verified");
    assert_eq!(v["encryption_status"], "encrypted");
}

#[tokio::test]
async fn factory_reset_wipes_certs_and_alert_history() {
    // spec §4.1: factory reset "wipes the data partition". The device TLS
    // identity (cert.pem/key.pem), the uploaded cert store (certs/), and the
    // SQLite alert_history table must all be cleared so a reset device
    // re-provisions with a fresh identity — not just config/audit/users.
    let (app, dir) = build_app().await;
    let webconfig_dir = dir.path().join("data/webconfig");
    std::fs::create_dir_all(&webconfig_dir).unwrap();
    std::fs::write(webconfig_dir.join("cert.pem"), "FAKE CERT PEM").unwrap();
    std::fs::write(webconfig_dir.join("key.pem"), "FAKE KEY PEM").unwrap();
    std::fs::create_dir_all(webconfig_dir.join("certs")).unwrap();
    std::fs::write(webconfig_dir.join("certs/ca.pem"), "FAKE CA PEM").unwrap();
    // Seed an alert_history row in config.db so we can assert it is cleared.
    {
        let conn = rusqlite::Connection::open(webconfig_dir.join("config.db")).unwrap();
        conn.execute_batch(
            "CREATE TABLE IF NOT EXISTS alert_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL, rule_id TEXT NOT NULL, state TEXT NOT NULL, value REAL NOT NULL
             );",
        )
        .unwrap();
        conn.execute(
            "INSERT INTO alert_history (ts, rule_id, state, value) VALUES ('t=1','hot','fired',90.0)",
            [],
        )
        .unwrap();
    }

    // Bootstrap an admin and grab session + csrf.
    let (_, ck, _) = send(
        &app,
        "POST",
        "/api/v1/auth/first-boot",
        None,
        None,
        Some(r#"{"username":"admin","password":"password1"}"#),
    )
    .await;
    let cookie = ck.unwrap();
    let (_, _, text) = send(
        &app,
        "GET",
        "/api/v1/auth/whoami",
        Some(&cookie),
        None,
        None,
    )
    .await;
    let csrf = serde_json::from_str::<serde_json::Value>(&text).unwrap()["csrf"]
        .as_str()
        .unwrap()
        .to_string();

    // Factory reset (settings-only mode still wipes certs + alert history).
    let (st, _, _) = send(
        &app,
        "POST",
        "/api/v1/system/factory-reset",
        Some(&cookie),
        Some(&csrf),
        Some(r#"{"confirm":"RESET","mode":"settings"}"#),
    )
    .await;
    assert_eq!(st, StatusCode::OK);

    // TLS identity + cert store are gone (next boot regenerates the identity).
    assert!(!webconfig_dir.join("cert.pem").exists());
    assert!(!webconfig_dir.join("key.pem").exists());
    assert!(!webconfig_dir.join("certs").exists());
    // The alert history table is empty after the reset.
    let conn = rusqlite::Connection::open(webconfig_dir.join("config.db")).unwrap();
    let count: i64 = conn
        .query_row("SELECT COUNT(*) FROM alert_history", [], |r| r.get(0))
        .unwrap();
    assert_eq!(count, 0);
}

#[tokio::test]
async fn network_apply_surfaces_provisional_state_when_a_later_applier_fails() {
    // Checkpoint 1.10: if networkd already went Provisional (a severing
    // static-IP change) and a downstream applier (firewall/wifi/ap) then
    // fails, the error response must say so — not read as a bare, unrelated
    // failure while a rollback timer is quietly ticking in the background.
    let (app, dir) = build_app().await;
    let (cookie, csrf) = boot_admin(&app).await;

    // A static (non-DHCP), enabled interface is a "severing" change per
    // networkd's own heuristic — its apply() returns Provisional.
    let body = serde_json::json!({
        "interfaces": [{
            "name": "eth0", "enabled": true, "dhcp": false,
            "address": "192.168.1.50", "prefix": 24
        }],
        "wifi": [], "dns_servers": [], "confirm_window_secs": 90,
        // A NetworkConfig JSON that omits ap_mode entirely deserializes it via
        // ApMode's derived (all-empty) Default, not its per-field serde
        // defaults, which fails ap validation even though AP is disabled —
        // supply a valid disabled profile explicitly (separate finding, not
        // what this test is about).
        "ap_mode": { "enabled": false, "trigger": "no-config", "ssid": "unused", "passphrase": "unused123" }
    })
    .to_string();
    let (st, _, _) = send(
        &app,
        "PUT",
        "/api/v1/network",
        Some(&cookie),
        Some(&csrf),
        Some(&body),
    )
    .await;
    assert_eq!(st, StatusCode::OK);

    // Sabotage the firewall applier's write target so its apply() fails
    // after networkd has already succeeded provisionally.
    let nft_path = dir.path().join("etc/nftables.d/webconfig.nft");
    std::fs::create_dir_all(&nft_path).unwrap(); // a directory where a file must go

    let (st, _, body) = send(
        &app,
        "POST",
        "/api/v1/network/apply",
        Some(&cookie),
        Some(&csrf),
        None,
    )
    .await;
    assert_eq!(st, StatusCode::CONFLICT, "response: {body}");
    assert!(
        body.to_lowercase().contains("provisional"),
        "error response must mention the pending provisional state: {body}"
    );
    assert!(
        body.contains("confirm_window_secs"),
        "error response must carry the confirm window: {body}"
    );
}

#[tokio::test]
async fn network_apply_confirm_and_rollback_via_http() {
    // Checkpoint 4.1: /network/apply, /network/confirm, /network/rollback
    // exercised end to end via real HTTP, with a real (not directly-planted)
    // provisional state — confirm clears it and leaves the applied config in
    // place; rollback (in a fresh apply) restores the pre-apply state.
    let (app, dir) = build_app().await;
    let (cookie, csrf) = boot_admin(&app).await;

    let severing_body = serde_json::json!({
        "interfaces": [{
            "name": "eth0", "enabled": true, "dhcp": false,
            "address": "192.168.1.50", "prefix": 24
        }],
        "wifi": [], "dns_servers": [], "confirm_window_secs": 90,
        "ap_mode": { "enabled": false, "trigger": "no-config", "ssid": "unused", "passphrase": "unused123" }
    })
    .to_string();

    // --- apply -> confirm ---
    let (st, _, _) = send(
        &app,
        "PUT",
        "/api/v1/network",
        Some(&cookie),
        Some(&csrf),
        Some(&severing_body),
    )
    .await;
    assert_eq!(st, StatusCode::OK);

    let (st, _, body) = send(
        &app,
        "POST",
        "/api/v1/network/apply",
        Some(&cookie),
        Some(&csrf),
        None,
    )
    .await;
    assert_eq!(st, StatusCode::OK);
    let v: serde_json::Value = serde_json::from_str(&body).unwrap();
    assert_eq!(
        v["provisional"], true,
        "static-IP change must be provisional: {body}"
    );

    let network_unit = dir.path().join("etc/systemd/network/10-eth0.network");
    assert!(
        network_unit.exists(),
        "networkd unit must be written by apply"
    );

    let (st, _, body) = send(
        &app,
        "POST",
        "/api/v1/network/confirm",
        Some(&cookie),
        Some(&csrf),
        None,
    )
    .await;
    assert_eq!(st, StatusCode::OK);
    let v: serde_json::Value = serde_json::from_str(&body).unwrap();
    assert_eq!(v["confirmed"], true, "{body}");
    // Confirmed — the applied config stays in place.
    assert!(network_unit.exists());

    // --- rollback, on a fresh app so there's genuinely no prior state ---
    let (app2, dir2) = build_app().await;
    let (cookie2, csrf2) = boot_admin(&app2).await;
    let (st, _, _) = send(
        &app2,
        "PUT",
        "/api/v1/network",
        Some(&cookie2),
        Some(&csrf2),
        Some(&severing_body),
    )
    .await;
    assert_eq!(st, StatusCode::OK);
    let (st, _, _) = send(
        &app2,
        "POST",
        "/api/v1/network/apply",
        Some(&cookie2),
        Some(&csrf2),
        None,
    )
    .await;
    assert_eq!(st, StatusCode::OK);
    let network_unit2 = dir2.path().join("etc/systemd/network/10-eth0.network");
    assert!(
        network_unit2.exists(),
        "networkd unit must be written by apply"
    );

    let (st, _, body) = send(
        &app2,
        "POST",
        "/api/v1/network/rollback",
        Some(&cookie2),
        Some(&csrf2),
        None,
    )
    .await;
    assert_eq!(st, StatusCode::OK, "{body}");
    // No prior networkd unit existed before this apply, so rollback must
    // restore that "did not exist" state.
    assert!(
        !network_unit2.exists(),
        "rollback must restore the pre-apply state (no unit existed before)"
    );
}

#[tokio::test]
async fn network_scan_and_connectivity_test_via_http() {
    // Checkpoint 4.1: GET /network/scan and POST /network/connectivity-test
    // exercised via real HTTP against the fake sysroot (deterministic,
    // hardware-free).
    let (app, _dir) = build_app().await;
    let (cookie, csrf) = boot_admin(&app).await;

    let (st, _, body) = send(
        &app,
        "GET",
        "/api/v1/network/scan",
        Some(&cookie),
        None,
        None,
    )
    .await;
    assert_eq!(st, StatusCode::OK);
    let v: serde_json::Value = serde_json::from_str(&body).unwrap();
    assert!(
        v.as_array().unwrap().is_empty(),
        "fake sysroot has no wireless hardware: {body}"
    );

    let (st, _, body) = send(
        &app,
        "POST",
        "/api/v1/network/connectivity-test",
        Some(&cookie),
        Some(&csrf),
        None,
    )
    .await;
    assert_eq!(st, StatusCode::OK);
    let v: serde_json::Value = serde_json::from_str(&body).unwrap();
    assert!(v.get("gateway").is_some());
    assert!(v.get("public").is_some());
    assert!(v.get("dns").is_some());
    // Checkpoint 5.22: with no global DNS configured, the detail must not
    // fabricate a specific server as "the" resolver used — it names the
    // absence honestly instead of a hardcoded fallback IP.
    let dns_detail = v["dns"]["detail"].as_str().unwrap();
    assert!(
        dns_detail.contains("no global DNS override configured"),
        "unconfigured DNS must not claim a fabricated server: {dns_detail}"
    );

    // Configuring a global DNS server changes the detail to name it as a
    // *preference*, not a proven fact about which server actually answered.
    let body = serde_json::json!({
        "interfaces": [], "wifi": [], "dns_servers": ["9.9.9.9"], "confirm_window_secs": 90,
    })
    .to_string();
    let (st, _, _) = send(
        &app,
        "PUT",
        "/api/v1/network",
        Some(&cookie),
        Some(&csrf),
        Some(&body),
    )
    .await;
    assert_eq!(st, StatusCode::OK);

    let (st, _, body) = send(
        &app,
        "POST",
        "/api/v1/network/connectivity-test",
        Some(&cookie),
        Some(&csrf),
        None,
    )
    .await;
    assert_eq!(st, StatusCode::OK);
    let v: serde_json::Value = serde_json::from_str(&body).unwrap();
    let dns_detail = v["dns"]["detail"].as_str().unwrap();
    assert!(
        dns_detail.contains("9.9.9.9") && dns_detail.contains("preference"),
        "configured DNS must be named as a preference, not a proven source: {dns_detail}"
    );
}

#[tokio::test]
async fn reboot_shutdown_root_password_last_admin_guard_and_groups_via_http() {
    // Checkpoint 4.8: reboot/shutdown/root-password/last-admin-guard/group
    // management, all via real HTTP (none had any coverage before).
    let (app, _dir) = build_app().await;
    let (cookie, csrf) = boot_admin(&app).await;

    // --- reboot / shutdown (gated by is_real_root(), safe on fake sysroot) ---
    let (st, _, body) = send(
        &app,
        "POST",
        "/api/v1/system/reboot",
        Some(&cookie),
        Some(&csrf),
        None,
    )
    .await;
    assert_eq!(st, StatusCode::OK, "{body}");
    let v: serde_json::Value = serde_json::from_str(&body).unwrap();
    assert_eq!(v["rebooting"], true);

    let (st, _, body) = send(
        &app,
        "POST",
        "/api/v1/system/shutdown",
        Some(&cookie),
        Some(&csrf),
        None,
    )
    .await;
    assert_eq!(st, StatusCode::OK, "{body}");
    let v: serde_json::Value = serde_json::from_str(&body).unwrap();
    assert_eq!(v["shutting_down"], true);

    // --- root password: too short is rejected, a valid one succeeds ---
    let (st, _, _) = send(
        &app,
        "POST",
        "/api/v1/system/root-password",
        Some(&cookie),
        Some(&csrf),
        Some(r#"{"password":"short"}"#),
    )
    .await;
    assert_eq!(st, StatusCode::BAD_REQUEST);

    // An embedded newline would inject an extra `user:password` line into
    // chpasswd's stdin — must be rejected, not passed through.
    let (st, _, _) = send(
        &app,
        "POST",
        "/api/v1/system/root-password",
        Some(&cookie),
        Some(&csrf),
        Some(r#"{"password":"long-enough\nattacker:pwned"}"#),
    )
    .await;
    assert_eq!(st, StatusCode::BAD_REQUEST);

    let (st, _, _) = send(
        &app,
        "POST",
        "/api/v1/system/root-password",
        Some(&cookie),
        Some(&csrf),
        Some(r#"{"password":"a-real-root-password"}"#),
    )
    .await;
    assert_eq!(st, StatusCode::OK);

    // --- last-admin-delete guard: the only admin cannot be deleted ---
    let (st, _, body) = send(
        &app,
        "DELETE",
        "/api/v1/system/users/admin",
        Some(&cookie),
        Some(&csrf),
        None,
    )
    .await;
    assert_eq!(
        st,
        StatusCode::CONFLICT,
        "must refuse to delete the last admin: {body}"
    );

    // A second admin makes the first one deletable.
    let (st, _, _) = send(
        &app,
        "POST",
        "/api/v1/system/users",
        Some(&cookie),
        Some(&csrf),
        Some(r#"{"username":"admin2","password":"password1","role":"admin"}"#),
    )
    .await;
    assert_eq!(st, StatusCode::OK);
    let (st, _, body) = send(
        &app,
        "DELETE",
        "/api/v1/system/users/admin",
        Some(&cookie),
        Some(&csrf),
        None,
    )
    .await;
    assert_eq!(
        st,
        StatusCode::OK,
        "a second admin exists, so this delete must succeed: {body}"
    );

    // --- group management (re-authenticate as admin2, since "admin" no
    // longer has a valid account to keep using) ---
    let (st, admin2_ck, _) = send(
        &app,
        "POST",
        "/api/v1/auth/login",
        None,
        None,
        Some(r#"{"username":"admin2","password":"password1"}"#),
    )
    .await;
    assert_eq!(st, StatusCode::OK);
    let admin2_cookie = admin2_ck.unwrap();
    let (_, _, text) = send(
        &app,
        "GET",
        "/api/v1/auth/whoami",
        Some(&admin2_cookie),
        None,
        None,
    )
    .await;
    let admin2_csrf = serde_json::from_str::<serde_json::Value>(&text).unwrap()["csrf"]
        .as_str()
        .unwrap()
        .to_string();

    let (st, _, body) = send(
        &app,
        "POST",
        "/api/v1/system/groups",
        Some(&admin2_cookie),
        Some(&admin2_csrf),
        Some(r#"{"name":"field-techs"}"#),
    )
    .await;
    assert_eq!(st, StatusCode::OK, "{body}");

    let (st, _, _) = send(
        &app,
        "PUT",
        "/api/v1/system/groups/field-techs/members/admin2",
        Some(&admin2_cookie),
        Some(&admin2_csrf),
        None,
    )
    .await;
    assert_eq!(st, StatusCode::OK);

    let (st, _, body) = send(
        &app,
        "GET",
        "/api/v1/system/groups",
        Some(&admin2_cookie),
        None,
        None,
    )
    .await;
    assert_eq!(st, StatusCode::OK);
    let groups: serde_json::Value = serde_json::from_str(&body).unwrap();
    let group = groups
        .as_array()
        .unwrap()
        .iter()
        .find(|g| g["name"] == "field-techs")
        .expect("group present");
    assert!(group["members"]
        .as_array()
        .unwrap()
        .iter()
        .any(|m| m == "admin2"));

    let (st, _, _) = send(
        &app,
        "DELETE",
        "/api/v1/system/groups/field-techs/members/admin2",
        Some(&admin2_cookie),
        Some(&admin2_csrf),
        None,
    )
    .await;
    assert_eq!(st, StatusCode::OK);

    let (st, _, _) = send(
        &app,
        "DELETE",
        "/api/v1/system/groups/field-techs",
        Some(&admin2_cookie),
        Some(&admin2_csrf),
        None,
    )
    .await;
    assert_eq!(st, StatusCode::OK);
}

#[tokio::test]
async fn monitoring_logs_and_io_happy_paths_via_http() {
    // Checkpoint 4.9: at least one HTTP-level happy-path test per
    // monitoring/logs/io route that previously had zero coverage (their own
    // files only had pure-parser unit tests, e.g. lsusb/lspci/i2cdetect
    // output parsing).
    let mut caps = serde_json::Map::new();
    caps.insert("direction".into(), serde_json::Value::String("out".into()));
    let manifest = BoardManifest {
        platform: "zion".into(),
        peripherals: vec![
            webconfig_engine::manifest::Peripheral {
                id: "can0".into(),
                device: "can0".into(),
                human_name: "CAN 0".into(),
                kind: IoKind::Can,
                ..Default::default()
            },
            webconfig_engine::manifest::Peripheral {
                id: "led0".into(),
                device: "gpiochip1 line 7".into(),
                human_name: "Status LED".into(),
                kind: IoKind::Gpio,
                caps,
            },
            webconfig_engine::manifest::Peripheral {
                id: "0".into(),
                device: "/dev/i2c-0".into(),
                human_name: "I2C bus 0".into(),
                kind: IoKind::I2c,
                ..Default::default()
            },
        ],
        service_allowlist: vec!["sshd.service".into()],
        ..Default::default()
    };
    let (app, _dir) = build_app_full(Schema::default_permissive(), None, Some(manifest)).await;
    let (cookie, csrf) = boot_admin(&app).await;

    // --- monitoring ---
    for path in [
        "/api/v1/monitoring/vitals",
        "/api/v1/monitoring/emmc/mmcblk0",
        "/api/v1/monitoring/tiles",
        "/api/v1/monitoring/telemetry",
    ] {
        let (st, _, body) = send(&app, "GET", path, Some(&cookie), None, None).await;
        assert_eq!(st, StatusCode::OK, "GET {path}: {body}");
    }
    let (st, _, body) = send(
        &app,
        "PUT",
        "/api/v1/monitoring/telemetry",
        Some(&cookie),
        Some(&csrf),
        Some(&serde_json::json!({ "enabled": false, "server_url": "", "username": "", "password": "", "publish_interval_secs": 60, "include_diagnostics": false }).to_string()),
    )
    .await;
    assert_eq!(st, StatusCode::OK, "{body}");

    // --- logs ---
    for path in [
        "/api/v1/logs",
        "/api/v1/logs/sources",
        "/api/v1/logs/config",
        "/api/v1/logs/download",
    ] {
        let (st, _, body) = send(&app, "GET", path, Some(&cookie), None, None).await;
        assert_eq!(st, StatusCode::OK, "GET {path}: {body}");
    }
    let (st, _, body) = send(
        &app,
        "PUT",
        "/api/v1/logs/config",
        Some(&cookie),
        Some(&csrf),
        Some(&serde_json::json!({ "storage": "volatile", "max_size_bytes": 10_000_000u64, "retention_days": 7, "remote_syslog": null }).to_string()),
    )
    .await;
    assert_eq!(st, StatusCode::OK, "{body}");

    // --- io ---
    for path in [
        "/api/v1/io",
        "/api/v1/io/usb",
        "/api/v1/io/pci",
        "/api/v1/io/i2c",
        "/api/v1/io/spi",
        "/api/v1/io/platform-panel",
    ] {
        let (st, _, body) = send(&app, "GET", path, Some(&cookie), None, None).await;
        assert_eq!(st, StatusCode::OK, "GET {path}: {body}");
    }
    let (st, _, body) = send(
        &app,
        "POST",
        "/api/v1/io/i2c/scan?bus=0",
        Some(&cookie),
        Some(&csrf),
        None,
    )
    .await;
    assert_eq!(st, StatusCode::OK, "{body}");

    let (st, _, body) = send(
        &app,
        "POST",
        "/api/v1/io/can/apply",
        Some(&cookie),
        Some(&csrf),
        None,
    )
    .await;
    assert_eq!(st, StatusCode::OK, "{body}");

    let (st, _, body) = send(
        &app,
        "POST",
        "/api/v1/io/gpio/led0/toggle",
        Some(&cookie),
        Some(&csrf),
        None,
    )
    .await;
    assert_eq!(st, StatusCode::OK, "{body}");
}

#[tokio::test]
async fn security_put_apply_and_exposure_via_http() {
    // Checkpoint 4.5: PUT /security (idle timeout / plain-HTTP opt-in /
    // WireGuard config save), POST /security/apply (WireGuard applier), and
    // GET /security/exposure exercised via real HTTP. GET /security/trust
    // already has dedicated coverage (Checkpoint 0.3's test).
    let (app, _dir) = build_app().await;
    let (cookie, csrf) = boot_admin(&app).await;

    let (st, _, body) = send(
        &app,
        "PUT",
        "/api/v1/security",
        Some(&cookie),
        Some(&csrf),
        Some(
            &serde_json::json!({
                "session_idle_timeout_secs": 1200,
                "allow_plain_http": true,
                "wireguard": { "enabled": false, "port": 51820, "peers": [] },
                "certificates": [],
            })
            .to_string(),
        ),
    )
    .await;
    assert_eq!(st, StatusCode::OK, "{body}");
    let v: serde_json::Value = serde_json::from_str(&body).unwrap();
    assert_eq!(v["session_idle_timeout_secs"], 1200);
    assert_eq!(v["allow_plain_http"], true);

    let (st, _, body) = send(
        &app,
        "POST",
        "/api/v1/security/apply",
        Some(&cookie),
        Some(&csrf),
        None,
    )
    .await;
    assert_eq!(st, StatusCode::OK, "{body}");
    let v: serde_json::Value = serde_json::from_str(&body).unwrap();
    assert_eq!(v["applied"][0], "wireguard");

    let (st, _, body) = send(
        &app,
        "GET",
        "/api/v1/security/exposure",
        Some(&cookie),
        None,
        None,
    )
    .await;
    assert_eq!(st, StatusCode::OK, "{body}");
    let v: serde_json::Value = serde_json::from_str(&body).unwrap();
    assert_eq!(v["plain_http"], true);
    assert!(v.get("per_interface").is_some());
}

#[tokio::test(start_paused = true)]
async fn events_sse_requires_auth_and_emits_vitals_over_http() {
    // Checkpoint 4.4: /events at the HTTP layer — the auth gate (checked
    // before the stream starts, so no waiting involved) and a real emitted
    // `vitals` frame (the journal-parsing/cursor logic itself already has
    // dedicated pure-parser unit tests in sse.rs). `start_paused` lets
    // tokio's virtual clock auto-advance through the handler's internal
    // JOURNAL_TICK_SECS sleep instead of a real multi-second wait.
    let (app, _dir) = build_app().await;

    let req = Request::builder()
        .method("GET")
        .uri("/api/v1/events")
        .body(Body::empty())
        .unwrap();
    let resp = app.clone().oneshot(req).await.unwrap();
    assert_eq!(resp.status(), StatusCode::UNAUTHORIZED);

    let (cookie, _csrf) = boot_admin(&app).await;
    let req = Request::builder()
        .method("GET")
        .uri("/api/v1/events")
        .header("cookie", &cookie)
        .body(Body::empty())
        .unwrap();
    let resp = app.clone().oneshot(req).await.unwrap();
    assert_eq!(resp.status(), StatusCode::OK);
    assert!(
        resp.headers()
            .get("content-type")
            .and_then(|v| v.to_str().ok())
            .unwrap_or("")
            .starts_with("text/event-stream"),
        "SSE response must be text/event-stream"
    );

    use futures_util::StreamExt;
    let mut stream = resp.into_body().into_data_stream();
    let chunk = tokio::time::timeout(std::time::Duration::from_secs(10), stream.next())
        .await
        .expect("first SSE frame must arrive")
        .expect("stream must yield at least one item")
        .expect("frame must not be an error");
    let text = String::from_utf8_lossy(&chunk);
    assert!(
        text.contains("event: vitals") || text.contains("vitals"),
        "first SSE frame must be a vitals event: {text}"
    );
}

#[tokio::test]
async fn events_sse_concurrency_cap_rejects_beyond_max() {
    // Checkpoint 5.12: the `/events` semaphore permit is acquired synchronously
    // in the handler before the streaming response is returned, so opening
    // MAX_SSE_CONNECTIONS oneshot responses (without reading their bodies)
    // holds all permits without needing to actually drive the streams.
    let (app, _dir) = build_app().await;
    let (cookie, _csrf) = boot_admin(&app).await;

    let mut held = Vec::new();
    for _ in 0..crate::bootstrap::MAX_SSE_CONNECTIONS {
        let req = Request::builder()
            .method("GET")
            .uri("/api/v1/events")
            .header("cookie", &cookie)
            .body(Body::empty())
            .unwrap();
        let resp = app.clone().oneshot(req).await.unwrap();
        assert_eq!(resp.status(), StatusCode::OK);
        held.push(resp);
    }

    let req = Request::builder()
        .method("GET")
        .uri("/api/v1/events")
        .header("cookie", &cookie)
        .body(Body::empty())
        .unwrap();
    let resp = app.clone().oneshot(req).await.unwrap();
    assert_eq!(
        resp.status(),
        StatusCode::SERVICE_UNAVAILABLE,
        "connection beyond the cap must be rejected, not silently accepted"
    );

    // Dropping one held connection releases its permit for a new caller.
    held.remove(0);
    let req = Request::builder()
        .method("GET")
        .uri("/api/v1/events")
        .header("cookie", &cookie)
        .body(Body::empty())
        .unwrap();
    let resp = app.clone().oneshot(req).await.unwrap();
    assert_eq!(resp.status(), StatusCode::OK);
}

#[tokio::test]
async fn updates_install_ota_backup_restore_via_http() {
    // Checkpoint 4.3: install (path-based), ota get/put/check, backup
    // (plaintext GET already covered elsewhere; encrypted POST here), and
    // restore (both plaintext and encrypted) exercised via real HTTP.
    let (app, dir) = build_app().await;
    let (cookie, csrf) = boot_admin(&app).await;

    // --- install: a pre-staged bundle + sibling manifest (mock backend
    // convention) ---
    let bundle_path = dir.path().join("staged.raucb");
    std::fs::write(&bundle_path, b"fake bundle").unwrap();
    std::fs::write(
        format!("{}.manifest.json", bundle_path.display()),
        r#"{"platform":"zion","variant":null,"version":"9.9.9","description":"install test"}"#,
    )
    .unwrap();
    let (st, _, body) = send(
        &app,
        "POST",
        "/api/v1/updates/install",
        Some(&cookie),
        Some(&csrf),
        Some(&serde_json::json!({ "bundle_path": bundle_path.to_string_lossy() }).to_string()),
    )
    .await;
    assert_eq!(st, StatusCode::OK, "{body}");
    let v: serde_json::Value = serde_json::from_str(&body).unwrap();
    assert_eq!(v["manifest"]["version"], "9.9.9");

    // --- ota: get default, put a server url, get reflects it, check with no
    // real server on the fake sysroot reports a clear non-error skip ---
    let (st, _, body) = send(
        &app,
        "GET",
        "/api/v1/updates/ota",
        Some(&cookie),
        None,
        None,
    )
    .await;
    assert_eq!(st, StatusCode::OK, "{body}");

    let (st, _, body) = send(
        &app,
        "PUT",
        "/api/v1/updates/ota",
        Some(&cookie),
        Some(&csrf),
        Some(&serde_json::json!({ "enabled": true, "server_url": "https://ota.example/check", "auto_apply": false }).to_string()),
    )
    .await;
    assert_eq!(st, StatusCode::OK, "{body}");
    let v: serde_json::Value = serde_json::from_str(&body).unwrap();
    assert_eq!(v["server_url"], "https://ota.example/check");

    let (st, _, body) = send(
        &app,
        "GET",
        "/api/v1/updates/ota/check",
        Some(&cookie),
        None,
        None,
    )
    .await;
    assert_eq!(st, StatusCode::OK, "{body}");
    let v: serde_json::Value = serde_json::from_str(&body).unwrap();
    assert_eq!(v["checked"], true);
    assert_eq!(v["update_available"], serde_json::Value::Null);

    // --- backup: encrypted POST, then restore that same envelope ---
    let (st, _, body) = send(
        &app,
        "POST",
        "/api/v1/updates/backup",
        Some(&cookie),
        Some(&csrf),
        Some(&serde_json::json!({ "passphrase": "correct-horse-battery" }).to_string()),
    )
    .await;
    assert_eq!(st, StatusCode::OK, "{body}");
    let backup: serde_json::Value = serde_json::from_str(&body).unwrap();
    assert_eq!(backup["secrets_included"], true);
    assert!(backup["encrypted"].is_object());

    let (st, _, body) = send(
        &app,
        "POST",
        "/api/v1/updates/restore",
        Some(&cookie),
        Some(&csrf),
        Some(
            &serde_json::json!({
                "encrypted": backup["encrypted"],
                "passphrase": "correct-horse-battery",
            })
            .to_string(),
        ),
    )
    .await;
    assert_eq!(st, StatusCode::OK, "encrypted restore: {body}");

    // --- restore: plaintext (unencrypted) config file round-trip ---
    let (st, _, plain_body) = send(
        &app,
        "GET",
        "/api/v1/updates/backup?include_secrets=false",
        Some(&cookie),
        None,
        None,
    )
    .await;
    assert_eq!(st, StatusCode::OK);
    let plain: serde_json::Value = serde_json::from_str(&plain_body).unwrap();
    let config_file = serde_json::json!({
        "schema_version": plain["schema_version"],
        "config": plain["config"],
    });
    let (st, _, body) = send(
        &app,
        "POST",
        "/api/v1/updates/restore",
        Some(&cookie),
        Some(&csrf),
        Some(&config_file.to_string()),
    )
    .await;
    assert_eq!(st, StatusCode::OK, "plaintext restore: {body}");
}

#[tokio::test]
async fn factory_reset_reports_partial_failure_and_skips_reboot_on_broken_db() {
    // Checkpoint 1.6: a locked/corrupt config.db must not be presented as a
    // clean success — the failure is logged and surfaced in the response
    // rather than silently swallowed with `let _ =`.
    let (app, dir) = build_app().await;
    let webconfig_dir = dir.path().join("data/webconfig");
    std::fs::create_dir_all(&webconfig_dir).unwrap();
    // A directory where config.db should be a file — Connection::open fails.
    std::fs::create_dir_all(webconfig_dir.join("config.db")).unwrap();

    let (cookie, csrf) = boot_admin(&app).await;
    let (st, _, body) = send(
        &app,
        "POST",
        "/api/v1/system/factory-reset",
        Some(&cookie),
        Some(&csrf),
        Some(r#"{"confirm":"RESET","mode":"settings"}"#),
    )
    .await;
    assert_eq!(st, StatusCode::OK);
    let v: serde_json::Value = serde_json::from_str(&body).unwrap();
    assert_eq!(
        v["factory_reset"], false,
        "must not report clean success: {body}"
    );
    assert_eq!(v["partial"], true);
    let errors = v["errors"].as_array().expect("errors array present");
    assert!(
        errors
            .iter()
            .any(|e| e.as_str().unwrap_or("").contains("config.db")),
        "expected a config.db failure reported: {body}"
    );
}

#[tokio::test]
async fn cert_store_upload_list_delete_round_trip() {
    // spec §4.8: device cert store accepts uploaded CA/client certs, lists
    // their metadata, and removes them. PEM is validated server-side; the PEM
    // file lands in <webconfig_dir>/certs/<id>.pem and the metadata in
    // config.security.certificates.
    let (app, dir) = build_app().await;
    let webconfig_dir = dir.path().join("data/webconfig");

    // Bootstrap admin + csrf.
    let (_, ck, _) = send(
        &app,
        "POST",
        "/api/v1/auth/first-boot",
        None,
        None,
        Some(r#"{"username":"admin","password":"password1"}"#),
    )
    .await;
    let cookie = ck.unwrap();
    let (_, _, text) = send(
        &app,
        "GET",
        "/api/v1/auth/whoami",
        Some(&cookie),
        None,
        None,
    )
    .await;
    let v: serde_json::Value = serde_json::from_str(&text).unwrap();
    let csrf = v["csrf"].as_str().unwrap().to_string();

    // A real self-signed cert PEM generated with rcgen.
    let mut params = rcgen::CertificateParams::new(vec!["ca.example".to_string()]).unwrap();
    params.distinguished_name = rcgen::DistinguishedName::new();
    params
        .distinguished_name
        .push(rcgen::DnType::CommonName, "ca.example");
    let key_pair = rcgen::KeyPair::generate_for(&rcgen::PKCS_ECDSA_P256_SHA256).unwrap();
    let pem = params.self_signed(&key_pair).unwrap().pem();

    let body = serde_json::json!({
        "id": "ca-root",
        "kind": "ca",
        "subject": "CN=ca.example",
        "not_after": "2026-08-23",
        "pem": pem,
    })
    .to_string();
    let (st, _, text) = send(
        &app,
        "POST",
        "/api/v1/security/certs",
        Some(&cookie),
        Some(&csrf),
        Some(&body),
    )
    .await;
    assert_eq!(st, StatusCode::OK);
    let resp: serde_json::Value = serde_json::from_str(&text).unwrap();
    assert_eq!(resp["id"], "ca-root");
    assert_eq!(resp["kind"], "ca");

    // The PEM file is on the data partition.
    assert!(webconfig_dir.join("certs/ca-root.pem").exists());

    // GET /security/certs lists it.
    let (st, _, text) = send(
        &app,
        "GET",
        "/api/v1/security/certs",
        Some(&cookie),
        None,
        None,
    )
    .await;
    assert_eq!(st, StatusCode::OK);
    let list: Vec<serde_json::Value> = serde_json::from_str(&text).unwrap();
    assert!(list.iter().any(|c| c["id"] == "ca-root"));

    // A garbage PEM is rejected at save.
    let bad = serde_json::json!({
        "id": "bad", "kind": "ca", "subject": "x", "not_after": "2026-08-23", "pem": "not a cert"
    })
    .to_string();
    let (st, _, _) = send(
        &app,
        "POST",
        "/api/v1/security/certs",
        Some(&cookie),
        Some(&csrf),
        Some(&bad),
    )
    .await;
    assert_eq!(st, StatusCode::BAD_REQUEST);

    // DELETE removes file + metadata.
    let (st, _, _) = send(
        &app,
        "DELETE",
        "/api/v1/security/certs/ca-root",
        Some(&cookie),
        Some(&csrf),
        None,
    )
    .await;
    assert_eq!(st, StatusCode::OK);
    assert!(!webconfig_dir.join("certs/ca-root.pem").exists());

    // Deleting a missing id → NotFound.
    let (st, _, _) = send(
        &app,
        "DELETE",
        "/api/v1/security/certs/nope",
        Some(&cookie),
        Some(&csrf),
        None,
    )
    .await;
    assert_eq!(st, StatusCode::NOT_FOUND);
}

#[tokio::test]
async fn tls_identity_upload_replaces_cert_and_rejects_mismatch() {
    // Spec §4.8: "upload of customer cert/key" replaces the device TLS
    // identity. The pair is validated (a mismatched cert/key is rejected) and
    // only then written to cert.pem/key.pem — a bad upload can never brick the
    // listener on the next boot.
    let (app, dir) = build_app().await;
    let webconfig_dir = dir.path().join("data/webconfig");
    let (cookie, csrf) = boot_admin(&app).await;

    // A real self-signed identity generated client-side = the "customer" pair.
    let mut params = rcgen::CertificateParams::new(vec!["customer.example".to_string()]).unwrap();
    params.distinguished_name = rcgen::DistinguishedName::new();
    params
        .distinguished_name
        .push(rcgen::DnType::CommonName, "customer.example");
    let kp = rcgen::KeyPair::generate_for(&rcgen::PKCS_ECDSA_P256_SHA256).unwrap();
    let cert_pem = params.self_signed(&kp).unwrap().pem();
    let key_pem = kp.serialize_pem();

    let body = serde_json::json!({ "cert": cert_pem, "key": key_pem }).to_string();
    let (st, _, resp) = send(
        &app,
        "POST",
        "/api/v1/security/tls",
        Some(&cookie),
        Some(&csrf),
        Some(&body),
    )
    .await;
    assert_eq!(st, StatusCode::OK);
    let v: serde_json::Value = serde_json::from_str(&resp).unwrap();
    assert_eq!(v["ok"], true);
    assert_eq!(v["restart_required"], true);
    // The customer identity replaced cert.pem/key.pem on the data partition.
    let on_disk = std::fs::read_to_string(webconfig_dir.join("cert.pem")).unwrap();
    assert_eq!(on_disk, cert_pem, "cert.pem must be the uploaded cert");
    assert_eq!(
        std::fs::read_to_string(webconfig_dir.join("key.pem")).unwrap(),
        key_pem
    );
    // The private key is mode 0600 — not world-readable.
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let mode = std::fs::metadata(webconfig_dir.join("key.pem"))
            .unwrap()
            .permissions()
            .mode();
        assert_eq!(mode & 0o077, 0, "key.pem must not be group/world readable");
    }

    // A cert uploaded against a DIFFERENT key is rejected before disk changes.
    let other_kp = rcgen::KeyPair::generate_for(&rcgen::PKCS_ECDSA_P256_SHA256).unwrap();
    let bad = serde_json::json!({ "cert": cert_pem, "key": other_kp.serialize_pem() }).to_string();
    let (st, _, _) = send(
        &app,
        "POST",
        "/api/v1/security/tls",
        Some(&cookie),
        Some(&csrf),
        Some(&bad),
    )
    .await;
    assert_eq!(st, StatusCode::BAD_REQUEST);
    // cert.pem is still the previously-accepted one (not overwritten on reject).
    assert_eq!(
        std::fs::read_to_string(webconfig_dir.join("cert.pem")).unwrap(),
        cert_pem
    );

    // Empty body → 400.
    let (st, _, _) = send(
        &app,
        "POST",
        "/api/v1/security/tls",
        Some(&cookie),
        Some(&csrf),
        Some(r#"{"cert":"","key":""}"#),
    )
    .await;
    assert_eq!(st, StatusCode::BAD_REQUEST);
}

#[tokio::test]
async fn oversized_request_body_is_rejected_before_buffering() {
    // Checkpoint 1.8: upload-heavy routes must reject an oversized body
    // rather than buffering it unbounded (spec §2 RAM budget). /security/tls
    // is capped well below 1 MB; send a body well past that cap.
    let (app, _dir) = build_app().await;
    let (cookie, csrf) = boot_admin(&app).await;

    let huge_body = "x".repeat(2 * 1024 * 1024); // 2 MiB, well over the 64 KiB cap
    let req = Request::builder()
        .method("POST")
        .uri("/api/v1/security/tls")
        .header("cookie", &cookie)
        .header("X-CSRF-Token", &csrf)
        .header("content-type", "application/json")
        .body(Body::from(huge_body))
        .unwrap();
    let resp = app.clone().oneshot(req).await.unwrap();
    assert_eq!(resp.status(), StatusCode::PAYLOAD_TOO_LARGE);
}

#[tokio::test]
async fn mcu_firmware_verifies_real_signature_and_rejects_tampering() {
    // Spec §4.9 (P0 0.1): firmware upload must cryptographically verify a
    // detached signature against a manifest-declared trust anchor — a
    // client-supplied "signed" boolean is never trusted on its own, and a
    // convincing no-op must not report a fabricated flash.
    use ring::signature::KeyPair;

    let rng = ring::rand::SystemRandom::new();
    let pkcs8 = ring::signature::Ed25519KeyPair::generate_pkcs8(&rng).unwrap();
    let keypair = ring::signature::Ed25519KeyPair::from_pkcs8(pkcs8.as_ref()).unwrap();
    let pubkey_hex = hex::encode(keypair.public_key().as_ref());

    let mut caps = serde_json::Map::new();
    caps.insert(
        "firmware_pubkey_ed25519".into(),
        serde_json::Value::String(pubkey_hex),
    );
    let manifest = BoardManifest {
        platform: "zion".into(),
        peripherals: vec![webconfig_engine::manifest::Peripheral {
            id: "mcu-stm32".into(),
            device: "/dev/ttyACM0".into(),
            human_name: "STM32 companion MCU".into(),
            kind: IoKind::Mcu,
            caps,
        }],
        ..Default::default()
    };
    let (app, dir) = build_app_full(Schema::default_permissive(), None, Some(manifest)).await;
    let (cookie, csrf) = boot_admin(&app).await;

    let firmware_bytes = b"fake-stm32-firmware-image-bytes-v1".to_vec();
    let signature = keypair.sign(&firmware_bytes);
    let sig_hex = hex::encode(signature.as_ref());

    // Tampered bytes (the signature doesn't match this body) are rejected —
    // proves verification runs against the bytes actually received, not a
    // client-asserted flag.
    let tampered = b"tampered-firmware-bytes-different".to_vec();
    let req = Request::builder()
        .method("POST")
        .uri(format!(
            "/api/v1/io/mcu/firmware?version=1.0.0&signature={sig_hex}"
        ))
        .header("cookie", &cookie)
        .header("X-CSRF-Token", &csrf)
        .header("content-type", "application/octet-stream")
        .body(Body::from(tampered))
        .unwrap();
    let resp = app.clone().oneshot(req).await.unwrap();
    assert_eq!(resp.status(), StatusCode::BAD_REQUEST);

    // A garbage (non-matching-length) signature is rejected too.
    let req = Request::builder()
        .method("POST")
        .uri("/api/v1/io/mcu/firmware?version=1.0.0&signature=deadbeef")
        .header("cookie", &cookie)
        .header("X-CSRF-Token", &csrf)
        .header("content-type", "application/octet-stream")
        .body(Body::from(firmware_bytes.clone()))
        .unwrap();
    let resp = app.clone().oneshot(req).await.unwrap();
    assert_eq!(resp.status(), StatusCode::BAD_REQUEST);

    // Real bytes with a matching signature succeed, land on disk, and the
    // fake sysroot honestly reports a simulated (not real) flash.
    let req = Request::builder()
        .method("POST")
        .uri(format!(
            "/api/v1/io/mcu/firmware?version=1.0.0&signature={sig_hex}"
        ))
        .header("cookie", &cookie)
        .header("X-CSRF-Token", &csrf)
        .header("content-type", "application/octet-stream")
        .body(Body::from(firmware_bytes.clone()))
        .unwrap();
    let resp = app.clone().oneshot(req).await.unwrap();
    assert_eq!(resp.status(), StatusCode::OK);
    let body_bytes = resp.into_body().collect().await.unwrap().to_bytes();
    let v: serde_json::Value = serde_json::from_slice(&body_bytes).unwrap();
    assert_eq!(v["flashed"], false);
    assert_eq!(v["bytes_written"], firmware_bytes.len());
    assert!(v["reason"].as_str().unwrap().contains("simulated"));
    assert!(dir
        .path()
        .join("data/webconfig/mcu-firmware/1.0.0.bin")
        .exists());
}

#[tokio::test]
async fn io_put_rejects_stale_if_match_but_allows_omitted_header() {
    // Checkpoint Phase 5 item 7: PUT /io has an optimistic-concurrency guard
    // (If-Match against a GET-supplied io_version fingerprint) preventing two
    // concurrent editors from silently clobbering each other — but it's
    // opt-in via the header, so a client that omits it still works exactly
    // as before.
    let (app, _dir) = build_app().await;
    let (cookie, csrf) = boot_admin(&app).await;

    let (_, _, get_body) = send(&app, "GET", "/api/v1/io", Some(&cookie), None, None).await;
    let v: serde_json::Value = serde_json::from_str(&get_body).unwrap();
    let stale_version = v["io_version"].as_str().unwrap().to_string();

    let body_a = serde_json::json!({
        "serial": [], "serial_enabled": true, "can": [], "can_enabled": true,
        "gpio": [], "gpio_enabled": true, "cameras": [], "cameras_enabled": true,
        "mcu": null, "board_leds": {},
    })
    .to_string();
    let body_b = serde_json::json!({
        "serial": [], "serial_enabled": false, "can": [], "can_enabled": true,
        "gpio": [], "gpio_enabled": true, "cameras": [], "cameras_enabled": true,
        "mcu": null, "board_leds": {},
    })
    .to_string();

    // A concurrent editor's write lands first (no If-Match — same behavior as
    // before this fix), changing the fingerprint out from under the version
    // captured above.
    let (st, _, _) = send(
        &app,
        "PUT",
        "/api/v1/io",
        Some(&cookie),
        Some(&csrf),
        Some(&body_a),
    )
    .await;
    assert_eq!(
        st,
        StatusCode::OK,
        "baseline write (no If-Match) must succeed"
    );

    // This editor's write, using the now-stale version it originally loaded,
    // must be rejected rather than silently overwriting the other edit.
    let req = Request::builder()
        .method("PUT")
        .uri("/api/v1/io")
        .header("cookie", &cookie)
        .header("X-CSRF-Token", &csrf)
        .header("content-type", "application/json")
        .header("if-match", &stale_version)
        .body(Body::from(body_b.clone()))
        .unwrap();
    let resp = app.clone().oneshot(req).await.unwrap();
    assert_eq!(
        resp.status(),
        StatusCode::CONFLICT,
        "a stale If-Match must be rejected"
    );

    // A PUT with the CURRENT version as If-Match succeeds.
    let (_, _, get_body) = send(&app, "GET", "/api/v1/io", Some(&cookie), None, None).await;
    let v: serde_json::Value = serde_json::from_str(&get_body).unwrap();
    let current_version = v["io_version"].as_str().unwrap().to_string();
    let req = Request::builder()
        .method("PUT")
        .uri("/api/v1/io")
        .header("cookie", &cookie)
        .header("X-CSRF-Token", &csrf)
        .header("content-type", "application/json")
        .header("if-match", &current_version)
        .body(Body::from(body_b))
        .unwrap();
    let resp = app.clone().oneshot(req).await.unwrap();
    assert_eq!(
        resp.status(),
        StatusCode::OK,
        "a fresh If-Match must succeed"
    );
}

#[tokio::test]
async fn serial_apply_writes_baud_unit_and_rejects_conflicts() {
    // Spec §4.9: serial ownership + baud is applied (not just persisted), and
    // duplicate-device conflicts are prevented at PUT time.
    let (app, dir) = build_app().await;
    let (cookie, csrf) = boot_admin(&app).await;

    // A valid serial port on a manifest-declared UART.
    let body = serde_json::json!({
        "serial": [{ "device": "/dev/ttyTHS0", "enabled": true, "baud": 115200, "mode": "rs232", "owner": "customer" }],
        "serial_enabled": true,
        "can": [], "can_enabled": true, "gpio": [], "gpio_enabled": true,
        "cameras": [], "cameras_enabled": true, "mcu": null, "board_leds": {},
    })
    .to_string();
    let (st, _, _) = send(
        &app,
        "PUT",
        "/api/v1/io",
        Some(&cookie),
        Some(&csrf),
        Some(&body),
    )
    .await;
    assert_eq!(st, StatusCode::OK);

    // Apply serial -> the baud unit lands under the sysroot's /etc, not the
    // data partition (matches SerialApplier's `sysroot.join("/etc/...")`).
    let (st, _, _) = send(
        &app,
        "POST",
        "/api/v1/io/serial/apply",
        Some(&cookie),
        Some(&csrf),
        None,
    )
    .await;
    assert_eq!(st, StatusCode::OK);
    assert!(dir
        .path()
        .join("etc/systemd/system/webconfig-serial-ttyTHS0.service")
        .exists());

    // Duplicate device (same UART, two entries) is rejected at PUT (409), even
    // though the two owners differ — ownership is per-device (spec §4.9).
    let dup = serde_json::json!({
        "serial": [
            { "device": "/dev/ttyTHS0", "enabled": true, "baud": 115200, "mode": "rs232", "owner": "console" },
            { "device": "/dev/ttyTHS0", "enabled": true, "baud": 9600, "mode": "rs232", "owner": "customer" }
        ],
        "serial_enabled": true,
        "can": [], "can_enabled": true, "gpio": [], "gpio_enabled": true,
        "cameras": [], "cameras_enabled": true, "mcu": null, "board_leds": {},
    })
    .to_string();
    let (st, _, _) = send(
        &app,
        "PUT",
        "/api/v1/io",
        Some(&cookie),
        Some(&csrf),
        Some(&dup),
    )
    .await;
    assert_eq!(st, StatusCode::CONFLICT);

    // A port not in the manifest is rejected at PUT (400).
    let bad = serde_json::json!({
        "serial": [{ "device": "/dev/ttyS9", "enabled": true, "baud": 9600, "mode": "rs232", "owner": "unassigned" }],
        "serial_enabled": true,
        "can": [], "can_enabled": true, "gpio": [], "gpio_enabled": true,
        "cameras": [], "cameras_enabled": true, "mcu": null, "board_leds": {},
    })
    .to_string();
    let (st, _, _) = send(
        &app,
        "PUT",
        "/api/v1/io",
        Some(&cookie),
        Some(&csrf),
        Some(&bad),
    )
    .await;
    assert_eq!(st, StatusCode::BAD_REQUEST);
}

/// A systemd client that fails every verb — used to prove failures propagate
/// as real HTTP errors and land in the audit trail as failures, not "ok".
#[derive(Debug, Default)]
struct FailingSystemd;

#[async_trait::async_trait]
impl crate::systemd::SystemdClient for FailingSystemd {
    async fn status(
        &self,
        unit: &str,
    ) -> Result<crate::systemd::UnitState, crate::systemd::SystemdError> {
        Err(crate::systemd::SystemdError::NotFound(unit.to_string()))
    }
    async fn start(&self, _unit: &str) -> Result<(), crate::systemd::SystemdError> {
        Err(crate::systemd::SystemdError::Dbus(
            "simulated failure".into(),
        ))
    }
    async fn stop(&self, _unit: &str) -> Result<(), crate::systemd::SystemdError> {
        Err(crate::systemd::SystemdError::Dbus(
            "simulated failure".into(),
        ))
    }
    async fn restart(&self, _unit: &str) -> Result<(), crate::systemd::SystemdError> {
        Err(crate::systemd::SystemdError::Dbus(
            "simulated failure".into(),
        ))
    }
    async fn enable(&self, _unit: &str) -> Result<(), crate::systemd::SystemdError> {
        Err(crate::systemd::SystemdError::Dbus(
            "simulated failure".into(),
        ))
    }
    async fn disable(&self, _unit: &str) -> Result<(), crate::systemd::SystemdError> {
        Err(crate::systemd::SystemdError::Dbus(
            "simulated failure".into(),
        ))
    }
    async fn journal_tail(
        &self,
        _unit: &str,
        _n: usize,
    ) -> Result<String, crate::systemd::SystemdError> {
        Ok(String::new())
    }
}

#[tokio::test]
async fn services_verbs_report_real_failure_not_fake_ok() {
    // Spec §4.1/§4.3 (P0 0.2): a systemd failure must surface as a non-200
    // error and the audit entry must show "failed", never "ok":true.
    let (app, _dir) =
        build_app_with_systemd(Schema::default_permissive(), Some(Arc::new(FailingSystemd))).await;
    let (cookie, csrf) = boot_admin(&app).await;

    for (method, path) in [
        ("POST", "/api/v1/services/sshd.service/start"),
        ("POST", "/api/v1/services/sshd.service/stop"),
        ("POST", "/api/v1/services/sshd.service/restart"),
    ] {
        let (st, _, _) = send(&app, method, path, Some(&cookie), Some(&csrf), None).await;
        assert_eq!(st, StatusCode::UNPROCESSABLE_ENTITY, "{method} {path}");
    }

    let (st, _, _) = send(
        &app,
        "PUT",
        "/api/v1/services/sshd.service/enabled",
        Some(&cookie),
        Some(&csrf),
        Some(r#"{"enabled": true}"#),
    )
    .await;
    assert_eq!(st, StatusCode::UNPROCESSABLE_ENTITY);

    // The audit log reflects failure for every verb, never a fabricated "ok".
    let (st, _, text) = send(
        &app,
        "GET",
        "/api/v1/logs/audit",
        Some(&cookie),
        Some(&csrf),
        None,
    )
    .await;
    assert_eq!(st, StatusCode::OK);
    let entries: serde_json::Value = serde_json::from_str(&text).unwrap();
    let entries = entries.as_array().expect("audit log is a JSON array");
    let failed_count = entries
        .iter()
        .filter(|e| {
            e["action"]
                .as_str()
                .map(|a| a.starts_with("services."))
                .unwrap_or(false)
        })
        .filter(|e| {
            e["detail"]
                .as_str()
                .map(|d| d.contains("failed"))
                .unwrap_or(false)
        })
        .count();
    assert_eq!(
        failed_count, 4,
        "expected all 4 services.* audit entries to record failure: {text}"
    );
}
