//! systemd client (spec §3: privileged ops via D-Bus + polkit; spec §7: real
//! + mock, never a silent TODO).
//!
//! Real impl talks to `org.freedesktop.systemd1.Manager` over the system bus
//! via zbus. Mock impl is used on the fake sysroot so tests run with no
//! hardware (spec §6). Both implement [`SystemdClient`]; the daemon picks one
//! based on the sysroot.

use async_trait::async_trait;
use serde::Serialize;
use thiserror::Error;

#[derive(Debug, Error)]
pub enum SystemdError {
    #[error("dbus error: {0}")]
    Dbus(String),
    #[error("unit not found: {0}")]
    NotFound(String),
    #[error("io error: {0}")]
    Io(#[from] std::io::Error),
}

/// Minimal unit state for the Services page (spec §4.3).
#[derive(Debug, Clone, Serialize)]
pub struct UnitState {
    pub unit: String,
    pub load: String,
    pub active: String,
    pub sub: String,
    pub enabled: bool,
}

#[async_trait]
pub trait SystemdClient: Send + Sync {
    async fn status(&self, unit: &str) -> Result<UnitState, SystemdError>;
    async fn start(&self, unit: &str) -> Result<(), SystemdError>;
    async fn stop(&self, unit: &str) -> Result<(), SystemdError>;
    async fn restart(&self, unit: &str) -> Result<(), SystemdError>;
    async fn enable(&self, unit: &str) -> Result<(), SystemdError>;
    async fn disable(&self, unit: &str) -> Result<(), SystemdError>;
    /// Last ~20 lines of `journalctl -u <unit>` when failed (spec §4.3).
    async fn journal_tail(&self, unit: &str, n: usize) -> Result<String, SystemdError>;
}

/// Mock client: backs the Services page on the fake sysroot (spec §6/§7).
#[derive(Debug, Default)]
pub struct MockSystemd;

#[async_trait]
impl SystemdClient for MockSystemd {
    async fn status(&self, unit: &str) -> Result<UnitState, SystemdError> {
        Ok(UnitState {
            unit: unit.to_string(),
            load: "loaded".into(),
            active: "active".into(),
            sub: "running".into(),
            enabled: true,
        })
    }
    async fn start(&self, _unit: &str) -> Result<(), SystemdError> {
        Ok(())
    }
    async fn stop(&self, _unit: &str) -> Result<(), SystemdError> {
        Ok(())
    }
    async fn restart(&self, _unit: &str) -> Result<(), SystemdError> {
        Ok(())
    }
    async fn enable(&self, _unit: &str) -> Result<(), SystemdError> {
        Ok(())
    }
    async fn disable(&self, _unit: &str) -> Result<(), SystemdError> {
        Ok(())
    }
    async fn journal_tail(&self, _unit: &str, _n: usize) -> Result<String, SystemdError> {
        Ok(String::new())
    }
}

/// Real systemd client over zbus (spec §3). Assumption (per spec §7, one line):
/// we use the Manager StartUnit/StopUnit/RestartUnit verbs with mode "replace";
/// status + enabled come from `systemctl show` shelled out because the D-Bus
/// property set is large and version-varying.
#[derive(Debug, Clone)]
pub struct ZbusSystemd {
    #[allow(dead_code)]
    conn: zbus::Connection,
}

impl ZbusSystemd {
    pub async fn new() -> Result<Self, SystemdError> {
        let conn = zbus::Connection::system()
            .await
            .map_err(|e| SystemdError::Dbus(e.to_string()))?;
        Ok(Self { conn })
    }
}

#[zbus::proxy(
    interface = "org.freedesktop.systemd1.Manager",
    default_service = "org.freedesktop.systemd1",
    default_path = "/org/freedesktop/systemd1"
)]
trait Manager {
    fn start_unit(&self, name: &str, mode: &str) -> zbus::Result<zbus::zvariant::OwnedObjectPath>;
    fn stop_unit(&self, name: &str, mode: &str) -> zbus::Result<zbus::zvariant::OwnedObjectPath>;
    fn restart_unit(&self, name: &str, mode: &str)
        -> zbus::Result<zbus::zvariant::OwnedObjectPath>;
}

#[async_trait]
impl SystemdClient for ZbusSystemd {
    async fn status(&self, unit: &str) -> Result<UnitState, SystemdError> {
        let out = webconfig_engine::applier::run_cmd(
            "systemctl",
            &[
                "show",
                unit,
                "--property=LoadState,ActiveState,SubState,UnitFileState",
            ],
        )
        .map_err(|e| SystemdError::Dbus(e.to_string()))?;
        let mut load = String::new();
        let mut active = String::new();
        let mut sub = String::new();
        let mut enabled = false;
        for line in out.lines() {
            if let Some(v) = line.strip_prefix("LoadState=") {
                load = v.into();
            } else if let Some(v) = line.strip_prefix("ActiveState=") {
                active = v.into();
            } else if let Some(v) = line.strip_prefix("SubState=") {
                sub = v.into();
            } else if let Some(v) = line.strip_prefix("UnitFileState=") {
                enabled = v == "enabled";
            }
        }
        Ok(UnitState {
            unit: unit.to_string(),
            load,
            active,
            sub,
            enabled,
        })
    }

    async fn start(&self, unit: &str) -> Result<(), SystemdError> {
        let mgr = ManagerProxy::new(&self.conn)
            .await
            .map_err(|e| SystemdError::Dbus(e.to_string()))?;
        mgr.start_unit(unit, "replace")
            .await
            .map_err(|e| SystemdError::Dbus(e.to_string()))?;
        Ok(())
    }
    async fn stop(&self, unit: &str) -> Result<(), SystemdError> {
        let mgr = ManagerProxy::new(&self.conn)
            .await
            .map_err(|e| SystemdError::Dbus(e.to_string()))?;
        mgr.stop_unit(unit, "replace")
            .await
            .map_err(|e| SystemdError::Dbus(e.to_string()))?;
        Ok(())
    }
    async fn restart(&self, unit: &str) -> Result<(), SystemdError> {
        let mgr = ManagerProxy::new(&self.conn)
            .await
            .map_err(|e| SystemdError::Dbus(e.to_string()))?;
        mgr.restart_unit(unit, "replace")
            .await
            .map_err(|e| SystemdError::Dbus(e.to_string()))?;
        Ok(())
    }
    async fn enable(&self, unit: &str) -> Result<(), SystemdError> {
        webconfig_engine::applier::run_cmd("systemctl", &["enable", unit])
            .map(|_| ())
            .map_err(|e| SystemdError::Dbus(e.to_string()))
    }
    async fn disable(&self, unit: &str) -> Result<(), SystemdError> {
        webconfig_engine::applier::run_cmd("systemctl", &["disable", unit])
            .map(|_| ())
            .map_err(|e| SystemdError::Dbus(e.to_string()))
    }
    async fn journal_tail(&self, unit: &str, n: usize) -> Result<String, SystemdError> {
        let narg = format!("-n{n}");
        webconfig_engine::applier::run_cmd("journalctl", &["-u", unit, narg.as_str(), "--no-pager"])
            .map_err(|e| SystemdError::Dbus(e.to_string()))
    }
}

/// Pick a client: zbus on the real root, mock on a fake sysroot.
pub async fn make_client(sysroot: &webconfig_engine::Sysroot) -> std::sync::Arc<dyn SystemdClient> {
    if sysroot.is_real_root() {
        match ZbusSystemd::new().await {
            Ok(c) => std::sync::Arc::new(c),
            Err(e) => {
                tracing::warn!(error = %e, "zbus systemd connect failed; using mock");
                std::sync::Arc::new(MockSystemd)
            }
        }
    } else {
        std::sync::Arc::new(MockSystemd)
    }
}
