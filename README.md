# SoCcentric WebConfig

An **on-device, browser-based configuration web UI** for the Zion
Yocto/embedded-Linux platform (Zynq UltraScale+). One small Rust daemon serves a vanilla-JS
frontend over TLS; board-specific data lives in a single board-manifest file
(`board-manifests/zion.json`).

> The full design specification lives in [`docs/webconfig-spec.md`](docs/webconfig-spec.md).

## What it is

A field engineer or operator points a browser at the device
(`https://<device>:8443`) and configures it: system identity, networking,
services, updates, monitoring, logs, alerts, security, and I/O — all behind
authenticated, role-based access with a commit/confirm rollback safety net for
networking changes.

- **One daemon**, `current_thread` tokio runtime, rustls (no OpenSSL), no
  database (JSON + ring files). Target: <15 MB RAM, 3-6 MB stripped binary.
- **Vanilla JS frontend**, no build step, no node at build time, <500 KB total.
- **Schema-driven rendering**: adding a setting is a schema file — zero UI code.
- **Flagship networking rollback**: network changes are provisional; reconnect
  and confirm within a window or they auto-roll-back.
- **Real + mock system interactions**: RAUC/zbus talk to real backends on-device
  and to mocks on the fake-sysroot test harness (no hardware to test).

## Repository layout

```
Cargo.toml                  # workspace, pinned deps, exact release profile
crates/
  webconfig-engine/         # config store, appliers, schema, manifest, alert/monitoring
  webconfigd/               # axum HTTP daemon (TLS, auth, CSRF, 9 section APIs, SSE)
frontend/                   # vanilla JS: index.html, styles.css, schema-form.js, pages.js, app.js
schemas/                    # *.schema.json — schema-driven rendering + validation
board-manifests/            # zion.json (board-specific data) + generic.yaml fallback
meta-webconfig/             # Yocto layer: daemon, static assets, schemas, manifest, hardened unit
docs/                       # spec, API reference, schema format, board-manifest spec, planning/audits
Makefile                    # build | release | test | lint | fmt | vendor | cross | run | clean
```

## Build & test

The repo-root Makefile is the single entry point (per SoCcentric conventions):

```bash
make build    # debug build (host target)
make release  # exact spec release profile (opt-level=z, lto, panic=abort, stripped)
make test     # engine + HTTP integration tests (fake sysroot, no hardware)
make lint     # clippy -D warnings + rustfmt --check
make vendor   # regenerate the offline crate tarball for reproducible builds
make cross    # build all three cross-targets: x86_64, aarch64, armv7
make run      # run the daemon against a tempdir fake sysroot (dev)
```

Validation gate (used by the git workflows and CI): `make build && make test && make lint`.

## Cross-targets

`x86_64-unknown-linux-gnu`, `aarch64-unknown-linux-gnu`,
`armv7-unknown-linux-gnueabihf`. Vendored crates + `CARGO_NET_OFFLINE=1` make
builds fully offline and reproducible.

## Deploying (Yocto)

Add the layer and packages to your image:

```bitbake
IMAGE_INSTALL:append = " webconfigd webconfig-static webconfig-schemas webconfig-manifest"
```

Map your BSP `MACHINE` to its board manifest in
`meta-webconfig/recipes-core/webconfig/webconfig-manifest.bbappend`.
See [`meta-webconfig/README.md`](meta-webconfig/README.md).

## Documentation

- [Design specification](docs/webconfig-spec.md)
- [REST API reference](docs/api-reference.md)
- [Schema format](docs/schema-format.md)
- [Board-manifest format](docs/board-manifest.md)
- [Production-readiness audit](docs/planning/MasterPlan.md) and its [acceptance checklist](docs/planning/Checkpoint.md)

## License

CLOSED (SoCcentric internal IV&V tooling).