//! Networking section handlers (spec §4.2 — flagship commit/confirm rollback).
//!
//! Ethernet (dynamic enumeration), Wi-Fi scan/join, AP provisioning mode, DNS,
//! service-based firewall, connectivity test. Any severing change is applied
//! *provisionally*; the user must `POST /network/confirm` within the window or
//! the backend auto-rolls-back via the engine bridge's confirm timer
//! (spec §4.2).

use axum::extract::State;
use axum::Json;
use serde::Serialize;
use tower_sessions::Session;
use webconfig_engine::store::network::NetworkConfig;

use crate::api::{apply_and_audit, audit, username};
use crate::auth::AuthRole;
use crate::error::{ApiError, ApiResult};
use crate::state::AppState;

/// `GET /api/v1/network` — the network section + enumerated live interfaces.
pub async fn get(State(app): State<AppState>) -> ApiResult<Json<serde_json::Value>> {
    let cfg = app.engine.load()?;
    let live = enumerate_live(&app.engine.sysroot);
    Ok(Json(serde_json::json!({
        "config": cfg.network,
        "live": live,
    })))
}

/// Enumerate live interfaces from `/sys/class/net` (spec §4.2: never hardcoded;
/// the Zynq UltraScale+ board can expose multiple MACs). Shared by the network page and
/// the diagnostics bundle (spec §4.4 "network state"), so the two never drift.
pub fn enumerate_live(sysroot: &webconfig_engine::Sysroot) -> Vec<serde_json::Value> {
    let netdir = sysroot.join("/sys/class/net");
    let mut live = Vec::new();
    if let Ok(entries) = std::fs::read_dir(&netdir) {
        for e in entries.flatten() {
            let name = e.file_name().to_string_lossy().to_string();
            if name == "lo" {
                continue;
            }
            let mac = std::fs::read_to_string(netdir.join(&name).join("address"))
                .map(|s| s.trim().to_string())
                .unwrap_or_default();
            let operstate = std::fs::read_to_string(netdir.join(&name).join("operstate"))
                .map(|s| s.trim().to_string())
                .unwrap_or_else(|_| "unknown".into());
            let addresses = live_addresses(sysroot, &name);
            live.push(serde_json::json!({
                "name": name,
                "mac": mac,
                "operstate": operstate,
                "addresses": addresses,
            }));
        }
    }
    live
}

fn live_addresses(sysroot: &webconfig_engine::Sysroot, iface: &str) -> Vec<String> {
    if !sysroot.is_real_root() {
        return Vec::new();
    }
    let Ok(raw) =
        webconfig_engine::applier::run_cmd("ip", &["-j", "address", "show", "dev", iface])
    else {
        return Vec::new();
    };
    let Ok(entries) = serde_json::from_str::<Vec<serde_json::Value>>(&raw) else {
        return Vec::new();
    };
    entries
        .iter()
        .flat_map(|entry| entry["addr_info"].as_array().into_iter().flatten())
        .filter_map(|address| address["local"].as_str().map(str::to_string))
        .collect()
}

/// `PUT /api/v1/network` — update the network section. Admin only (operator
/// may not edit network — severing risk).
pub async fn put(
    State(app): State<AppState>,
    role: AuthRole,
    session: Session,
    Json(req): Json<NetworkConfig>,
) -> ApiResult<Json<serde_json::Value>> {
    role.require_admin("network.put")?;
    let user = username(&session).await;
    let cfg = app.engine.update(move |c| {
        c.network = req.clone();
        Ok(())
    })?;
    audit(&app, &user, "network.put", None);
    Ok(Json(serde_json::to_value(&cfg.network).unwrap_or_default()))
}

/// `POST /api/v1/network/apply` — apply networkd + firewall. Returns
/// `provisional: true` when the change could sever the session (spec §4.2).
pub async fn apply(
    State(app): State<AppState>,
    role: AuthRole,
    session: Session,
) -> ApiResult<Json<serde_json::Value>> {
    role.require_admin("network.apply")?;
    let user = username(&session).await;
    app.engine.validate("networkd").await?;
    app.engine.validate("firewall").await?;
    app.engine.validate("wifi").await?;
    app.engine.validate("ap").await?;
    let outcome = apply_and_audit(&app, &user, "networkd").await?;
    let provisional = matches!(outcome, webconfig_engine::ApplyOutcome::Provisional { .. });
    let window = if let webconfig_engine::ApplyOutcome::Provisional {
        confirm_window_secs,
    } = outcome
    {
        confirm_window_secs
    } else {
        0
    };

    // If networkd already went Provisional, a rollback timer is now ticking
    // in the background regardless of what happens next — a downstream
    // applier failing here must not read to the operator as a clean no-op;
    // the error response has to carry that pending-rollback state too
    // (Checkpoint 1.10).
    for subsystem in ["firewall", "wifi", "ap"] {
        if let Err(e) = apply_and_audit(&app, &user, subsystem).await {
            if provisional {
                return Err(ApiError::Conflict(format!(
                    "{subsystem} apply failed ({e}); networkd was already applied \
                     provisionally (confirm_window_secs={window}) — a rollback timer is \
                     pending unless you confirm now or fix and re-apply before it expires"
                )));
            }
            return Err(e);
        }
    }

    Ok(Json(serde_json::json!({
        "provisional": provisional,
        "confirm_window_secs": window,
    })))
}

/// `POST /api/v1/network/confirm` — confirm the provisional change (spec §4.2).
pub async fn confirm(
    State(app): State<AppState>,
    role: AuthRole,
    session: Session,
) -> ApiResult<Json<serde_json::Value>> {
    role.require_admin("network.confirm")?;
    let user = username(&session).await;
    let ok = app.engine.confirm_pending().await?;
    audit(
        &app,
        &user,
        "network.confirm",
        Some(format!("confirmed={ok}")),
    );
    Ok(Json(serde_json::json!({ "confirmed": ok })))
}

/// `POST /api/v1/network/rollback` — manually roll back the provisional change.
pub async fn rollback(
    State(app): State<AppState>,
    role: AuthRole,
    session: Session,
) -> ApiResult<Json<serde_json::Value>> {
    role.require_admin("network.rollback")?;
    let user = username(&session).await;
    app.engine.confirm_pending().await.ok(); // clear pending
    app.engine.rollback("networkd").await?;
    app.engine.rollback("firewall").await.ok();
    app.engine.rollback("wifi").await.ok();
    app.engine.rollback("ap").await.ok();
    audit(&app, &user, "network.rollback", None);
    Ok(Json(serde_json::json!({ "rolled_back": true })))
}

/// `GET /api/v1/network/scan` — Wi-Fi scan (SSID/signal list, spec §4.2).
/// Scans the first live wireless interface (detected from `/sys/class/net`,
/// never hardcoded — some interfaces are named `wlp*`); on the fake sysroot
/// returns an empty list so the UI still renders.
pub async fn scan(State(app): State<AppState>, _role: AuthRole) -> ApiResult<Json<Vec<WifiScan>>> {
    let sys = &app.engine.sysroot;
    if sys.is_real_root() {
        let Some(iface) = first_wireless_iface(sys) else {
            // No wireless hardware present — return an empty scan rather than
            // erroring on a missing `wlan0` (spec §4.2).
            return Ok(Json(Vec::new()));
        };
        let out = webconfig_engine::applier::run_cmd("iw", &["dev", &iface, "scan"])
            .map_err(ApiError::from)?;
        // Parse the `iw scan` dump for SSID + signal. Best-effort.
        let mut nets = Vec::new();
        let mut cur: Option<WifiScan> = None;
        for line in out.lines() {
            let line = line.trim();
            if line.starts_with("BSS ") {
                if let Some(c) = cur.take() {
                    nets.push(c);
                }
                cur = Some(WifiScan::default());
            } else if let Some(s) = line.strip_prefix("SSID: ") {
                if let Some(c) = cur.as_mut() {
                    c.ssid = s.trim().to_string();
                }
            } else if let Some(s) = line.strip_prefix("signal: ") {
                if let Some(c) = cur.as_mut() {
                    c.signal_dbm = s
                        .split_whitespace()
                        .next()
                        .and_then(|n| n.parse().ok())
                        .unwrap_or(0);
                }
            }
        }
        if let Some(c) = cur.take() {
            nets.push(c);
        }
        return Ok(Json(nets));
    }
    Ok(Json(Vec::new()))
}

/// Pick the first wireless interface from `/sys/class/net` (spec §4.2: never
/// hardcode `wlan0`). A wireless interface exposes a `wireless` symlink and/or
/// a `phy80211` directory under its net-class node. `None` when no Wi-Fi
/// hardware is present.
fn first_wireless_iface(sysroot: &webconfig_engine::Sysroot) -> Option<String> {
    let netdir = sysroot.join("/sys/class/net");
    let entries = std::fs::read_dir(&netdir).ok()?;
    for e in entries.flatten() {
        let name = e.file_name().to_string_lossy().to_string();
        if name == "lo" {
            continue;
        }
        let has_wireless = sysroot
            .join(format!("/sys/class/net/{name}/wireless"))
            .exists();
        let has_phy = sysroot
            .join(format!("/sys/class/net/{name}/phy80211"))
            .exists();
        if has_wireless || has_phy {
            return Some(name);
        }
    }
    None
}

#[derive(Serialize, Default, serde::Deserialize)]
pub struct WifiScan {
    pub ssid: String,
    pub signal_dbm: i32,
}

/// `POST /api/v1/network/connectivity-test` — ping gateway → public IP → DNS
/// resolve, reporting which layer failed (spec §4.2).
#[derive(Serialize)]
pub struct ConnectivityResult {
    pub gateway: LayerResult,
    pub public: LayerResult,
    pub dns: LayerResult,
}

#[derive(Serialize)]
pub struct LayerResult {
    pub ok: bool,
    pub detail: String,
}

pub async fn connectivity_test(
    State(app): State<AppState>,
    _role: AuthRole,
) -> ApiResult<Json<ConnectivityResult>> {
    let sys = &app.engine.sysroot;
    let cfg = app.engine.load()?;
    // Use the configured gateway of the first interface that has one (spec
    // §4.2: ping gateway → ping public IP → DNS resolve). Fall back to a
    // conventional default only if nothing is configured.
    let gateway = cfg
        .network
        .interfaces
        .iter()
        .find_map(|i| i.gateway.clone())
        .unwrap_or_else(|| "192.168.1.1".into());
    let run = |cmd: &str, args: &[&str]| -> (bool, String) {
        if !sys.is_real_root() {
            return (true, "skipped (fake sysroot)".into());
        }
        match webconfig_engine::applier::run_cmd(cmd, args) {
            Ok(_) => (true, "ok".into()),
            Err(e) => (false, e.to_string()),
        }
    };
    Ok(Json(ConnectivityResult {
        gateway: {
            let (ok, d) = run("ping", &["-c", "1", "-W", "1", &gateway]);
            LayerResult {
                ok,
                detail: format!("gateway {gateway}: {d}"),
            }
        },
        public: {
            let (ok, d) = run("ping", &["-c", "1", "-W", "1", "1.1.1.1"]);
            LayerResult { ok, detail: d }
        },
        dns: {
            // Resolve a well-known hostname through the system resolver
            // (global DNS override, per-interface DHCP-provided servers, or
            // /etc/resolv.conf as shipped — whichever it's actually
            // configured to use). Pinging the DNS server itself would only
            // prove L3 reachability, not resolution. getent's exit status
            // proves resolution succeeded but can't say which server
            // answered, so the detail names the configured *preference*
            // rather than claiming it as the proven source.
            let (ok, d) = run("getent", &["hosts", "example.com"]);
            LayerResult {
                ok,
                detail: if ok {
                    match cfg.network.dns_servers.first() {
                        Some(dns) => format!(
                            "resolved example.com via the system resolver (configured DNS preference: {dns})"
                        ),
                        None => "resolved example.com via the system resolver (no global DNS override configured)".into(),
                    }
                } else {
                    format!("resolve failed: {d}")
                },
            }
        },
    }))
}

#[cfg(test)]
mod tests {
    use super::first_wireless_iface;
    use webconfig_engine::Sysroot;

    fn fake_sysroot() -> tempfile::TempDir {
        tempfile::tempdir().unwrap()
    }

    #[test]
    fn picks_wireless_iface_by_wireless_node() {
        // `wlan0` exposes the `wireless` symlink — the classic convention.
        let dir = fake_sysroot();
        let sys = Sysroot::at(dir.path());
        std::fs::create_dir_all(sys.join("/sys/class/net/eth0")).unwrap();
        std::fs::create_dir_all(sys.join("/sys/class/net/wlan0/wireless")).unwrap();
        std::fs::create_dir_all(sys.join("/sys/class/net/lo")).unwrap();
        assert_eq!(first_wireless_iface(&sys).as_deref(), Some("wlan0"));
    }

    #[test]
    fn picks_wireless_iface_by_phy80211_node() {
        // Some radios are named `wlp3s0` and expose `phy80211` instead of
        // `wireless` — must still be detected, not hardcoded to `wlan0`
        // (spec §4.2).
        let dir = fake_sysroot();
        let sys = Sysroot::at(dir.path());
        std::fs::create_dir_all(sys.join("/sys/class/net/wlp3s0/phy80211")).unwrap();
        assert_eq!(first_wireless_iface(&sys).as_deref(), Some("wlp3s0"));
    }

    #[test]
    fn returns_none_when_no_wireless_hardware() {
        let dir = fake_sysroot();
        let sys = Sysroot::at(dir.path());
        std::fs::create_dir_all(sys.join("/sys/class/net/eth0")).unwrap();
        assert_eq!(first_wireless_iface(&sys), None);
    }
}
