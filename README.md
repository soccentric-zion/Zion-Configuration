# Zion-Configuration

## Project Summary

Zion-Configuration is an on-device configuration service for Zion Yocto and embedded-Linux systems. It combines a Rust workspace with two principal concerns: `webconfig-engine` owns configuration state, schemas, board manifests, system appliers, monitoring, alerts, and persistence; `webconfigd` exposes the HTTP service, authentication, CSRF protection, section APIs, and server-sent events. The frontend is vanilla JavaScript and is served without a Node build step. JSON schemas drive forms and validation, while `board-manifests/zion.json` supplies board-specific data and a generic fallback supports other environments. The repository also contains a Yocto layer that packages the daemon, static assets, schemas, manifest, and hardened service unit into an image. Existing documentation describes a fake-sysroot harness so RAUC and zbus interactions can be replaced by mocks during host tests; this makes the project testable without physical hardware, but does not prove production integration. The Makefile is the documented entry point for debug and release builds, tests, linting, formatting, vendoring, cross compilation, and a fake-sysroot run. Release settings emphasize a small stripped binary, offline reproducibility, and x86_64, aarch64, and armv7 targets. The flagship safety behavior is provisional network configuration with a commit/confirm window and automatic rollback if connectivity is not confirmed. This README summarizes repository evidence rather than claiming that a device, TLS certificate, role flow, rollback, or Yocto image has been manually exercised. Consult `docs/` for API, schema, board-manifest, and readiness details.

## Feature Status

| # | Name / Description | Implemented? | Test Coverage? | Manual Test |
|---:|---|---|---|---|
| 1 | Rust workspace and reproducible release profile | Implemented | `make build`/CI path documented; execution not verified | [ ] Manual verification required |
| 2 | Configuration store with JSON/ring-file persistence | Implemented | Engine tests documented; scope unknown | [ ] Manual verification required |
| 3 | Schema-driven frontend forms and validation | Implemented | Schema/API tests present by layout; coverage unknown | [ ] Manual verification required |
| 4 | Board manifest loading with Zion and generic data | Implemented | Manifest parsing coverage unknown | [ ] Manual verification required |
| 5 | Axum daemon and section REST APIs | Implemented | HTTP integration tests documented | [ ] Manual verification required |
| 6 | TLS serving | Implemented | Automated coverage not confirmed | [ ] Manual verification required |
| 7 | Authentication, roles, and CSRF protection | Implemented | Dedicated breadth unknown | [ ] Manual verification required |
| 8 | SSE monitoring/alert/event delivery | Partial | Integration coverage not confirmed | [ ] Manual verification required |
| 9 | Provisional network changes with rollback window | Implemented | Test presence requires review | [ ] Manual verification required |
| 10 | RAUC/zbus real and fake backends | Partial | Fake-sysroot documented; real backend unverified | [ ] Manual verification required |
| 11 | Yocto packaging layer and hardened unit | Implemented | BitBake build not verified | [ ] Manual verification required |
| 12 | Offline vendoring and three cross targets | Partial | Cross build not run | [ ] Manual verification required |

## Tests and Commands

```bash
make build
make test
make lint
make run
```

The documented validation gate is `make build && make test && make lint`. API, schema, board-manifest, and readiness documentation lives under `docs/`.

## Limitations

No physical board, browser role flow, TLS deployment, rollback timeout, RAUC update, or Yocto image was manually verified. Readiness statements remain dependent on the audit and acceptance documents.

## Detailed Reference

The retained WebConfig guide below contains the repository's extended product, layout, build, and deployment notes.
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

MIT. See [LICENSE](LICENSE).