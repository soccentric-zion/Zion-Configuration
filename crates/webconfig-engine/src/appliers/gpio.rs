//! GPIO applier (spec §4.9 — manifest-named pins only, never raw banks).
//!
//! Unlike CAN/serial, `GpioPin` (the stored config) carries only a manifest
//! `name` — the actual `gpiochip<N> line <M>` device string lives on the
//! matching manifest `Peripheral` entry, not in the stored config (spec §4.9:
//! "GPIO: manifest-**named** pins only"). This applier is therefore
//! constructed with a snapshot of the board manifest's GPIO peripherals
//! (captured once at daemon startup — the manifest does not change at
//! runtime) so `apply()` can resolve name → device without threading the
//! manifest through the whole `Applier` trait for every applier.
//!
//! Materializes **output** pins only: direction/active_low/initial-state are
//! brought up at boot via a generated systemd unit running
//! `gpioset --mode=signal <chip> <line>=<value>` (assumption per spec §7:
//! libgpiod v1.x CLI, matching the `gpioget`/`gpioset` tools the existing
//! manual-toggle handler already shells out to). `--mode=signal` keeps the
//! process running and holding the line at that value until the unit is
//! stopped — the classic libgpiod v1.x pattern for a persistently-driven
//! line, since `--mode=exit` releases the line immediately after setting it.
//! Input pins have nothing to actively drive at boot and are not applied here
//! (direction is read, not written, for them).

use std::sync::Mutex;

use crate::applier::{run_cmd, Applier, ApplyError, ApplyOutcome, FileBackup};
use crate::manifest::{IoKind, Peripheral};
use crate::store::io::GpioPin;
use crate::store::Config;
use crate::sysroot::Sysroot;

#[derive(Debug)]
pub struct GpioApplier {
    /// Manifest GPIO peripherals, captured at construction (name → device).
    peripherals: Vec<Peripheral>,
    backups: Mutex<Vec<FileBackup>>,
}

impl GpioApplier {
    pub fn new(manifest_peripherals: &[Peripheral]) -> Self {
        Self {
            peripherals: manifest_peripherals
                .iter()
                .filter(|p| p.kind == IoKind::Gpio)
                .cloned()
                .collect(),
            backups: Mutex::new(Vec::new()),
        }
    }

    fn unit_path(sysroot: &Sysroot, name: &str) -> std::path::PathBuf {
        sysroot.join(format!("/etc/systemd/system/webconfig-gpio-{name}.service"))
    }

    /// Parse a manifest GPIO `device` string of the form `gpiochip1 line 7`
    /// into its chip id and line offset — the shape `gpioset`/`gpioget`
    /// expect (same convention as the manual-toggle HTTP handler).
    fn parse_device(device: &str) -> Option<(&str, &str)> {
        let parts: Vec<&str> = device.split_whitespace().collect();
        if parts.len() == 3 && parts[1] == "line" {
            Some((parts[0], parts[2]))
        } else {
            None
        }
    }

    fn device_for(&self, name: &str) -> Option<&str> {
        self.peripherals
            .iter()
            .find(|p| p.id == name)
            .map(|p| p.device.as_str())
    }

    /// Render the boot-time bring-up unit for one output pin.
    fn render_unit(name: &str, chip: &str, line: &str, physical_value: u8) -> String {
        format!(
            "[Unit]\nDescription=GPIO bring-up for {name}\nAfter=sysinit.target\n\n\
             [Service]\nType=simple\nExecStart=/usr/bin/gpioset --mode=signal {chip} {line}={physical_value}\n\
             Restart=on-failure\n\n\
             [Install]\nWantedBy=multi-user.target\n"
        )
    }

    fn validate_one(p: &GpioPin) -> Result<(), ApplyError> {
        if p.name.is_empty() {
            return Err(ApplyError::Validation {
                stage: crate::applier::ApplyStage::Validate,
                message: "GPIO pin with empty name".into(),
            });
        }
        if p.direction != "in" && p.direction != "out" {
            return Err(ApplyError::Validation {
                stage: crate::applier::ApplyStage::Validate,
                message: format!("GPIO {} direction must be in|out", p.name),
            });
        }
        Ok(())
    }
}

impl Applier for GpioApplier {
    fn id(&self) -> &'static str {
        "gpio"
    }

    fn validate(&self, _sysroot: &Sysroot, config: &Config) -> Result<(), ApplyError> {
        for p in &config.io.gpio {
            Self::validate_one(p)?;
            if p.enabled && !self.peripherals.iter().any(|m| m.id == p.name) {
                return Err(ApplyError::Validation {
                    stage: crate::applier::ApplyStage::Validate,
                    message: format!("GPIO {} not declared in board manifest", p.name),
                });
            }
        }
        Ok(())
    }

    fn apply(&self, sysroot: &Sysroot, config: &Config) -> Result<ApplyOutcome, ApplyError> {
        let mut backups = self.backups.lock().unwrap();
        backups.clear();
        // Subsystem gate, matching CAN/serial (spec: `gpio_enabled` gates the
        // detail settings).
        if !config.io.gpio_enabled {
            return Ok(ApplyOutcome::Committed);
        }
        for p in &config.io.gpio {
            let unit_name = format!("webconfig-gpio-{}.service", p.name);
            if !p.enabled || p.direction != "out" {
                // Disabled, or an input pin (nothing to actively drive) — stop
                // and disable any unit a previous apply brought up so turning
                // a pin off (or changing it to an input) actually takes effect.
                if sysroot.is_real_root() {
                    let _ = run_cmd("systemctl", &["stop", &unit_name]);
                    let _ = run_cmd("systemctl", &["disable", &unit_name]);
                }
                continue;
            }
            Self::validate_one(p)?;
            let Some(device) = self.device_for(&p.name) else {
                return Err(ApplyError::Validation {
                    stage: crate::applier::ApplyStage::Apply,
                    message: format!("GPIO {} not declared in board manifest", p.name),
                });
            };
            let Some((chip, line)) = Self::parse_device(device) else {
                return Err(ApplyError::Validation {
                    stage: crate::applier::ApplyStage::Apply,
                    message: format!("GPIO {} manifest device {device:?} malformed", p.name),
                });
            };
            let logical = p.initial_state.unwrap_or(false);
            let physical_value: u8 = if logical != p.active_low { 1 } else { 0 };

            let path = Self::unit_path(sysroot, &p.name);
            let backup = FileBackup::capture(path.clone())?;
            if let Some(parent) = path.parent() {
                std::fs::create_dir_all(parent)?;
            }
            std::fs::write(
                &path,
                Self::render_unit(&p.name, chip, line, physical_value),
            )?;
            backups.push(backup);
            if sysroot.is_real_root() {
                let _ = run_cmd("systemctl", &["enable", &unit_name]);
                let _ = run_cmd("systemctl", &["restart", &unit_name]);
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

    fn peripheral(id: &str, device: &str) -> Peripheral {
        Peripheral {
            id: id.into(),
            device: device.into(),
            human_name: id.into(),
            kind: IoKind::Gpio,
            ..Default::default()
        }
    }

    fn pin(name: &str, direction: &str, active_low: bool, initial_state: Option<bool>) -> GpioPin {
        GpioPin {
            name: name.into(),
            enabled: true,
            direction: direction.into(),
            active_low,
            initial_state,
        }
    }

    #[test]
    fn rejects_empty_name() {
        let a = GpioApplier::new(&[]);
        let sys = Sysroot::at("/tmp/never");
        let mut c = Config::default();
        c.io.gpio.push(pin("", "out", false, None));
        assert!(a.validate(&sys, &c).is_err());
    }

    #[test]
    fn rejects_bad_direction() {
        let a = GpioApplier::new(&[peripheral("led0", "gpiochip1 line 7")]);
        let sys = Sysroot::at("/tmp/never");
        let mut c = Config::default();
        c.io.gpio.push(pin("led0", "sideways", false, None));
        assert!(a.validate(&sys, &c).is_err());
    }

    #[test]
    fn rejects_pin_not_in_manifest() {
        let a = GpioApplier::new(&[]); // no manifest peripherals
        let sys = Sysroot::at("/tmp/never");
        let mut c = Config::default();
        c.io.gpio.push(pin("led0", "out", false, Some(true)));
        assert!(a.validate(&sys, &c).is_err());
    }

    #[test]
    fn writes_unit_for_output_pin_and_rolls_back() {
        let dir = tempfile::tempdir().unwrap();
        let sys = Sysroot::at(dir.path());
        let a = GpioApplier::new(&[peripheral("led0", "gpiochip1 line 7")]);
        let mut c = Config::default();
        c.io.gpio_enabled = true;
        c.io.gpio.push(pin("led0", "out", false, Some(true)));
        a.apply(&sys, &c).unwrap();
        let f = sys.join("/etc/systemd/system/webconfig-gpio-led0.service");
        assert!(f.exists());
        let contents = std::fs::read_to_string(&f).unwrap();
        assert!(contents.contains("gpiochip1"));
        assert!(
            contents.contains("7=1"),
            "logical true, active_low=false -> physical 1: {contents}"
        );
        a.rollback(&sys).unwrap();
        assert!(!f.exists());
    }

    #[test]
    fn active_low_inverts_the_driven_value() {
        let dir = tempfile::tempdir().unwrap();
        let sys = Sysroot::at(dir.path());
        let a = GpioApplier::new(&[peripheral("led0", "gpiochip1 line 7")]);
        let mut c = Config::default();
        c.io.gpio_enabled = true;
        c.io.gpio.push(pin("led0", "out", true, Some(true)));
        a.apply(&sys, &c).unwrap();
        let f = sys.join("/etc/systemd/system/webconfig-gpio-led0.service");
        let contents = std::fs::read_to_string(&f).unwrap();
        assert!(
            contents.contains("7=0"),
            "logical true, active_low=true -> physical 0: {contents}"
        );
    }

    #[test]
    fn input_pin_writes_no_unit() {
        let dir = tempfile::tempdir().unwrap();
        let sys = Sysroot::at(dir.path());
        let a = GpioApplier::new(&[peripheral("btn0", "gpiochip1 line 3")]);
        let mut c = Config::default();
        c.io.gpio_enabled = true;
        c.io.gpio.push(pin("btn0", "in", false, None));
        a.apply(&sys, &c).unwrap();
        assert!(!sys
            .join("/etc/systemd/system/webconfig-gpio-btn0.service")
            .exists());
    }

    #[test]
    fn subsystem_disabled_writes_no_units() {
        let dir = tempfile::tempdir().unwrap();
        let sys = Sysroot::at(dir.path());
        let a = GpioApplier::new(&[peripheral("led0", "gpiochip1 line 7")]);
        let mut c = Config::default();
        c.io.gpio_enabled = false;
        c.io.gpio.push(pin("led0", "out", false, Some(true)));
        a.apply(&sys, &c).unwrap();
        assert!(!sys
            .join("/etc/systemd/system/webconfig-gpio-led0.service")
            .exists());
    }

    #[test]
    fn disabled_entry_writes_no_unit() {
        let dir = tempfile::tempdir().unwrap();
        let sys = Sysroot::at(dir.path());
        let a = GpioApplier::new(&[peripheral("led0", "gpiochip1 line 7")]);
        let mut c = Config::default();
        c.io.gpio_enabled = true;
        let mut entry = pin("led0", "out", false, Some(true));
        entry.enabled = false;
        c.io.gpio.push(entry);
        a.apply(&sys, &c).unwrap();
        assert!(!sys
            .join("/etc/systemd/system/webconfig-gpio-led0.service")
            .exists());
    }
}
