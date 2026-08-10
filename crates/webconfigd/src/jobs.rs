//! Background feature workers owned by the daemon process.

use std::sync::Arc;

use webconfig_engine::alert::{merge_defaults, AlertEngine};
use webconfig_engine::manifest::BoardManifest;

use crate::alert_dispatch::dispatch_event;
use crate::bootstrap;
use crate::engine_bridge::EngineBridge;

/// Periodically evaluate configured alert rules against current vitals.
pub(crate) async fn run_alert_timer(
    engine: EngineBridge,
    manifest: Arc<BoardManifest>,
    alert_engine: Arc<tokio::sync::Mutex<AlertEngine>>,
) {
    let history = bootstrap::alert_history(&engine);
    loop {
        tokio::time::sleep(std::time::Duration::from_secs(5)).await;
        // Real wall-clock epoch seconds (spec §4.7): alert history `ts` must be
        // an ISO-8601 UTC stamp, so the engine receives the actual time rather
        // than a monotonic tick counter. The HTTP layer owns the clock; the
        // engine stays deterministic (it only formats what we pass).
        let now_secs = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_secs())
            .unwrap_or(0);
        let config = match engine.load() {
            Ok(config) => config,
            Err(_) => continue,
        };
        let vitals = {
            let mut v = webconfig_engine::monitoring::read_vitals(&engine.sysroot);
            // spec §4.8: cert expiry feeds the alert engine. The HTTP layer owns
            // the wall clock, so days-until-expiry is computed here from each
            // cert's operator-supplied ISO-8601 not_after and injected into the
            // vitals snapshot the engine evaluates (the engine stays pure).
            v.cert_expiries = webconfig_engine::monitoring::cert_expiries(
                &config.security.certificates,
                now_secs,
            );
            v
        };
        // Global alert switch (spec §4.7). When the engine is disabled, do not
        // evaluate rules — instead flush any firing state with clear events so
        // the dashboard red-state resets. This makes `alerts.enabled` actually
        // gate the engine (previously it was ignored).
        if !config.alerts.enabled {
            let clears = alert_engine.lock().await.clear_all(now_secs);
            for event in clears {
                tracing::info!(rule = %event.rule_id, state = ?event.state, "alert (engine disabled)");
                if let Err(error) = history.record(&event) {
                    tracing::warn!(error = %error, "alert history write failed");
                }
            }
            continue;
        }
        // Merge per-platform default rule packs with the user-configured rules
        // (spec §4.7). User rules override defaults by id; defaults ship in the
        // board manifest (Zion: throttling; all: disk-full, watchdog-reset).
        let rules = merge_defaults(&manifest.default_alert_rules, &config.alerts.rules);
        let events = alert_engine
            .lock()
            .await
            .evaluate(&rules, &vitals, now_secs);
        for event in events {
            tracing::info!(rule = %event.rule_id, state = ?event.state, "alert");
            // Event log (always) — append to the ring-buffer history.
            if let Err(error) = history.record(&event) {
                tracing::warn!(error = %error, "alert history write failed");
            }
            // Push actions (spec §4.7): webhook POST + MQTT event topic. UI
            // red-state is derived by the dashboard from this history. Look up
            // the effective rule (merged set) to read its actions.
            if let Some(rule) = rules.iter().find(|r| r.id == event.rule_id) {
                dispatch_event(
                    &engine.sysroot,
                    &config.alerts,
                    &config.services.mqtt,
                    rule,
                    &event,
                );
            }
        }
    }
}
