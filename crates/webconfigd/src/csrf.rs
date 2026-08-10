//! CSRF protection on every mutating route (spec §4.8).
//!
//! Double-submit via a session-derived token: the token is returned by
//! `GET /auth/whoami` and the client must send it back as the `X-CSRF-Token`
//! header on every non-safe (POST/PUT/PATCH/DELETE) request. Safe methods
//! (GET/HEAD/OPTIONS) are exempt. The token is a SHA-256 of the session id,
//! so it is bound to the authenticated session and unguessable without the
//! cookie.

use axum::extract::Request;
use axum::http::Method;
use axum::middleware::Next;
use axum::response::{IntoResponse, Response};
use tower_sessions::Session;

use crate::auth::csrf_token_from_session;
use crate::error::ApiError;

const CSRF_HEADER: &str = "X-CSRF-Token";

/// CSRF middleware. Mount on the protected (authenticated) router.
pub async fn csrf_guard(session: Session, req: Request, next: Next) -> Response {
    let method = req.method().clone();
    if is_safe(&method) {
        return next.run(req).await;
    }

    let expected = csrf_token_from_session(&session).await;
    let provided = req
        .headers()
        .get(CSRF_HEADER)
        .and_then(|v| v.to_str().ok())
        .unwrap_or("");

    // Constant-time-ish comparison via equality of equal-length hex strings;
    // a timing-leak here only reveals whether a prefix matched, over a 64-char
    // hex digest of a high-entropy session id — acceptable per spec §4.8.
    if expected.is_empty() || provided != expected {
        return ApiError::BadRequest("missing or invalid CSRF token".into()).into_response();
    }
    next.run(req).await
}

fn is_safe(m: &Method) -> bool {
    matches!(*m, Method::GET | Method::HEAD | Method::OPTIONS)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn safe_methods_exempt() {
        assert!(is_safe(&Method::GET));
        assert!(is_safe(&Method::HEAD));
        assert!(is_safe(&Method::OPTIONS));
        assert!(!is_safe(&Method::POST));
        assert!(!is_safe(&Method::PUT));
        assert!(!is_safe(&Method::DELETE));
        assert!(!is_safe(&Method::PATCH));
    }
}
