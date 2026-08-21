# Zion-Configuration Build and Deployment

## Purpose and scope
Zion-Configuration is the Rust WebConfig workspace. `webconfig-engine` provides configuration, schemas, manifests, persistence, monitoring, and real/mock system backends; `webconfigd` serves the HTTP/TLS UI and APIs. The Yocto layer under `meta-webconfig` packages the daemon and static assets.

## 1. Prerequisites
- Rustup and the pinned Rust `1.97.1` toolchain from `rust-toolchain.toml`, including rustfmt, clippy, and x86_64, aarch64, and armv7 targets.
- Network access for `make toolchain` unless the exact toolchain and vendored dependencies already exist.
- A Linux host; Docker/Yocto tooling is additionally needed for image integration.

## 2. Setup
Install the pinned Rust toolchain with the target dependencies described above.

## 3. Build and compile
```bash
make toolchain   # conditional one-time install; downloads rustup/toolchain/targets
make build       # debug workspace build
make release     # opt-level=z, LTO, one codegen unit, abort, stripped
```
The workspace is defined in `Cargo.toml`; `vendor/` supports offline dependency reproduction. `make cross` builds release binaries for all three pinned targets and requires their Rust target support.

## 4. Test and lint
```bash
make test
make lint
make fmt
make check
```
`test` runs all workspace targets, including the fake-sysroot HTTP/engine coverage. `lint` runs clippy with `-D warnings` and rustfmt check. `fmt` changes Rust files in place. `check` is the build/test/lint gate.

## 5. Packaging and artifacts
```bash
make vendor
make release-artifacts
```
`vendor` regenerates versioned vendored crates and should be rerun after dependency changes. `release-artifacts` calls `tools/release-artifacts.sh` to package the source/vendor tarballs and print SHA-256 values for the Yocto `webconfigd.bb` fetches. Release binaries are under Cargo `target/`; Yocto packages are produced by the consuming image build.

## 6. Deployment, install, and run
```bash
make run-mock       # no hardware; temporary fake sysroot and mock backends
make run            # real host sysroot, live /proc and /sys
make stop
```
The daemon defaults to TCP port 8443 through `WEBCONFIG_PORT`. `run` persists development state in `./data` and uses real systemd/update backends; it can affect the host. `run-mock` sets `WEBCONFIG_SYSROOT` and `WEBCONFIG_DATA_DIR` to a temporary fake root and is the preferred host verification path.

For device deployment, add `webconfigd webconfig-static webconfig-schemas webconfig-manifest` to a Yocto image through `meta-webconfig`; follow that layer's recipes and service unit. Credentials, TLS certificates, systemd, RAUC, and zbus are device provisioning concerns, not supplied by the Makefile.

## 7. Cleanup
```bash
make clean
```
This removes Cargo `target/`. It does not remove `vendor/`, source files, or the persistent `data/` directory used by `run`; remove `data/` deliberately to reset first-boot state.

## 8. Hardware, cloud, and manual verification caveats
Fake-sysroot tests do not prove real RAUC/zbus behavior, TLS, browser role flows, network rollback, systemd integration, or Yocto image boot. `make cross`, vendoring, and release artifact generation can require network or large disk space. Treat `make run` as conditional and host-impacting.
