//! TLS (spec §4.8).
//!
//! Per-device self-signed cert auto-generated on first boot via rcgen — never
//! a shared baked-in cert. Supports upload of a customer cert/key and CSR
//! generation. HTTP→HTTPS redirect on by default; plain HTTP only as an
//! explicit admin opt-in. No OpenSSL anywhere (spec §2).

use std::path::Path;
use std::sync::Arc;

use rustls::pki_types::{CertificateDer, PrivateKeyDer};
use rustls::ServerConfig;
use thiserror::Error;

#[derive(Debug, Error)]
pub enum TlsError {
    #[error("io error: {0}")]
    Io(#[from] std::io::Error),
    #[error("rcgen error: {0}")]
    Rcgen(String),
    #[error("pem parse error: {0}")]
    Pem(String),
}

/// Ensure a self-signed cert + key exist at `cert_path` / `key_path`; generate
/// them on first boot (spec §4.8). The subject CN is the device hostname when
/// available, else "webconfig".
pub fn ensure_self_signed(
    cert_path: &Path,
    key_path: &Path,
    hostname: Option<&str>,
) -> Result<(), TlsError> {
    if cert_path.exists() && key_path.exists() {
        return Ok(());
    }
    if let Some(parent) = cert_path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    if let Some(parent) = key_path.parent() {
        std::fs::create_dir_all(parent)?;
    }

    // rcgen: generate a self-signed cert. We use the minimal params that build
    // on all three targets (ring provider, no OpenSSL).
    let params = {
        let mut p =
            rcgen::CertificateParams::new(vec![hostname.unwrap_or("webconfig").to_string()])
                .map_err(|e| TlsError::Rcgen(e.to_string()))?;
        p.distinguished_name = rcgen::DistinguishedName::new();
        p.distinguished_name
            .push(rcgen::DnType::CommonName, hostname.unwrap_or("webconfig"));
        p
    };
    let key_pair = rcgen::KeyPair::generate_for(&rcgen::PKCS_ECDSA_P256_SHA256)
        .map_err(|e| TlsError::Rcgen(e.to_string()))?;
    let cert = params
        .self_signed(&key_pair)
        .map_err(|e| TlsError::Rcgen(e.to_string()))?;
    std::fs::write(cert_path, cert.pem())?;
    std::fs::write(key_path, key_pair.serialize_pem())?;
    tracing::info!("generated self-signed TLS cert at {}", cert_path.display());
    Ok(())
}

/// Build a `rustls` ServerConfig from PEM cert + key files.
pub fn server_config(cert_path: &Path, key_path: &Path) -> Result<ServerConfig, TlsError> {
    let cert_pem = std::fs::read(cert_path)?;
    let key_pem = std::fs::read(key_path)?;
    server_config_from_pem(&cert_pem, &key_pem)
}

/// Build a `rustls` ServerConfig from in-memory PEM cert + key bytes. Used by
/// the customer-cert upload route to validate a candidate pair **before**
/// writing it to disk — `with_single_cert` rejects a cert/key mismatch and an
/// unsupported key algorithm, so a bad upload can never leave the listener
/// unable to start on the next boot (spec §4.8: "upload of customer cert/key").
pub fn server_config_from_pem(
    mut cert_pem: &[u8],
    mut key_pem: &[u8],
) -> Result<ServerConfig, TlsError> {
    let certs: Vec<CertificateDer<'static>> = rustls_pemfile::certs(&mut cert_pem)
        .collect::<Result<Vec<_>, _>>()
        .map_err(|e| TlsError::Pem(e.to_string()))?;
    if certs.is_empty() {
        return Err(TlsError::Pem("no certificate in pem".into()));
    }

    let key: PrivateKeyDer<'static> = rustls_pemfile::private_key(&mut key_pem)
        .map_err(|e| TlsError::Pem(e.to_string()))?
        .ok_or_else(|| TlsError::Pem("no private key in pem".into()))?;

    ServerConfig::builder()
        .with_no_client_auth()
        .with_single_cert(certs, key)
        .map_err(|e| TlsError::Pem(e.to_string()))
}

/// An acceptor wrapping a ServerConfig for tokio-rustls.
pub fn acceptor(cert_path: &Path, key_path: &Path) -> Result<Arc<ServerConfig>, TlsError> {
    Ok(Arc::new(server_config(cert_path, key_path)?))
}

/// Generate a CSR (PEM) for a customer CA to sign (spec §4.8). Uses a fresh
/// keypair; the private key is returned alongside so the caller can store it.
pub fn generate_csr(cn: &str) -> Result<(String, String), TlsError> {
    let mut params = rcgen::CertificateParams::new(vec![cn.to_string()])
        .map_err(|e| TlsError::Rcgen(e.to_string()))?;
    params.distinguished_name = rcgen::DistinguishedName::new();
    params
        .distinguished_name
        .push(rcgen::DnType::CommonName, cn);
    let key_pair = rcgen::KeyPair::generate_for(&rcgen::PKCS_ECDSA_P256_SHA256)
        .map_err(|e| TlsError::Rcgen(e.to_string()))?;
    let csr = params
        .serialize_request(&key_pair)
        .map_err(|e| TlsError::Rcgen(e.to_string()))?
        .pem()
        .map_err(|e| TlsError::Rcgen(e.to_string()))?;
    let key = key_pair.serialize_pem();
    Ok((csr, key))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn generates_self_signed_on_first_boot() {
        let d = tempfile::tempdir().unwrap();
        let cert = d.path().join("cert.pem");
        let key = d.path().join("key.pem");
        ensure_self_signed(&cert, &key, Some("zion-01")).unwrap();
        assert!(cert.exists());
        assert!(key.exists());
        // Second call is a no-op.
        let before = std::fs::metadata(&cert).unwrap().modified().unwrap();
        ensure_self_signed(&cert, &key, Some("zion-01")).unwrap();
        let after = std::fs::metadata(&cert).unwrap().modified().unwrap();
        assert_eq!(before, after, "existing cert must not be overwritten");
    }

    #[test]
    fn builds_server_config_from_pem() {
        let d = tempfile::tempdir().unwrap();
        let cert = d.path().join("cert.pem");
        let key = d.path().join("key.pem");
        ensure_self_signed(&cert, &key, None).unwrap();
        let cfg = server_config(&cert, &key).unwrap();
        let _ = acceptor(&cert, &key).unwrap();
        // ServerConfig is debug-printable enough to ensure it built.
        drop(cfg);
    }

    #[test]
    fn csr_generation_returns_pem_pair() {
        let (csr, key) = generate_csr("zion-01").unwrap();
        assert!(csr.contains("BEGIN CERTIFICATE REQUEST"));
        assert!(key.contains("BEGIN PRIVATE KEY"));
    }

    #[test]
    fn rejects_cert_key_mismatch() {
        // Two independent self-signed identities — their cert and key do NOT
        // pair, so a customer cert uploaded against the wrong key must fail
        // validation rather than silently bricking the listener on reboot.
        let d = tempfile::tempdir().unwrap();
        let a = (d.path().join("a.pem"), d.path().join("a.key"));
        let b = (d.path().join("b.pem"), d.path().join("b.key"));
        ensure_self_signed(&a.0, &a.1, None).unwrap();
        ensure_self_signed(&b.0, &b.1, None).unwrap();
        let cert_a = std::fs::read(&a.0).unwrap();
        let key_b = std::fs::read(&b.1).unwrap();
        assert!(server_config_from_pem(&cert_a, &key_b).is_err());
        // The matching pair must still succeed.
        let key_a = std::fs::read(&a.1).unwrap();
        assert!(server_config_from_pem(&cert_a, &key_a).is_ok());
    }

    #[test]
    fn rejects_pem_with_no_cert() {
        let d = tempfile::tempdir().unwrap();
        let cert = d.path().join("c.pem");
        let key = d.path().join("k.pem");
        ensure_self_signed(&cert, &key, None).unwrap();
        let key_pem = std::fs::read(&key).unwrap();
        // Key-only PEM, no cert -> error, not a silent half-written identity.
        assert!(server_config_from_pem(&key_pem, &key_pem).is_err());
    }
}
