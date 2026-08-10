//! Serial applier (spec §4.9 — serial ownership lives here, like CAN).
//!
//! Per port: baud/format and **exclusive ownership** (console | customer |
//! modbus | unassigned). The applier:
//!   - sets the line baud on boot via a generated oneshot unit running
//!     `stty -F <dev> <baud>` (the most a userspace daemon can do for baud
//!     without owning the consumer);
//!   - enables `serial-getty@<dev>.service` when `owner == console` (the UART
//!     is the system console) and **stops + disables** it for any other owner,
//!     so a customer app or Modbus service gets exclusive access to the UART
//!     (spec §4.9: "exclusive ownership assignment with conflict prevention").
//!
//! RS-485 mode switching is a `TIOCSRS485` ioctl the vendored toolchain can't
//! issue from a shell, so `mode` is validated + recorded but the physical
//! switch is platform-specific (stated assumption, spec §7) — the applier
//! owns the testable, non-platform-specific parts: baud + console ownership.

use std::sync::Mutex;

use crate::applier::{run_cmd, Applier, ApplyError, ApplyOutcome, FileBackup};
use crate::store::io::SerialPort;
use crate::store::Config;
use crate::sysroot::Sysroot;

#[derive(Debug)]
pub struct SerialApplier {
    backups: Mutex<Vec<FileBackup>>,
}

impl SerialApplier {
    pub fn new() -> Self {
        Self {
            backups: Mutex::new(Vec::new()),
        }
    }

    /// `/dev/ttyTHS0` → `ttyTHS0`, the suffix systemd templates use for
    /// `serial-getty@<dev>.service` and for our own `webconfig-serial-<dev>`.
    /// Devices already given without `/dev/` are used as-is.
    fn dev_name(dev: &str) -> String {
        dev.strip_prefix("/dev/").unwrap_or(dev).to_string()
    }

    fn unit_path(sysroot: &Sysroot, dev: &str) -> std::path::PathBuf {
        sysroot.join(format!(
            "/etc/systemd/system/webconfig-serial-{}.service",
            Self::dev_name(dev)
        ))
    }

    /// Render the baud-setting oneshot for one serial port (spec §4.9).
    pub fn render_unit(s: &SerialPort) -> String {
        let dev = Self::dev_name(&s.device);
        format!(
            "[Unit]\nDescription=Serial baud for {dev}\nAfter=systemd-udevd.service\n\n\
             [Service]\nType=oneshot\nRemainAfterExit=yes\nExecStart=/bin/stty -F {device} {baud}\n\n\
             [Install]\nWantedBy=multi-user.target\n",
            dev = dev,
            device = s.device,
            baud = s.baud,
        )
    }

    fn validate_one(s: &SerialPort) -> Result<(), ApplyError> {
        if s.device.is_empty() {
            return Err(ApplyError::Validation {
                stage: crate::applier::ApplyStage::Validate,
                message: "serial port with empty device".into(),
            });
        }
        if s.baud == 0 {
            return Err(ApplyError::Validation {
                stage: crate::applier::ApplyStage::Validate,
                message: format!("serial {} baud must be > 0", s.device),
            });
        }
        match s.mode.as_str() {
            "" | "rs232" | "rs485" => {}
            other => {
                return Err(ApplyError::Validation {
                    stage: crate::applier::ApplyStage::Validate,
                    message: format!("serial {} mode must be rs232|rs485, got {other}", s.device),
                });
            }
        }
        match s.owner.as_str() {
            "console" | "customer" | "modbus" | "unassigned" => {}
            other => {
                return Err(ApplyError::Validation {
                    stage: crate::applier::ApplyStage::Validate,
                    message: format!(
                        "serial {} owner must be console|customer|modbus|unassigned, got {other}",
                        s.device
                    ),
                });
            }
        }
        Ok(())
    }
}

impl Default for SerialApplier {
    fn default() -> Self {
        Self::new()
    }
}

impl Applier for SerialApplier {
    fn id(&self) -> &'static str {
        "serial"
    }

    fn validate(&self, _sysroot: &Sysroot, config: &Config) -> Result<(), ApplyError> {
        let mut seen_devices = std::collections::HashSet::new();
        for s in &config.io.serial {
            Self::validate_one(s)?;
            // Ownership is per-device — two entries claiming the same device is a
            // conflict (spec §4.9: "exclusive ownership assignment"). The route
            // already rejects duplicate singleton owners; this catches duplicate
            // devices the route can't see at config-edit time.
            if !seen_devices.insert(s.device.clone()) {
                return Err(ApplyError::Validation {
                    stage: crate::applier::ApplyStage::Validate,
                    message: format!("serial device {} claimed twice", s.device),
                });
            }
        }
        Ok(())
    }

    fn apply(&self, sysroot: &Sysroot, config: &Config) -> Result<ApplyOutcome, ApplyError> {
        let mut backups = self.backups.lock().unwrap();
        backups.clear();
        // The serial subsystem is gated by `io.serial_enabled` (spec §4.9). When
        // off, do not manage any serial units — previously a disabled subsystem
        // could still toggle gettys.
        if !config.io.serial_enabled {
            return Ok(ApplyOutcome::Committed);
        }
        for s in &config.io.serial {
            let dev = Self::dev_name(&s.device);
            // A disabled entry releases the UART: stop+disable any getty we
            // previously enabled and remove our baud unit, so disabling an
            // entry actually takes effect (not just "not started").
            if !s.enabled {
                if sysroot.is_real_root() {
                    let getty = format!("serial-getty@{dev}.service");
                    let _ = run_cmd("systemctl", &["stop", &getty]);
                    let _ = run_cmd("systemctl", &["disable", &getty]);
                    let unit = format!("webconfig-serial-{dev}.service");
                    let _ = run_cmd("systemctl", &["stop", &unit]);
                    let _ = run_cmd("systemctl", &["disable", &unit]);
                }
                let _ = std::fs::remove_file(Self::unit_path(sysroot, &s.device));
                continue;
            }
            Self::validate_one(s)?;
            // Write the baud unit (rolled back on failure).
            let path = Self::unit_path(sysroot, &s.device);
            let backup = FileBackup::capture(path.clone())?;
            if let Some(parent) = path.parent() {
                std::fs::create_dir_all(parent)?;
            }
            std::fs::write(&path, Self::render_unit(s))?;
            backups.push(backup);
            if sysroot.is_real_root() {
                let baud_unit = format!("webconfig-serial-{dev}.service");
                let _ = run_cmd("systemctl", &["enable", &baud_unit]);
                let _ = run_cmd("systemctl", &["start", &baud_unit]);
                // Exclusive ownership (spec §4.9): console owns the UART via a
                // getty; any other owner must release it so the customer app or
                // Modbus service is the sole consumer.
                let getty = format!("serial-getty@{dev}.service");
                if s.owner == "console" {
                    let _ = run_cmd("systemctl", &["enable", &getty]);
                    let _ = run_cmd("systemctl", &["start", &getty]);
                } else {
                    let _ = run_cmd("systemctl", &["stop", &getty]);
                    let _ = run_cmd("systemctl", &["disable", &getty]);
                }
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

    fn port(device: &str, baud: u32, owner: &str) -> SerialPort {
        SerialPort {
            device: device.into(),
            enabled: true,
            baud,
            mode: "rs232".into(),
            owner: owner.into(),
        }
    }

    #[test]
    fn renders_baud_unit() {
        let u = SerialApplier::render_unit(&port("/dev/ttyTHS0", 115_200, "customer"));
        assert!(u.contains("stty -F /dev/ttyTHS0 115200"));
        assert!(u.contains("WantedBy=multi-user.target"));
    }

    #[test]
    fn rejects_zero_baud() {
        let a = SerialApplier::new();
        let sys = Sysroot::at("/tmp/never");
        let mut c = Config::default();
        c.io.serial.push(port("/dev/ttyTHS0", 0, "unassigned"));
        assert!(a.validate(&sys, &c).is_err());
    }

    #[test]
    fn rejects_bad_mode_and_owner() {
        let a = SerialApplier::new();
        let sys = Sysroot::at("/tmp/never");
        let mut c = Config::default();
        let mut p = port("/dev/ttyTHS0", 115_200, "unassigned");
        p.mode = "ttl".into();
        c.io.serial.push(p);
        assert!(a.validate(&sys, &c).is_err());

        let mut c = Config::default();
        let mut p = port("/dev/ttyTHS0", 115_200, "unassigned");
        p.owner = "debug".into();
        c.io.serial.push(p);
        assert!(a.validate(&sys, &c).is_err());
    }

    #[test]
    fn rejects_duplicate_device() {
        let a = SerialApplier::new();
        let sys = Sysroot::at("/tmp/never");
        let mut c = Config::default();
        c.io.serial.push(port("/dev/ttyTHS0", 115_200, "console"));
        c.io.serial.push(port("/dev/ttyTHS0", 9600, "customer"));
        // Same device twice is a conflict even with different owners —
        // ownership is per-device (spec §4.9).
        assert!(a.validate(&sys, &c).is_err());
    }

    #[test]
    fn writes_unit_and_rolls_back() {
        let dir = tempfile::tempdir().unwrap();
        let sys = Sysroot::at(dir.path());
        let a = SerialApplier::new();
        let mut c = Config::default();
        c.io.serial_enabled = true;
        c.io.serial.push(port("/dev/ttyTHS0", 115_200, "customer"));
        a.apply(&sys, &c).unwrap();
        let f = sys.join("/etc/systemd/system/webconfig-serial-ttyTHS0.service");
        assert!(f.exists());
        a.rollback(&sys).unwrap();
        assert!(!f.exists());
    }

    #[test]
    fn subsystem_disabled_writes_no_units() {
        let dir = tempfile::tempdir().unwrap();
        let sys = Sysroot::at(dir.path());
        let a = SerialApplier::new();
        let mut c = Config::default();
        c.io.serial_enabled = false;
        c.io.serial.push(port("/dev/ttyTHS0", 115_200, "console"));
        a.apply(&sys, &c).unwrap();
        assert!(!sys
            .join("/etc/systemd/system/webconfig-serial-ttyTHS0.service")
            .exists());
    }

    #[test]
    fn disabled_entry_removes_unit() {
        let dir = tempfile::tempdir().unwrap();
        let sys = Sysroot::at(dir.path());
        let a = SerialApplier::new();
        let mut c = Config::default();
        c.io.serial_enabled = true;
        c.io.serial.push(port("/dev/ttyTHS0", 115_200, "customer"));
        a.apply(&sys, &c).unwrap();
        let f = sys.join("/etc/systemd/system/webconfig-serial-ttyTHS0.service");
        assert!(f.exists());
        // Re-apply with the entry disabled — the baud unit is removed.
        let mut c = Config::default();
        c.io.serial_enabled = true;
        let mut p = port("/dev/ttyTHS0", 115_200, "customer");
        p.enabled = false;
        c.io.serial.push(p);
        a.apply(&sys, &c).unwrap();
        assert!(!f.exists());
    }

    #[test]
    fn empty_mode_is_allowed() {
        // A non-switchable UART has no mode — empty is valid (the manifest's
        // caps.modes may be a single entry or absent).
        let a = SerialApplier::new();
        let sys = Sysroot::at("/tmp/never");
        let mut c = Config::default();
        let mut p = port("/dev/ttyS0", 9600, "unassigned");
        p.mode = String::new();
        c.io.serial.push(p);
        assert!(a.validate(&sys, &c).is_ok());
    }
}
