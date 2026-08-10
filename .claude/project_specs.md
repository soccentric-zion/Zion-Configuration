# Zion-Configuration — specs

Target: Zynq UltraScale+.
Stack: Rust daemon (`webconfigd`) + engine + vanilla-JS frontend. Full spec in `docs/webconfig-spec.md`.

WebConfig, the Zion on-device UI. Board-specific data is isolated to a single manifest,
`board-manifests/zion.json`. Ships into Zion-OS as the `meta-webconfig` Yocto layer.

## Architecture

* **Real vs mock** — system interactions sit behind narrow traits (`UpdateBackend`, `SystemdClient`) with both impls; `make_backend` / `make_client` choose from `Sysroot::is_real_root()`.
* **Testing** — integration tests for the binary crate live as `#[cfg(test)] mod tests;` in `src/main.rs` -> `src/tests.rs`. Drive the axum router with `tower::ServiceExt::oneshot` + `http_body_util::BodyExt` against a tempdir `Sysroot`.
* **Frontend** — native ES modules, one file per component/page under `frontend/{core,components,pages}/`, no bundler. New page = a module in `frontend/pages/` plus a line in `registry.js`. Styles under `frontend/styles/*.css`, composed by `main.css` via plain `@import`. Typography is fully tokenized; `--primary` (#0ea5e9) is the single accent.
* **Secrets / backup** — `POST /updates/backup` returns a passphrase-encrypted envelope (Argon2id + ChaCha20-Poly1305 via `ring`, base64 fields). `GET` always redacts. Restore detects `.encrypted` and prompts.
* **Config snapshot** — `config_state_backup` holds the previous committed row; `commit()` snapshots then upserts inside one `BEGIN`/`COMMIT`.
* **System appliers** — `system.apply` runs hostname, time, ssh in order. `network.apply` runs networkd, firewall, wifi, ap, each validated and applied explicitly, with `network.rollback` reversing all four.
* **NTP** — `config.system.ntp_servers` (not under `config.system.time`) writes a `systemd-timesyncd.conf.d/webconfig.conf` drop-in with a `FileBackup` for rollback.
* **Wi-Fi / AP** — `config.network.wifi` and `regulatory_domain` -> `wpa_supplicant.conf` + `iw reg set`. `config.network.ap_mode` -> `hostapd.conf` + `dnsmasq.d` + a static `20-ap.network`.
* **DNS** — global `config.network.dns_servers` -> `/etc/systemd/resolved.conf.d/webconfig.conf`. `connectivity_test` pings the gateway, then a public IP, then resolves via the configured DNS.
* **App settings** — `PUT /apps/:id/settings` fires the schema's `apply_hooks` after commit (restart / reload / sighup), looked up by schema file stem, best-effort and audited.
* **Updates** — `UpdateBackend::mark_good()` (RAUC) is exposed at `POST /api/v1/updates/confirm`, admin only, audited. The diagnostics bundle carries journal, network state, config, slots, dmesg, alert history and audit log.
* **Monitoring** — Vitals carries `load[3]`, `mem_available_kb`, mounts, temperatures (from `thermal_zone*` and `hwmon*/temp*_input`), per-iface net bytes, uptime, boot count, last boot reason. `TempZone.throttled` reads `cdev*/cur_state`, never hardcoded.
* **Logs** — `/events` SSE emits vitals and journal-follow chunks, cursor-based, polled every 2s because the daemon is `current_thread`. `/logs/download` accepts `?unit=`. The journald applier materializes remote syslog as `webconfig-syslog-forward.service`. The audit log is append-only.

## Not like the other -Configuration leaves

This is a small Rust daemon plus vanilla JS. The other 8 projects' `-Configuration` leaves are a
Python/FastAPI/Vue dashboard. The two coexist unreconciled — don't port conventions between them
without checking. `REPO_MAP.md` documents the split.
