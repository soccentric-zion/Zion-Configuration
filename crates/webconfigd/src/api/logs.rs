//! Logs handlers (spec §4.6): journal viewer + SSE follow, log config, audit
//! log view, download.

use axum::extract::State;
use axum::Json;
use tower_sessions::Session;
use webconfig_engine::store::logs::LogsConfig;

use crate::api::{apply_and_audit, username};
use crate::auth::AuthRole;
use crate::error::{ApiError, ApiResult};
use crate::state::AppState;

/// `GET /api/v1/logs?unit=&priority=&since=&until=&limit=` — journal read.
/// On the real root shells out to `journalctl`; fake sysroot returns empty.
pub async fn read(
    State(app): State<AppState>,
    _role: AuthRole,
    axum::extract::Query(q): axum::extract::Query<LogsQuery>,
) -> ApiResult<Json<serde_json::Value>> {
    if !app.engine.sysroot.is_real_root() {
        return Ok(Json(serde_json::json!({ "lines": [] })));
    }
    let mut args: Vec<String> = vec!["--no-pager".into(), "--output=json".into()];
    if !q.unit.is_empty() {
        args.push("-u".into());
        args.push(q.unit.clone());
    }
    if !q.priority.is_empty() {
        args.push("-p".into());
        args.push(q.priority.clone());
    }
    if !q.since.is_empty() {
        args.push("--since".into());
        args.push(q.since.clone());
    }
    if !q.until.is_empty() {
        args.push("--until".into());
        args.push(q.until.clone());
    }
    let limit = q.limit.unwrap_or(200);
    args.push("-n".into());
    args.push(limit.to_string());
    let refs: Vec<&str> = args.iter().map(|s| s.as_str()).collect();
    let out = webconfig_engine::applier::run_cmd("journalctl", &refs).map_err(ApiError::from)?;
    Ok(Json(
        serde_json::json!({ "lines": out.lines().collect::<Vec<_>>() }),
    ))
}

#[derive(serde::Deserialize, Default)]
pub struct LogsQuery {
    #[serde(default)]
    pub unit: String,
    #[serde(default)]
    pub priority: String,
    #[serde(default)]
    pub since: String,
    #[serde(default)]
    pub until: String,
    pub limit: Option<usize>,
}

/// `GET /api/v1/logs/config` — journald config (spec §4.6).
pub async fn get_config(
    State(app): State<AppState>,
    _role: AuthRole,
) -> ApiResult<Json<LogsConfig>> {
    Ok(Json(app.engine.load()?.logs))
}

/// `GET /api/v1/logs/sources` — available local log sources.
pub async fn sources(
    State(app): State<AppState>,
    _role: AuthRole,
) -> ApiResult<Json<Vec<serde_json::Value>>> {
    let real_root = app.engine.sysroot.is_real_root();
    Ok(Json(vec![
        serde_json::json!({
            "id": "system-journal",
            "label": "System journal",
            "description": "Service and system messages collected by systemd-journald.",
            "available": real_root,
        }),
        serde_json::json!({
            "id": "kernel",
            "label": "Kernel log",
            "description": "Kernel messages from the journal and kernel ring buffer.",
            "available": real_root,
        }),
        serde_json::json!({
            "id": "syslog",
            "label": "Syslog",
            "description": "Traditional syslog-compatible messages, when provided by the image.",
            "available": app.engine.sysroot.join("/var/log/syslog").exists(),
        }),
        serde_json::json!({
            "id": "audit",
            "label": "WebConfig audit log",
            "description": "Administrative actions recorded by WebConfig.",
            "available": true,
        }),
        serde_json::json!({
            "id": "webconfigd",
            "label": "WebConfig daemon",
            "description": "Messages emitted by webconfigd.service.",
            "available": real_root,
        }),
    ]))
}

/// `PUT /api/v1/logs/config` — apply journald config (admin only; writes via
/// the journald applier, spec §4.6).
pub async fn put_config(
    State(app): State<AppState>,
    role: AuthRole,
    session: Session,
    Json(req): Json<LogsConfig>,
) -> ApiResult<Json<LogsConfig>> {
    role.require_admin("logs.put")?;
    let user = username(&session).await;
    let cfg = app.engine.update(move |c| {
        c.logs = req.clone();
        Ok(())
    })?;
    let _ = apply_and_audit(&app, &user, "journald").await?;
    Ok(Json(cfg.logs))
}

/// `GET /api/v1/logs/audit` — the audit log, viewable under Logs (spec §4.8).
/// The audit log is **append-only**: there is no DELETE/clear route. Only a
/// full factory reset wipes it (see System factory-reset).
pub async fn audit_log(
    State(app): State<AppState>,
    _role: AuthRole,
) -> ApiResult<Json<Vec<webconfig_engine::audit::AuditEntry>>> {
    Ok(Json(app.audit.read_all().unwrap_or_default()))
}

/// `GET /api/v1/logs/download?unit=` — full journal export, or a per-unit
/// export when `unit` is given (spec §4.6: "full journal export and per-unit
/// export").
pub async fn download(
    State(app): State<AppState>,
    _role: AuthRole,
    axum::extract::Query(q): axum::extract::Query<DownloadQuery>,
) -> ApiResult<Json<serde_json::Value>> {
    if !app.engine.sysroot.is_real_root() {
        return Ok(Json(serde_json::json!({ "export": "", "unit": q.unit })));
    }
    let mut args: Vec<String> = vec!["--no-pager".into()];
    if !q.unit.is_empty() {
        args.push("-u".into());
        args.push(q.unit.clone());
    }
    let refs: Vec<&str> = args.iter().map(|s| s.as_str()).collect();
    let out = webconfig_engine::applier::run_cmd("journalctl", &refs).map_err(ApiError::from)?;
    Ok(Json(serde_json::json!({ "export": out, "unit": q.unit })))
}

#[derive(serde::Deserialize, Default)]
pub struct DownloadQuery {
    #[serde(default)]
    pub unit: String,
}
