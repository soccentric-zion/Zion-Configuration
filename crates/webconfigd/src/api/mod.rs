//! REST API handlers for the nine functional sections (spec §4).
//!
//! Routes are mounted under `/api/v1/...` by [`crate::routes`]. Every mutating
//! route requires an authenticated session (admin or operator per action) and
//! a valid CSRF token (spec §4.8). The security boundary is the API: all input
//! is re-validated server-side by the engine + appliers (spec §2).

pub mod alerts;
pub mod io;
pub mod logs;
pub mod meta;
pub mod monitoring;
pub mod network;
pub mod security;
pub mod services;
pub mod system;
pub mod updates;

use webconfig_engine::audit::AuditEntry;

use crate::error::ApiResult;
use crate::state::AppState;

/// Append an audit entry (spec §4.8). Failures are logged, not fatal — the
/// protected action already succeeded.
pub fn audit(app: &AppState, user: &str, action: &str, detail: Option<String>) {
    if let Err(e) = app.audit.append(&AuditEntry {
        ts: now_iso(),
        user: user.to_string(),
        action: action.to_string(),
        detail,
    }) {
        tracing::warn!(error = %e, "audit append failed");
    }
}

/// Resolve the username from the session role extension. The `AuthRole`
/// extractor only carries the role; the username travels via the session and
/// is read here for audit attribution.
pub async fn username(session: &tower_sessions::Session) -> String {
    session
        .get::<String>("username")
        .await
        .ok()
        .flatten()
        .unwrap_or_else(|| "unknown".to_string())
}

/// Real ISO-8601 UTC timestamp for audit entries (spec §4.8: the `ts` field
/// is documented as "ISO-8601 timestamp (UTC)"). The HTTP layer owns the wall
/// clock — the deterministic engine never does. Uses the already-vendored
/// `time` crate (no new dependency).
pub(crate) fn now_iso() -> String {
    use time::OffsetDateTime;
    let now = OffsetDateTime::now_utc();
    now.format(time::macros::format_description!(
        "[year]-[month]-[day]T[hour]:[minute]:[second]Z"
    ))
    .unwrap_or_else(|_| format!("t={}", now.unix_timestamp()))
}

/// Helper to apply a section's applier by id and audit the result.
pub async fn apply_and_audit(
    app: &AppState,
    user: &str,
    applier_id: &str,
) -> ApiResult<webconfig_engine::ApplyOutcome> {
    let outcome = app.engine.apply(applier_id).await?;
    audit(
        app,
        user,
        &format!("{applier_id}.apply"),
        Some(format!("{:?}", outcome)),
    );
    Ok(outcome)
}

#[cfg(test)]
mod tests {
    use super::now_iso;

    #[test]
    fn now_iso_is_rfc3339_utc() {
        // spec §4.8: audit `ts` is an ISO-8601 UTC stamp, not a `t=<epoch>`
        // placeholder. Shape: YYYY-MM-DDTHH:MM:SSZ (no fractional seconds).
        let ts = now_iso();
        assert_eq!(ts.len(), 20, "audit ts '{ts}' wrong length");
        assert_eq!(ts.chars().nth(4), Some('-'));
        assert_eq!(ts.chars().nth(7), Some('-'));
        assert_eq!(ts.chars().nth(10), Some('T'));
        assert_eq!(ts.chars().nth(13), Some(':'));
        assert_eq!(ts.chars().nth(16), Some(':'));
        assert!(ts.ends_with('Z'), "audit ts must be UTC (Z suffix)");
        // No `t=` placeholder may survive.
        assert!(!ts.starts_with("t="), "audit ts still a placeholder: {ts}");
    }
}
