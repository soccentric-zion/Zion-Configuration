//! Security handlers (spec §4.8): TLS/cert store, WireGuard, exposure summary,
//! trust status (read-only), session/audit config.

use axum::extract::State;
use axum::Json;
use tower_sessions::Session;
use webconfig_engine::store::security::SecurityConfig;

use crate::api::{apply_and_audit, audit, username};
use crate::auth::AuthRole;
use crate::error::{ApiError, ApiResult};
use crate::state::AppState;

/// `GET /api/v1/security` — security section (spec §4.8).
pub async fn get(State(app): State<AppState>, _role: AuthRole) -> ApiResult<Json<SecurityConfig>> {
    Ok(Json(app.engine.load()?.security))
}

/// `PUT /api/v1/security` — idle timeout / plain-HTTP opt-in / WireGuard
/// config. Admin only. WireGuard apply happens on the next `POST /security/apply`.
pub async fn put(
    State(app): State<AppState>,
    role: AuthRole,
    session: Session,
    Json(req): Json<SecurityConfig>,
) -> ApiResult<Json<SecurityConfig>> {
    role.require_admin("security.put")?;
    let user = username(&session).await;
    let cfg = app.engine.update(move |c| {
        c.security = req.clone();
        Ok(())
    })?;
    audit(&app, &user, "security.put", None);
    Ok(Json(cfg.security))
}

/// `POST /api/v1/security/apply` — apply the WireGuard applier (spec §4.8).
pub async fn apply(
    State(app): State<AppState>,
    role: AuthRole,
    session: Session,
) -> ApiResult<Json<serde_json::Value>> {
    role.require_admin("security.apply")?;
    let user = username(&session).await;
    app.engine.validate("wireguard").await?;
    let _ = apply_and_audit(&app, &user, "wireguard").await?;
    Ok(Json(serde_json::json!({ "applied": ["wireguard"] })))
}

/// `GET /api/v1/security/wireguard/pubkey` — public key only; the private key
/// never leaves the device (spec §4.8).
pub async fn wg_pubkey(
    State(app): State<AppState>,
    _role: AuthRole,
) -> ApiResult<Json<serde_json::Value>> {
    let key_path = app.engine.sysroot.webconfig_dir().join("wireguard.key");
    let has = key_path.exists();
    // Derive the public key from the on-disk private key with `wg pubkey`.
    // If no key exists yet, return empty so the UI can prompt to enable WG.
    let pubkey = if has && app.engine.sysroot.is_real_root() {
        let privkey = std::fs::read_to_string(&key_path).unwrap_or_default();
        use std::io::Write;
        let mut child = std::process::Command::new("wg")
            .arg("pubkey")
            .stdin(std::process::Stdio::piped())
            .stdout(std::process::Stdio::piped())
            .spawn()
            .map_err(|_| ApiError::Internal)?;
        if let Some(stdin) = child.stdin.as_mut() {
            let _ = stdin.write_all(privkey.as_bytes());
        }
        let out = child.wait_with_output().map_err(|_| ApiError::Internal)?;
        String::from_utf8_lossy(&out.stdout).trim().to_string()
    } else {
        String::new()
    };
    Ok(Json(
        serde_json::json!({ "public_key": pubkey, "has_private_key": has }),
    ))
}

/// `POST /api/v1/security/csr` — generate a CSR (spec §4.8). Returns the PEM
/// CSR + the private key so the admin can store it in the cert store.
pub async fn generate_csr(
    State(app): State<AppState>,
    role: AuthRole,
    session: Session,
    Json(req): Json<CsrRequest>,
) -> ApiResult<Json<serde_json::Value>> {
    role.require_admin("security.csr")?;
    let user = username(&session).await;
    let (csr, key) =
        crate::tls::generate_csr(&req.cn).map_err(|e| ApiError::BadRequest(e.to_string()))?;
    audit(&app, &user, "security.csr", Some(req.cn.clone()));
    Ok(Json(serde_json::json!({ "csr": csr, "private_key": key })))
}

#[derive(serde::Deserialize)]
pub struct CsrRequest {
    pub cn: String,
}

/// `POST /api/v1/security/tls` — replace the device TLS identity with a
/// customer-provided cert/key pair (spec §4.8: "upload of customer cert/key").
/// The pair is validated in memory by building a real `rustls` ServerConfig —
/// `with_single_cert` rejects a cert/key mismatch or an unsupported key
/// algorithm, so a bad upload can never leave the listener unable to start on
/// the next boot. On success both files are atomically replaced (write temp +
/// rename). Admin only; audited.
///
/// Stated v1 assumption: the running listener holds the prior `ServerConfig`
/// in memory, so the new identity takes effect on the next daemon restart (or
/// reboot), not mid-session. Surfacing that to the operator is a frontend job.
pub async fn upload_tls_identity(
    State(app): State<AppState>,
    role: AuthRole,
    session: Session,
    Json(req): Json<TlsUploadRequest>,
) -> ApiResult<Json<serde_json::Value>> {
    role.require_admin("security.tls.upload")?;
    if req.cert.trim().is_empty() || req.key.trim().is_empty() {
        return Err(ApiError::BadRequest(
            "cert and key are both required".into(),
        ));
    }
    // Validate the pair before touching disk — never write a half-identity.
    crate::tls::server_config_from_pem(req.cert.as_bytes(), req.key.as_bytes())
        .map_err(|e| ApiError::BadRequest(format!("invalid cert/key pair: {e}")))?;

    let dir = app.engine.sysroot.webconfig_dir().to_path_buf();
    let cert_path = dir.join("cert.pem");
    let key_path = dir.join("key.pem");
    std::fs::create_dir_all(&dir).map_err(|_| ApiError::Internal)?;
    // Atomic replace: write to sibling temp files, fsync, then rename onto the
    // real paths. The window between the two renames is two syscalls; a crash
    // there is no worse than the pre-existing first-boot case (ensure_self_signed
    // regenerates). Order: key first, then cert — a restart sees a valid pair
    // only once both land, and a half-swapped pair fails loud on boot rather
    // than silently serving the wrong identity.
    let cert_tmp = dir.join("cert.pem.new");
    let key_tmp = dir.join("key.pem.new");
    {
        use std::io::Write;
        let mut f = std::fs::File::create(&cert_tmp).map_err(|_| ApiError::Internal)?;
        f.write_all(req.cert.as_bytes())
            .map_err(|_| ApiError::Internal)?;
        f.sync_all().map_err(|_| ApiError::Internal)?;
        let mut f = std::fs::File::create(&key_tmp).map_err(|_| ApiError::Internal)?;
        f.write_all(req.key.as_bytes())
            .map_err(|_| ApiError::Internal)?;
        f.sync_all().map_err(|_| ApiError::Internal)?;
    }
    // 0o600 on the private key — it must not be world-readable.
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(&key_tmp, std::fs::Permissions::from_mode(0o600))
            .map_err(|_| ApiError::Internal)?;
    }
    std::fs::rename(&key_tmp, &key_path).map_err(|_| ApiError::Internal)?;
    std::fs::rename(&cert_tmp, &cert_path).map_err(|_| ApiError::Internal)?;

    let user = username(&session).await;
    audit(&app, &user, "security.tls.upload", None);
    Ok(Json(serde_json::json!({
        "ok": true,
        "restart_required": true,
    })))
}

#[derive(serde::Deserialize)]
pub struct TlsUploadRequest {
    /// PEM certificate chain (leaf first, then intermediates if any).
    pub cert: String,
    /// PEM private key matching the leaf cert.
    pub key: String,
}

/// `GET /api/v1/security/exposure` — one glanceable statement of currently
/// reachable services per interface (spec §4.8), derived from firewall +
/// service state.
pub async fn exposure(
    State(app): State<AppState>,
    _role: AuthRole,
) -> ApiResult<Json<serde_json::Value>> {
    let cfg = app.engine.load()?;
    let mut per_iface = Vec::new();
    for iface in &cfg.network.interfaces {
        if !iface.enabled {
            continue;
        }
        per_iface.push(serde_json::json!({
            "interface": iface.name,
            "open_services": iface.open_services,
        }));
    }
    Ok(Json(serde_json::json!({
        "ui_always_reachable": true,
        "plain_http": cfg.security.allow_plain_http,
        "per_interface": per_iface,
    })))
}

/// The BSP platform-status file (spec §4.8: "verity/encryption status — read
/// from the BSP platform-status file"). Written by BSP/OTA tooling at
/// `<share_dir>/platform-status.json` (the same read-only BSP-provided data
/// directory as manifests/schemas); absent on any platform whose BSP hasn't
/// implemented it yet, and always absent on the fake sysroot.
#[derive(Debug, Clone, Default, serde::Deserialize)]
struct PlatformStatus {
    #[serde(default)]
    update_signing_key_id: Option<String>,
    #[serde(default)]
    verity_status: Option<String>,
    #[serde(default)]
    encryption_status: Option<String>,
}

/// Read the platform-status file. `None` means "not available on this
/// platform" — distinct from a value that failed to read after the file was
/// found (which is logged and also treated as unavailable, since a corrupt
/// trust-status source cannot be trusted either).
fn read_platform_status(sysroot: &webconfig_engine::Sysroot) -> Option<PlatformStatus> {
    let path = sysroot.share_dir().join("platform-status.json");
    let raw = std::fs::read_to_string(&path).ok()?;
    match serde_json::from_str(&raw) {
        Ok(status) => Some(status),
        Err(e) => {
            tracing::warn!(error = %e, path = %path.display(), "platform-status.json present but failed to parse; trust status reported as unavailable");
            None
        }
    }
}

/// `GET /api/v1/security/trust` — read-only trust status from the BSP
/// platform-status file / manifest (spec §4.8). Never configurable from the UI.
pub async fn trust(
    State(app): State<AppState>,
    _role: AuthRole,
) -> ApiResult<Json<serde_json::Value>> {
    let m = &app.manifest;
    let status = read_platform_status(&app.engine.sysroot);
    Ok(Json(serde_json::json!({
        "secure_boot": m.secure_boot,
        "tpm_present": m.has_tpm,
        // Distinct from a bare "unknown" value: this tells the UI whether the
        // three fields below are a real read or simply not available on this
        // platform yet, so a missing BSP source is never mistaken for a
        // completed read that happened to come back null.
        "platform_status_available": status.is_some(),
        "update_signing_key_id": status.as_ref().and_then(|s| s.update_signing_key_id.clone()),
        "verity_status": status.as_ref().and_then(|s| s.verity_status.clone()),
        "encryption_status": status.as_ref().and_then(|s| s.encryption_status.clone()),
    })))
}

/// `GET /api/v1/security/certs` — device cert store metadata (spec §4.8).
pub async fn certs(
    State(app): State<AppState>,
    _role: AuthRole,
) -> ApiResult<Json<Vec<webconfig_engine::store::security::CertMeta>>> {
    Ok(Json(app.engine.load()?.security.certificates))
}

#[derive(serde::Deserialize)]
pub struct CertUploadRequest {
    pub id: String,
    pub kind: String, // "ca" | "client"
    pub subject: String,
    /// Cert expiry, ISO-8601 (`YYYY-MM-DD` or `YYYY-MM-DDTHH:MM:SSZ`). Operator-
    /// supplied because the daemon has no X.509 parser in its vendored supply
    /// chain (rustls_pemfile only extracts DER blobs); it is shown in the UI and
    /// feeds the cert-expiry alert (spec §4.8). The PEM itself is validated below.
    pub not_after: String,
    pub pem: String,
}

/// The on-disk cert store: `<webconfig_dir>/certs/<id>.pem`. Created on demand.
fn cert_store_dir(app: &AppState) -> std::path::PathBuf {
    app.engine.sysroot.webconfig_dir().join("certs")
}

/// A cert id must be a safe filename component — alnum, dash, underscore only —
/// so it can never escape the `certs/` directory via `..` or a path separator.
fn validate_cert_id(id: &str) -> ApiResult<()> {
    if id.is_empty()
        || !id
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || c == '-' || c == '_')
    {
        return Err(ApiError::BadRequest(
            "cert id must be non-empty alnum/-/_ only".into(),
        ));
    }
    Ok(())
}

/// Confirm the PEM actually contains at least one certificate (defends against
/// storing a key-only or garbage blob as a "cert"). Uses the already-vendored
/// `rustls_pemfile` — no new X.509 dependency.
fn validate_pem_cert(pem: &str) -> ApiResult<()> {
    let mut cursor = pem.as_bytes();
    let mut found = false;
    for item in rustls_pemfile::certs(&mut cursor) {
        item.map_err(|e| ApiError::BadRequest(format!("pem parse error: {e}")))?;
        found = true;
    }
    if !found {
        return Err(ApiError::BadRequest("no certificate found in pem".into()));
    }
    Ok(())
}

/// `POST /api/v1/security/certs` — upload a CA or client cert PEM into the
/// device cert store (spec §4.8: "CA + client certs for MQTT/VPN, uploaded
/// here, referenced elsewhere"). The PEM is written to `certs/<id>.pem` and its
/// metadata upserted into `config.security.certificates` so `/security/certs`
/// and the cert-expiry alert reflect it. Admin only.
pub async fn upload_cert(
    State(app): State<AppState>,
    role: AuthRole,
    session: Session,
    Json(req): Json<CertUploadRequest>,
) -> ApiResult<Json<webconfig_engine::store::security::CertMeta>> {
    role.require_admin("security.certs.upload")?;
    validate_cert_id(&req.id)?;
    if req.kind != "ca" && req.kind != "client" {
        return Err(ApiError::BadRequest("kind must be ca|client".into()));
    }
    if req.subject.trim().is_empty() {
        return Err(ApiError::BadRequest("subject required".into()));
    }
    if !looks_like_iso_date(&req.not_after) {
        return Err(ApiError::BadRequest(
            "not_after must be ISO-8601 (YYYY-MM-DD or YYYY-MM-DDTHH:MM:SSZ)".into(),
        ));
    }
    validate_pem_cert(&req.pem)?;
    let dir = cert_store_dir(&app);
    std::fs::create_dir_all(&dir).map_err(|_| ApiError::Internal)?;
    std::fs::write(dir.join(format!("{}.pem", req.id)), &req.pem)
        .map_err(|_| ApiError::Internal)?;
    let meta = webconfig_engine::store::security::CertMeta {
        id: req.id.clone(),
        kind: req.kind.clone(),
        subject: req.subject.clone(),
        not_after: req.not_after.clone(),
    };
    let user = username(&session).await;
    let id_for_closure = req.id.clone();
    let meta_for_closure = meta.clone();
    app.engine.update(move |c| {
        // Upsert by id — re-uploading a same-id cert replaces it.
        if let Some(existing) = c
            .security
            .certificates
            .iter_mut()
            .find(|m| m.id == id_for_closure)
        {
            *existing = meta_for_closure.clone();
        } else {
            c.security.certificates.push(meta_for_closure.clone());
        }
        Ok(())
    })?;
    audit(
        &app,
        &user,
        "security.certs.upload",
        Some(format!("{}:{}", req.kind, req.id)),
    );
    Ok(Json(meta))
}

/// `DELETE /api/v1/security/certs/:id` — remove a cert from the store (spec
/// §4.8). Deletes the PEM file and the metadata entry. Admin only.
pub async fn delete_cert(
    State(app): State<AppState>,
    role: AuthRole,
    session: Session,
    axum::extract::Path(id): axum::extract::Path<String>,
) -> ApiResult<Json<serde_json::Value>> {
    role.require_admin("security.certs.delete")?;
    validate_cert_id(&id)?;
    let pem_path = cert_store_dir(&app).join(format!("{id}.pem"));
    let _ = std::fs::remove_file(&pem_path);
    // NotFound if the id was never in the store (the PEM file removal is
    // best-effort either way, so a stray file is still cleaned up).
    let exists = app
        .engine
        .load()?
        .security
        .certificates
        .iter()
        .any(|m| m.id == id);
    if !exists {
        return Err(ApiError::NotFound);
    }
    let user = username(&session).await;
    let id_for_closure = id.clone();
    app.engine.update(move |c| {
        c.security.certificates.retain(|m| m.id != id_for_closure);
        Ok(())
    })?;
    audit(&app, &user, "security.certs.delete", Some(id.clone()));
    Ok(Json(serde_json::json!({ "deleted": id })))
}

/// Lenient ISO-8601 date shape check: accepts `YYYY-MM-DD` and a full
/// `YYYY-MM-DDTHH:MM:SSZ` stamp. The cert-expiry alert parses the same shape.
fn looks_like_iso_date(s: &str) -> bool {
    let s = s.trim();
    s.len() >= 10
        && s[4..5].contains('-')
        && s[7..8].contains('-')
        && s[0..4].chars().all(|c| c.is_ascii_digit())
        && s[5..7].chars().all(|c| c.is_ascii_digit())
        && s[8..10].chars().all(|c| c.is_ascii_digit())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn valid_pem() -> String {
        // Generate a real self-signed cert PEM via rcgen (already vendored) so
        // validate_pem_cert sees a genuine certificate, not a placeholder.
        let mut params = rcgen::CertificateParams::new(vec!["ca.example".to_string()]).unwrap();
        params.distinguished_name = rcgen::DistinguishedName::new();
        params
            .distinguished_name
            .push(rcgen::DnType::CommonName, "ca.example");
        let key_pair = rcgen::KeyPair::generate_for(&rcgen::PKCS_ECDSA_P256_SHA256).unwrap();
        params.self_signed(&key_pair).unwrap().pem()
    }

    #[test]
    fn accepts_a_real_cert_pem() {
        assert!(validate_pem_cert(&valid_pem()).is_ok());
    }

    #[test]
    fn rejects_non_cert_pem() {
        // A private key alone is not a cert.
        let key_pair = rcgen::KeyPair::generate_for(&rcgen::PKCS_ECDSA_P256_SHA256).unwrap();
        let key_pem = key_pair.serialize_pem();
        assert!(validate_pem_cert(&key_pem).is_err());
        assert!(validate_pem_cert("not a pem at all").is_err());
    }

    #[test]
    fn cert_id_must_be_safe_filename() {
        assert!(validate_cert_id("ca-root").is_ok());
        assert!(validate_cert_id("mqtt_client_1").is_ok());
        // Path traversal and separators rejected — can never escape certs/.
        assert!(validate_cert_id("").is_err());
        assert!(validate_cert_id("../etc").is_err());
        assert!(validate_cert_id("ca/root").is_err());
        assert!(validate_cert_id("ca root").is_err());
    }

    #[test]
    fn iso_date_shape_check() {
        assert!(looks_like_iso_date("2026-08-23"));
        assert!(looks_like_iso_date("2026-08-23T12:00:00Z"));
        assert!(!looks_like_iso_date("2026/08/23"));
        assert!(!looks_like_iso_date("soon"));
        assert!(!looks_like_iso_date("2026-8-3"));
    }
}
