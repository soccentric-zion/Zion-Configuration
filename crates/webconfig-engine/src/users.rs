//! Web-UI user store (spec §4.1).
//!
//! Local user store in the SQLite database at `/data/webconfig/config.db`, argon2 hashes, roles
//! **admin / operator / viewer** (admin: everything; operator: app settings +
//! service restart only; viewer: read-only). First boot has **no default
//! password** — the API forces admin password creation on first access
//! (spec §4.8, CRA/SB-327 compliance). Changing the Linux root password is a
//! separate single action (`chpasswd`) handled by the HTTP layer, not here.
//!
use std::path::{Path, PathBuf};

use argon2::password_hash::{PasswordHash, SaltString};
use argon2::{Argon2, PasswordHasher, PasswordVerifier};
use rand_core::OsRng;
use rusqlite::{params, Connection};
use serde::{Deserialize, Serialize};
use thiserror::Error;

#[derive(Debug, Error)]
pub enum UserError {
    #[error("io error: {0}")]
    Io(#[from] std::io::Error),
    #[error("parse error: {0}")]
    Parse(#[from] serde_json::Error),
    #[error("sqlite error: {0}")]
    Sqlite(#[from] rusqlite::Error),
    #[error("hash error: {0}")]
    Hash(String),
    #[error("user not found: {0}")]
    NotFound(String),
    #[error("user already exists: {0}")]
    Exists(String),
    #[error("group not found: {0}")]
    GroupNotFound(String),
    #[error("group already exists: {0}")]
    GroupExists(String),
    #[error("user is already a member of group: {0}")]
    MembershipExists(String),
    #[error("user is not a member of group: {0}")]
    MembershipNotFound(String),
    #[error("invalid credentials")]
    InvalidCredentials,
    #[error("last admin cannot be removed or demoted")]
    LastAdmin,
    #[error("invalid role: {0}")]
    InvalidRole(String),
}

/// Authorization role (spec §4.1).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Role {
    Admin,
    Operator,
    Viewer,
}

impl Role {
    pub fn as_str(&self) -> &'static str {
        match self {
            Role::Admin => "admin",
            Role::Operator => "operator",
            Role::Viewer => "viewer",
        }
    }

    pub fn parse(s: &str) -> Result<Self, UserError> {
        match s {
            "admin" => Ok(Role::Admin),
            "operator" => Ok(Role::Operator),
            "viewer" => Ok(Role::Viewer),
            other => Err(UserError::InvalidRole(other.into())),
        }
    }

    /// Whether this role may perform mutating app-settings / service-restart
    /// actions (spec §4.1: operator = app settings + service restart only).
    pub fn can_mutate_apps(&self) -> bool {
        matches!(self, Role::Admin | Role::Operator)
    }

    pub fn is_admin(&self) -> bool {
        matches!(self, Role::Admin)
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct UserRecord {
    pub username: String,
    /// argon2 PHC string.
    pub password_hash: String,
    pub role: Role,
    #[serde(default)]
    pub groups: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct GroupRecord {
    pub name: String,
    #[serde(default)]
    pub members: Vec<String>,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct UserStore {
    pub users: Vec<UserRecord>,
    #[serde(default)]
    pub groups: Vec<GroupRecord>,
}

impl UserStore {
    pub fn load(path: &Path) -> Result<Self, UserError> {
        if path.extension().and_then(|e| e.to_str()) == Some("db") {
            return Self::load_sqlite(path);
        }
        if !path.exists() {
            return Ok(Self::default());
        }
        let raw = std::fs::read_to_string(path)?;
        Ok(serde_json::from_str(&raw)?)
    }

    pub fn save(&self, path: &Path) -> Result<(), UserError> {
        if path.extension().and_then(|e| e.to_str()) == Some("db") {
            return self.save_sqlite(path);
        }
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent)?;
        }
        // Atomic write.
        let tmp = path.with_extension("json.tmp");
        std::fs::write(&tmp, serde_json::to_vec_pretty(self)?)?;
        std::fs::rename(&tmp, path)?;
        Ok(())
    }

    fn load_sqlite(path: &Path) -> Result<Self, UserError> {
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent)?;
        }
        let conn = Connection::open(path)?;
        conn.execute_batch(
            "CREATE TABLE IF NOT EXISTS users (
                username TEXT PRIMARY KEY, password_hash TEXT NOT NULL, role TEXT NOT NULL
             );
             CREATE TABLE IF NOT EXISTS groups (
                name TEXT PRIMARY KEY
             );
             CREATE TABLE IF NOT EXISTS group_members (
                group_name TEXT NOT NULL, username TEXT NOT NULL,
                PRIMARY KEY (group_name, username)
             );",
        )?;
        let mut users = Vec::new();
        let mut stmt =
            conn.prepare("SELECT username, password_hash, role FROM users ORDER BY username")?;
        let rows = stmt.query_map([], |row| {
            let username: String = row.get(0)?;
            let password_hash: String = row.get(1)?;
            let role: String = row.get(2)?;
            Ok((username, password_hash, role))
        })?;
        for row in rows {
            let (username, password_hash, role) = row?;
            let groups = conn
                .prepare(
                    "SELECT group_name FROM group_members WHERE username = ?1 ORDER BY group_name",
                )?
                .query_map(params![username], |member| member.get(0))?
                .collect::<Result<Vec<String>, _>>()?;
            users.push(UserRecord {
                username,
                password_hash,
                role: Role::parse(&role)?,
                groups,
            });
        }
        let mut group_stmt = conn.prepare("SELECT name FROM groups ORDER BY name")?;
        let groups = group_stmt
            .query_map([], |row| {
                Ok(GroupRecord {
                    name: row.get(0)?,
                    members: Vec::new(),
                })
            })?
            .collect::<Result<Vec<_>, _>>()?;
        let mut store = Self { users, groups };
        for group in &mut store.groups {
            group.members = conn
                .prepare(
                    "SELECT username FROM group_members WHERE group_name = ?1 ORDER BY username",
                )?
                .query_map(params![group.name], |row| row.get(0))?
                .collect::<Result<Vec<String>, _>>()?;
        }
        if store.users.is_empty() {
            let legacy = path.with_file_name("users.json");
            if legacy.exists() {
                let imported: Self = serde_json::from_str(&std::fs::read_to_string(legacy)?)?;
                imported.save_sqlite(path)?;
                return Ok(imported);
            }
        }
        Ok(store)
    }

    fn save_sqlite(&self, path: &Path) -> Result<(), UserError> {
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent)?;
        }
        let mut conn = Connection::open(path)?;
        conn.execute_batch(
            "CREATE TABLE IF NOT EXISTS users (username TEXT PRIMARY KEY, password_hash TEXT NOT NULL, role TEXT NOT NULL);
             CREATE TABLE IF NOT EXISTS groups (name TEXT PRIMARY KEY);
             CREATE TABLE IF NOT EXISTS group_members (group_name TEXT NOT NULL, username TEXT NOT NULL, PRIMARY KEY (group_name, username));",
        )?;
        let tx = conn.transaction()?;
        tx.execute("DELETE FROM group_members", [])?;
        tx.execute("DELETE FROM groups", [])?;
        tx.execute("DELETE FROM users", [])?;
        for user in &self.users {
            tx.execute(
                "INSERT INTO users (username, password_hash, role) VALUES (?1, ?2, ?3)",
                params![user.username, user.password_hash, user.role.as_str()],
            )?;
        }
        for group in &self.groups {
            tx.execute("INSERT INTO groups (name) VALUES (?1)", params![group.name])?;
            for username in &group.members {
                tx.execute(
                    "INSERT INTO group_members (group_name, username) VALUES (?1, ?2)",
                    params![group.name, username],
                )?;
            }
        }
        tx.commit()?;
        Ok(())
    }

    /// First-boot probe: true when there are no users at all (spec §4.8 —
    /// force admin password creation on first access).
    pub fn needs_first_boot_admin(&self) -> bool {
        self.users.is_empty()
    }

    pub fn find(&self, username: &str) -> Option<&UserRecord> {
        self.users.iter().find(|u| u.username == username)
    }

    /// Verify credentials and return the user's role on success.
    pub fn authenticate(&self, username: &str, password: &str) -> Result<Role, UserError> {
        let user = self
            .find(username)
            .ok_or(UserError::NotFound(username.into()))?;
        let parsed =
            PasswordHash::new(&user.password_hash).map_err(|e| UserError::Hash(e.to_string()))?;
        Argon2::default()
            .verify_password(password.as_bytes(), &parsed)
            .map_err(|_| UserError::InvalidCredentials)?;
        Ok(user.role)
    }

    /// Create a user with an argon2 hash of `password`.
    pub fn add(&mut self, username: &str, password: &str, role: Role) -> Result<(), UserError> {
        if self.find(username).is_some() {
            return Err(UserError::Exists(username.into()));
        }
        let salt = SaltString::generate(&mut OsRng);
        let hash = Argon2::default()
            .hash_password(password.as_bytes(), &salt)
            .map_err(|e| UserError::Hash(e.to_string()))?
            .to_string();
        self.users.push(UserRecord {
            username: username.into(),
            password_hash: hash,
            role,
            groups: Vec::new(),
        });
        Ok(())
    }

    pub fn set_password(&mut self, username: &str, password: &str) -> Result<(), UserError> {
        let user = self
            .users
            .iter_mut()
            .find(|u| u.username == username)
            .ok_or(UserError::NotFound(username.into()))?;
        let salt = SaltString::generate(&mut OsRng);
        let hash = Argon2::default()
            .hash_password(password.as_bytes(), &salt)
            .map_err(|e| UserError::Hash(e.to_string()))?
            .to_string();
        user.password_hash = hash;
        Ok(())
    }

    pub fn set_role(&mut self, username: &str, role: Role) -> Result<(), UserError> {
        let idx = self
            .users
            .iter()
            .position(|u| u.username == username)
            .ok_or(UserError::NotFound(username.into()))?;
        // Guard: never demote/remove the last admin (spec §4.1). Compute the
        // admin count via an index lookup so we don't hold a borrow over it.
        let is_admin = self.users[idx].role == Role::Admin;
        if is_admin && role != Role::Admin && self.admin_count() <= 1 {
            return Err(UserError::LastAdmin);
        }
        self.users[idx].role = role;
        Ok(())
    }

    pub fn remove(&mut self, username: &str) -> Result<(), UserError> {
        let is_admin = self
            .find(username)
            .map(|u| u.role == Role::Admin)
            .unwrap_or(false);
        if is_admin && self.admin_count() <= 1 {
            return Err(UserError::LastAdmin);
        }
        self.users.retain(|u| u.username != username);
        for group in &mut self.groups {
            group.members.retain(|member| member != username);
        }
        Ok(())
    }

    pub fn add_group(&mut self, name: &str) -> Result<(), UserError> {
        if self.groups.iter().any(|group| group.name == name) {
            return Err(UserError::GroupExists(name.into()));
        }
        self.groups.push(GroupRecord {
            name: name.into(),
            members: Vec::new(),
        });
        Ok(())
    }

    pub fn remove_group(&mut self, name: &str) -> Result<(), UserError> {
        if !self.groups.iter().any(|group| group.name == name) {
            return Err(UserError::GroupNotFound(name.into()));
        }
        self.groups.retain(|group| group.name != name);
        for user in &mut self.users {
            user.groups.retain(|group| group != name);
        }
        Ok(())
    }

    pub fn add_member(&mut self, group_name: &str, username: &str) -> Result<(), UserError> {
        if self.find(username).is_none() {
            return Err(UserError::NotFound(username.into()));
        }
        let group = self
            .groups
            .iter_mut()
            .find(|group| group.name == group_name)
            .ok_or_else(|| UserError::GroupNotFound(group_name.into()))?;
        if group.members.iter().any(|member| member == username) {
            return Err(UserError::MembershipExists(username.into()));
        }
        group.members.push(username.into());
        if let Some(user) = self.users.iter_mut().find(|user| user.username == username) {
            user.groups.push(group_name.into());
        }
        Ok(())
    }

    pub fn remove_member(&mut self, group_name: &str, username: &str) -> Result<(), UserError> {
        let group = self
            .groups
            .iter_mut()
            .find(|group| group.name == group_name)
            .ok_or_else(|| UserError::GroupNotFound(group_name.into()))?;
        if !group.members.iter().any(|member| member == username) {
            return Err(UserError::MembershipNotFound(username.into()));
        }
        group.members.retain(|member| member != username);
        if let Some(user) = self.users.iter_mut().find(|user| user.username == username) {
            user.groups.retain(|group| group != group_name);
        }
        Ok(())
    }

    fn admin_count(&self) -> usize {
        self.users.iter().filter(|u| u.role == Role::Admin).count()
    }

    /// Where the store lives on the data partition.
    pub fn default_path(webconfig_dir: &Path) -> PathBuf {
        webconfig_dir.join("config.db")
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn tmp() -> PathBuf {
        let d = tempfile::tempdir().unwrap();
        d.path().join("users.json")
    }

    #[test]
    fn first_boot_when_empty() {
        assert!(UserStore::default().needs_first_boot_admin());
    }

    #[test]
    fn add_authenticate_roundtrip() {
        let path = tmp();
        let mut store = UserStore::default();
        store.add("admin", "correct horse", Role::Admin).unwrap();
        store.save(&path).unwrap();

        let loaded = UserStore::load(&path).unwrap();
        assert_eq!(
            loaded.authenticate("admin", "correct horse").unwrap(),
            Role::Admin
        );
        assert!(loaded.authenticate("admin", "wrong").is_err());
        assert!(loaded.authenticate("nobody", "x").is_err());
    }

    #[test]
    fn sqlite_authenticate_roundtrip() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("config.db");
        let mut store = UserStore::default();
        store.add("admin", "correct horse", Role::Admin).unwrap();
        store.add_group("operators").unwrap();
        store.add_member("operators", "admin").unwrap();
        store.save(&path).unwrap();

        let loaded = UserStore::load(&path).unwrap();
        assert_eq!(
            loaded.authenticate("admin", "correct horse").unwrap(),
            Role::Admin
        );
        assert_eq!(loaded.groups[0].members, vec!["admin"]);
    }

    #[test]
    fn cannot_remove_last_admin() {
        let mut store = UserStore::default();
        store.add("admin", "pw", Role::Admin).unwrap();
        assert!(matches!(store.remove("admin"), Err(UserError::LastAdmin)));
        assert!(matches!(
            store.set_role("admin", Role::Viewer),
            Err(UserError::LastAdmin)
        ));
    }

    #[test]
    fn duplicate_user_rejected() {
        let mut store = UserStore::default();
        store.add("u", "pw", Role::Viewer).unwrap();
        assert!(matches!(
            store.add("u", "pw", Role::Viewer),
            Err(UserError::Exists(_))
        ));
    }

    #[test]
    fn groups_assign_members_and_cleanup() {
        let mut store = UserStore::default();
        store.add("admin", "pw", Role::Admin).unwrap();
        store.add("operator", "pw", Role::Operator).unwrap();
        store.add_group("maintainers").unwrap();
        store.add_member("maintainers", "operator").unwrap();
        assert_eq!(store.groups[0].members, vec!["operator"]);
        assert_eq!(store.users[1].groups, vec!["maintainers"]);

        store.remove_member("maintainers", "operator").unwrap();
        assert!(store.groups[0].members.is_empty());
        store.add_member("maintainers", "operator").unwrap();
        store.remove("operator").unwrap();
        assert!(store.groups[0].members.is_empty());
        store.remove_group("maintainers").unwrap();
        assert!(store.groups.is_empty());
    }

    #[test]
    fn role_permissions() {
        assert!(Role::Admin.can_mutate_apps() && Role::Admin.is_admin());
        assert!(Role::Operator.can_mutate_apps() && !Role::Operator.is_admin());
        assert!(!Role::Viewer.can_mutate_apps() && !Role::Viewer.is_admin());
    }
}
