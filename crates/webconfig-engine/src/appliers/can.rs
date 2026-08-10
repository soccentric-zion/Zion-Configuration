//! CAN applier (spec §4.9 — CAN lives here, not in Networking).
//!
//! Per interface: bitrate, CAN-FD + data bitrate, listen-only, restart-ms,
//! bring-up on boot via a generated systemd unit doing
//! `ip link set <iface> type can bitrate <n> ...`. Live bus state and
//! termination are read-only surfaces handled by the I/O page / monitoring,
//! not this applier. Writes:
//!   - `/etc/systemd/system/can-<iface>.service` (oneshot, brought up at boot)
//!   - the unit is enabled on boot when `up_on_boot` is true.

use std::sync::Mutex;

use crate::applier::{run_cmd, Applier, ApplyError, ApplyOutcome, FileBackup};
use crate::store::io::CanInterface;
use crate::store::Config;
use crate::sysroot::Sysroot;

#[derive(Debug)]
pub struct CanApplier {
    backups: Mutex<Vec<FileBackup>>,
}

impl CanApplier {
    pub fn new() -> Self {
        Self {
            backups: Mutex::new(Vec::new()),
        }
    }

    fn unit_path(sysroot: &Sysroot, iface: &str) -> std::path::PathBuf {
        sysroot.join(format!("/etc/systemd/system/can-{iface}.service"))
    }

    /// Render the bring-up unit for one CAN interface (spec §4.9).
    pub fn render_unit(c: &CanInterface) -> String {
        let mut ip_args = format!(
            "link set {name} up type can bitrate {br} ",
            name = c.name,
            br = c.bitrate
        );
        if c.fd {
            // `fd on` is mandatory for the data bitrate to take effect — without
            // it `ip` brings the interface up in classic CAN mode and `dbitrate`
            // is silently ignored (spec §4.9: CAN-FD + data bitrate).
            ip_args.push_str(&format!("dbitrate {} fd on ", c.data_bitrate));
        }
        if c.listen_only {
            ip_args.push_str("listen-only ");
        }
        if c.restart_ms > 0 {
            ip_args.push_str(&format!("restart-ms {} ", c.restart_ms));
        }
        format!(
            "[Unit]\nDescription=CAN bring-up for {name}\nAfter=network-pre.target\n\n\
             [Service]\nType=oneshot\nRemainAfterExit=yes\nExecStart=/sbin/ip {args}\n\
             ExecStop=/sbin/ip link set {name} down\n\n\
             [Install]\nWantedBy=multi-user.target\n",
            name = c.name,
            args = ip_args.trim_end()
        )
    }

    fn validate_one(c: &CanInterface) -> Result<(), ApplyError> {
        if c.name.is_empty() {
            return Err(ApplyError::Validation {
                stage: crate::applier::ApplyStage::Validate,
                message: "CAN interface with empty name".into(),
            });
        }
        if c.bitrate == 0 {
            return Err(ApplyError::Validation {
                stage: crate::applier::ApplyStage::Validate,
                message: format!("CAN {} bitrate must be > 0", c.name),
            });
        }
        if c.fd && c.data_bitrate == 0 {
            return Err(ApplyError::Validation {
                stage: crate::applier::ApplyStage::Validate,
                message: format!("CAN-FD {} data_bitrate must be > 0", c.name),
            });
        }
        Ok(())
    }
}

impl Default for CanApplier {
    fn default() -> Self {
        Self::new()
    }
}

impl Applier for CanApplier {
    fn id(&self) -> &'static str {
        "can"
    }

    fn validate(&self, _sysroot: &Sysroot, config: &Config) -> Result<(), ApplyError> {
        for c in &config.io.can {
            Self::validate_one(c)?;
        }
        Ok(())
    }

    fn apply(&self, sysroot: &Sysroot, config: &Config) -> Result<ApplyOutcome, ApplyError> {
        let mut backups = self.backups.lock().unwrap();
        backups.clear();
        // The CAN subsystem is gated by `io.can_enabled` (spec §4.9 / API
        // reference: can_enabled gates the subsystem detail settings). When
        // off, do not write or start any CAN units — previously every entry was
        // written/enabled/started unconditionally.
        if !config.io.can_enabled {
            return Ok(ApplyOutcome::Committed);
        }
        for c in &config.io.can {
            // Per-entry `enabled` flag: skip disabled interfaces, and on the
            // real root stop+disable a unit we previously brought up so that
            // disabling an entry actually takes effect (not just "not started").
            if !c.enabled {
                if sysroot.is_real_root() {
                    let unit = format!("can-{}.service", c.name);
                    let _ = run_cmd("systemctl", &["stop", &unit]);
                    let _ = run_cmd("systemctl", &["disable", &unit]);
                }
                continue;
            }
            Self::validate_one(c)?;
            let path = Self::unit_path(sysroot, &c.name);
            let backup = FileBackup::capture(path.clone())?;
            if let Some(parent) = path.parent() {
                std::fs::create_dir_all(parent)?;
            }
            std::fs::write(&path, Self::render_unit(c))?;
            backups.push(backup);
            if sysroot.is_real_root() {
                if c.up_on_boot {
                    let _ = run_cmd("systemctl", &["enable", &format!("can-{}.service", c.name)]);
                }
                // Bring it up immediately.
                let _ = run_cmd("systemctl", &["start", &format!("can-{}.service", c.name)]);
            }
        }
        Ok(ApplyOutcome::Committed)
    }

    fn rollback(&self, sysroot: &Sysroot) -> Result<(), ApplyError> {
        let mut backups = self.backups.lock().unwrap();
        for b in backups.drain(..) {
            b.restore()?;
        }
        if sysroot.is_real_root() {
            let _ = run_cmd("systemctl", &["daemon-reload"]);
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn can(name: &str, bitrate: u32, fd: bool, db: u32) -> CanInterface {
        CanInterface {
            name: name.into(),
            enabled: true,
            bitrate,
            fd,
            data_bitrate: db,
            listen_only: false,
            restart_ms: 100,
            up_on_boot: true,
        }
    }

    #[test]
    fn renders_fd_unit() {
        let u = CanApplier::render_unit(&can("can0", 500_000, true, 2_000_000));
        assert!(u.contains("bitrate 500000"));
        assert!(u.contains("dbitrate 2000000"));
        assert!(u.contains("fd on"));
        assert!(u.contains("restart-ms 100"));
        assert!(u.contains("WantedBy=multi-user.target"));
    }

    #[test]
    fn classic_can_has_no_fd_flag() {
        let u = CanApplier::render_unit(&can("can0", 500_000, false, 0));
        assert!(u.contains("bitrate 500000"));
        assert!(!u.contains("dbitrate"));
        assert!(!u.contains("fd on"));
    }

    #[test]
    fn rejects_zero_bitrate() {
        let a = CanApplier::new();
        let sys = Sysroot::at("/tmp/never");
        let mut c = Config::default();
        c.io.can.push(can("can0", 0, false, 0));
        assert!(a.validate(&sys, &c).is_err());
    }

    #[test]
    fn rejects_fd_without_data_bitrate() {
        let a = CanApplier::new();
        let sys = Sysroot::at("/tmp/never");
        let mut c = Config::default();
        c.io.can.push(can("can0", 500_000, true, 0));
        assert!(a.validate(&sys, &c).is_err());
    }

    #[test]
    fn writes_units_and_rolls_back() {
        let dir = tempfile::tempdir().unwrap();
        let sys = Sysroot::at(dir.path());
        let a = CanApplier::new();
        let mut c = Config::default();
        c.io.can_enabled = true;
        c.io.can.push(can("can0", 500_000, false, 0));
        a.apply(&sys, &c).unwrap();
        let f = sys.join("/etc/systemd/system/can-can0.service");
        assert!(f.exists());
        a.rollback(&sys).unwrap();
        assert!(!f.exists());
    }

    #[test]
    fn subsystem_disabled_writes_no_units() {
        let dir = tempfile::tempdir().unwrap();
        let sys = Sysroot::at(dir.path());
        let a = CanApplier::new();
        let mut c = Config::default();
        c.io.can_enabled = false; // CAN subsystem off — nothing should apply.
        c.io.can.push(can("can0", 500_000, false, 0));
        a.apply(&sys, &c).unwrap();
        assert!(!sys.join("/etc/systemd/system/can-can0.service").exists());
    }

    #[test]
    fn disabled_entry_writes_no_unit() {
        let dir = tempfile::tempdir().unwrap();
        let sys = Sysroot::at(dir.path());
        let a = CanApplier::new();
        let mut c = Config::default();
        c.io.can_enabled = true;
        let mut entry = can("can0", 500_000, false, 0);
        entry.enabled = false; // subsystem on, but this entry disabled.
        c.io.can.push(entry);
        a.apply(&sys, &c).unwrap();
        assert!(!sys.join("/etc/systemd/system/can-can0.service").exists());
    }
}
