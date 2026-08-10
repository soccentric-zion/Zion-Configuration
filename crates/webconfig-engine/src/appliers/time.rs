//! Time / NTP applier (spec §4.1).
//!
//! Applies timezone, NTP on/off, and manual date/time via `timedatectl`:
//!   - `timedatectl set-timezone <tz>`
//!   - `timedatectl set-ntp true|false`
//!   - `timedatectl set-time 'YYYY-MM-DD HH:MM:SS'` (air-gapped sites; only
//!     when NTP is off)
//!
//! After a manual set, where an RTC is present, `timedatectl` syncs the RTC
//! automatically; the manifest `has_rtc` flag drives the UI warning banner
//! when no RTC and NTP is unreachable (spec §4.1).
//!
//! Validation rejects an unknown timezone by checking it exists under
//! `/usr/share/zoneinfo` (real root) or skipping the check in a tempdir
//! sysroot so tests stay hardware-free.

use std::sync::Mutex;

use crate::applier::{run_cmd, Applier, ApplyError, ApplyOutcome, FileBackup};
use crate::store::Config;
use crate::sysroot::Sysroot;

#[derive(Debug)]
pub struct TimeApplier {
    last: Mutex<Option<TimeSnapshot>>,
    /// Backup of the timesyncd drop-in written to apply the NTP server list,
    /// so rollback can restore the prior file (or its absence).
    dropin_backup: Mutex<Option<FileBackup>>,
}

#[derive(Debug, Clone)]
struct TimeSnapshot {
    timezone: Option<String>,
    ntp: Option<bool>,
    ptp: bool,
    ptp_interface: Option<String>,
    gps: bool,
}

/// systemd-timesyncd drop-in carrying the operator-configured NTP server list
/// (spec §4.1). Written only when NTP is the active sync source.
const TIMESYNCD_DROPIN: &str = "/etc/systemd/timesyncd.conf.d/webconfig.conf";

impl TimeApplier {
    pub fn new() -> Self {
        Self {
            last: Mutex::new(None),
            dropin_backup: Mutex::new(None),
        }
    }

    fn validate_ntp_server(server: &str) -> Result<(), ApplyError> {
        let s = server.trim();
        if s.is_empty() {
            return Err(ApplyError::Validation {
                stage: crate::applier::ApplyStage::Validate,
                message: "ntp_servers contains an empty entry".into(),
            });
        }
        // Hostnames, IPv4, and bracketless IPv6 share this character set; we
        // reject anything that could break out of the conf line (whitespace,
        // quotes, shell metacharacters) — the value is interpolated into a
        // space-separated `NTP=` line, never a shell, but tightening here keeps
        // the drop-in unambiguous.
        if !s
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b == b'.' || b == b':' || b == b'-')
        {
            return Err(ApplyError::Validation {
                stage: crate::applier::ApplyStage::Validate,
                message: format!("ntp_servers entry has invalid characters: {server}"),
            });
        }
        Ok(())
    }

    fn zoneinfo(sysroot: &Sysroot, tz: &str) -> std::path::PathBuf {
        sysroot.join(format!("/usr/share/zoneinfo/{tz}"))
    }

    fn validate_timezone(sysroot: &Sysroot, tz: &str) -> Result<(), ApplyError> {
        if tz.is_empty()
            || tz
                .chars()
                .any(|c| !(c.is_ascii_alphanumeric() || c == '/' || c == '_' || c == '-'))
        {
            return Err(ApplyError::Validation {
                stage: crate::applier::ApplyStage::Validate,
                message: format!("invalid timezone characters: {tz}"),
            });
        }
        // On the real root, confirm tzdata actually ships the zone. In a
        // tempdir sysroot the zone file won't exist, so skip the existence
        // check there (spec §6: no hardware, no tzdata in tempdir).
        if sysroot.is_real_root() && !Self::zoneinfo(sysroot, tz).exists() {
            return Err(ApplyError::Validation {
                stage: crate::applier::ApplyStage::Validate,
                message: format!("unknown timezone: {tz}"),
            });
        }
        Ok(())
    }

    /// Write (or clear) the timesyncd drop-in carrying the NTP server list and
    /// snapshot the prior file so [`rollback`] can restore it. Empty list →
    /// remove the drop-in so timesyncd falls back to its default pools.
    fn apply_ntp_dropin(&self, sysroot: &Sysroot, servers: &[String]) -> Result<(), ApplyError> {
        for s in servers {
            Self::validate_ntp_server(s)?;
        }
        let path = sysroot.join(TIMESYNCD_DROPIN);
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent)?;
        }
        let backup = FileBackup::capture(path.clone())?;
        if servers.is_empty() {
            if path.exists() {
                std::fs::remove_file(&path)?;
            }
        } else {
            let joined = servers
                .iter()
                .map(|s| s.trim())
                .filter(|s| !s.is_empty())
                .collect::<Vec<_>>()
                .join(" ");
            std::fs::write(&path, format!("[Time]\nNTP={joined}\n"))?;
        }
        *self.dropin_backup.lock().unwrap() = Some(backup);
        Ok(())
    }

    fn parse_manual(s: &str) -> Result<(), ApplyError> {
        // Accept 'YYYY-MM-DD HH:MM:SS'. timedatectl enforces semantics; we
        // only check the shape so we never pass garbage to the verb.
        let stripped = s.trim();
        if stripped.len() < 19
            || !stripped[4..5].contains('-')
            || !stripped[7..8].contains('-')
            || !stripped[10..11].contains(' ')
            || !stripped[13..14].contains(':')
            || !stripped[16..17].contains(':')
        {
            return Err(ApplyError::Validation {
                stage: crate::applier::ApplyStage::Validate,
                message: format!("manual_datetime must be 'YYYY-MM-DD HH:MM:SS', got {s:?}"),
            });
        }
        Ok(())
    }
}

impl Default for TimeApplier {
    fn default() -> Self {
        Self::new()
    }
}

impl Applier for TimeApplier {
    fn id(&self) -> &'static str {
        "time"
    }

    fn validate(&self, sysroot: &Sysroot, config: &Config) -> Result<(), ApplyError> {
        for server in &config.system.ntp_servers {
            Self::validate_ntp_server(server)?;
        }
        if let Some(tz) = &config.system.time.timezone {
            Self::validate_timezone(sysroot, tz)?;
        }
        if let Some(manual) = &config.system.time.manual_datetime {
            Self::parse_manual(manual)?;
            if config.system.time.ntp_enabled
                || config.system.time.ptp_enabled
                || config.system.time.gps_enabled
            {
                return Err(ApplyError::Validation {
                    stage: crate::applier::ApplyStage::Validate,
                    message:
                        "manual_datetime requires NTP, PTP, and GPS synchronization to be disabled"
                            .into(),
                });
            }
        }
        let sync_sources = [
            config.system.time.ntp_enabled,
            config.system.time.ptp_enabled,
            config.system.time.gps_enabled,
        ]
        .into_iter()
        .filter(|enabled| *enabled)
        .count();
        if sync_sources > 1 {
            return Err(ApplyError::Validation {
                stage: crate::applier::ApplyStage::Validate,
                message: "NTP, PTP, and GPS cannot be enabled together".into(),
            });
        }
        if config.system.time.ptp_enabled {
            let interface = config.system.time.ptp_interface.as_deref().unwrap_or("");
            if interface.is_empty()
                || interface
                    .chars()
                    .any(|c| !(c.is_ascii_alphanumeric() || c == '_' || c == '-' || c == '.'))
            {
                return Err(ApplyError::Validation {
                    stage: crate::applier::ApplyStage::Validate,
                    message: "PTP requires a valid network interface".into(),
                });
            }
            if config
                .system
                .time
                .ptp_domain
                .is_some_and(|domain| domain > 127)
            {
                return Err(ApplyError::Validation {
                    stage: crate::applier::ApplyStage::Validate,
                    message: "PTP domain must be between 0 and 127".into(),
                });
            }
        }
        if config.system.time.gps_enabled {
            for (label, device) in [
                ("GPS", config.system.time.gps_device.as_deref()),
                ("GPS PPS", config.system.time.gps_pps_device.as_deref()),
            ] {
                if let Some(path) = device {
                    if path.is_empty()
                        || path
                            .chars()
                            .any(|c| !(c.is_ascii_alphanumeric() || "/_-.:".contains(c)))
                    {
                        return Err(ApplyError::Validation {
                            stage: crate::applier::ApplyStage::Validate,
                            message: format!("{label} device path is invalid"),
                        });
                    }
                }
            }
            if config.system.time.gps_device.is_none() {
                return Err(ApplyError::Validation {
                    stage: crate::applier::ApplyStage::Validate,
                    message: "GPS requires a device path".into(),
                });
            }
        }
        Ok(())
    }

    fn apply(&self, sysroot: &Sysroot, config: &Config) -> Result<ApplyOutcome, ApplyError> {
        let t = &config.system.time;
        if let Some(tz) = &t.timezone {
            Self::validate_timezone(sysroot, tz)?;
            if sysroot.is_real_root() {
                run_cmd("timedatectl", &["set-timezone", tz])?;
            }
        }
        // Set NTP off before a manual time set, then re-enable as requested.
        if let Some(manual) = &t.manual_datetime {
            Self::parse_manual(manual)?;
            if sysroot.is_real_root() {
                run_cmd("timedatectl", &["set-ntp", "false"])?;
                run_cmd("timedatectl", &["set-time", manual])?;
            }
        }
        if t.ptp_enabled {
            if let Some(interface) = &t.ptp_interface {
                if sysroot.is_real_root() {
                    run_cmd(
                        "systemctl",
                        &["restart", &format!("ptp4l@{interface}.service")],
                    )?;
                    run_cmd("systemctl", &["restart", "phc2sys.service"])?;
                }
            }
        } else if sysroot.is_real_root() {
            let _ = run_cmd("systemctl", &["stop", "phc2sys.service"]);
        }
        if t.gps_enabled {
            if sysroot.is_real_root() {
                run_cmd("systemctl", &["restart", "gpsd.service"])?;
                run_cmd("systemctl", &["restart", "chrony.service"])?;
            }
        } else if sysroot.is_real_root() {
            let _ = run_cmd("systemctl", &["stop", "gpsd.service"]);
        }
        // Apply the operator-configured NTP server list as a timesyncd drop-in
        // (spec §4.1). `timedatectl set-ntp` only flips the unit on/off; the
        // server list is a separate file systemd-timesyncd reads at start.
        self.apply_ntp_dropin(sysroot, &config.system.ntp_servers)?;
        if sysroot.is_real_root() && t.ntp_enabled && !t.ptp_enabled {
            // Pick up the freshly written drop-in.
            let _ = run_cmd("systemctl", &["restart", "systemd-timesyncd.service"]);
        }

        if sysroot.is_real_root() {
            run_cmd(
                "timedatectl",
                &[
                    "set-ntp",
                    if t.ntp_enabled && !t.ptp_enabled {
                        "true"
                    } else {
                        "false"
                    },
                ],
            )?;
        }
        *self.last.lock().unwrap() = Some(TimeSnapshot {
            timezone: t.timezone.clone(),
            ntp: Some(t.ntp_enabled),
            ptp: t.ptp_enabled,
            ptp_interface: t.ptp_interface.clone(),
            gps: t.gps_enabled,
        });
        Ok(ApplyOutcome::Committed)
    }

    fn rollback(&self, sysroot: &Sysroot) -> Result<(), ApplyError> {
        // Restore the prior timesyncd drop-in (or its absence) first, so a
        // restart picks up the reverted NTP server list.
        if let Some(b) = self.dropin_backup.lock().unwrap().take() {
            b.restore()?;
            if sysroot.is_real_root() {
                let _ = run_cmd("systemctl", &["restart", "systemd-timesyncd.service"]);
            }
        }
        if let Some(prev) = self.last.lock().unwrap().take() {
            if sysroot.is_real_root() {
                if let Some(tz) = prev.timezone {
                    run_cmd("timedatectl", &["set-timezone", &tz])?;
                }
                if let Some(ntp) = prev.ntp {
                    run_cmd(
                        "timedatectl",
                        &["set-ntp", if ntp { "true" } else { "false" }],
                    )?;
                }
                if prev.ptp {
                    if let Some(interface) = prev.ptp_interface {
                        let _ = run_cmd(
                            "systemctl",
                            &["restart", &format!("ptp4l@{interface}.service")],
                        );
                    }
                    let _ = run_cmd("systemctl", &["restart", "phc2sys.service"]);
                } else {
                    let _ = run_cmd("systemctl", &["stop", "phc2sys.service"]);
                }
                if prev.gps {
                    let _ = run_cmd("systemctl", &["restart", "gpsd.service"]);
                    let _ = run_cmd("systemctl", &["restart", "chrony.service"]);
                } else {
                    let _ = run_cmd("systemctl", &["stop", "gpsd.service"]);
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
    fn validates_timezone_shape() {
        let sys = Sysroot::at("/tmp/never");
        let a = TimeApplier::new();
        let mut c = Config::default();
        c.system.time.timezone = Some("Europe/Berlin".into());
        assert!(a.validate(&sys, &c).is_ok());
        c.system.time.timezone = Some("../etc/passwd".into());
        assert!(a.validate(&sys, &c).is_err());
    }

    #[test]
    fn rejects_manual_time_with_ntp_on() {
        let a = TimeApplier::new();
        let sys = Sysroot::at("/tmp/never");
        let mut c = Config::default();
        c.system.time.ntp_enabled = true;
        c.system.time.manual_datetime = Some("2026-01-02 03:04:05".into());
        assert!(a.validate(&sys, &c).is_err());
    }

    #[test]
    fn accepts_manual_time_with_ntp_off() {
        let a = TimeApplier::new();
        let sys = Sysroot::at("/tmp/never");
        let mut c = Config::default();
        c.system.time.ntp_enabled = false;
        c.system.time.manual_datetime = Some("2026-01-02 03:04:05".into());
        assert!(a.validate(&sys, &c).is_ok());
    }

    #[test]
    fn rejects_ptp_without_interface() {
        let a = TimeApplier::new();
        let sys = Sysroot::at("/tmp/never");
        let mut c = Config::default();
        c.system.time.ptp_enabled = true;
        assert!(a.validate(&sys, &c).is_err());
    }

    #[test]
    fn rejects_ntp_and_ptp_together() {
        let a = TimeApplier::new();
        let sys = Sysroot::at("/tmp/never");
        let mut c = Config::default();
        c.system.time.ntp_enabled = true;
        c.system.time.ptp_enabled = true;
        c.system.time.ptp_interface = Some("eth0".into());
        assert!(a.validate(&sys, &c).is_err());
    }

    #[test]
    fn rejects_malformed_manual_time() {
        let a = TimeApplier::new();
        let sys = Sysroot::at("/tmp/never");
        let mut c = Config::default();
        c.system.time.manual_datetime = Some("not a date".into());
        assert!(a.validate(&sys, &c).is_err());
    }

    #[test]
    fn rejects_gps_without_device() {
        let a = TimeApplier::new();
        let sys = Sysroot::at("/tmp/never");
        let mut c = Config::default();
        c.system.time.gps_enabled = true;
        assert!(a.validate(&sys, &c).is_err());
    }

    #[test]
    fn rejects_gps_with_ptp() {
        let a = TimeApplier::new();
        let sys = Sysroot::at("/tmp/never");
        let mut c = Config::default();
        c.system.time.gps_enabled = true;
        c.system.time.gps_device = Some("/dev/ttyUSB0".into());
        c.system.time.ptp_enabled = true;
        c.system.time.ptp_interface = Some("eth0".into());
        assert!(a.validate(&sys, &c).is_err());
    }

    #[test]
    fn ntp_dropin_writes_server_list_to_fake_sysroot() {
        let dir = tempfile::tempdir().unwrap();
        let sys = Sysroot::at(dir.path());
        let a = TimeApplier::new();
        let mut c = Config::default();
        c.system.time.ntp_enabled = true;
        c.system.ntp_servers = vec!["pool.ntp.org".into(), "time.example.com".into()];
        a.apply(&sys, &c).unwrap();
        let written = std::fs::read_to_string(
            dir.path()
                .join("etc/systemd/timesyncd.conf.d/webconfig.conf"),
        )
        .unwrap();
        assert!(written.contains("[Time]"));
        assert!(written.contains("NTP=pool.ntp.org time.example.com"));
    }

    #[test]
    fn empty_ntp_servers_removes_dropin() {
        let dir = tempfile::tempdir().unwrap();
        let sys = Sysroot::at(dir.path());
        let a = TimeApplier::new();
        let mut c = Config::default();
        c.system.time.ntp_enabled = true;
        c.system.ntp_servers = vec!["pool.ntp.org".into()];
        a.apply(&sys, &c).unwrap();
        let path = dir
            .path()
            .join("etc/systemd/timesyncd.conf.d/webconfig.conf");
        assert!(path.exists());

        c.system.ntp_servers = Vec::new();
        a.apply(&sys, &c).unwrap();
        assert!(!path.exists());
    }

    #[test]
    fn rejects_invalid_ntp_server() {
        let a = TimeApplier::new();
        let sys = Sysroot::at("/tmp/never");
        let mut c = Config::default();
        c.system.ntp_servers = vec!["server; rm -rf /".into()];
        assert!(a.validate(&sys, &c).is_err());
    }
}
