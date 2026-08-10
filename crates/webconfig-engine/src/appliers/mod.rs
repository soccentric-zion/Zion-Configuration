//! Appliers (spec §3, §7.3). One module per system verb.
//!
//! Each applier implements [`crate::applier::Applier`] with the exact
//! `validate / apply / rollback` shape, takes a sysroot parameter, and is
//! testable against a tempdir fake sysroot with no hardware. Real system
//! interactions that cannot be fully implemented go through a narrow trait
//! with a real implementation **and** a mock — never a silent TODO (spec §7).

pub mod ap;
pub mod can;
pub mod firewall;
pub mod gpio;
pub mod hostname;
pub mod journald;
pub mod networkd;
pub mod serial;
pub mod ssh;
pub mod time;
pub mod wifi;
pub mod wireguard;

pub use ap::ApApplier;
pub use can::CanApplier;
pub use firewall::FirewallApplier;
pub use gpio::GpioApplier;
pub use hostname::HostnameApplier;
pub use journald::JournaldApplier;
pub use networkd::NetworkdApplier;
pub use serial::SerialApplier;
pub use ssh::SshApplier;
pub use time::TimeApplier;
pub use wifi::WifiApplier;
pub use wireguard::WireGuardApplier;

use crate::applier::Applier;
use crate::manifest::{Peripheral, WifiRadio};

/// The full ordered applier set for a platform. Order matters only where one
/// applier depends on another's output (e.g. CAN uses interfaces networkd
/// brought up); here each is independent so ordering is by spec §4 section.
///
/// `manifest_peripherals` and `wifi_radio` are snapshots of the board
/// manifest (only `GpioApplier` needs the former, to resolve a GPIO pin's
/// manifest `name` to its `gpiochip<N> line <M>` device string; only
/// `ApApplier` needs the latter, for its interface/hw_mode/channel) — the
/// manifest doesn't change at runtime, so a one-time snapshot at construction
/// is sufficient; no need to thread it through every applier's `apply()` call.
pub fn all(manifest_peripherals: &[Peripheral], wifi_radio: &WifiRadio) -> Vec<Box<dyn Applier>> {
    vec![
        Box::new(HostnameApplier::new()),
        Box::new(TimeApplier::new()),
        Box::new(SshApplier::new()),
        Box::new(NetworkdApplier::new()),
        Box::new(FirewallApplier::new()),
        Box::new(WifiApplier::new()),
        Box::new(ApApplier::new(wifi_radio)),
        Box::new(CanApplier::new()),
        Box::new(SerialApplier::new()),
        Box::new(GpioApplier::new(manifest_peripherals)),
        Box::new(JournaldApplier::new()),
        Box::new(WireGuardApplier::new()),
    ]
}
