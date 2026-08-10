//! Passphrase-encrypted config backup (spec §4.4).
//!
//! Secrets are **excluded by default** from backups/diagnostics. They may be
//! included *only* inside a passphrase-encrypted backup blob (spec §4.4). This
//! module implements that encryption: Argon2id derives a 32-byte key from the
//! user's passphrase + a fresh random salt, and ChaCha20-Poly1305 (ring AEAD)
//! authenticated-encrypts the config payload with a fresh random nonce. The
//! envelope is base64 so it serializes cleanly to JSON for download.
//!
//! The engine is HTTP-free and async-free (spec §6): this is pure, testable
//! crypto with no I/O, so the round-trip is covered by a unit test.

use base64::Engine as _;

/// Envelope format tag — bumped only if the envelope shape changes.
const ENCRYPTED_BACKUP_V1: &str = "webconfig-encrypted-backup-v1";

/// Argon2id memory cost (19 MiB), tuned for an embedded device: enough to slow
/// offline brute force, low enough not to threaten the < 15 MB resident budget
/// for the short, one-shot derivation.
const ARGON2_M_KIB: u32 = 19 * 1024;
const ARGON2_T: u32 = 2;
const ARGON2_P: u32 = 1;
const KEY_LEN: usize = 32;
const SALT_LEN: usize = 16;

#[derive(Debug, thiserror::Error)]
pub enum BackupError {
    #[error("backup crypto error: {0}")]
    Crypto(String),
    #[error("backup base64 error: {0}")]
    Base64(#[from] base64::DecodeError),
}

/// The encrypted backup envelope. All binary fields are base64 (STANDARD) so
/// the whole struct serializes to JSON for download and restore upload.
#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub struct EncryptedBackup {
    /// Envelope format tag (`webconfig-encrypted-backup-v1`).
    pub format: String,
    /// KDF used (`argon2id`).
    pub kdf: String,
    /// base64 Argon2id salt (16 bytes).
    pub salt: String,
    /// base64 ChaCha20-Poly1305 nonce (12 bytes).
    pub nonce: String,
    /// base64 ciphertext + Poly1305 auth tag.
    pub ciphertext: String,
}

/// Encrypt a config payload with a passphrase (spec §4.4). Returns a base64
/// envelope safe to serialize to JSON. A fresh salt and nonce are drawn from
/// `ring`'s CSRNG on every call, so re-encrypting the same payload with the
/// same passphrase still yields a distinct ciphertext.
pub fn encrypt(payload: &[u8], passphrase: &str) -> Result<EncryptedBackup, BackupError> {
    use ring::aead;
    use ring::rand::SecureRandom;

    let rng = ring::rand::SystemRandom::new();
    let mut salt = [0u8; SALT_LEN];
    rng.fill(&mut salt)
        .map_err(|_| BackupError::Crypto("salt generation failed".into()))?;

    let key_bytes = derive_key(passphrase, &salt)?;

    let alg = &aead::CHACHA20_POLY1305;
    let mut nonce = [0u8; 12];
    rng.fill(&mut nonce)
        .map_err(|_| BackupError::Crypto("nonce generation failed".into()))?;

    let unbound = aead::UnboundKey::new(alg, &key_bytes)
        .map_err(|_| BackupError::Crypto("aead key init failed".into()))?;
    let key = aead::LessSafeKey::new(unbound);

    // seal_in_place_append_tag requires a Vec it can extend with the auth tag.
    let mut in_out = payload.to_vec();
    let nonce_obj = aead::Nonce::assume_unique_for_key(nonce);
    key.seal_in_place_append_tag(nonce_obj, aead::Aad::empty(), &mut in_out)
        .map_err(|_| BackupError::Crypto("encryption (seal) failed".into()))?;

    let b64 = base64::engine::general_purpose::STANDARD;
    Ok(EncryptedBackup {
        format: ENCRYPTED_BACKUP_V1.into(),
        kdf: "argon2id".into(),
        salt: b64.encode(salt),
        nonce: b64.encode(nonce),
        ciphertext: b64.encode(&in_out),
    })
}

/// Decrypt an envelope produced by [`encrypt`] (used by restore to recover a
/// secret-bearing backup). Returns the plaintext config payload.
pub fn decrypt(env: &EncryptedBackup, passphrase: &str) -> Result<Vec<u8>, BackupError> {
    use ring::aead;

    if env.format != ENCRYPTED_BACKUP_V1 {
        return Err(BackupError::Crypto(format!(
            "unsupported backup format: {}",
            env.format
        )));
    }
    if env.kdf != "argon2id" {
        return Err(BackupError::Crypto(format!("unsupported kdf: {}", env.kdf)));
    }
    let b64 = base64::engine::general_purpose::STANDARD;
    let salt = b64.decode(&env.salt)?;
    let nonce = b64.decode(&env.nonce)?;
    let mut ciphertext = b64.decode(&env.ciphertext)?;

    if nonce.len() != 12 {
        return Err(BackupError::Crypto("nonce must be 12 bytes".into()));
    }
    let key_bytes = derive_key(passphrase, &salt)?;
    let unbound = aead::UnboundKey::new(&aead::CHACHA20_POLY1305, &key_bytes)
        .map_err(|_| BackupError::Crypto("aead key init failed".into()))?;
    let key = aead::LessSafeKey::new(unbound);

    let mut nonce_arr = [0u8; 12];
    nonce_arr.copy_from_slice(&nonce);
    let nonce_obj = aead::Nonce::assume_unique_for_key(nonce_arr);
    key.open_in_place(nonce_obj, aead::Aad::empty(), &mut ciphertext)
        .map_err(|_| {
            // Wrong passphrase or tampered ciphertext — do not distinguish to
            // avoid leaking which it was.
            BackupError::Crypto("decryption failed: wrong passphrase or corrupt backup".into())
        })?;
    // open_in_place returns &plaintext (ciphertext minus tag, in place); trim
    // the trailing 16-byte Poly1305 tag off the Vec.
    let plaintext_len = ciphertext.len() - aead::CHACHA20_POLY1305.tag_len();
    ciphertext.truncate(plaintext_len);
    Ok(ciphertext)
}

/// Derive a 32-byte AEAD key from the passphrase + salt via Argon2id.
fn derive_key(passphrase: &str, salt: &[u8]) -> Result<[u8; KEY_LEN], BackupError> {
    use argon2::{Algorithm, Argon2, Params, Version};
    let params = Params::new(ARGON2_M_KIB, ARGON2_T, ARGON2_P, Some(KEY_LEN))
        .map_err(|e| BackupError::Crypto(format!("argon2 params: {e}")))?;
    let argon = Argon2::new(Algorithm::Argon2id, Version::V0x13, params);
    let mut out = [0u8; KEY_LEN];
    argon
        .hash_password_into(passphrase.as_bytes(), salt, &mut out)
        .map_err(|e| BackupError::Crypto(format!("argon2 derive: {e}")))?;
    Ok(out)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn encrypt_decrypt_roundtrip() {
        let payload =
            br#"{"system":{"hostname":"zion-01"},"network":{"wifi":[{"psk":"secret"}]}}"#;
        let env = encrypt(payload, "correct-horse-battery-staple").unwrap();
        assert_eq!(env.format, ENCRYPTED_BACKUP_V1);
        assert_eq!(env.kdf, "argon2id");
        let out = decrypt(&env, "correct-horse-battery-staple").unwrap();
        assert_eq!(out.as_slice(), payload);
    }

    #[test]
    fn wrong_passphrase_fails_to_decrypt() {
        let env = encrypt(b"sensitive", "the-right-passphrase").unwrap();
        let err = decrypt(&env, "the-wrong-passphrase").unwrap_err();
        assert!(err.to_string().contains("decryption failed"));
    }

    #[test]
    fn fresh_nonce_per_call_yields_distinct_ciphertext() {
        // Same payload + passphrase must still produce distinct ciphertext
        // (fresh salt + nonce each call) so re-downloading a backup doesn't
        // leak that the config is unchanged.
        let a = encrypt(b"same", "pass").unwrap();
        let b = encrypt(b"same", "pass").unwrap();
        assert_ne!(a.ciphertext, b.ciphertext);
        assert_ne!(a.salt, b.salt);
        // Both still decrypt to the same payload.
        assert_eq!(decrypt(&a, "pass").unwrap(), b"same");
        assert_eq!(decrypt(&b, "pass").unwrap(), b"same");
    }

    #[test]
    fn tampered_ciphertext_is_rejected() {
        use base64::Engine as _;
        let mut env = encrypt(b"secret", "pass").unwrap();
        // Corrupt the first byte of the decoded ciphertext (inside the
        // AEAD-protected region) and re-encode — the Poly1305 tag must no longer
        // verify.
        let b64 = base64::engine::general_purpose::STANDARD;
        let mut bytes = b64.decode(&env.ciphertext).unwrap();
        bytes[0] ^= 0xff;
        env.ciphertext = b64.encode(&bytes);
        assert!(decrypt(&env, "pass").is_err());
    }
}
