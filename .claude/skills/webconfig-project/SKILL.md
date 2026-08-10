---
name: webconfig-project
description: 'Deep orientation on the Zion-Configuration repo ("SoCcentric WebConfig"): a Rust daemon + engine + vanilla-JS on-device config UI for the Zynq UltraScale+ platform. Use whenever working in this repo - implementing a new setting/section, wiring a frontend page to a backend route, adding an applier, touching auth/CSRF/backup/audit, editing schemas or board-manifests, or running the build/test/lint validation gate. Covers architecture, directory layout, request lifecycle, schema-driven config pattern, board-manifest pattern, security model, frontend conventions, testing patterns (fake sysroot), and where documentation lives.'
---

# SoCcentric WebConfig (Zion-Configuration)

On-device, browser-based configuration UI for the Zion platform (NVIDIA
Zynq UltraScale+/ application processor + STM32 real-time co-processor, Yocto/L4T
embedded Linux). One small Rust daemon (webconfigd) serves a vanilla-JS
frontend over TLS; all board-specific data lives in one file
(board-manifests/zion.json). This is a leaf repo in a larger
project-managers fleet — see REPO_MAP.md for fleet context and
.claude/CLAUDE.md for the authoritative, actively-maintained rules (this
skill is orientation/knowledge, not a replacement for it — always defer to
.claude/CLAUDE.md when the two disagree).

## Full documentation index

Read these on demand rather than duplicating them here:

| Doc | Contents |
|---|---|
| [`docs/webconfig-spec.md`](../../../docs/webconfig-spec.md) | Full design spec: sections 1-9 (scope, config model, sections/routes, security, testing, deployment) |
| [`docs/api-reference.md`](../../../docs/api-reference.md) | Every REST route, request/response shapes |
| [`docs/schema-format.md`](../../../docs/schema-format.md) | `*.schema.json` format for schema-driven rendering |
| [`docs/board-manifest.md`](../../../docs/board-manifest.md) | Board-manifest field reference |
| [`docs/planning/MasterPlan.md`](../../../docs/planning/MasterPlan.md) | Historical production-readiness audit (P0-P3 findings) |
| [`docs/planning/Checkpoint.md`](../../../docs/planning/Checkpoint.md) | Check-off acceptance criteria for the audit above (all closed) |
| [`.claude/CLAUDE.md`](../../CLAUDE.md) | Live, curated Do/Don't rules — the single source of truth for conventions |
| [`README.md`](../../../README.md) | Quick start, build commands, deploy-via-Yocto steps |

## Architecture at a glance

```
crates/webconfig-engine/   # pure config engine: no HTTP, no I/O side effects except appliers
  store.rs                 # ConfigStore: atomic JSON load/commit + config_state_backup snapshot
  schema.rs                # loads *.schema.json, validates PUT bodies against them
  manifest.rs               # board-manifest model (Peripheral, WifiRadio, caps, etc.)
  applier.rs + appliers/    # one file per OS-facing side effect (see table below)
  alert.rs, monitoring.rs   # alert rule evaluation, Vitals collection
  audit.rs                  # append-only, bounded ring-buffer audit log
  backup.rs                 # Argon2id + ChaCha20-Poly1305 (ring) encrypted config backup/restore
  users.rs, sysroot.rs      # local user/group model; Sysroot = real root vs tempdir fake root

crates/webconfigd/          # axum HTTP daemon: TLS, sessions, CSRF, routing, SSE
  main.rs -> tests.rs        # binary crate; integration tests live IN-CRATE (see Testing below)
  server.rs, tls.rs          # axum server bootstrap, rustls TLS
  auth.rs                    # first-boot admin creation, login, LoginThrottle (bounded+lockout)
  csrf.rs                    # CSRF guard: missing/invalid token -> 400, NOT 403
  routes.rs                  # route table, one module per section
  engine_bridge.rs           # bridges HTTP handlers to webconfig-engine
  sse.rs                     # /events: vitals + journal follow, semaphore-capped connections
  systemd.rs, update_backend.rs # SystemdClient / UpdateBackend traits: real + mock impls
  api/                       # one file per HTTP section (see table below)

frontend/                   # vanilla ES modules, no bundler, no Node at build time, <500KB
  core/                      # api.js, app.js, router.js, store.js, auth.js, dom.js, shortcuts.js
  components/                # reusable widgets: modal.js, table.js, toggle.js, sidebar.js, ...
  pages/                     # one file per section + registry.js wiring them to routes
  styles/                    # tokens.css (typography/color tokens), base/components/layout/pages.css

schemas/                    # *.schema.json - one per app-settings schema (schema-driven rendering)
board-manifests/            # zion.json (real board) + generic.yaml (fallback)
meta-webconfig/              # Yocto layer: recipes for daemon, static assets, schemas, manifest
```

### HTTP sections <-> appliers <-> frontend pages

Each backend "section" in crates/webconfigd/src/api/ roughly maps to one or
more frontend pages and, where it touches the OS, one or more appliers in
crates/webconfig-engine/src/appliers/:

| `api/*.rs` | Appliers involved | Frontend pages |
|---|---|---|
| `system.rs` | `hostname`, `time`, `ssh` (in that order, spec §4.1) | system.js, time.js, ssh.js, users.js, system-control.js |
| `network.rs` | `networkd`, `firewall`, `wifi`, `ap` (in that order, spec §4.2) | network.js, wired.js, wireless.js, firewall.js |
| `services.rs` | — (calls `SystemdClient` directly) | services.js |
| `security.rs` | `wireguard` (+ trust/cert status reads) | security.js |
| `io.rs` | `can`, `gpio`, `serial` | can.js, gpio.js, serial.js, mcu.js, camera.js, usb.js, pci.js |
| `updates.rs` | `UpdateBackend` (RAUC) | updates.js, remote-updates.js, backup.js |
| `monitoring.rs` | — (reads `/proc`, `/sys`) | monitoring.js, overview.js, telemetry.js |
| `logs.rs` | `journald` (remote syslog forward) | logs.js, remote-logs.js |
| `alerts.rs` | — (evaluates against Vitals) | alerts.js |
| `meta.rs` | — (auth/whoami/manifest) | core/auth.js |

Convention: when a section has multiple appliers, they must be registered
in appliers::all() and explicitly validated + applied in that section's
apply()/put() handler in the declared order — apply_all() is not a
blanket call used by every route.

## Request lifecycle (typical PUT + apply)

1. Frontend page calls api.put('/api/v1/<section>', body) (frontend/core/api.js).
2. routes.rs dispatches to api/<section>.rs::put (role-gated, e.g. admin-only).
3. Handler validates the body against the section's Rust struct (and, for
app-settings, against the matching schemas/*.schema.json via schema.rs).
4. ConfigStore::commit() snapshots the previous row into config_state_backup
then atomically upserts the new config — one BEGIN/COMMIT.
5. Handler calls audit() (crates/webconfigd/src/api/mod.rs) which appends a
JSONL entry (ts, user, action, detail) to the bounded audit ring
(crates/webconfig-engine/src/audit.rs) — every mutating endpoint does
this except auth endpoints (no audit on login attempts, by design) and
GET/HEAD/OPTIONS.
6. A separate POST /<section>/apply (or the same handler) runs the section's
appliers in declared order; network.apply additionally has a
   provisional/confirm-window rollback safety net (see below).
7. Response is a JSON copy of the updated config.

## Flagship feature: network rollback safety net

network.apply treats the change as provisional. The operator must
reconnect and call network.confirm within a window, or the networkd
applier's outcome triggers network.rollback, restoring the previous
config_state_backup snapshot and re-applying it. This is the single most
important behavior to preserve when touching appliers/networkd.rs or
api/network.rs — never bypass the provisional/confirm/rollback sequence for
convenience.

## Schema-driven config pattern

Adding a new app setting (not a core section) is data, not code: drop a
schemas/<name>.schema.json describing its fields, and frontend/components/schema-form.js
renders the form with zero page-specific UI code. Validators are keyed by the
schema file stem (e.g. app-example), while "is this an app" is decided by
descriptor.id.starts_with("app:") — app settings target
config.app_settings[<stem>], top-level schemas target config[<stem>].
PUT /apps/:id/settings fires the schema's apply_hooks after commit
(restart -> systemd.restart, reload -> systemctl reload, sighup ->
pkill -HUP -x <proc>), deduped, best-effort, and recorded in the audit
entry. See [`docs/schema-format.md`](../../../docs/schema-format.md).

## Board-manifest pattern

All platform/board-specific data (peripherals, radio interface/hw_mode/channel,
firmware pubkey, flash command, capability flags) lives in
board-manifests/zion.json, never hardcoded in Rust or JS. generic.yaml is
the fallback used when a BSP build can't select the real manifest. See
[`docs/board-manifest.md`](../../../docs/board-manifest.md).

## Security model (don't regress these)

- TLS-only. Session cookie is always Secure + HttpOnly. Never set
  with_secure(false).
- CSRF: missing/invalid X-CSRF-Token on a mutating route -> 400, not 403.
- Login throttle: LoginThrottle is a bounded/evicting map with an atomic
  begin_attempt() (single lock acquisition, no .await inside the critical
  section) — hard lockout after N failures in a window. Do not reintroduce a
  check-then-record split; it's racy under concurrent flooding.
- Backup encryption: POST /updates/backup {passphrase} -> Argon2id key
  derivation + ChaCha20-Poly1305 AEAD via ring (already vendored transitively
  through rustls/rcgen/tokio-rustls — no new external crate). GET /updates/backup
  always redacts secrets; restore detects .encrypted and prompts for the
  passphrase. Passphrase travels in the POST body, never a query string.
- Audit log is append-only. No DELETE /logs/audit route, no
  AuditLog::clear(). Only a full factory reset wipes it (inlined in
  system::factory_reset, which also wipes config_state/users/groups).
- SSH keys: authorized_keys validation rejects private-key material
  (-----BEGIN) and requires a known OpenSSH key-type prefix + base64 blob
  per non-comment line (appliers/ssh.rs).
- Shell-injection guards: journald remote-syslog host and WireGuard
  endpoint/allowed_ips are charset/shape-validated before being written
  into config files that get shelled out to (nc, wg-quick).
- Passwords: reject embedded control characters (\n, \r, tab, etc.)
  before piping to chpasswd — otherwise a password can inject an extra
  user:password line.
- MCU firmware: signature-verified via the same trust chain as
  update_backend.rs::verify_bundle — a client-supplied signed: true is
  never trusted alone. Real-root flashing without a manifest-declared
  caps.flash_command reports "not implemented", never a fabricated success.
- Never report success without doing the work. Systemd verbs
  (start/stop/restart/enable) propagate the real Result to ApiError;
  audit entries reflect actual outcome, not just "attempted."

## Frontend conventions

- Native ES modules, <script type="module">, no bundler, no Node at build
  time, <500KB total. Add a page: drop a module in frontend/pages/ + one
  line in frontend/pages/registry.js. Add a widget under frontend/components/.
- Styles are split under frontend/styles/*.css, composed by main.css via
  plain @import — no preprocessor.
- Typography is fully tokenized — never hardcode font-size/font-weight
  in CSS or inline in JS. Use --text-xs..--text-2xl, --weight-*,
  --leading-*, --tracking-* from frontend/styles/tokens.css. Use the
  .text-sm/.muted utility classes for secondary text.
- Never set display unconditionally in a component class — base.css
  has a global [hidden] { display: none !important; } specifically to
  prevent the hidden-attribute-vs-display bug from recurring.
- No native dialogs. confirm()/prompt() are banned — use
  confirmDialog/promptDialog/formDialog from frontend/components/modal.js.
- --primary (light blue #0ea5e9) is the single accent; derive shades with
  color-mix(in srgb, var(--primary) N%, ...).
- Router has a real 404 (EmptyState "Page not found"), not a silent
  redirect-to-overview for unknown routes.

## Testing patterns

- webconfigd is a binary crate — it has no lib target, so integration
  tests cannot live under a top-level tests/ directory and import
  crate::routes/crate::state. They live in-crate:
  #[cfg(test)] mod tests; in main.rs -> src/tests.rs.
- Tests drive the real axum router with tower::ServiceExt::oneshot +
  http_body_util::BodyExt, against a tempdir fake sysroot
  (Sysroot::new_fake(...) or similar — parallel-safe, no hardware).
- System interactions are behind narrow traits with real + mock impls
  (UpdateBackend, SystemdClient). make_backend(&Sysroot) /
  make_client(&Sysroot) pick real vs mock via Sysroot::is_real_root().
  Never silently stub a new interaction with a TODO — add both impls.
- crates/webconfig-engine has its own unit test suite (appliers'
  validate/rollback logic, store atomicity, backup crypto, audit ring bounds).

## Build / validate

Always go through the root Makefile (never bypass with ad-hoc cargo in
CI/PR validation):

```bash
make build      # debug build (host target)
make release    # exact spec profile: opt-level=z, lto, codegen-units=1, panic=abort, stripped
make test       # engine unit tests + in-crate HTTP integration tests, no hardware
make lint       # cargo clippy -D warnings + cargo fmt --check
make cross      # x86_64 / aarch64-unknown-linux-gnu / armv7-gnueabihf
make vendor     # regenerate offline crate tarball (CARGO_NET_OFFLINE=1 builds)
make stop       # stop a local daemon; WEBCONFIG_PORT=<port> to target a non-default port
```

Validation gate (matches CI / git workflow step 6): make build && make test && make lint.

Repo-local validation wrapper: .claude/validate.sh quick|full (also runnable
via /validate). Exit codes: 0 pass, 1-76 fail, 77 = a check never ran
(missing toolchain) — not a pass. .claude/lib/validate-lib.sh is
human-owned; never edit it to make a check pass.

## Fleet context

This repo is one leaf (<Project>-Configuration) of the Zion project in
the larger project-managers fleet (see REPO_MAP.md). It is branded
"SoCcentric WebConfig" and is architecturally distinct from sibling projects'
-Configuration leaves, some of which run an unrelated Python/FastAPI/Vue
dashboard instead. Do not port fleet-wide -Configuration conventions between
these two unreconciled approaches without checking which one a given repo uses.

## When you're about to do one of these, read further first

- New HTTP section or route: read docs/api-reference.md + the closest
  existing api/*.rs file for the exact handler/audit/schema pattern.
- New applier: read an existing simple one (appliers/hostname.rs) and a
  complex one with rollback (appliers/networkd.rs), plus spec §4.1/§4.2.
- New schema-driven app setting: read docs/schema-format.md and an
  existing schemas/*.schema.json.
- Board-manifest field: read docs/board-manifest.md and
  crates/webconfig-engine/src/manifest.rs.
- Anything security-sensitive (auth, CSRF, backup, secrets): read the
  Security model section above in full and grep for the exact existing
  pattern before adding a new one — don't invent a new crypto/validation
  approach when the repo already has one.
