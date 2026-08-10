//! Schema engine (spec §3: schema-driven rendering everywhere).
//!
//! Schemas are declarative JSON files dropped into `/usr/share/webconfig/schemas/`
//! by other Yocto recipes. The frontend contains a generic schema→form renderer
//! (types: string, int, bool, enum, secret; validation rules; grouping; an
//! `apply_hook`). Adding a customer setting must require **zero UI code
//! changes** (spec §3).
//!
//! This module loads schemas, validates config sections against them with the
//! `jsonschema` crate, and exposes the typed [`SchemaField`] shape the frontend
//! renderer consumes (via the API). The same schema drives both frontend
//! rendering and backend validation — the security boundary is the API
//! (spec §2), so every input is re-validated server-side here.

use std::path::Path;
use std::sync::Arc;

use serde::{Deserialize, Serialize};
use serde_json::Value;
use thiserror::Error;

use crate::store::Config;

#[derive(Debug, Error)]
pub enum SchemaError {
    #[error("schema parse error: {0}")]
    Parse(#[from] serde_json::Error),
    #[error("schema io error: {0}")]
    Io(#[from] std::io::Error),
    #[error("schema validation error: {0}")]
    Validation(String),
}

/// A loaded schema set. The default-permissive variant validates anything
/// (used by tests and first-boot before schemas are installed).
#[derive(Debug, Clone)]
pub struct Schema {
    /// JSON Schema (draft-07) documents keyed by section name, e.g.
    /// "system", "network", or "app:my-app". Wrapped in `Arc` because
    /// `jsonschema::JSONSchema` is not `Clone`, but `Schema` is shared between
    /// the config store and the daemon `AppState`.
    validators: Vec<(String, Arc<jsonschema::JSONSchema>)>,
    /// Raw field descriptors for the frontend renderer, keyed the same way.
    descriptors: Vec<(String, SchemaDescriptor)>,
    permissive: bool,
}

impl Schema {
    /// Empty, permissive schema — accepts anything. For tests and first boot.
    pub fn default_permissive() -> Self {
        Self {
            validators: Vec::new(),
            descriptors: Vec::new(),
            permissive: true,
        }
    }

    /// Load every `*.schema.json` under `dir` and compile them.
    pub fn load_dir(dir: &Path) -> Result<Self, SchemaError> {
        let mut out = Self {
            validators: Vec::new(),
            descriptors: Vec::new(),
            permissive: false,
        };
        if !dir.exists() {
            tracing::warn!("schema dir {:?} missing — permissive mode", dir);
            out.permissive = true;
            return Ok(out);
        }
        for entry in std::fs::read_dir(dir)? {
            let entry = entry?;
            let path = entry.path();
            // Only `*.schema.json` files are schemas (spec §3). A bare `.json`
            // file in this directory — a manifest, notes, anything else — must
            // not be loaded as a schema, or it would either mis-key a validator
            // or break load_dir entirely when it fails to parse as a descriptor.
            let is_schema = path
                .file_name()
                .and_then(|s| s.to_str())
                .map(|name| name.ends_with(".schema.json"))
                .unwrap_or(false);
            if !is_schema {
                continue;
            }
            let stem = path
                .file_stem()
                .and_then(|s| s.to_str())
                .unwrap_or("")
                .trim_end_matches(".schema")
                .to_string();
            let raw = std::fs::read_to_string(&path)?;
            let value: Value = serde_json::from_str(&raw)?;
            // The descriptor (for the frontend) is the same document; we also
            // compile a JSON-Schema validator from the `validate` subschema if
            // present, else from the whole document.
            let descriptor: SchemaDescriptor = serde_json::from_value(value.clone())?;
            let schema_doc = descriptor.validate.clone().unwrap_or(value);
            let validator = jsonschema::JSONSchema::compile(&schema_doc)
                .map_err(|e| SchemaError::Validation(format!("compile {stem}: {e}")))?;
            out.validators.push((stem.clone(), Arc::new(validator)));
            out.descriptors.push((stem, descriptor));
        }
        Ok(out)
    }

    /// Validate a config tree against all loaded validators. Permissive mode
    /// accepts anything.
    ///
    /// Validators are keyed by the schema file *stem* (e.g. `app-example`), but
    /// whether a schema governs an app setting is decided by the descriptor's
    /// `id` (`app:example`), not the stem. App settings live under
    /// `config.app_settings[<stem>]`; top-level sections live under
    /// `config[<stem>]`. The two vectors are pushed in lockstep in `load_dir`,
    /// so a `zip` pairs each validator with its descriptor.
    pub fn validate(&self, config: &Config) -> Result<(), SchemaError> {
        if self.permissive {
            return Ok(());
        }
        let whole = serde_json::to_value(config).map_err(SchemaError::Parse)?;
        for ((stem, validator), (_stem, descriptor)) in
            self.validators.iter().zip(self.descriptors.iter())
        {
            let target = if descriptor.id.starts_with("app:") {
                // App settings are keyed by the file stem (the URL `:id` the
                // services API uses), not by the `app:`-prefixed descriptor id.
                whole
                    .get("app_settings")
                    .and_then(|a| a.get(stem.as_str()))
                    .cloned()
                    .unwrap_or(Value::Null)
            } else {
                whole.get(stem.as_str()).cloned().unwrap_or(Value::Null)
            };
            if target.is_null() {
                continue;
            }
            // `is_valid` returns a plain bool — `validate` returns an iterator
            // that borrows `target`, and that borrow outlives the temporary,
            // so `is_valid` avoids the lifetime entanglement here (spec §3).
            if !validator.is_valid(&target) {
                return Err(SchemaError::Validation(format!(
                    "section {}: validation failed",
                    descriptor.id
                )));
            }
        }
        Ok(())
    }

    /// All descriptors, for the frontend renderer API.
    pub fn descriptors(&self) -> &[(String, SchemaDescriptor)] {
        &self.descriptors
    }
}

/// A schema descriptor — the unit the frontend renders into a form (spec §3).
/// This is the on-disk format other Yocto recipes drop into `schemas/`.
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct SchemaDescriptor {
    /// Section id, e.g. "system", "network", "app:customer-app".
    pub id: String,
    /// Human-readable title.
    #[serde(default)]
    pub title: String,
    /// Logical field groups for layout.
    #[serde(default)]
    pub groups: Vec<SchemaGroup>,
    /// Optional embedded JSON Schema used for server-side validation. When
    /// absent, the whole descriptor is treated as the validator (render-only
    /// schemas may omit it).
    #[serde(default)]
    pub validate: Option<Value>,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct SchemaGroup {
    pub id: String,
    #[serde(default)]
    pub title: String,
    #[serde(default)]
    pub fields: Vec<SchemaField>,
}

/// One renderable field. The `field_type` set is fixed (spec §3): string, int,
/// bool, enum, secret.
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct SchemaField {
    pub id: String,
    #[serde(default)]
    pub title: String,
    /// string | int | bool | enum | secret
    pub field_type: String,
    #[serde(default)]
    pub required: bool,
    /// Enum choices (only for `enum`).
    #[serde(default)]
    pub options: Vec<EnumOption>,
    /// Validation rules.
    #[serde(default)]
    pub validation: ValidationRules,
    /// Help text shown under the field.
    #[serde(default)]
    pub help: String,
    /// Declares a side effect to run after apply (spec §3 `apply_hook`):
    /// "restart unit X" or "sighup pid of X".
    #[serde(default)]
    pub apply_hook: Option<ApplyHook>,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct EnumOption {
    pub value: String,
    #[serde(default)]
    pub label: String,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct ValidationRules {
    #[serde(default)]
    pub min: Option<f64>,
    #[serde(default)]
    pub max: Option<f64>,
    #[serde(default)]
    pub pattern: Option<String>,
    #[serde(default)]
    pub min_len: Option<usize>,
    #[serde(default)]
    pub max_len: Option<usize>,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct ApplyHook {
    /// `restart` | `sighup` | `reload`.
    pub kind: String,
    /// A systemd unit name (for restart/reload) or a process name (for sighup).
    pub target: String,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn permissive_schema_accepts_anything() {
        let s = Schema::default_permissive();
        s.validate(&Config::default()).unwrap();
    }

    #[test]
    fn missing_dir_falls_back_to_permissive() {
        let s = Schema::load_dir(Path::new("/no/such/dir/here")).unwrap();
        s.validate(&Config::default()).unwrap();
    }

    #[test]
    fn real_mcu_stm32_schema_loads_and_renders_real_fields() {
        // Checkpoint 3.1: board-manifests/zion.json's mcu-stm32 peripheral
        // declares settings_schema: "app:mcu-stm32" — the schema must
        // actually exist and describe real fields, not render an empty/error
        // panel. Loads the real repo-root schemas/ directory (the canonical
        // source per docs/schema-format.md), not a fixture.
        let dir = Path::new(concat!(env!("CARGO_MANIFEST_DIR"), "/../../schemas"));
        let s = Schema::load_dir(dir).unwrap();
        let (stem, descriptor) = s
            .descriptors()
            .iter()
            .find(|(stem, _)| stem == "app-mcu-stm32")
            .expect("app-mcu-stm32.schema.json must be present in schemas/");
        assert_eq!(stem, "app-mcu-stm32");
        assert_eq!(descriptor.id, "app:mcu-stm32");
        let field_ids: Vec<&str> = descriptor
            .groups
            .iter()
            .flat_map(|g| g.fields.iter().map(|f| f.id.as_str()))
            .collect();
        assert!(field_ids.contains(&"heartbeat_interval_ms"));
        assert!(field_ids.contains(&"watchdog_timeout_ms"));
        assert!(!field_ids.is_empty(), "MCU schema must render real fields");
    }

    #[test]
    fn app_schema_validates_app_settings_by_stem() {
        // A schema whose descriptor id is `app:example` (file stem
        // `app-example`) must validate `config.app_settings["app-example"]`,
        // not `config["app:example"]`. This is the bug where app schemas were
        // routed by `name.starts_with("app:")` on the stem — which never
        // matched — so app settings were never server-validated.
        let dir = tempfile::tempdir().unwrap();
        let schema = r#"{
            "id": "app:example",
            "title": "Example app",
            "validate": {
                "type": "object",
                "required": ["site_id"],
                "properties": { "site_id": { "type": "string" } }
            }
        }"#;
        std::fs::write(dir.path().join("app-example.schema.json"), schema).unwrap();
        let s = Schema::load_dir(dir.path()).unwrap();

        // Missing required `site_id` under app_settings["app-example"] → fails.
        let mut cfg = Config::default();
        cfg.app_settings
            .insert("app-example".into(), serde_json::json!({}));
        assert!(s.validate(&cfg).is_err());

        // Present → passes.
        cfg.app_settings.insert(
            "app-example".into(),
            serde_json::json!({"site_id":"site-1"}),
        );
        assert!(s.validate(&cfg).is_ok());
    }

    #[test]
    fn top_level_schema_validates_its_section() {
        // A non-app schema (id == stem == "system") validates config.system.
        let dir = tempfile::tempdir().unwrap();
        let schema = r#"{
            "id": "system",
            "title": "System",
            "validate": {
                "type": "object",
                "properties": { "hostname": { "type": "string", "minLength": 3 } }
            }
        }"#;
        std::fs::write(dir.path().join("system.schema.json"), schema).unwrap();
        let s = Schema::load_dir(dir.path()).unwrap();

        let mut cfg = Config::default();
        cfg.system.hostname = Some("ab".into()); // too short
        assert!(s.validate(&cfg).is_err());
        cfg.system.hostname = Some("zion-01".into());
        assert!(s.validate(&cfg).is_ok());
    }

    #[test]
    fn load_dir_ignores_non_schema_json_files() {
        // A bare .json file in the schemas dir is not a schema and must be
        // skipped — even when its content would not parse as a descriptor.
        let dir = tempfile::tempdir().unwrap();
        let schema = r#"{ "id": "system", "title": "System" }"#;
        std::fs::write(dir.path().join("system.schema.json"), schema).unwrap();
        // Not a schema: wrong name. Malformed content would have broken
        // load_dir before the filter was tightened.
        std::fs::write(dir.path().join("notes.json"), r#"{ "not": "a schema" }"#).unwrap();
        std::fs::write(dir.path().join("README.txt"), "ignore me").unwrap();
        let s = Schema::load_dir(dir.path()).unwrap();
        assert_eq!(s.descriptors.len(), 1);
        assert_eq!(s.descriptors[0].0, "system");
    }
}
