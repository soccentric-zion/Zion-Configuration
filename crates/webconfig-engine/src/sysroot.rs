//! Sysroot path resolution.
//!
//! Every applier takes a sysroot path parameter (default `/`) so CI can run
//! the entire config engine against a tempdir fake sysroot on x86 with no
//! hardware (spec §3, applier trait). All filesystem reads/writes done by
//! appliers must go through [`Sysroot`]; nothing in the engine hardcodes `/`.

use std::path::{Path, PathBuf};

/// A rooted view of the target filesystem. In production this wraps `/`; in
/// tests it wraps a `tempfile` directory. Construct with [`Sysroot::new`] for
/// the real root or [`Sysroot::at`] for a tempdir.
#[derive(Debug, Clone)]
pub struct Sysroot {
    root: PathBuf,
}

impl Sysroot {
    /// The real root filesystem.
    pub fn new() -> Self {
        Self {
            root: PathBuf::from("/"),
        }
    }

    /// A sysroot rooted at `root` (used by tests and CI's fake sysroot).
    pub fn at(root: impl Into<PathBuf>) -> Self {
        Self { root: root.into() }
    }

    /// Raw root path.
    pub fn root(&self) -> &Path {
        &self.root
    }

    /// Whether this is the real root filesystem (`/`). Appliers use this to
    /// decide whether to invoke live system verbs (hostnamectl, timedatectl,
    /// …) — a tempdir sysroot skips them so tests run with no hardware and no
    /// host side effects.
    pub fn is_real_root(&self) -> bool {
        self.root == Path::new("/")
    }

    /// Join an absolute guest path (e.g. `/etc/hostname`) onto this sysroot.
    /// `guest` must be absolute; passing a relative path is a programmer error
    /// and panics, since every applier deals in absolute system paths.
    pub fn join(&self, guest: impl AsRef<Path>) -> PathBuf {
        let guest = guest.as_ref();
        assert!(
            guest.is_absolute(),
            "sysroot guest paths must be absolute, got {:?}",
            guest
        );
        // Strip the leading `/` so `join` produces `<root>/etc/hostname`
        // rather than replacing `root` entirely.
        let rel = guest.strip_prefix("/").expect("absolute after assert");
        self.root.join(rel)
    }

    /// The writable data partition root (spec §2: mutable state lives on the
    /// data partition, e.g. `/data`). Resolved from `WEBCONFIG_DATA_DIR` so the
    /// dev `make run` target and tests can redirect it to a tempdir.
    pub fn data_dir(&self) -> PathBuf {
        if let Ok(dir) = std::env::var("WEBCONFIG_DATA_DIR") {
            return PathBuf::from(dir);
        }
        self.join("/data")
    }

    /// `/data/webconfig` — the canonical mutable state directory.
    pub fn webconfig_dir(&self) -> PathBuf {
        self.data_dir().join("webconfig")
    }

    /// `/usr/share/webconfig` — BSP-provided, read-only manifests & schemas.
    pub fn share_dir(&self) -> PathBuf {
        self.join("/usr/share/webconfig")
    }
}

impl Default for Sysroot {
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn join_strips_leading_slash_for_tempdir_root() {
        let s = Sysroot::at("/tmp/fake");
        assert_eq!(
            s.join("/etc/hostname"),
            PathBuf::from("/tmp/fake/etc/hostname")
        );
    }

    #[test]
    fn real_root_resolves_to_absolute_paths() {
        let s = Sysroot::new();
        assert_eq!(s.join("/etc/hostname"), PathBuf::from("/etc/hostname"));
    }

    #[test]
    #[should_panic(expected = "must be absolute")]
    fn relative_guest_path_panics() {
        let s = Sysroot::at("/tmp/fake");
        let _ = s.join("etc/hostname");
    }
}
