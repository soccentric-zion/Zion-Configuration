//! Alert engine (spec §4.7).
//!
//! Threshold rules: metric (temp, disk %, memory, service state, MCU
//! heartbeat) + comparison + **duration** ("temp > 85 for 60 s") to prevent
//! flapping. Actions: UI red-state + event log (always); webhook POST; MQTT
//! event topic. [v2] email. Alert history: fired/cleared ring-buffer file with
//! timestamps, viewable + exportable. Per-platform default rule packs ship as
//! schema files (Zion: throttling; all: disk-full, watchdog-reset).
//!
//! The engine evaluates rules on the same data the dashboard reads, on a timer
//! inside the daemon — no extra process (spec §4.7).

use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::fs::OpenOptions;
use std::io::Write;
use std::path::Path;

use crate::monitoring::Vitals;
use crate::store::alerts::AlertRule;
use rusqlite::{params, Connection};

/// One history entry — fired or cleared.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AlertEvent {
    pub ts: String,
    pub rule_id: String,
    pub state: AlertState,
    pub value: f64,
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "lowercase")]
pub enum AlertState {
    Fired,
    Cleared,
}

/// In-memory state for the duration anti-flap logic: the timestamp the
/// condition first became true (None = not currently true).
#[derive(Debug, Clone, Default)]
pub struct RuleState {
    pub held_since: Option<u64>,
    pub firing: bool,
}

/// The alert engine. `evaluate` is called on each timer tick with the current
/// vitals and the current monotonic-ish timestamp (seconds, supplied by the
/// caller so the engine stays deterministic and testable).
#[derive(Debug, Default)]
pub struct AlertEngine {
    states: HashMap<String, RuleState>,
}

impl AlertEngine {
    pub fn new() -> Self {
        Self::default()
    }

    /// Evaluate all rules against `vitals` at wall-clock `now_secs` (supplied
    /// by caller). Returns the set of events to log/emit this tick (fires and
    /// clears). Pure function of inputs + internal state; no I/O.
    pub fn evaluate(
        &mut self,
        rules: &[AlertRule],
        vitals: &Vitals,
        now_secs: u64,
    ) -> Vec<AlertEvent> {
        let mut out = Vec::new();
        for rule in rules {
            // Honor the per-rule `enabled` flag (spec §4.7 / API reference:
            // "override/disable a default by adding a same-id rule"). A disabled
            // rule never fires; if it was firing when it got disabled, emit a
            // clear so the UI red-state drops and no stale event lingers.
            if !rule.enabled {
                let st = self.states.entry(rule.id.clone()).or_default();
                if st.firing {
                    st.firing = false;
                    st.held_since = None;
                    out.push(AlertEvent {
                        ts: ts_from_secs(now_secs),
                        rule_id: rule.id.clone(),
                        state: AlertState::Cleared,
                        value: f64::NAN,
                    });
                }
                continue;
            }
            let value = match resolve_metric(&rule.metric, vitals) {
                Some(v) => v,
                None => continue, // metric not available this tick
            };
            let cond = compare(value, &rule.comparison, rule.threshold);
            let st = self.states.entry(rule.id.clone()).or_default();

            if cond {
                // First time true — record when we started holding.
                if st.held_since.is_none() {
                    st.held_since = Some(now_secs);
                }
                let held = now_secs.saturating_sub(st.held_since.unwrap());
                if !st.firing && held >= rule.duration_secs {
                    st.firing = true;
                    out.push(AlertEvent {
                        ts: ts_from_secs(now_secs),
                        rule_id: rule.id.clone(),
                        state: AlertState::Fired,
                        value,
                    });
                }
            } else if st.firing {
                // Condition cleared — emit clear.
                st.firing = false;
                st.held_since = None;
                out.push(AlertEvent {
                    ts: ts_from_secs(now_secs),
                    rule_id: rule.id.clone(),
                    state: AlertState::Cleared,
                    value,
                });
            } else if !cond {
                st.held_since = None;
            }
        }
        out
    }

    pub fn is_firing(&self, rule_id: &str) -> bool {
        self.states.get(rule_id).map(|s| s.firing).unwrap_or(false)
    }

    /// Drop all rule state, emitting a `Cleared` event for every rule that was
    /// firing. Used when the global alert switch (`alerts.enabled`) is turned
    /// off so the dashboard red-state resets and no stale firing state survives
    /// a disable/re-enable cycle (spec §4.7). Idempotent: an empty/cleared
    /// engine yields no events.
    pub fn clear_all(&mut self, now_secs: u64) -> Vec<AlertEvent> {
        let mut out = Vec::new();
        for (id, st) in self.states.drain() {
            if st.firing {
                out.push(AlertEvent {
                    ts: ts_from_secs(now_secs),
                    rule_id: id,
                    state: AlertState::Cleared,
                    value: f64::NAN,
                });
            }
        }
        out
    }
}

/// Resolve a metric id to a numeric value from the vitals snapshot.
/// Supported metric ids (spec §4.7):
///   - `temp` (max zone, °C), `temp.<zone>`
///   - `disk.pct` (max mount used %)
///   - `memory.pct` (used % of total)
///   - `service.<unit>` (1 = failed, 0 = ok) — wired by the caller via a
///     custom vitals extension; here returns None if not present.
///   - `mcu.heartbeat` (0 = stale, 1 = ok)
fn resolve_metric(metric: &str, vitals: &Vitals) -> Option<f64> {
    if metric == "temp" {
        return vitals
            .temperatures
            .iter()
            .map(|z| z.temp_millideg_c as f64 / 1000.0)
            .fold(f64::NEG_INFINITY, f64::max)
            .into_option();
    }
    // `temp.throttled` — 1.0 if any thermal zone is actively throttling (a
    // cooling device has cur_state > 0), else 0.0. Backs the per-platform
    // throttling default pack (spec §4.7, Zion). Must be checked BEFORE the
    // `temp.<zone>` prefix branch, which would otherwise treat "throttled" as
    // a zone name.
    if metric == "temp.throttled" {
        return Some(vitals.temperatures.iter().any(|z| z.throttled) as u8 as f64);
    }
    if let Some(zone) = metric.strip_prefix("temp.") {
        return vitals
            .temperatures
            .iter()
            .find(|z| z.zone == zone)
            .map(|z| z.temp_millideg_c as f64 / 1000.0);
    }
    if metric == "disk.pct" {
        return vitals
            .mounts
            .iter()
            .map(used_pct)
            .fold(0.0_f64, f64::max)
            .into_option();
    }
    if metric == "memory.pct" {
        if vitals.mem_total_kb == 0 {
            return None;
        }
        let used = vitals.mem_total_kb.saturating_sub(vitals.mem_available_kb);
        return Some(used as f64 * 100.0 / vitals.mem_total_kb as f64);
    }
    // `boot.watchdog` — 1.0 if the last boot reason indicates a hardware/
    // watchdog reset, else 0.0. Backs the watchdog-reset default pack
    // (spec §4.7, all platforms). Returns 0.0 (not None) when no reason is
    // recorded — the absence of a reset is a definite "not throttled".
    if metric == "boot.watchdog" {
        let reason = vitals.last_boot_reason.as_deref().unwrap_or("");
        let hit = reason.to_ascii_lowercase().contains("watchdog")
            || reason.to_ascii_lowercase().contains("hardware");
        return Some(hit as u8 as f64);
    }
    // `cert.<id>` — days until that cert expires (spec §4.8: "expires < 30
    // days feeds the alert engine"). `cert.*` aggregates to the minimum days
    // across the whole cert store, so a single default rule fires when ANY
    // installed cert nears expiry. Negative = already expired. Checked before
    // the `cert.<id>` prefix branch so the `*` wildcard isn't treated as an id.
    if metric == "cert.*" {
        return vitals
            .cert_expiries
            .iter()
            .map(|c| c.days_until_expiry as f64)
            .fold(f64::INFINITY, f64::min)
            .into_option();
    }
    if let Some(id) = metric.strip_prefix("cert.") {
        return vitals
            .cert_expiries
            .iter()
            .find(|c| c.id == id)
            .map(|c| c.days_until_expiry as f64);
    }
    // `service.<unit>` and `mcu.heartbeat` are spec §4.7 metrics but are not
    // carried by `Vitals`; they resolve to None when absent (the engine skips
    // the rule that tick), so a rule using them simply never fires until the
    // data source is wired.
    None
}

/// Merge per-platform default rule packs with the user-configured rules
/// (spec §4.7: "Per-platform default rule packs shipped as schema files").
/// User rules take precedence by id — an operator can override a default's
/// threshold/duration/actions, or disable it by setting `enabled = false`.
/// Defaults without a matching user rule are appended, so the shipped pack
/// always contributes unless explicitly replaced.
pub fn merge_defaults(defaults: &[AlertRule], user: &[AlertRule]) -> Vec<AlertRule> {
    let mut out: Vec<AlertRule> = user.to_vec();
    let user_ids: std::collections::HashSet<&str> = user.iter().map(|r| r.id.as_str()).collect();
    for d in defaults {
        if !user_ids.contains(d.id.as_str()) {
            out.push(d.clone());
        }
    }
    out
}

fn used_pct(m: &crate::monitoring::Mount) -> f64 {
    if m.total_bytes == 0 {
        return 0.0;
    }
    let used = m.total_bytes.saturating_sub(m.avail_bytes);
    used as f64 * 100.0 / m.total_bytes as f64
}

fn compare(value: f64, op: &str, threshold: f64) -> bool {
    match op {
        ">" => value > threshold,
        "<" => value < threshold,
        ">=" => value >= threshold,
        "<=" => value <= threshold,
        "==" => (value - threshold).abs() < f64::EPSILON,
        "!=" => (value - threshold).abs() >= f64::EPSILON,
        _ => false,
    }
}

/// Format an epoch-seconds value (supplied by the caller — the engine is
/// deterministic and never reads the wall clock) as an ISO-8601 UTC stamp
/// `YYYY-MM-DDTHH:MM:SSZ`, matching the audit `ts` contract (spec §4.7/§4.8).
/// Uses the already-vendored `time` crate purely for formatting.
fn ts_from_secs(secs: u64) -> String {
    let dt = time::OffsetDateTime::from_unix_timestamp(secs as i64)
        .unwrap_or(time::OffsetDateTime::UNIX_EPOCH);
    dt.format(time::macros::format_description!(
        "[year]-[month]-[day]T[hour]:[minute]:[second]Z"
    ))
    .unwrap_or_else(|_| format!("t={secs}"))
}

/// A bounded ring-buffer history file (spec §4.7).
#[derive(Debug)]
pub struct AlertHistory {
    path: std::path::PathBuf,
    max_entries: usize,
}

impl AlertHistory {
    pub fn new(path: impl Into<std::path::PathBuf>, max_entries: usize) -> Self {
        Self {
            path: path.into(),
            max_entries,
        }
    }

    pub fn record(&self, event: &AlertEvent) -> std::io::Result<()> {
        if self.is_sqlite() {
            let conn = self.open_sqlite()?;
            conn.execute(
                "INSERT INTO alert_history (ts, rule_id, state, value) VALUES (?1, ?2, ?3, ?4)",
                params![
                    event.ts,
                    event.rule_id,
                    serde_json::to_string(&event.state)?,
                    event.value
                ],
            )
            .map_err(sqlite_io)?;
            conn.execute(
                "DELETE FROM alert_history WHERE id NOT IN (SELECT id FROM alert_history ORDER BY id DESC LIMIT ?1)",
                params![self.max_entries as i64],
            )
            .map_err(sqlite_io)?;
            return Ok(());
        }
        if let Some(parent) = self.path.parent() {
            std::fs::create_dir_all(parent)?;
        }
        let mut f = OpenOptions::new()
            .create(true)
            .append(true)
            .open(&self.path)?;
        writeln!(f, "{}", serde_json::to_string(event)?)?;
        drop(f);
        self.trim()?;
        Ok(())
    }

    pub fn read_all(&self) -> std::io::Result<Vec<AlertEvent>> {
        if self.is_sqlite() {
            let conn = self.open_sqlite()?;
            let mut stmt = conn
                .prepare("SELECT ts, rule_id, state, value FROM alert_history ORDER BY id")
                .map_err(sqlite_io)?;
            let rows = stmt
                .query_map([], |row| {
                    let state: String = row.get(2)?;
                    Ok(AlertEvent {
                        ts: row.get(0)?,
                        rule_id: row.get(1)?,
                        state: serde_json::from_str(&state).map_err(|error| {
                            rusqlite::Error::FromSqlConversionFailure(
                                2,
                                rusqlite::types::Type::Text,
                                Box::new(error),
                            )
                        })?,
                        value: row.get(3)?,
                    })
                })
                .map_err(sqlite_io)?;
            return rows.map(|row| row.map_err(sqlite_io)).collect();
        }
        if !self.path.exists() {
            return Ok(Vec::new());
        }
        let raw = std::fs::read_to_string(&self.path)?;
        Ok(raw
            .lines()
            .filter(|l| !l.trim().is_empty())
            .filter_map(|l| serde_json::from_str::<AlertEvent>(l).ok())
            .collect())
    }

    fn trim(&self) -> std::io::Result<()> {
        let raw = std::fs::read_to_string(&self.path).unwrap_or_default();
        let mut entries: Vec<AlertEvent> = raw
            .lines()
            .filter(|l| !l.trim().is_empty())
            .filter_map(|l| serde_json::from_str(l).ok())
            .collect();
        if entries.len() <= self.max_entries {
            return Ok(());
        }
        let drop = entries.len() - self.max_entries;
        entries.drain(..drop);
        let mut s = String::new();
        for e in &entries {
            s.push_str(&serde_json::to_string(e).unwrap());
            s.push('\n');
        }
        std::fs::write(&self.path, s)?;
        Ok(())
    }

    pub fn path(&self) -> &Path {
        &self.path
    }

    fn is_sqlite(&self) -> bool {
        self.path.extension().and_then(|e| e.to_str()) == Some("db")
    }

    fn open_sqlite(&self) -> std::io::Result<Connection> {
        if let Some(parent) = self.path.parent() {
            std::fs::create_dir_all(parent)?;
        }
        let conn = Connection::open(&self.path).map_err(sqlite_io)?;
        conn.execute_batch(
            "CREATE TABLE IF NOT EXISTS alert_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL, rule_id TEXT NOT NULL, state TEXT NOT NULL, value REAL NOT NULL
             );",
        )
        .map_err(sqlite_io)?;
        Ok(conn)
    }
}

fn sqlite_io(error: rusqlite::Error) -> std::io::Error {
    std::io::Error::other(error)
}

/// Helper trait so `f64::NEG_INFINITY.into_option()` reads cleanly.
trait IntoOption {
    fn into_option(self) -> Option<f64>;
}
impl IntoOption for f64 {
    fn into_option(self) -> Option<f64> {
        if self.is_finite() {
            Some(self)
        } else {
            None
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn rule(id: &str, metric: &str, op: &str, thr: f64, dur: u64) -> AlertRule {
        AlertRule {
            id: id.into(),
            enabled: true,
            metric: metric.into(),
            comparison: op.into(),
            threshold: thr,
            duration_secs: dur,
            actions: vec!["ui".into()],
        }
    }

    fn vitals(temp_c: f64) -> Vitals {
        let mut v = Vitals::default();
        v.temperatures.push(crate::monitoring::TempZone {
            zone: "thermal_zone0".into(),
            temp_millideg_c: (temp_c * 1000.0) as i64,
            throttled: false,
        });
        v
    }

    fn vitals_with_certs(days: &[(&str, i64)]) -> Vitals {
        let mut v = vitals(40.0);
        v.cert_expiries = days
            .iter()
            .map(|(id, d)| crate::monitoring::CertExpiry {
                id: (*id).into(),
                days_until_expiry: *d,
            })
            .collect();
        v
    }

    #[test]
    fn cert_star_resolves_to_min_days() {
        // No certs → None (rule skipped, no false alarm).
        assert_eq!(resolve_metric("cert.*", &vitals(40.0)), None);
        // Min across the store.
        let v = vitals_with_certs(&[("ca-root", 120), ("mqtt-client", 10), ("old", -2)]);
        assert_eq!(resolve_metric("cert.*", &v), Some(-2.0));
    }

    #[test]
    fn cert_id_resolves_to_that_cert() {
        let v = vitals_with_certs(&[("ca-root", 120), ("mqtt-client", 10)]);
        assert_eq!(resolve_metric("cert.ca-root", &v), Some(120.0));
        assert_eq!(resolve_metric("cert.mqtt-client", &v), Some(10.0));
        // Unknown id → None (rule skips that tick).
        assert_eq!(resolve_metric("cert.missing", &v), None);
    }

    #[test]
    fn cert_expiry_rule_fires_within_30_days() {
        // spec §4.8: 'expires < 30 days feeds the alert engine'. Each scenario
        // uses a fresh engine so a prior fire doesn't suppress re-emission.
        let rules = vec![rule("cert-expiry", "cert.*", "<", 30.0, 0)];
        // 120 days out → no fire.
        let mut eng = AlertEngine::new();
        let e = eng.evaluate(&rules, &vitals_with_certs(&[("ca", 120)]), 0);
        assert!(e.is_empty());
        // 10 days out → fires.
        let mut eng = AlertEngine::new();
        let e = eng.evaluate(&rules, &vitals_with_certs(&[("ca", 10)]), 0);
        assert_eq!(e.len(), 1);
        assert_eq!(e[0].state, AlertState::Fired);
        // Already expired (negative) → fires too (urgent).
        let mut eng = AlertEngine::new();
        let e = eng.evaluate(&rules, &vitals_with_certs(&[("ca", -5)]), 0);
        assert_eq!(e.len(), 1);
        assert_eq!(e[0].state, AlertState::Fired);
    }

    #[test]
    fn fires_only_after_duration_holds() {
        let mut eng = AlertEngine::new();
        let rules = vec![rule("hot", "temp", ">", 85.0, 60)];

        // Tick 0: condition true, but not held long enough.
        let e = eng.evaluate(&rules, &vitals(90.0), 0);
        assert!(e.is_empty());
        assert!(!eng.is_firing("hot"));

        // Tick 30: still holding, not yet 60s.
        let e = eng.evaluate(&rules, &vitals(90.0), 30);
        assert!(e.is_empty());

        // Tick 60: fires.
        let e = eng.evaluate(&rules, &vitals(90.0), 60);
        assert_eq!(e.len(), 1);
        assert_eq!(e[0].state, AlertState::Fired);
        assert!(eng.is_firing("hot"));
    }

    #[test]
    fn clears_when_condition_drops() {
        let mut eng = AlertEngine::new();
        let rules = vec![rule("hot", "temp", ">", 85.0, 60)];
        // warm up to firing
        for t in [0, 60] {
            eng.evaluate(&rules, &vitals(90.0), t);
        }
        assert!(eng.is_firing("hot"));
        let e = eng.evaluate(&rules, &vitals(40.0), 70);
        assert_eq!(e.len(), 1);
        assert_eq!(e[0].state, AlertState::Cleared);
        assert!(!eng.is_firing("hot"));
    }

    #[test]
    fn does_not_refire_if_dip_is_brief() {
        let mut eng = AlertEngine::new();
        let rules = vec![rule("hot", "temp", ">", 85.0, 60)];
        eng.evaluate(&rules, &vitals(90.0), 0);
        eng.evaluate(&rules, &vitals(90.0), 60); // fires
                                                 // brief dip then back up — must hold a fresh 60s, not refire instantly
        eng.evaluate(&rules, &vitals(40.0), 61); // clear
        let e = eng.evaluate(&rules, &vitals(90.0), 62);
        assert!(e.is_empty(), "should not refire immediately");
        let e = eng.evaluate(&rules, &vitals(90.0), 122);
        assert_eq!(e.len(), 1);
        assert_eq!(e[0].state, AlertState::Fired);
    }

    #[test]
    fn history_records_and_trims() {
        let dir = tempfile::tempdir().unwrap();
        let h = AlertHistory::new(dir.path().join("alert.jsonl"), 3);
        for i in 0..10 {
            h.record(&AlertEvent {
                ts: format!("t={i}"),
                rule_id: "r".into(),
                state: AlertState::Fired,
                value: i as f64,
            })
            .unwrap();
        }
        let raw = std::fs::read_to_string(h.path()).unwrap();
        let count = raw.lines().filter(|l| !l.is_empty()).count();
        assert_eq!(count, 3);
    }

    #[test]
    fn sqlite_history_records_and_trims() {
        let dir = tempfile::tempdir().unwrap();
        let history = AlertHistory::new(dir.path().join("config.db"), 2);
        for index in 0..3 {
            history
                .record(&AlertEvent {
                    ts: format!("t={index}"),
                    rule_id: "disk".into(),
                    state: AlertState::Fired,
                    value: index as f64,
                })
                .unwrap();
        }
        let events = history.read_all().unwrap();
        assert_eq!(events.len(), 2);
        assert_eq!(events[0].value, 1.0);
    }

    #[test]
    fn throttled_metric_reflects_zone_throttle_flag() {
        let mut v = vitals(40.0);
        v.temperatures[0].throttled = true;
        assert_eq!(resolve_metric("temp.throttled", &v), Some(1.0));
        v.temperatures[0].throttled = false;
        assert_eq!(resolve_metric("temp.throttled", &v), Some(0.0));
    }

    #[test]
    fn watchdog_metric_detects_reset_reason() {
        let mut v = Vitals::default();
        assert_eq!(resolve_metric("boot.watchdog", &v), Some(0.0));
        v.last_boot_reason = Some("Power-on reset".into());
        assert_eq!(resolve_metric("boot.watchdog", &v), Some(0.0));
        v.last_boot_reason = Some("Watchdog timeout".into());
        assert_eq!(resolve_metric("boot.watchdog", &v), Some(1.0));
        v.last_boot_reason = Some("hardware reset".into());
        assert_eq!(resolve_metric("boot.watchdog", &v), Some(1.0));
    }

    #[test]
    fn merge_defaults_appends_unless_user_overrides() {
        let defaults = vec![
            rule("disk-full", "disk.pct", ">", 90.0, 60),
            rule("watchdog-reset", "boot.watchdog", ">", 0.0, 0),
        ];
        let user = vec![
            rule("disk-full", "disk.pct", ">", 95.0, 30),
            rule("custom", "temp", ">", 80.0, 10),
        ];
        let merged = merge_defaults(&defaults, &user);
        // user disk-full wins (threshold 95), custom kept, watchdog-reset appended.
        let by_id: HashMap<String, AlertRule> =
            merged.iter().cloned().map(|r| (r.id.clone(), r)).collect();
        assert_eq!(by_id.len(), 3);
        assert_eq!(by_id["disk-full"].threshold, 95.0);
        assert!(by_id.contains_key("watchdog-reset"));
        assert!(by_id.contains_key("custom"));
    }

    #[test]
    fn disabled_rule_never_fires() {
        let mut eng = AlertEngine::new();
        let mut r = rule("hot", "temp", ">", 85.0, 0);
        r.enabled = false;
        let rules = vec![r];
        // Condition is true well past duration, but the rule is disabled.
        let e = eng.evaluate(&rules, &vitals(90.0), 0);
        assert!(e.is_empty(), "disabled rule must not fire");
        let e = eng.evaluate(&rules, &vitals(90.0), 100);
        assert!(e.is_empty(), "disabled rule must not fire on later ticks");
        assert!(!eng.is_firing("hot"));
    }

    #[test]
    fn disabling_a_firing_rule_emits_clear() {
        let mut eng = AlertEngine::new();
        let rules = vec![rule("hot", "temp", ">", 85.0, 0)];
        eng.evaluate(&rules, &vitals(90.0), 0); // fires (duration 0)
        assert!(eng.is_firing("hot"));
        // Same rule, now disabled — must clear, not keep firing.
        let mut r = rule("hot", "temp", ">", 85.0, 0);
        r.enabled = false;
        let e = eng.evaluate(&[r], &vitals(90.0), 5);
        assert_eq!(e.len(), 1);
        assert_eq!(e[0].state, AlertState::Cleared);
        assert!(!eng.is_firing("hot"));
    }

    #[test]
    fn clear_all_flushes_firing_rules() {
        let mut eng = AlertEngine::new();
        let rules = vec![rule("hot", "temp", ">", 85.0, 0)];
        eng.evaluate(&rules, &vitals(90.0), 0);
        assert!(eng.is_firing("hot"));
        let clears = eng.clear_all(10);
        assert_eq!(clears.len(), 1);
        assert_eq!(clears[0].state, AlertState::Cleared);
        assert!(!eng.is_firing("hot"));
        // Idempotent on an already-cleared engine.
        assert!(eng.clear_all(20).is_empty());
    }

    #[test]
    fn alerts_config_defaults_enabled() {
        // First-boot seed is Config::default(); alerts must be on out of the
        // box so the shipped default packs protect the device (spec §4.7).
        assert!(crate::store::alerts::AlertsConfig::default().enabled);
    }

    #[test]
    fn ts_from_secs_is_iso8601_utc() {
        // 2026-01-01T00:00:00Z == epoch 1767225600.
        let ts = ts_from_secs(1_767_225_600);
        assert_eq!(ts, "2026-01-01T00:00:00Z");
        // No `t=` placeholder may survive for a real epoch value.
        assert!(!ts.starts_with("t="));
    }

    #[test]
    fn evaluate_emits_iso8601_timestamp() {
        // The engine must stamp fired events with ISO-8601, not `t=<tick>`.
        let mut eng = AlertEngine::new();
        let rules = vec![rule("hot", "temp", ">", 85.0, 0)];
        let events = eng.evaluate(&rules, &vitals(90.0), 1_767_225_600);
        assert_eq!(events.len(), 1);
        assert_eq!(events[0].ts, "2026-01-01T00:00:00Z");
    }
}
