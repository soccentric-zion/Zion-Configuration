//! Hostname applier (spec §4.1).
//!
//! Sets the OS hostname via `hostnamectl` and writes `/etc/hostname` so the
//! change survives reboot. The free-text asset tag is **config store only** —
//! it never reaches the OS hostname (spec §4.1) and is therefore not handled
//! here; it is read directly from the store by the System page.
//!
//! Rollback restores the prior `/etc/hostname` and re-runs `hostnamectl
//! set-hostname` to the old value. The hostnamectl call is the one real
//! system verb; tests cover the file-writing path against a tempdir sysroot.

use std::sync::Mutex;

use crate::applier::{run_cmd, Applier, ApplyError, ApplyOutcome, FileBackup};
use crate::store::Config;
use crate::sysroot::Sysroot;

#[derive(Debug)]
pub struct HostnameApplier {
    last_backup: Mutex<Option<FileBackup>>,
    last_hostname: Mutex<Option<String>>,
}

impl HostnameApplier {
    pub fn new() -> Self {
        Self {
            last_backup: Mutex::new(None),
            last_hostname: Mutex::new(None),
        }
    }

    fn etc_hostname(sysroot: &Sysroot) -> std::path::PathBuf {
        sysroot.join("/etc/hostname")
    }

    fn validate_hostname(h: &str) -> Result<(), ApplyError> {
        // RFC 1123: letters, digits, hyphens; 1..=63 chars; no leading/trailing
        // hyphen. We forbid dots to keep the asset tag / DNS split clean.
        if h.is_empty() || h.len() > 63 {
            return Err(ApplyError::Validation {
                stage: crate::applier::ApplyStage::Validate,
                message: format!("hostname length must be 1..=63, got {}", h.len()),
            });
        }
        if h.starts_with('-') || h.ends_with('-') {
            return Err(ApplyError::Validation {
                stage: crate::applier::ApplyStage::Validate,
                message: "hostname must not start or end with '-'".into(),
            });
        }
        if !h.chars().all(|c| c.is_ascii_alphanumeric() || c == '-') {
            return Err(ApplyError::Validation {
                stage: crate::applier::ApplyStage::Validate,
                message: "hostname must be alphanumeric or '-' only".into(),
            });
        }
        Ok(())
    }
}

impl Default for HostnameApplier {
    fn default() -> Self {
        Self::new()
    }
}

impl Applier for HostnameApplier {
    fn id(&self) -> &'static str {
        "hostname"
    }

    fn validate(&self, _sysroot: &Sysroot, config: &Config) -> Result<(), ApplyError> {
        if let Some(h) = &config.system.hostname {
            Self::validate_hostname(h)?;
        }
        Ok(())
    }

    fn apply(&self, sysroot: &Sysroot, config: &Config) -> Result<ApplyOutcome, ApplyError> {
        let Some(hostname) = &config.system.hostname else {
            return Ok(ApplyOutcome::Committed); // nothing to do
        };
        Self::validate_hostname(hostname)?;

        let path = Self::etc_hostname(sysroot);
        let prior = FileBackup::capture(path.clone())?;
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent)?;
        }
        std::fs::write(&path, format!("{hostname}\n"))?;

        // Real system verb — only against the real root. A tempdir sysroot
        // (tests / CI fake sysroot) skips the live call (spec §6: no hardware).
        if sysroot.is_real_root() {
            if let Err(e) = run_cmd("hostnamectl", &["set-hostname", hostname]) {
                let _ = FileBackup {
                    path: path.clone(),
                    prior: prior.prior.clone(),
                }
                .restore();
                return Err(e);
            }
        }

        *self.last_backup.lock().unwrap() = Some(prior);
        *self.last_hostname.lock().unwrap() = Some(hostname.clone());
        Ok(ApplyOutcome::Committed)
    }

    fn rollback(&self, sysroot: &Sysroot) -> Result<(), ApplyError> {
        let backup = self.last_backup.lock().unwrap().take();
        if let Some(b) = backup {
            b.restore()?;
            let prev = self.last_hostname.lock().unwrap().take();
            if sysroot.is_real_root() {
                if let Some(prev_host) = prev {
                    run_cmd("hostnamectl", &["set-hostname", &prev_host])?;
                }
            }
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::store::ConfigFile;

    #[test]
    fn writes_etc_hostname_and_rolls_back() {
        let dir = tempfile::tempdir().unwrap();
        let sys = Sysroot::at(dir.path()); // not the real root -> skips hostnamectl
        let a = HostnameApplier::new();

        let mut file = ConfigFile::new();
        file.config.system.hostname = Some("zion-07".into());
        a.apply(&sys, &file.config).unwrap();

        let written = std::fs::read_to_string(sys.join("/etc/hostname")).unwrap();
        assert_eq!(written, "zion-07\n");

        a.rollback(&sys).unwrap();
        assert!(!sys.join("/etc/hostname").exists());
    }

    #[test]
    fn rejects_bad_hostnames() {
        let a = HostnameApplier::new();
        let sys = Sysroot::at("/tmp/never");
        let mut c = Config::default();
        c.system.hostname = Some("-bad".into());
        assert!(a.validate(&sys, &c).is_err());
        c.system.hostname = Some("has space".into());
        assert!(a.validate(&sys, &c).is_err());
        c.system.hostname = Some("ok-host".into());
        assert!(a.validate(&sys, &c).is_ok());
    }
}
