//! API error → HTTP status mapping (spec §2: security boundary is the API;
//! error messages never leak internals).

use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use axum::Json;
use serde_json::json;

#[derive(Debug, thiserror::Error)]
pub enum ApiError {
    #[error("not found")]
    NotFound,
    #[error("bad request: {0}")]
    BadRequest(String),
    #[error("unauthorized")]
    Unauthorized,
    #[error("forbidden — role {0} may not perform {1}")]
    Forbidden(String, String),
    #[error("conflict: {0}")]
    Conflict(String),
    #[error("not ready: {0}")]
    NotReady(String),
    #[error("engine error: {0}")]
    Engine(String),
    #[error("internal error")]
    Internal,
}

impl IntoResponse for ApiError {
    fn into_response(self) -> Response {
        let (status, msg) = match &self {
            ApiError::NotFound => (StatusCode::NOT_FOUND, "not found".to_string()),
            ApiError::BadRequest(m) => (StatusCode::BAD_REQUEST, m.clone()),
            ApiError::Unauthorized => (StatusCode::UNAUTHORIZED, "unauthorized".to_string()),
            ApiError::Forbidden(role, action) => (
                StatusCode::FORBIDDEN,
                format!("role {role} may not {action}"),
            ),
            ApiError::Conflict(m) => (StatusCode::CONFLICT, m.clone()),
            ApiError::NotReady(m) => (StatusCode::SERVICE_UNAVAILABLE, m.clone()),
            ApiError::Engine(m) => (StatusCode::UNPROCESSABLE_ENTITY, m.clone()),
            ApiError::Internal => (StatusCode::INTERNAL_SERVER_ERROR, "internal error".into()),
        };
        // Log the detailed error server-side; return a generic-ish message to
        // the client (spec: never expose internal paths/stacks).
        match status {
            StatusCode::INTERNAL_SERVER_ERROR => tracing::error!(error = %self, "internal"),
            _ => tracing::info!(error = %self, "api error"),
        }
        (status, Json(json!({ "error": msg }))).into_response()
    }
}

impl From<webconfig_engine::StoreError> for ApiError {
    fn from(e: webconfig_engine::StoreError) -> Self {
        match e {
            webconfig_engine::StoreError::Validation(m) => ApiError::BadRequest(m),
            webconfig_engine::StoreError::NotFound(_) => ApiError::NotFound,
            webconfig_engine::StoreError::Version { .. } => ApiError::Conflict(e.to_string()),
            other => ApiError::Engine(other.to_string()),
        }
    }
}

impl From<webconfig_engine::ApplyError> for ApiError {
    fn from(e: webconfig_engine::ApplyError) -> Self {
        match e {
            webconfig_engine::ApplyError::Validation { message, .. } => {
                ApiError::BadRequest(message)
            }
            other => ApiError::Engine(other.to_string()),
        }
    }
}

impl From<std::io::Error> for ApiError {
    fn from(e: std::io::Error) -> Self {
        // The client only ever sees the generic "internal error" (never leak
        // internal paths/details), but the real cause must survive server-side
        // for field debugging — it was previously discarded entirely.
        tracing::error!(error = %e, "io error converted to ApiError::Internal");
        ApiError::Internal
    }
}

impl From<crate::systemd::SystemdError> for ApiError {
    fn from(e: crate::systemd::SystemdError) -> Self {
        ApiError::Engine(e.to_string())
    }
}

impl From<webconfig_engine::users::UserError> for ApiError {
    fn from(e: webconfig_engine::users::UserError) -> Self {
        match e {
            webconfig_engine::users::UserError::NotFound(_) => ApiError::NotFound,
            webconfig_engine::users::UserError::Exists(_) => ApiError::Conflict(e.to_string()),
            webconfig_engine::users::UserError::GroupExists(_) => ApiError::Conflict(e.to_string()),
            webconfig_engine::users::UserError::MembershipExists(_) => {
                ApiError::Conflict(e.to_string())
            }
            webconfig_engine::users::UserError::LastAdmin => ApiError::Conflict(e.to_string()),
            webconfig_engine::users::UserError::InvalidCredentials => ApiError::Unauthorized,
            webconfig_engine::users::UserError::InvalidRole(_) => {
                ApiError::BadRequest(e.to_string())
            }
            other => ApiError::BadRequest(other.to_string()),
        }
    }
}

pub type ApiResult<T> = Result<T, ApiError>;
