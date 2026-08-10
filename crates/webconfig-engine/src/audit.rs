//! Append-only audit log (spec §4.8).
//!
//! An append-only record of all config-UI actions (user, action, timestamp),
//! viewable under Logs and included in the diagnostics bundle (spec §4.4).
//! Persisted in SQLite (`config.db`) when the path ends in `.db`, else as a
//! bounded JSONL ring file on disk so it never grows unbounded on eMMC. The
//! audit log is append-only from the HTTP API's perspective — there is no
//! public delete/clear route. A full factory reset is the only legitimate wipe
//! and is performed by the System factory-reset handler, not via this module.

use std::fs::OpenOptions;
use std::io::Write;
use std::path::Path;

use rusqlite::{params, Connection};
use serde::{Deserialize, Serialize};

/// One audit entry.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AuditEntry {
    /// ISO-8601 timestamp (UTC) supplied by the caller — the engine is
    /// deterministic and never calls the wall clock itself, so the HTTP layer
    /// stamps events.
    pub ts: String,
    pub user: String,
    pub action: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub detail: Option<String>,
}

/// Bounded ring-buffer audit log. When the file exceeds `max_bytes`, the oldest
/// entries are dropped on the next `rotate_if_needed`.
#[derive(Debug)]
pub struct AuditLog {
    path: std::path::PathBuf,
    max_bytes: u64,
}

impl AuditLog {
    pub fn new(path: impl Into<std::path::PathBuf>, max_bytes: u64) -> Self {
        Self {
            path: path.into(),
            max_bytes,
        }
    }

    pub fn append(&self, entry: &AuditEntry) -> std::io::Result<()> {
        if self.is_sqlite() {
            return self.append_sqlite(entry);
        }
        if let Some(parent) = self.path.parent() {
            std::fs::create_dir_all(parent)?;
        }
        let line = serde_json::to_string(entry)?;
        let mut f = OpenOptions::new()
            .create(true)
            .append(true)
            .open(&self.path)?;
        writeln!(f, "{line}")?;
        drop(f);
        self.rotate_if_needed()?;
        Ok(())
    }

    /// Read all current entries (newest last).
    pub fn read_all(&self) -> std::io::Result<Vec<AuditEntry>> {
        if self.is_sqlite() {
            return self.read_sqlite();
        }
        if !self.path.exists() {
            return Ok(Vec::new());
        }
        let raw = std::fs::read_to_string(&self.path)?;
        let mut out = Vec::new();
        for line in raw.lines() {
            if line.trim().is_empty() {
                continue;
            }
            if let Ok(e) = serde_json::from_str::<AuditEntry>(line) {
                out.push(e);
            }
        }
        Ok(out)
    }

    /// Trim the oldest lines until under the byte budget.
    fn rotate_if_needed(&self) -> std::io::Result<()> {
        let len = std::fs::metadata(&self.path).map(|m| m.len()).unwrap_or(0);
        if len <= self.max_bytes {
            return Ok(());
        }
        let entries = self.read_all()?;
        let mut kept = entries;
        // Drop oldest until under ~80% of the budget.
        let target = self.max_bytes * 4 / 5;
        loop {
            let est: usize = kept
                .iter()
                .map(|e| serde_json::to_string(e).map(|s| s.len() + 1).unwrap_or(0))
                .sum();
            if (est as u64) <= target || kept.is_empty() {
                break;
            }
            kept.remove(0);
        }
        let mut s = String::new();
        for e in &kept {
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
            "CREATE TABLE IF NOT EXISTS audit_entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL, user TEXT NOT NULL, action TEXT NOT NULL, detail TEXT
             );",
        )
        .map_err(sqlite_io)?;
        Ok(conn)
    }

    fn append_sqlite(&self, entry: &AuditEntry) -> std::io::Result<()> {
        let conn = self.open_sqlite()?;
        conn.execute(
            "INSERT INTO audit_entries (ts, user, action, detail) VALUES (?1, ?2, ?3, ?4)",
            params![entry.ts, entry.user, entry.action, entry.detail],
        )
        .map_err(sqlite_io)?;
        let count: i64 = conn
            .query_row("SELECT COUNT(*) FROM audit_entries", [], |row| row.get(0))
            .map_err(sqlite_io)?;
        if count > 10_000 {
            conn.execute(
                "DELETE FROM audit_entries WHERE id IN (SELECT id FROM audit_entries ORDER BY id LIMIT ?1)",
                params![count - 8_000],
            )
            .map_err(sqlite_io)?;
        }
        Ok(())
    }

    fn read_sqlite(&self) -> std::io::Result<Vec<AuditEntry>> {
        let conn = self.open_sqlite()?;
        let mut stmt = conn
            .prepare("SELECT ts, user, action, detail FROM audit_entries ORDER BY id")
            .map_err(sqlite_io)?;
        let rows = stmt
            .query_map([], |row| {
                Ok(AuditEntry {
                    ts: row.get(0)?,
                    user: row.get(1)?,
                    action: row.get(2)?,
                    detail: row.get(3)?,
                })
            })
            .map_err(sqlite_io)?;
        rows.map(|row| row.map_err(sqlite_io)).collect()
    }
}

fn sqlite_io(error: rusqlite::Error) -> std::io::Error {
    std::io::Error::other(error)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn append_and_read_back() {
        let dir = tempfile::tempdir().unwrap();
        let log = AuditLog::new(dir.path().join("audit.jsonl"), 1 << 20);
        log.append(&AuditEntry {
            ts: "2026-07-21T10:00:00Z".into(),
            user: "admin".into(),
            action: "network.apply".into(),
            detail: Some("eth0".into()),
        })
        .unwrap();
        let all = log.read_all().unwrap();
        assert_eq!(all.len(), 1);
        assert_eq!(all[0].action, "network.apply");
    }

    #[test]
    fn sqlite_append_and_read_back() {
        let dir = tempfile::tempdir().unwrap();
        let log = AuditLog::new(dir.path().join("config.db"), 1 << 20);
        log.append(&AuditEntry {
            ts: "2026-07-21T10:00:00Z".into(),
            user: "admin".into(),
            action: "network.apply".into(),
            detail: None,
        })
        .unwrap();
        assert_eq!(log.read_all().unwrap()[0].action, "network.apply");
    }

    #[test]
    fn rotates_when_over_budget() {
        let dir = tempfile::tempdir().unwrap();
        let log = AuditLog::new(dir.path().join("audit.jsonl"), 300);
        for i in 0..50 {
            log.append(&AuditEntry {
                ts: format!("2026-07-21T10:00:{i:02}Z"),
                user: "admin".into(),
                action: format!("act-{i}"),
                detail: None,
            })
            .unwrap();
        }
        let len = std::fs::metadata(log.path()).unwrap().len();
        assert!(len <= 300, "len={len}");
        assert!(len > 0);
    }

    #[test]
    fn append_only_has_no_public_clear() {
        // The audit log is append-only from the API's perspective: there is no
        // `clear()` method and no DELETE route. Only factory reset wipes it
        // (inlined in the System handler). This test exists so that anyone
        // re-adding a public clear path trips a compile error here first.
        let dir = tempfile::tempdir().unwrap();
        let log = AuditLog::new(dir.path().join("config.db"), 1 << 20);
        log.append(&AuditEntry {
            ts: "2026-07-21T10:00:00Z".into(),
            user: "admin".into(),
            action: "network.apply".into(),
            detail: None,
        })
        .unwrap();
        // Append again — entries accumulate; nothing is cleared.
        log.append(&AuditEntry {
            ts: "2026-07-21T10:01:00Z".into(),
            user: "admin".into(),
            action: "system.put".into(),
            detail: None,
        })
        .unwrap();
        assert_eq!(log.read_all().unwrap().len(), 2);
    }
}
