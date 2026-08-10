# Zion-Configuration

**Remote:** `git@github.com:SiliconCentric/Zion-Configuration.git`
**Parent project:** [Zion](../Zion.md) (target hardware: **Zynq UltraScale+**.)
**Leaf type:** `<Project>-Configuration`
**Status:** REAL — actively built out

Branded **"SoCcentric WebConfig"** — architecturally distinct from every other project's `-Configuration` leaf. An on-device, browser-based configuration web UI for the Zion platform, with board-specific data isolated to a single board-manifest file (`board-manifests/zion.json`).

- A small **Rust** daemon (`webconfigd`, axum framework, `current_thread` tokio runtime, rustls — no OpenSSL, no database, just JSON + ring log files). Target footprint: <15MB RAM, 3–6MB stripped binary.
- **Vanilla-JS frontend**, no build step, no Node at build time, <500KB total; schema-driven rendering — adding a setting is a schema file, zero UI code.
- **Flagship feature — networking rollback safety net:** network changes are provisional; the operator must reconnect and confirm within a window or the change auto-rolls-back.
- Real backends (RAUC/zbus) talk to hardware on-device and to mocks on a fake-sysroot test harness for CI (no hardware needed to test).
- Cross-compiles to `x86_64-unknown-linux-gnu`, `aarch64-unknown-linux-gnu`, `armv7-unknown-linux-gnueabihf`; fully offline (`CARGO_NET_OFFLINE=1`) via vendored crates; ships as a Yocto layer (`meta-webconfig`).

**Layout:** `crates/webconfig-engine` (config store, appliers, schema, manifest, alert/monitoring), `crates/webconfigd` (axum HTTP daemon: TLS, auth, CSRF, 9 section APIs, SSE), `frontend/` (vanilla JS), `schemas/` (`*.schema.json`), `board-manifests/` (one per platform — the only platform-specific data), `meta-webconfig/` (Yocto layer).

**Build:** `make build`/`release`/`test`/`lint`/`vendor`/`cross`/`run`; validation gate is `make build && make test && make lint`.

**Important discrepancy:** this repo is Zion-only, but other sibling projects in the `project-managers` fleet have their own `-Configuration` leaves that run a completely different Python/FastAPI/Vue dashboard (see the `Configuration` section in other projects' docs). Don't port fleet-wide `-Configuration` conventions from one implementation to the other without checking.

---
Part of the `Zion` project in the `project-managers` fleet. See `docs/project-managers.md` for the full 9-project / 8-leaf hierarchy and shared conventions.
