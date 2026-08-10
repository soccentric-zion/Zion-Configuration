//! Auth: sessions, roles, first-boot admin creation, login delay (spec §4.1/§4.8).
//!
//! Sessions are httpOnly + Secure cookies (set by tower-sessions). Configurable
//! idle timeout (default 15 min). First boot has **no default password** — the
//! only endpoint allowed before an admin exists is `POST /auth/first-boot`
//! (CRA/SB-327, spec §4.8). Incremental delay on failed logins.
//!
//! CSRF protection on every mutating route is in [`crate::csrf`].

use std::collections::HashMap;
use std::sync::Arc;
use std::time::{Duration, Instant};

use axum::extract::Request;
use axum::extract::State;
use axum::middleware::Next;
use axum::response::{IntoResponse, Response};
use axum::Json;
use serde::{Deserialize, Serialize};
use tokio::sync::Mutex;
use tower_sessions::Session;

use webconfig_engine::users::Role;

use crate::error::{ApiError, ApiResult};
use crate::state::AppState;

/// Session keys.
const SK_USER: &str = "username";
const SK_ROLE: &str = "role";

/// Cap on distinct usernames tracked at once. Without this, an attacker
/// submitting many distinct (nonexistent) usernames would grow the map
/// forever — an unbounded-memory DoS. When the cap is hit, the
/// least-recently-active entry is evicted to make room.
const MAX_TRACKED_USERNAMES: usize = 1_000;
/// Failures within `LOCKOUT_DURATION` before a username is hard-locked
/// (login rejected outright, independent of the incremental delay below).
const LOCKOUT_THRESHOLD: u32 = 10;
const LOCKOUT_DURATION: Duration = Duration::from_secs(60);

#[derive(Debug, Clone, Copy)]
struct Attempt {
    count: u32,
    last_attempt: Instant,
}

/// Login delay state: per-username failed-attempt counter + last failure time.
#[derive(Debug, Default)]
pub struct LoginThrottle {
    attempts: Mutex<HashMap<String, Attempt>>,
}

/// Outcome of atomically checking and reserving a login attempt slot.
enum AttemptDecision {
    /// Hard-locked; the login must be rejected without even calling
    /// `authenticate()`.
    Locked,
    /// Not locked — the slot has already been reserved (count incremented)
    /// against the threshold; sleep `delay_ms` before authenticating.
    Allowed { delay_ms: u64 },
}

impl LoginThrottle {
    pub fn new() -> Self {
        Self::default()
    }

    /// Atomically check lockout state and reserve this attempt's slot in one
    /// lock acquisition. Checking and recording used to be two separate
    /// locked operations (`is_locked_out` then, much later, `record_failure`)
    /// with the actual authentication check running in between unlocked —
    /// concurrent requests for the same username could all read "not locked
    /// yet" before any single one's failure was recorded, letting an
    /// attacker's parallel guesses race past the hard lockout entirely.
    /// Reserving the slot up front (before authentication even runs) closes
    /// that race: the `LOCKOUT_THRESHOLD`-th concurrent caller queued behind
    /// this lock sees the prior callers' counts already applied.
    async fn begin_attempt(&self, username: &str) -> AttemptDecision {
        let mut map = self.attempts.lock().await;
        if !map.contains_key(username) && map.len() >= MAX_TRACKED_USERNAMES {
            if let Some(oldest) = map
                .iter()
                .min_by_key(|(_, a)| a.last_attempt)
                .map(|(k, _)| k.clone())
            {
                map.remove(&oldest);
            }
        }
        let now = Instant::now();
        let e = map.entry(username.to_string()).or_insert(Attempt {
            count: 0,
            last_attempt: now,
        });
        // A fresh lockout cycle starts once the previous one has expired,
        // rather than accumulating failures across unrelated time windows.
        if now.duration_since(e.last_attempt) >= LOCKOUT_DURATION {
            e.count = 0;
        }
        if e.count >= LOCKOUT_THRESHOLD {
            // Already locked: don't touch last_attempt, so the lockout
            // expires LOCKOUT_DURATION after the last *counted* failure
            // rather than being extended indefinitely by continued hammering.
            return AttemptDecision::Locked;
        }
        // Exponential delay uses the count *before* this attempt (spec §4.8).
        let base = 200u64;
        let cap = 5_000u64;
        let delay_ms = base.saturating_mul(1u64 << e.count.min(10)).min(cap);
        e.count = e.count.saturating_add(1);
        e.last_attempt = now;
        AttemptDecision::Allowed { delay_ms }
    }

    async fn record_success(&self, username: &str) {
        self.attempts.lock().await.remove(username);
    }
}

#[derive(Debug, Deserialize)]
pub struct LoginRequest {
    pub username: String,
    pub password: String,
}

#[derive(Debug, Serialize)]
pub struct LoginResponse {
    pub username: String,
    pub role: String,
}

/// `POST /api/v1/auth/login` (spec §4.1/§4.8).
pub async fn login(
    State(app): State<AppState>,
    throttle: State<Arc<LoginThrottle>>,
    session: Session,
    Json(req): Json<LoginRequest>,
) -> ApiResult<impl IntoResponse> {
    // First-boot gate: no users yet → login is refused; the first-boot endpoint
    // is the only way forward (spec §4.8).
    let users = app.load_users().map_err(|_| ApiError::Internal)?;
    if users.needs_first_boot_admin() {
        return Err(ApiError::NotReady(
            "first-boot admin not yet created".into(),
        ));
    }

    // Hard lockout + incremental delay: checked and reserved atomically so
    // concurrent requests for the same username can't race past the lockout
    // (see `LoginThrottle::begin_attempt`'s doc comment).
    let delay = match throttle.begin_attempt(&req.username).await {
        AttemptDecision::Locked => return Err(ApiError::Unauthorized),
        AttemptDecision::Allowed { delay_ms } => delay_ms,
    };
    if delay > 0 {
        tokio::time::sleep(std::time::Duration::from_millis(delay)).await;
    }

    match users.authenticate(&req.username, &req.password) {
        Ok(role) => {
            throttle.record_success(&req.username).await;
            session.insert(SK_USER, &req.username).await.ok();
            session.insert(SK_ROLE, role.as_str()).await.ok();
            // CSRF token is minted by the csrf layer on the next GET /auth/whoami.
            Ok(Json(LoginResponse {
                username: req.username,
                role: role.as_str().to_string(),
            }))
        }
        Err(_) => {
            // Failure was already counted by begin_attempt's reservation.
            Err(ApiError::Unauthorized)
        }
    }
}

/// `POST /api/v1/auth/logout` (spec §4.1).
pub async fn logout(session: Session) -> ApiResult<impl IntoResponse> {
    session.clear().await;
    Ok(Json(serde_json::json!({ "ok": true })))
}

/// `GET /api/v1/auth/whoami` — current user + role + CSRF token (spec §4.8).
pub async fn whoami(State(app): State<AppState>, session: Session) -> ApiResult<impl IntoResponse> {
    let username: Option<String> = session.get(SK_USER).await.ok().flatten();
    let role: Option<String> = session.get(SK_ROLE).await.ok().flatten();
    let csrf = csrf_token_from_session(&session).await;
    let first_boot = app
        .load_users()
        .map(|u| u.needs_first_boot_admin())
        .unwrap_or(true);
    Ok(Json(serde_json::json!({
        "username": username,
        "role": role,
        "csrf": csrf,
        "first_boot": first_boot,
    })))
}

/// First-boot admin creation: `POST /api/v1/auth/first-boot` (spec §4.8).
/// Allowed only when no users exist. Creates a single admin and logs the
/// caller in.
#[derive(Debug, Deserialize)]
pub struct FirstBootRequest {
    pub username: String,
    pub password: String,
}

pub async fn first_boot(
    State(app): State<AppState>,
    session: Session,
    Json(req): Json<FirstBootRequest>,
) -> ApiResult<impl IntoResponse> {
    if req.username.is_empty() || req.password.len() < 8 {
        return Err(ApiError::BadRequest(
            "username required and password >= 8 chars".into(),
        ));
    }
    let mut users = app.load_users().map_err(|_| ApiError::Internal)?;
    if !users.needs_first_boot_admin() {
        return Err(ApiError::Conflict("first-boot already completed".into()));
    }
    users
        .add(&req.username, &req.password, Role::Admin)
        .map_err(|e| ApiError::BadRequest(e.to_string()))?;
    app.save_users(&users).map_err(|_| ApiError::Internal)?;

    session.insert(SK_USER, &req.username).await.ok();
    session.insert(SK_ROLE, Role::Admin.as_str()).await.ok();

    app.audit
        .append(&webconfig_engine::audit::AuditEntry {
            ts: crate::api::now_iso(),
            user: req.username.clone(),
            action: "auth.first-boot".into(),
            detail: None,
        })
        .ok();

    Ok(Json(LoginResponse {
        username: req.username,
        role: Role::Admin.as_str().to_string(),
    }))
}

/// `require_auth` middleware: rejects unauthenticated requests, and (via the
/// first-boot gate) forces first-boot completion before any other endpoint is
/// usable. Stashes the role in a request extension for the [`AuthRole`]
/// extractor.
pub async fn require_auth(
    State(app): State<AppState>,
    session: Session,
    mut req: Request,
    next: Next,
) -> Response {
    // First-boot gate: if no admin exists yet, only /auth/first-boot and
    // /auth/whoami are usable. The router mounts this middleware on the
    // protected sub-router only, so first-boot is reachable.
    let username: Option<String> = session.get(SK_USER).await.ok().flatten();
    let role: Option<String> = session.get(SK_ROLE).await.ok().flatten();

    if username.is_none() {
        return ApiError::Unauthorized.into_response();
    }

    // Session idle timeout (spec §4.8). tower-sessions handles absolute
    // expiry via the session store TTL; idle timeout is enforced here by
    // checking the last-seen extension and refreshing it.
    let idle = idle_timeout_secs(&app);
    let last_seen: Option<u64> = session.get("last_seen").await.ok().flatten();
    let now = now_secs();
    if let Some(ls) = last_seen {
        if now.saturating_sub(ls) > idle {
            session.clear().await;
            return ApiError::Unauthorized.into_response();
        }
    }
    session.insert("last_seen", now).await.ok();

    if let Some(r) = role {
        if let Ok(role) = webconfig_engine::users::Role::parse(&r) {
            req.extensions_mut().insert(AuthRole(role));
        }
    }
    next.run(req).await
}

/// Extractor for the authenticated user's role, placed by `require_auth`.
#[derive(Debug, Clone, Copy)]
pub struct AuthRole(pub Role);

impl AuthRole {
    /// Ensure the role may perform `action`; otherwise return Forbidden.
    pub fn require_admin(&self, action: &str) -> ApiResult<()> {
        if self.0.is_admin() {
            Ok(())
        } else {
            Err(ApiError::Forbidden(self.0.as_str().into(), action.into()))
        }
    }

    pub fn require_mutate_apps(&self, action: &str) -> ApiResult<()> {
        if self.0.can_mutate_apps() {
            Ok(())
        } else {
            Err(ApiError::Forbidden(self.0.as_str().into(), action.into()))
        }
    }
}

#[axum::async_trait]
impl axum::extract::FromRequestParts<AppState> for AuthRole {
    type Rejection = ApiError;
    async fn from_request_parts(
        parts: &mut axum::http::request::Parts,
        _state: &AppState,
    ) -> Result<Self, Self::Rejection> {
        parts
            .extensions
            .get::<AuthRole>()
            .copied()
            .ok_or(ApiError::Unauthorized)
    }
}

/// Idle timeout from the current config (spec §4.8 default 15 min).
fn idle_timeout_secs(app: &AppState) -> u64 {
    app.engine
        .load()
        .map(|c| c.security.idle_timeout_secs())
        .unwrap_or(900)
}

fn now_secs() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0)
}

/// CSRF token derivation from the session id (spec §4.8). tower-sessions
/// exposes a stable session id we hash into a token; the csrf middleware
/// compares the `X-CSRF-Token` header against this value on mutating routes.
pub(crate) async fn csrf_token_from_session(session: &Session) -> String {
    let id = session.id().unwrap_or_default();
    use sha2::Digest;
    let mut h = sha2::Sha256::default();
    h.update(id.to_string().as_bytes());
    hex::encode(h.finalize())
}

#[cfg(test)]
mod tests {
    use super::*;

    async fn is_locked(t: &LoginThrottle, username: &str) -> bool {
        matches!(t.begin_attempt(username).await, AttemptDecision::Locked)
    }

    #[tokio::test]
    async fn lockout_does_not_trigger_below_threshold() {
        let t = LoginThrottle::new();
        for _ in 0..(LOCKOUT_THRESHOLD - 1) {
            t.begin_attempt("alice").await;
        }
        assert!(!is_locked(&t, "alice").await);
    }

    #[tokio::test]
    async fn lockout_triggers_at_threshold() {
        let t = LoginThrottle::new();
        for _ in 0..LOCKOUT_THRESHOLD {
            t.begin_attempt("alice").await;
        }
        assert!(is_locked(&t, "alice").await);
        // A different username is unaffected — lockout is per-username.
        assert!(!is_locked(&t, "bob").await);
    }

    #[tokio::test]
    async fn record_success_clears_lockout() {
        let t = LoginThrottle::new();
        for _ in 0..LOCKOUT_THRESHOLD {
            t.begin_attempt("alice").await;
        }
        assert!(is_locked(&t, "alice").await);
        t.record_success("alice").await;
        assert!(!is_locked(&t, "alice").await);
    }

    #[tokio::test]
    async fn concurrent_attempts_cannot_race_past_lockout() {
        // Regression test: check-then-later-record (two separate lock
        // acquisitions with unlocked work in between) let concurrent requests
        // for the same username all read "not locked" before any single
        // one's failure was recorded, bypassing the hard lockout entirely.
        // begin_attempt's atomic check-and-reserve must cap the number of
        // attempts ever allowed through at exactly LOCKOUT_THRESHOLD, no
        // matter how many fire concurrently.
        let t = Arc::new(LoginThrottle::new());
        let mut tasks = Vec::new();
        for _ in 0..(LOCKOUT_THRESHOLD as usize * 3) {
            let t = t.clone();
            tasks.push(tokio::spawn(async move { t.begin_attempt("alice").await }));
        }
        let mut allowed = 0;
        for task in tasks {
            if matches!(task.await.unwrap(), AttemptDecision::Allowed { .. }) {
                allowed += 1;
            }
        }
        assert_eq!(
            allowed, LOCKOUT_THRESHOLD as usize,
            "at most LOCKOUT_THRESHOLD attempts may ever be allowed through, regardless of concurrency"
        );
    }

    #[tokio::test]
    async fn bounded_map_evicts_oldest_when_over_capacity() {
        let t = LoginThrottle::new();
        for i in 0..(MAX_TRACKED_USERNAMES + 50) {
            t.begin_attempt(&format!("user-{i}")).await;
        }
        let size = t.attempts.lock().await.len();
        assert!(
            size <= MAX_TRACKED_USERNAMES,
            "map grew unbounded: {size} entries tracked"
        );
        // The earliest usernames should have been evicted to make room for
        // the most recent ones.
        let map = t.attempts.lock().await;
        assert!(!map.contains_key("user-0"));
        assert!(map.contains_key(&format!("user-{}", MAX_TRACKED_USERNAMES + 49)));
    }
}
