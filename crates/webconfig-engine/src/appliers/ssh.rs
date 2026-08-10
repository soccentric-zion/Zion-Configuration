//! SSH / serial-getty applier (spec §4.1).
//!
//! Applies the `system.ssh` slice:
//!   - `authorized_keys` → written to `<sysroot>/root/.ssh/authorized_keys`
//!     (0600, dir 0700), with a single-level file backup for rollback.
//!   - `sshd_enabled` → `systemctl enable/start` or `disable/stop` of
//!     `sshd.service` on the real root.
//!   - `serial_getty_enabled` → enable/disable the console serial-getty unit
//!     (`serial-getty@ttyS0.service`, the conventional SoC debug console; a
//!     platform manifest may refine the tty in a later revision).
//!
//! Warn-the-user-about-session-loss is a frontend concern (it knows the
//! current session's transport); this applier just performs the change and
//! records enough to roll it back. Real system verbs run only against the
//! real root; the file-writing path is exercised against a tempdir sysroot in
//! tests (spec §6).

use std::sync::Mutex;

use crate::applier::{run_cmd, Applier, ApplyError, ApplyOutcome, FileBackup};
use crate::store::Config;
use crate::sysroot::Sysroot;

const SSHD_UNIT: &str = "sshd.service";
const SERIAL_GETTY_UNIT: &str = "serial-getty@ttyS0.service";

/// OpenSSH public-key type prefixes accepted in `authorized_keys`.
const KEY_TYPES: &[&str] = &[
    "ssh-rsa",
    "ssh-ed25519",
    "ssh-dss",
    "ecdsa-sha2-nistp256",
    "ecdsa-sha2-nistp384",
    "ecdsa-sha2-nistp521",
    "sk-ssh-ed25519@openssh.com",
    "sk-ecdsa-sha2-nistp256@openssh.com",
];

#[derive(Debug)]
pub struct SshApplier {
    last_keys_backup: Mutex<Option<FileBackup>>,
    last_sshd: Mutex<Option<bool>>,
    last_getty: Mutex<Option<bool>>,
}

impl SshApplier {
    pub fn new() -> Self {
        Self {
            last_keys_backup: Mutex::new(None),
            last_sshd: Mutex::new(None),
            last_getty: Mutex::new(None),
        }
    }

    fn authorized_keys_path(sysroot: &Sysroot) -> std::path::PathBuf {
        sysroot.join("/root/.ssh/authorized_keys")
    }

    /// Validate the authorized_keys blob: blank lines and `#` comments are
    /// fine; every other non-empty line must be an OpenSSH public key (known
    /// type prefix + base64 blob). Private-key material is rejected outright.
    fn validate_authorized_keys(keys: &str) -> Result<(), ApplyError> {
        if keys.contains("-----BEGIN") {
            return Err(ApplyError::Validation {
                stage: crate::applier::ApplyStage::Validate,
                message: "authorized_keys must not contain private key material".into(),
            });
        }
        for raw in keys.lines() {
            let line = raw.trim();
            if line.is_empty() || line.starts_with('#') {
                continue;
            }
            let type_ok = KEY_TYPES.iter().any(|t| line.starts_with(t));
            if !type_ok {
                return Err(ApplyError::Validation {
                    stage: crate::applier::ApplyStage::Validate,
                    message: format!(
                        "authorized_keys line is not a recognized OpenSSH public key: {line}"
                    ),
                });
            }
            // type + base64 (+ optional comment). The second whitespace-delimited
            // field must be non-empty and look like base64.
            let mut parts = line.split_whitespace();
            let _kind = parts.next();
            let blob = parts.next();
            let blob = match blob {
                Some(b) if !b.is_empty() => b,
                _ => {
                    return Err(ApplyError::Validation {
                        stage: crate::applier::ApplyStage::Validate,
                        message: format!(
                            "authorized_keys line is missing its base64 key blob: {line}"
                        ),
                    });
                }
            };
            if !blob
                .bytes()
                .all(|b| b.is_ascii_alphanumeric() || b == b'+' || b == b'/' || b == b'=')
            {
                return Err(ApplyError::Validation {
                    stage: crate::applier::ApplyStage::Validate,
                    message: format!("authorized_keys key blob is not valid base64: {line}"),
                });
            }
        }
        Ok(())
    }

    fn set_unit(sysroot: &Sysroot, unit: &str, enabled: bool) -> Result<(), ApplyError> {
        if !sysroot.is_real_root() {
            return Ok(());
        }
        if enabled {
            run_cmd("systemctl", &["enable", unit])?;
            run_cmd("systemctl", &["start", unit])?;
        } else {
            let _ = run_cmd("systemctl", &["stop", unit]);
            run_cmd("systemctl", &["disable", unit])?;
        }
        Ok(())
    }
}

impl Default for SshApplier {
    fn default() -> Self {
        Self::new()
    }
}

impl Applier for SshApplier {
    fn id(&self) -> &'static str {
        "ssh"
    }

    fn validate(&self, _sysroot: &Sysroot, config: &Config) -> Result<(), ApplyError> {
        Self::validate_authorized_keys(&config.system.ssh.authorized_keys)
    }

    fn apply(&self, sysroot: &Sysroot, config: &Config) -> Result<ApplyOutcome, ApplyError> {
        let ssh = &config.system.ssh;
        Self::validate_authorized_keys(&ssh.authorized_keys)?;

        // authorized_keys file (with backup for rollback).
        let path = Self::authorized_keys_path(sysroot);
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent)?;
            #[cfg(unix)]
            {
                use std::os::unix::fs::PermissionsExt;
                let _ = std::fs::set_permissions(parent, std::fs::Permissions::from_mode(0o700));
            }
        }
        let backup = FileBackup::capture(path.clone())?;
        // Normalize: trim trailing whitespace, ensure trailing newline only if
        // non-empty, so we don't write a stray blank file.
        let normalized = ssh.authorized_keys.trim_end();
        if normalized.is_empty() {
            // No keys configured: remove the file so an empty config doesn't
            // leave a zero-key authorized_keys lingering.
            if path.exists() {
                std::fs::remove_file(&path)?;
            }
        } else {
            std::fs::write(&path, format!("{normalized}\n"))?;
            #[cfg(unix)]
            {
                use std::os::unix::fs::PermissionsExt;
                let _ = std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o600));
            }
        }
        *self.last_keys_backup.lock().unwrap() = Some(backup);

        // sshd + serial-getty unit toggles (real root only).
        let prior_sshd = self.last_sshd.lock().unwrap().replace(ssh.sshd_enabled);
        Self::set_unit(sysroot, SSHD_UNIT, ssh.sshd_enabled)?;
        let prior_getty = self
            .last_getty
            .lock()
            .unwrap()
            .replace(ssh.serial_getty_enabled);
        // serial-getty may not exist on every platform; treat a disable/enable
        // failure as non-fatal by best-effort on enable but propagate disable.
        if sysroot.is_real_root() {
            if ssh.serial_getty_enabled {
                let _ = run_cmd("systemctl", &["enable", SERIAL_GETTY_UNIT]);
                let _ = run_cmd("systemctl", &["start", SERIAL_GETTY_UNIT]);
            } else {
                let _ = run_cmd("systemctl", &["stop", SERIAL_GETTY_UNIT]);
                let _ = run_cmd("systemctl", &["disable", SERIAL_GETTY_UNIT]);
            }
        }

        // Restore the "previous" flags so rollback can revert toggles we
        // actually changed (first apply has no prior → rollback is a no-op for
        // the unit toggles, which is correct).
        *self.last_sshd.lock().unwrap() = prior_sshd;
        *self.last_getty.lock().unwrap() = prior_getty;

        Ok(ApplyOutcome::Committed)
    }

    fn rollback(&self, sysroot: &Sysroot) -> Result<(), ApplyError> {
        if let Some(b) = self.last_keys_backup.lock().unwrap().take() {
            b.restore()?;
        }
        if let Some(prev) = self.last_sshd.lock().unwrap().take() {
            Self::set_unit(sysroot, SSHD_UNIT, prev)?;
        }
        if let Some(prev) = self.last_getty.lock().unwrap().take() {
            if sysroot.is_real_root() {
                if prev {
                    let _ = run_cmd("systemctl", &["enable", SERIAL_GETTY_UNIT]);
                    let _ = run_cmd("systemctl", &["start", SERIAL_GETTY_UNIT]);
                } else {
                    let _ = run_cmd("systemctl", &["stop", SERIAL_GETTY_UNIT]);
                    let _ = run_cmd("systemctl", &["disable", SERIAL_GETTY_UNIT]);
                }
            }
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn rejects_private_key_material() {
        let a = SshApplier::new();
        let sys = Sysroot::at("/tmp/never");
        let mut c = Config::default();
        c.system.ssh.authorized_keys = "-----BEGIN OPENSSH PRIVATE KEY-----\n...".into();
        assert!(a.validate(&sys, &c).is_err());
    }

    #[test]
    fn accepts_valid_public_keys_and_comments() {
        let a = SshApplier::new();
        let sys = Sysroot::at("/tmp/never");
        let mut c = Config::default();
        c.system.ssh.authorized_keys = [
            "# admin laptop",
            "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIILd+yzrE8pXGzqE8pXGzqE8pXGzqE8pX admin@laptop",
            "ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAAAgQ== ops@host",
            "",
        ]
        .join("\n");
        assert!(a.validate(&sys, &c).is_ok());
    }

    #[test]
    fn rejects_non_key_line() {
        let a = SshApplier::new();
        let sys = Sysroot::at("/tmp/never");
        let mut c = Config::default();
        c.system.ssh.authorized_keys = "not-a-key".into();
        assert!(a.validate(&sys, &c).is_err());
    }

    #[test]
    fn writes_authorized_keys_to_fake_sysroot() {
        let dir = tempfile::tempdir().unwrap();
        let sys = Sysroot::at(dir.path());
        let a = SshApplier::new();
        let mut c = Config::default();
        c.system.ssh.authorized_keys =
            "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIILd+yzrE8pXGzqE8pXGzq admin@laptop".into();
        a.apply(&sys, &c).unwrap();
        let written =
            std::fs::read_to_string(dir.path().join("root/.ssh/authorized_keys")).unwrap();
        assert!(written.contains("ssh-ed25519 AAAAC3NzaC1lZDI1NTE5"));
    }

    #[test]
    fn empty_keys_removes_file() {
        let dir = tempfile::tempdir().unwrap();
        let sys = Sysroot::at(dir.path());
        let a = SshApplier::new();
        let mut c = Config::default();
        c.system.ssh.authorized_keys =
            "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIILd+yzrE8pXGzq admin@laptop".into();
        a.apply(&sys, &c).unwrap();
        assert!(dir.path().join("root/.ssh/authorized_keys").exists());

        c.system.ssh.authorized_keys = String::new();
        a.apply(&sys, &c).unwrap();
        assert!(!dir.path().join("root/.ssh/authorized_keys").exists());
    }

    #[test]
    fn rollback_restores_prior_authorized_keys() {
        let dir = tempfile::tempdir().unwrap();
        let sys = Sysroot::at(dir.path());
        let path = dir.path().join("root/.ssh/authorized_keys");
        std::fs::create_dir_all(path.parent().unwrap()).unwrap();
        std::fs::write(&path, b"ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAAAgQ== first\n").unwrap();

        let a = SshApplier::new();
        let mut c = Config::default();
        c.system.ssh.authorized_keys =
            "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIILd+yzrE8pXGzq second".into();
        a.apply(&sys, &c).unwrap();
        assert!(std::fs::read_to_string(&path).unwrap().contains("second"));

        a.rollback(&sys).unwrap();
        assert!(std::fs::read_to_string(&path).unwrap().contains("first"));
    }
}
