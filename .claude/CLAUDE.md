# Zion-Configuration (WebConfig)

Rust daemon plus vanilla-JS frontend: the on-device config UI for the Zynq UltraScale+. Ships into Zion-OS
as the `meta-webconfig` Yocto layer. Board specifics live in one manifest file, not in code.

## How you work here

* Commands: `.claude/commands/` — check, lint, validate.
* Write: system interactions sit behind narrow traits with real and mock impls. Pick one explicitly via `make_backend` / `make_client` — never stub with a TODO.
* Test: engine unit tests plus in-crate HTTP integration tests against a tempdir fake sysroot. No hardware needed.
* Frontend has no build step. Native ES modules, no bundler, no node at build time, under 500 KB.

## How you validate

* Gate: `./.claude/validate.sh quick` (seconds) or `full` (adds build + tests). Run it before you claim done.
* Exit codes: `0` pass, `1`-`76` fail, `77` a check never ran. **77 is not a pass.**
* The gate and everything under `.claude/lib`, `hooks`, `commands`, `settings.json` is human-owned. Run it, never edit it.

## Do

* Go through the root Makefile. Never bypass it with ad-hoc cargo commands in CI or PR validation.
* Keep the in-crate test pattern (`src/main.rs` -> `src/tests.rs`) — a binary crate has no lib target, so external `tests/` cannot import `crate::routes` or `crate::state`.
* Give every field type a `Default` impl when a struct derives `Default` — one missing derive fails the whole crate.
* Match the webconfig-engine handler contracts exactly when wiring the frontend. Shapes are in `docs/api-reference.md`.
* Register every system applier in `appliers::all()` **and** validate + apply it explicitly in `system::apply` / `network::apply`. `apply_all()` is not used on those routes, so a forgotten applier means the config saves but never applies.
* Use `confirmDialog` / `promptDialog` / `formDialog` from `frontend/components/modal.js`.
* Use the `.text-sm` utility class (with `.muted`) for secondary text.
* Return per-interface MACs under `macs` plus `uptime_secs` from `/system/info`.

## Don't

* **Don't let a component class set `display` unconditionally.** `base.css` carries a global `[hidden] { display: none !important; }` precisely so no component can re-break the hidden-attribute-vs-class-display bug.
* **Don't use native `confirm()` / `prompt()`** in the frontend. Banned.
* **Don't route app-schema validation by `name.starts_with("app:")`.** Validators are keyed by schema file stem; app-ness is decided by `descriptor.id`. Route via `descriptor.id`.
* **Don't add a `DELETE /logs/audit` route or an `AuditLog::clear()`.** The audit log is append-only; only a full factory reset wipes it.
* **Don't set the session cookie `with_secure(false)`.** The daemon is TLS-only.
* **Don't expect 403 from the CSRF guard.** A missing or invalid `X-CSRF-Token` returns 400 BadRequest. Assert `BAD_REQUEST`.
* **Don't trust any non-empty line in `authorized_keys`.** Reject `-----BEGIN` private-key material; require a known OpenSSH key-type prefix plus a base64 blob.
* **Don't pass a message to `ApiError::NotFound`** — it is a unit variant.
* **Don't add node or a build step to the frontend.**
* **Don't hardcode I/O or board behavior.** It lives in `board-manifests/zion.json` as data.
* **Don't write `meta-soccentric-webconfig` as a path.** The layer directory is `meta-webconfig`. The `soccentric-webconfig` string is legitimately the Yocto collection name in `layer.conf` — a different thing, correct as-is.
* **Don't move `MasterPlan.md` / `Checkpoint.md` out of `docs/planning/`.** They cross-reference each other by bare filename, so splitting them breaks both.
* **Don't assume CI and `Cargo.toml` agree on the Rust version.** `.github/workflows/ci.yml` pins 1.75.0 while `Cargo.toml` / `rust-toolchain.toml` declare 1.85. Establish which is authoritative before bumping either.

<!-- BEGIN loop-engine -->
## Do's and Don'ts (maintained by the Loop Engine)

Appended by the Loop Engine on loop close; curated by a human when approving a loop out of in-review/. Nothing outside this fence is engine-writable.

<!-- END loop-engine -->
