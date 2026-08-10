//! Meta handlers: board manifest + per-section schema descriptor (spec §3).
//!
//! The frontend fetches the manifest for the platform badge, the I/O page, and
//! the service allowlist; and a per-section schema descriptor for schema-driven
//! rendering of app settings. These are read-only views of state already held
//! in [`AppState`].

use axum::extract::{Path, State};
use axum::Json;
use serde::Serialize;

use crate::auth::AuthRole;
use crate::error::{ApiError, ApiResult};
use crate::state::AppState;

/// `GET /api/v1/manifest` — the board manifest (platform, peripherals, tiles,
/// service allowlist, trust flags). Read-only (spec §3 platform layer).
pub async fn manifest(
    State(app): State<AppState>,
    _role: AuthRole,
) -> ApiResult<Json<webconfig_engine::manifest::BoardManifest>> {
    Ok(Json((*app.manifest).clone()))
}

/// `GET /api/v1/schemas` is in [`super::services`]; this is the per-section
/// variant: `GET /api/v1/schemas/:section` — one descriptor for the renderer.
pub async fn schema_section(
    State(app): State<AppState>,
    _role: AuthRole,
    Path(section): Path<String>,
) -> ApiResult<Json<SchemaResponse>> {
    for (id, desc) in app.schema.descriptors() {
        if id == &section {
            return Ok(Json(SchemaResponse {
                id: id.clone(),
                descriptor: serde_json::to_value(desc).unwrap_or_default(),
            }));
        }
    }
    Err(ApiError::NotFound)
}

#[derive(Debug, Serialize)]
pub struct SchemaResponse {
    pub id: String,
    pub descriptor: serde_json::Value,
}
