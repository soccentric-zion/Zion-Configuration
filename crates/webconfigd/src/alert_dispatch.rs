//! Alert action dispatch (spec §4.7).
//!
//! The alert engine emits `AlertEvent`s for every fire/clear transition; the
//! UI red-state + event-log actions are handled by recording into the alert
//! history (always). This module delivers the two *push* actions a rule can
//! request — **webhook POST** and **MQTT event topic** — by shelling out to
//! system tools on the real root only. No new crate dependency is introduced
//! (global rule: new deps require approval); this matches the codebase idiom
//! of shelling out to image-shipped tools (`journalctl`, `nc`, `ping`, …).
//!
//! Assumptions (spec §7 — state in one line):
//! - The image ships `curl` (webhook POST) and `mosquitto_pub` (MQTT publish).
//!   If a tool is absent the dispatch is best-effort and logged, never fatal.
//! - Dispatch is fire-and-forget; a failed POST/publish does not block the
//!   timer or re-raise. Payloads are JSON.

use std::io::Write;

use webconfig_engine::alert::AlertEvent;
use webconfig_engine::applier::run_cmd;
use webconfig_engine::store::alerts::{AlertRule, AlertsConfig};
use webconfig_engine::store::services::MqttConfig;
use webconfig_engine::Sysroot;

/// Build the JSON payload for a webhook/MQTT push (spec §4.7). Includes the
/// event transition plus the rule that triggered it, so receivers can route.
pub fn event_payload(event: &AlertEvent, rule: &AlertRule) -> String {
    serde_json::json!({
        "rule_id": event.rule_id,
        "state": event.state,
        "value": event.value,
        "ts": event.ts,
        "metric": rule.metric,
        "comparison": rule.comparison,
        "threshold": rule.threshold,
    })
    .to_string()
}

/// Write a payload to a per-event temp file and return its path, so we can pass
/// `--data @file` / `-f @file` to the tool without shell-escaping the body.
fn stage_payload(
    sysroot: &Sysroot,
    event: &AlertEvent,
    payload: &str,
) -> std::io::Result<std::path::PathBuf> {
    let dir = sysroot.webconfig_dir().join("alert-out");
    std::fs::create_dir_all(&dir)?;
    let path = dir.join(format!("{}.json", sanitize(&event.ts)));
    let mut f = std::fs::OpenOptions::new()
        .create(true)
        .write(true)
        .truncate(true)
        .open(&path)?;
    f.write_all(payload.as_bytes())?;
    Ok(path)
}

/// Replace characters that are unsafe in a filename with `_`.
fn sanitize(s: &str) -> String {
    s.chars()
        .map(|c| {
            if c.is_ascii_alphanumeric() || c == '-' || c == '.' {
                c
            } else {
                '_'
            }
        })
        .collect()
}

/// `curl` argument list for a JSON POST of the staged payload file.
pub fn webhook_args(url: &str, payload_file: &std::path::Path) -> Vec<String> {
    vec![
        "-sS".into(),
        "-m".into(),
        "10".into(),
        "-X".into(),
        "POST".into(),
        "-H".into(),
        "Content-Type: application/json".into(),
        "--data-binary".into(),
        format!("@{}", payload_file.display()),
        url.into(),
    ]
}

/// POST the event to every configured webhook URL (spec §4.7). Real root only;
/// on the fake sysroot this is a no-op so tests never touch the network.
pub fn dispatch_webhook(sysroot: &Sysroot, webhooks: &[String], payload: &str, event: &AlertEvent) {
    if !sysroot.is_real_root() || webhooks.is_empty() {
        return;
    }
    let Ok(file) = stage_payload(sysroot, event, payload) else {
        return;
    };
    for url in webhooks {
        let args = webhook_args(url, &file);
        let refs: Vec<&str> = args.iter().map(|s| s.as_str()).collect();
        if let Err(e) = run_cmd("curl", &refs) {
            tracing::warn!(url = %url, error = %e, "alert webhook POST failed");
        }
    }
}

/// Compose the MQTT topic: `<base>/<rule_id>` (spec §4.7 event topic).
pub fn mqtt_topic(base: &str, rule_id: &str) -> String {
    let base = base.trim_end_matches('/');
    format!("{base}/{rule_id}")
}

/// `mosquitto_pub` argument list for publishing the staged payload to `topic`,
/// derived from the MQTT broker config (host/port/credentials/TLS).
pub fn mqtt_args(
    cfg: &MqttConfig,
    topic: &str,
    payload_file: &std::path::Path,
) -> Option<Vec<String>> {
    let host = cfg.host.as_deref().unwrap_or("");
    if host.is_empty() {
        return None;
    }
    let mut args: Vec<String> = vec![
        "-h".into(),
        host.into(),
        "-p".into(),
        cfg.port.unwrap_or(1883).to_string(),
        "-t".into(),
        topic.into(),
        "-f".into(),
        format!("@{}", payload_file.display()),
    ];
    if cfg.tls {
        args.push("--cafile".into());
        args.push("/etc/ssl/certs/ca-certificates.crt".into());
    }
    if let Some(u) = &cfg.username {
        args.push("-u".into());
        args.push(u.clone());
    }
    if let Some(p) = &cfg.password {
        args.push("-P".into());
        args.push(p.clone());
    }
    Some(args)
}

/// Publish the event to the configured MQTT event topic (spec §4.7). Real root
/// only; no-op on the fake sysroot and when MQTT/broker is not configured.
pub fn dispatch_mqtt(
    sysroot: &Sysroot,
    mqtt_cfg: &MqttConfig,
    event_topic: Option<&str>,
    payload: &str,
    event: &AlertEvent,
) {
    if !sysroot.is_real_root() {
        return;
    }
    let Some(base) = event_topic else {
        return;
    };
    if base.is_empty() || !mqtt_cfg.enabled {
        return;
    }
    let Ok(file) = stage_payload(sysroot, event, payload) else {
        return;
    };
    let topic = mqtt_topic(base, &event.rule_id);
    let Some(args) = mqtt_args(mqtt_cfg, &topic, &file) else {
        return;
    };
    let refs: Vec<&str> = args.iter().map(|s| s.as_str()).collect();
    if let Err(e) = run_cmd("mosquitto_pub", &refs) {
        tracing::warn!(topic = %topic, error = %e, "alert MQTT publish failed");
    }
}

/// Dispatch the push actions for one event according to its rule's `actions`
/// and the alert config. UI + event-log are always handled by the caller
/// (history record); this handles `webhook` and `mqtt` only.
pub fn dispatch_event(
    sysroot: &Sysroot,
    alerts: &AlertsConfig,
    mqtt_cfg: &MqttConfig,
    rule: &AlertRule,
    event: &AlertEvent,
) {
    let wants_webhook = rule.actions.iter().any(|a| a == "webhook");
    let wants_mqtt = rule.actions.iter().any(|a| a == "mqtt");
    if !wants_webhook && !wants_mqtt {
        return;
    }
    let payload = event_payload(event, rule);
    if wants_webhook {
        dispatch_webhook(sysroot, &alerts.webhooks, &payload, event);
    }
    if wants_mqtt {
        dispatch_mqtt(
            sysroot,
            mqtt_cfg,
            alerts.mqtt_event_topic.as_deref(),
            &payload,
            event,
        );
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use webconfig_engine::alert::AlertState;

    fn ev() -> AlertEvent {
        AlertEvent {
            ts: "t=60".into(),
            rule_id: "disk-full".into(),
            state: AlertState::Fired,
            value: 94.0,
        }
    }

    fn rule_with(actions: &[&str]) -> AlertRule {
        AlertRule {
            id: "disk-full".into(),
            enabled: true,
            metric: "disk.pct".into(),
            comparison: ">".into(),
            threshold: 90.0,
            duration_secs: 60,
            actions: actions.iter().map(|s| (*s).into()).collect(),
        }
    }

    #[test]
    fn payload_carries_event_and_rule() {
        let p = event_payload(&ev(), &rule_with(&["webhook"]));
        let v: serde_json::Value = serde_json::from_str(&p).unwrap();
        assert_eq!(v["rule_id"], "disk-full");
        assert_eq!(v["state"], "fired");
        assert_eq!(v["value"], 94.0);
        assert_eq!(v["metric"], "disk.pct");
        assert_eq!(v["threshold"], 90.0);
    }

    #[test]
    fn webhook_args_use_data_binary_at_file() {
        let args = webhook_args(
            "https://hook.example/x",
            std::path::Path::new("/tmp/p.json"),
        );
        assert!(args.contains(&"-X".into()));
        assert!(args.contains(&"POST".into()));
        assert!(args.contains(&"@/tmp/p.json".into()));
        assert!(args.contains(&"https://hook.example/x".into()));
    }

    #[test]
    fn mqtt_topic_joins_base_and_rule() {
        assert_eq!(
            mqtt_topic("soccentric/events", "disk-full"),
            "soccentric/events/disk-full"
        );
        assert_eq!(
            mqtt_topic("soccentric/events/", "disk-full"),
            "soccentric/events/disk-full"
        );
    }

    #[test]
    fn mqtt_args_require_host_and_apply_credentials() {
        let mut cfg = MqttConfig::default();
        assert!(mqtt_args(&cfg, "t", std::path::Path::new("/p")).is_none());
        cfg.host = Some("broker.local".into());
        cfg.port = Some(8883);
        cfg.tls = true;
        cfg.username = Some("user".into());
        cfg.password = Some("pass".into());
        let args = mqtt_args(
            &cfg,
            "soccentric/events/disk-full",
            std::path::Path::new("/p.json"),
        )
        .unwrap();
        assert!(args.contains(&"-h".into()));
        assert!(args.contains(&"broker.local".into()));
        assert!(args.contains(&"8883".into()));
        assert!(args.contains(&"--cafile".into()));
        assert!(args.contains(&"-u".into()));
        assert!(args.contains(&"user".into()));
        assert!(args.contains(&"-P".into()));
        assert!(args.contains(&"pass".into()));
        assert!(args.contains(&"@/p.json".into()));
    }

    #[test]
    fn dispatch_is_noop_on_fake_sysroot() {
        let sys = Sysroot::at("/tmp/never-alert");
        let alerts = AlertsConfig {
            enabled: true,
            rules: vec![],
            webhooks: vec!["https://hook.example/x".into()],
            mqtt_event_topic: Some("soccentric/events".into()),
        };
        let mqtt = MqttConfig {
            enabled: true,
            host: Some("broker.local".into()),
            ..Default::default()
        };
        // Must not panic or touch the network on a fake sysroot.
        dispatch_event(
            &sys,
            &alerts,
            &mqtt,
            &rule_with(&["webhook", "mqtt"]),
            &ev(),
        );
    }

    #[test]
    fn dispatch_skips_when_no_push_actions() {
        // ui-only rule → no payload build, no dispatch path taken.
        let sys = Sysroot::at("/tmp/never-alert-ui");
        let alerts = AlertsConfig::default();
        let mqtt = MqttConfig::default();
        dispatch_event(&sys, &alerts, &mqtt, &rule_with(&["ui"]), &ev());
        // Asserting only that it returns without error; no staging dir created
        // because wants_webhook/wants_mqtt are both false.
        assert!(!sys.webconfig_dir().join("alert-out").exists());
    }
}
