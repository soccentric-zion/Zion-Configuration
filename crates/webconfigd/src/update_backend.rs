//! Update backend selection (spec §4.4 / §7).
//!
//! Re-exports the engine's narrow `UpdateBackend` trait and picks the real
//! (RAUC) or mock backend based on the sysroot: a tempdir/fake sysroot uses
//! the mock so CI runs with no hardware (spec §6); the real root uses RAUC.

pub use webconfig_engine::update::{
    MockUpdateBackend, RaucUpdateBackend, SlotStatus, UpdateBackend,
};

use std::process::Command;
use std::sync::Arc;
use webconfig_engine::Sysroot;

/// Pick the backend: RAUC on a real root when the RAUC executable is present,
/// mock on a fake sysroot or a developer host without RAUC installed.
pub fn make_backend(sysroot: &Sysroot) -> Arc<dyn UpdateBackend> {
    let real_root = sysroot.is_real_root();
    let rauc_ok = rauc_available();
    if real_root && rauc_ok {
        return Arc::new(RaucUpdateBackend::new());
    }
    if is_degraded_fallback(real_root, rauc_ok) {
        // A real device with no `rauc` on PATH is a degraded, unexpected
        // state — silently presenting fabricated Slot A/B data here would be
        // indistinguishable from the intentional dev/CI fake-sysroot case
        // (Checkpoint 1.5).
        tracing::warn!(
            "real root but rauc is unavailable — falling back to a mock update backend \
             with fabricated slot data; OS update status/install will not reflect reality"
        );
    }
    // Seed the mock with a plausible A/B pair so the Updates page has data.
    Arc::new(MockUpdateBackend::new(vec![
        SlotStatus {
            slot: "A".into(),
            version: "1.0.0".into(),
            running: true,
            confirmed: true,
            state: "good".into(),
        },
        SlotStatus {
            slot: "B".into(),
            version: "0.9.0".into(),
            running: false,
            confirmed: false,
            state: "unknown".into(),
        },
    ]))
}

/// Whether falling back to the mock backend is an *unexpected, degraded*
/// state (real hardware with no rauc on PATH) versus the *intentional*
/// dev/CI fake-sysroot case. Split out as a pure predicate so the decision is
/// unit-testable without depending on the actual filesystem root or a real
/// `rauc` binary being present.
fn is_degraded_fallback(is_real_root: bool, rauc_available: bool) -> bool {
    is_real_root && !rauc_available
}

fn rauc_available() -> bool {
    Command::new("rauc").arg("--version").output().is_ok()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn degraded_fallback_only_when_real_root_without_rauc() {
        // Checkpoint 1.5: the mock fallback is only "degraded" (worth a
        // warning) on real hardware with rauc missing — never on a fake
        // sysroot (dev/CI, spec §6), and never when rauc is actually there.
        assert!(is_degraded_fallback(true, false));
        assert!(!is_degraded_fallback(true, true));
        assert!(!is_degraded_fallback(false, false));
        assert!(!is_degraded_fallback(false, true));
    }
}
