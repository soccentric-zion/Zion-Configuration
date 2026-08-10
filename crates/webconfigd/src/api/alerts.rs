//! Alerts handlers (spec §4.7): threshold rules with duration, history,
//! webhook/MQTT actions.

use axum::extract::State;
use axum::Json;
use tower_sessions::Session;
use webconfig_engine::alert::AlertHistory;
use webconfig_engine::store::alerts::{AlertRule, AlertsConfig};

use crate::api::{audit, username};
use crate::auth::AuthRole;
use crate::error::{ApiError, ApiResult};
use crate::state::AppState;

/// `GET /api/v1/alerts` — rules + firing state + history + the per-platform
/// default rule packs shipped in the board manifest (spec §4.7). Defaults are
/// read-only here; they are merged with `rules` at evaluation time, and an
/// operator overrides or disables one by adding a same-id rule to `rules`.
pub async fn get(
    State(app): State<AppState>,
    _role: AuthRole,
) -> ApiResult<Json<serde_json::Value>> {
    let cfg = app.engine.load()?;
    let history = AlertHistory::new(app.engine.sysroot.webconfig_dir().join("config.db"), 1000)
        .read_all()
        .unwrap_or_default();
    Ok(Json(serde_json::json!({
        "enabled": cfg.alerts.enabled,
        "rules": cfg.alerts.rules,
        "default_rules": app.manifest.default_alert_rules,
        "webhooks": cfg.alerts.webhooks,
        "mqtt_event_topic": cfg.alerts.mqtt_event_topic,
        "history": history,
    })))
}

/// `PUT /api/v1/alerts` — update alert rules (admin only).
pub async fn put(
    State(app): State<AppState>,
    role: AuthRole,
    session: Session,
    Json(req): Json<AlertsConfig>,
) -> ApiResult<Json<serde_json::Value>> {
    role.require_admin("alerts.put")?;
    validate_alerts_config(&req)?;
    let user = username(&session).await;
    let cfg = app.engine.update(move |c| {
        c.alerts = req.clone();
        Ok(())
    })?;
    audit(&app, &user, "alerts.put", None);
    Ok(Json(serde_json::to_value(&cfg.alerts).unwrap_or_default()))
}

/// Validate a full `AlertsConfig` (spec §4.7): every rule is well-formed, rule
/// ids are unique, push actions are from the allowed set, and webhook URLs are
/// http(s) URLs (so a typo is rejected at save time rather than failing per
/// dispatch on the real root).
fn validate_alerts_config(cfg: &AlertsConfig) -> ApiResult<()> {
    let mut seen = std::collections::HashSet::new();
    for r in &cfg.rules {
        validate_rule(r)?;
        if !seen.insert(r.id.as_str()) {
            return Err(ApiError::BadRequest(format!(
                "duplicate alert rule id {}",
                r.id
            )));
        }
    }
    for url in &cfg.webhooks {
        if !url.starts_with("http://") && !url.starts_with("https://") {
            return Err(ApiError::BadRequest(format!(
                "webhook URL must be http(s): {url}"
            )));
        }
    }
    Ok(())
}

fn validate_rule(r: &AlertRule) -> ApiResult<()> {
    if r.id.is_empty() {
        return Err(ApiError::BadRequest("rule id empty".into()));
    }
    if r.metric.is_empty() {
        return Err(ApiError::BadRequest(format!("rule {} metric empty", r.id)));
    }
    if !matches!(r.comparison.as_str(), ">" | "<" | ">=" | "<=" | "==" | "!=") {
        return Err(ApiError::BadRequest(format!(
            "rule {} bad comparison",
            r.id
        )));
    }
    for a in &r.actions {
        if !matches!(a.as_str(), "ui" | "webhook" | "mqtt") {
            return Err(ApiError::BadRequest(format!(
                "rule {} bad action {}",
                r.id, a
            )));
        }
    }
    Ok(())
}

/// `GET /api/v1/alerts/history` — standalone history read (spec §4.7).
pub async fn history(
    State(app): State<AppState>,
    _role: AuthRole,
) -> ApiResult<Json<Vec<webconfig_engine::alert::AlertEvent>>> {
    let h = AlertHistory::new(app.engine.sysroot.webconfig_dir().join("config.db"), 1000)
        .read_all()
        .unwrap_or_default();
    Ok(Json(h))
}

#[cfg(test)]
mod tests {
    use super::*;
    use webconfig_engine::store::alerts::{AlertRule, AlertsConfig};

    fn rule(id: &str, metric: &str, op: &str) -> AlertRule {
        AlertRule {
            id: id.into(),
            enabled: true,
            metric: metric.into(),
            comparison: op.into(),
            threshold: 85.0,
            duration_secs: 60,
            actions: vec!["ui".into()],
        }
    }

    #[test]
    fn accepts_a_well_formed_config() {
        let cfg = AlertsConfig {
            enabled: true,
            rules: vec![rule("hot", "temp", ">")],
            webhooks: vec!["https://hooks.example.com/a".into()],
            mqtt_event_topic: Some("soccentric/events".into()),
        };
        assert!(validate_alerts_config(&cfg).is_ok());
    }

    #[test]
    fn rejects_empty_metric() {
        let mut r = rule("hot", "temp", ">");
        r.metric = "".into();
        let cfg = AlertsConfig {
            enabled: true,
            rules: vec![r],
            webhooks: vec![],
            mqtt_event_topic: None,
        };
        assert!(validate_alerts_config(&cfg).is_err());
    }

    #[test]
    fn rejects_duplicate_rule_ids() {
        let cfg = AlertsConfig {
            enabled: true,
            rules: vec![rule("hot", "temp", ">"), rule("hot", "disk.pct", ">")],
            webhooks: vec![],
            mqtt_event_topic: None,
        };
        assert!(validate_alerts_config(&cfg).is_err());
    }

    #[test]
    fn rejects_unknown_push_action() {
        let mut r = rule("hot", "temp", ">");
        r.actions = vec!["email".into()];
        let cfg = AlertsConfig {
            enabled: true,
            rules: vec![r],
            webhooks: vec![],
            mqtt_event_topic: None,
        };
        assert!(validate_alerts_config(&cfg).is_err());
    }

    #[test]
    fn rejects_non_http_webhook_url() {
        let cfg = AlertsConfig {
            enabled: true,
            rules: vec![rule("hot", "temp", ">")],
            webhooks: vec!["file:///etc/passwd".into()],
            mqtt_event_topic: None,
        };
        // A non-http(s) webhook URL must be rejected at save time, not deferred
        // to dispatch (spec §4.7).
        assert!(validate_alerts_config(&cfg).is_err());
    }
}
