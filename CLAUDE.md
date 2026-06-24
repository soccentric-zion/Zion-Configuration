# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

> Master SoCcentric CLAUDE.md applies. This file adds local knowledge only.

## Build / Run / Validate

The `Makefile` is the single source of truth for every task (`make help` lists them). All Python runs go through `.venv`.

| Task | Command |
| --- | --- |
| Install (runtime + dev) | `make install` |
| Run (http://localhost:8000, login `admin`/`admin`) | `make run` |
| Dev (uvicorn autoreload) | `make dev` |
| Lint (ruff check + format check) | `make lint` |
| Auto-format | `make format` |
| Type-check (mypy `--strict`) | `make typecheck` |
| Test suite (with coverage) | `make test` |
| Single test | `.venv/bin/pytest tests/test_alerts.py::test_foo -q` |
| Single test file | `.venv/bin/pytest tests/test_auth.py -q` |
| Full CI gate | `make validate` (or `./validate.sh`) |
| Re-vendor frontend libs (air-gap) | `make fetch-vendor` |
| Cache cleanup | `make clean` / `make distclean` |

Config is a single optional `config.yaml`; override path with `DASHBOARD_CONFIG=...`. Every field has a safe default — no config is required to boot. See `config.example.yaml`.

## High-level architecture

Single Python process: **FastAPI** + **aiosqlite** (WAL) + **Vue 3 SPA** served from `/static` (zero build, all libraries vendored under `static/vendor/` — **no CDN at runtime**).

```
Browser (Vue 3 / PrimeVue / Chart.js, hash router)
  │  REST /api/*    WS /ws/*
  ▼
FastAPI app (app/main.py — create_app() factory + lifespan)
  ├── Routers (app/routers/*)        one file per feature group
  ├── WebSockets (app/ws/routes.py)  metrics / ota / alerts / logs / can / terminal
  ├── HAL registry (app/hal)         modules talk to real HW or return simulated data
  ├── DB (app/db)                    aiosqlite, versioned SQL migrations, append-only audit
  ├── Auth (app/auth)                JWT (HS256) + bcrypt + roles (viewer<operator<admin)
  ├── Tasks (app/tasks)              sampler, alert engine, OTA, backup, retention
  └── Background broadcasts via Hub (app/ws/manager.py)
```

### HAL — Hardware Abstraction Layer (the most important pattern)

Every hardware-facing module subclasses `HalModule` (`app/hal/base.py`) and registers itself in `app/hal/registry.py`. Two non-negotiable rules:

1. **`detect()` returns True iff the real interface is present.** The constructor sets `self._real = (not force_sim) and self.detect()`.
2. **Every public method returns `self.envelope({...})`** — a dict that includes `"simulated": true|false`. The UI banner and the `GET /api/system/simulation` endpoint depend on this envelope; never return raw data.

Use the simulation helpers in `app/hal/base.py`:
- `sim_rng(*seed_parts)` — deterministic RNG so simulated topology (serials, MACs, addresses) is stable across calls/processes (matters for reproducible tests).
- `sim_wave(period_s, lo, hi, phase=0.0)` — smooth time-varying value in `[lo, hi]` for animated live readings.

To add a HAL module: subclass `HalModule`, set `name`, add to the `mods` list in `app/hal/registry.py`.

### Adding a new feature panel (the three-layer pattern)

The codebase is intentionally uniform — every new feature touches the same three layers (see README "Extending"):

1. **HAL** — add a method (or new `HalModule` + register in `registry.py`). Return `self.envelope({...})` and provide a deterministic simulated branch.
2. **Router** — add an endpoint in the matching file under `app/routers/`. Guard with `require_viewer` / `require_operator` / `require_admin` (from `app/auth/deps`). Wrap any state change in `await audit.record(...)`.
3. **Page** — add UI in the matching file under `static/js/pages/` and register the route in the SPA shell.
4. **Test** — the route-driven smoke test picks up new read endpoints automatically; add a targeted test for new write flows.

### Roles and auth

Three roles with rank `viewer (0) < operator (1) < admin (2)` — `ROLE_RANK` in `app/auth/deps.py`. Guards are FastAPI dependencies:

```python
@router.post(...)
async def x(user: CurrentUser = Depends(require_admin)) -> ...:
    await audit.record(username=user.username, ip=user.ip, action="x", target="...", new_value=...)
```

- **Always** call `audit.record(...)` for state-changing endpoints (it's append-only, no delete path — `app/db/audit.py`).
- The `users` table is seeded with the default admin on first run by `seed_default_admin()`; default credentials are forced to change on first login (`must_change_pw`).
- Login lockout: `auth.max_failed_logins` failures inside `auth.lockout_minutes` → HTTP 423 (`account_locked`). Tested in `tests/test_auth.py`.
- The **role matrix is auto-derived** from the live route table in tests — adding an unguarded write endpoint fails CI automatically.

### Error envelope

All API errors use `ApiError(code, message, status_code, detail)` (in `app/errors.py`) and are converted by handlers registered in `create_app()` to:

```json
{"error": {"code": "...", "message": "...", "detail": ...}}
```

Use `ApiError`, never raise raw `HTTPException` from routers.

### WebSockets

- Broadcast channels: `metrics`, `ota`, `alerts` — clients `join()` via `Hub` (`app/ws/manager.py`), producers `broadcast()`.
- Custom-stream sockets: `logs` (simulated journal), `can` (HAL frame stream), `terminal` (admin-only PTY bridge using `pty.fork`).
- Auth is via `?token=<jwt>` query param, validated *before* `accept()`. Use the `_authenticate` helper in `app/ws/routes.py`.
- `ws_terminal` audits `terminal_open` / `terminal_close` and is admin-only via `min_role="admin"`.

### Persistence

- Single aiosqlite connection, WAL mode, foreign keys ON — `app/db/database.py`. One `Database` instance, wired in `app.main.lifespan`; access via `get_db()`.
- All writes go through `db.execute()` (auto-commits, returns `lastrowid`).
- Migrations are append-only tuples `(version, sql)` in `app/db/migrations.py` — **never edit a historical migration, append a new one**. `migrate()` is idempotent and tracks `schema_version`.

### Privileged actions

Network, firewall, port, service, and device operations (per `req.md`):
- Always use the existing role-guard + audit mechanism.
- Destructive actions must be confirmed in the UI before execution.
- Validate inputs (IP/mask, port range, rule sanity) before applying.
- Never leave an interface or firewall in a half-applied state — match the existing rollback/error-handling behavior.

## Tests

Hermetic by design: `tests/conftest.py` builds a temp config (`simulation.force: true`, fixed JWT secret, throwaway SQLite, sandbox dir as the only allowlisted file root) and pins it via `DASHBOARD_CONFIG` **before** importing any app code. Add a fixture in `conftest.py` if you need shared state.

`tokens` fixture returns a JWT for each of `admin` / `operator` / `viewer` (admin created via the seeded default; operator+viewer provisioned via the API). Use `auth_headers(token)` to build the `Authorization: Bearer` header.

Coverage gate is **85%** (`pyproject.toml` `[tool.coverage.report] fail_under`). Use `pragma: no cover` sparingly; check `tests/test_smoke.py` to see how new read endpoints are auto-discovered.

## Conventions to match

- All public modules start with a one-line docstring; first paragraph is the *what*, second is the *why*.
- `from __future__ import annotations` at the top of every file.
- Pydantic `BaseModel` for all request/response bodies; FastAPI `Depends` for auth and DB access.
- HAL methods are synchronous (no `async`) and CPU-cheap; they're called from sync FastAPI handlers and from `asyncio.to_thread`-able background loops.
- No `print` — use `log_level` via uvicorn.
- New dependencies need approval (supply-chain concern) — update `requirements.txt` (+ `requirements-dev.txt` if dev-only) and rerun `make validate`.

## Do

- **Fix-issue loop (master §9):** one issue → one branch `fix/<#>-<slug>` → one PR → rebase-merge into `develop` → `gh issue close <#>` → next. Always run `make validate` (or `.venv/bin/ruff check app tests && .venv/bin/ruff format --check app tests && .venv/bin/mypy app && .venv/bin/pytest -q`) before pushing. Baseline 126 → 194 tests across the #2–#16 batch; 200+ after §9a #1–#26, 295 after #42–#54. **Use `scripts/fix-issue.sh` / `scripts/finish-issue.sh`** so the branch → PR → merge → close sequence is consistent and never amends `develop` directly.
- **Body-file for `gh issue create`:** backticks in `--body "..."` get shell-expanded even inside double quotes. Write the body to `/tmp/issue-<#>.md` (or `/tmp/req-close/<#>.md` for closures) and use `--body-file` to avoid mangled `cmd not found` noise in the rendered issue/comment.
- **Smallest change per issue:** when an issue (e.g. #5 unified interfaces) overlaps with another (#2 UART), keep the per-issue branch tight to that one endpoint surface; let the unifying refactor land in its own fix.
- **Audit before re-implementing:** before writing any new HAL method, `grep` the registry and existing routers — a prior commit (often `a76f29e`) may have already implemented the surface. The right "fix" can be `gh issue close` with a pointer comment, not a re-implementation.
- **Pydantic v2 + FastAPI 422:** `RequestValidationError` errors carry the original `ValueError` in `ctx` (not JSON-serializable). The global handler in `app/errors.py` strips `ctx` so the response is encodable. Re-apply that strip if the handler is ever rewritten.
- **`require_confirm` lives in `app/util/confirm.py`:** hoisted there from a private copy in `sysadmin.py`; both `settings.py` and `sysadmin.py` import the shared helper. The `sysadmin.py` router's local `_require_confirm` is gone; don't reintroduce it.
- **Don't #48 apply-state rollback attribution (#149):** `apply_state.rollback(plan_id, *, username, ip)` accepts kwargs so routers pass the triggering admin; watchdog passes the defaults (`system`/`127.0.0.1`). Don't reintroduce a duplicate audit row inside the router. Don't hardcode `system`/`127.0.0.1` in router code. Tests in `tests/test_apply_rollback_user.py` pin the contract for every registered rollback kind.
- **Recovering from a commit on `develop` (#149 mishap):** if you accidentally `git commit` on `develop` instead of a `fix/<#>-<slug>` branch, do `git checkout -b fix/<#>-<slug> <commit-sha>` to anchor a new branch on the commit, then `git checkout develop && git reset --hard origin/develop` to drop the stray commit from develop. Push the new branch, PR from there. Never `git push origin develop` or `git commit --amend` on develop to "fix" it.
- **Adding a privileged write to `apply_state` (#151 pattern):** `UbootEnvSet` (and similar) gets an optional `rollback_armed_s: int = Field(default=60, ge=1, le=3600)` so old tests still pass; the router calls `apply_state.arm(target=..., before=..., after=..., rollback_kind="...", rollback_armed_s=body.rollback_armed_s)` and returns `plan_id`/`armed_until`/`rollback_armed_s` in the response. Add `/apply/confirm` + `/apply/rollback` siblings that pass `username=user.username, ip=user.ip` to `apply_state.rollback(...)` (Don't #48).
- **`boot_env_kv` table (#151, migration 13):** durable store for the last-applied U-Boot env value per key. The `rollback_kind="boot_env"` handler writes the before-snapshot back into it. If you ever wire a real U-Boot env reader, layer it on top of this table rather than replacing it.
- **Response model `dict[str, bool]` blocks new fields:** if you need to add a non-boolean field to a `dict[str, bool]` response (e.g. listing which tables were reset), broaden the return annotation to `dict[str, Any]`.
- **Thermal `throttling` fix (#26):** when the cpufreq driver returns `(0, 0)`, pass the zeros through to the envelope (do NOT substitute a 1200/1800 default) and report `throttled=None` — otherwise `0 < 0` fires a false alarm.
- **Auth `get_current_user` revalidates DB (#25):** JWT only carries the claim; a deleted/disabled user must lose access within 10s. The `_user_state_cache` lives in `app/auth/deps.py` and is invalidated by `routers/auth.py` update/delete.
- **Per-kind pydantic schemas for `interfaces.configure` (#51):** `extra="forbid"` via a shared `_StrictModel` base rejects unknown keys (e.g. `duty_ns` on `i2c`) with 422. `model.model_validate(body)` raises `pydantic.ValidationError` — FastAPI does NOT auto-convert that one (it only converts `RequestValidationError` from FastAPI's own parsing), so catch `ValidationError` and re-raise as `ApiError("invalid_config", ..., 422)`. Schema lives in `app/util/models.py`; router does `interface_config_model(kind)` lookup.
- **HAL methods must enforce (#53):** routers pre-validate action params, but the HAL is the canonical enforcement point. Add an `else: raise ApiError("bad_action", ..., 400)` branch in any method that maps a small enum onto different behaviors — otherwise any direct caller (tests, future router) silently gets 200 with the unchanged state.
- **Auth lockout shadow rows (#54):** fixed lockout via `users.locked_until` works for real users with `UPDATE … SET locked_until = ?`. For unknown usernames (attackers probing) the UPDATE matches 0 rows and the lock is silently lost — fix is `INSERT … ON CONFLICT(username) DO UPDATE SET locked_until = excluded.locked_until` with `enabled=0` and a dummy `password_hash` so the shadow row is never authenticatable.
- **`Database.execute()` returns `lastrowid`, not `rowcount` (#54):** `app/db/database.py:45-49` returns `cur.lastrowid or 0`. Don't branch on the return value to detect "did this match?" — write UPSERT (`INSERT … ON CONFLICT …`) instead.
- **Config-tree natural format (#1 schema):** `FeaturesConfig` accepts both the natural operator YAML (`features: {settings: {notifications: false}}`) and the explicit-pydantic form (`features: {groups: {...}}`). A `@model_validator(mode="before")` on `FeaturesConfig._accept_natural_format` wraps the flat form into `{"groups": ...}` so pydantic routes it into the right field. Without this, the natural form silently routes into Pydantic's field-detection logic and gets dropped.
- **Config-tree emit-all-known-groups (#1 schema):** `get_feature_tree()` defaults to a curated list of every known group in `_KNOWN_SUBFEATURES` (12 groups). The SPA then has a complete nav tree even with an empty `features:` block — the gate still works per-sub-feature. Don't narrow the defaults; the SPA relies on every group being present.
- **Dynamic router registration test isolation:** testing a disabled-feature module should NOT use `importlib.reload()` on `app.config`/`app.config_features` — that re-evaluates the module-level `_db` singleton in `app/db/database.py` and breaks every test that follows. The minimal isolation pattern is: set `DASHBOARD_CONFIG` to a tmp_path config that includes `database.path` (so no real DB is touched), call `get_settings.cache_clear()`, call `is_subfeature_enabled(...)` + `resolve_routers()` directly, and `cache_clear()` again in the `finally` block. Don't reach for `importlib.reload()` to "freshen" config state.
- **Hard-coded `require_confirm({"confirm": True})` is a bug:** when a router calls `require_confirm` with a literal dict instead of `body.model_dump()`, the check is meaningless and the endpoint always succeeds. Pattern: take a `_Confirm(confirm: bool)` body and call `require_confirm(body.model_dump())`. The negative-path test sends `{"confirm": false}` to trigger 428; sending `{}` hits Pydantic `extra="forbid"` and returns 422 before `require_confirm` runs.
- **`_StrictModel` + `extra="forbid"` blocks extra keys in round-trip tests:** a round-trip like `export → import` will fail with 422 if the export body has fields the import model doesn't declare (e.g. `format_version`). In the test, strip the extra fields before POSTing back. The router layer doesn't auto-strip — that would defeat the `extra="forbid"` purpose.
- **Audit endpoint field is `entries`, not `rows`:** `GET /api/security/audit?action=…` returns `{"entries": [...]}`. If you write a test that checks `g.json().get("rows")`, it silently passes the empty-list assertion — write `g.json().get("entries")`.
- **Lost-staged-changes after `git reset --hard origin/develop`:** when you stage in-progress work on `develop`, then `git checkout -b fix/<#>-<slug>` (which creates a branch at HEAD without recording the index), then `git checkout develop && git reset --hard origin/develop` — the staged changes are gone. The fix branch was anchored at the same commit as develop, so it inherits no staging state. **Recovery**: re-apply the changes from notes / re-edit the files; the reflog shows where you were but the working tree is gone. Lesson: if you've made non-trivial progress, `git stash` before any branch-juggling + reset sequence.
- **`tests/conftest.py` exports `auth_headers(token: str)`:** `tests/test_new_features.py` imports it via `from tests.conftest import auth_headers  # noqa: F401`. Other available symbols in the conftest are `_login`, `client`, `sandbox`, `tokens` — don't import ones that don't exist (e.g. `client_factory`) or pytest collection will fail.
- **DataTable `editMode="cell"` requires a `@cell-edit-complete` handler:** adding `editMode="cell"` to a DataTable without a handler makes the cell show an edit affordance that does nothing on commit. If you only need inline widgets (SelectButton, InputText + `@blur`), leave the DataTable in default mode and use Vue's normal event bindings — `editMode` is only for the cell-editor flow.
- **Cross-field validation in pydantic v2:** when one field depends on another (e.g. `system_username` is required iff `user_source == "system"`), pydantic v2 doesn't have a clean first-class cross-field `@field_validator` for a field that is itself a `Literal`. Either: (a) use `@model_validator(mode="after")` on the model so all fields are set; (b) put the cross-field check in the router body. Don't try to chain `field_validator` + `info.data` in a confusing way — option (a) is cleanest.

## Don't

- Don't paste backticked code paths (e.g. `\`app/hal/hardware.py\``) directly into `gh issue create --body "..."` — the shell evaluates them. Use `--body-file` instead.
- Don't recreate an already-implemented HAL/router because a review issue says it's "missing" — read the existing module first. Many of req.md's gaps were closed by a single mega-commit (a76f29e) on the prior session.
- Don't use `pydantic.IPv4Address` as a type annotation in pydantic v2 — it's a function/validator, not a type. Use `str` + `field_validator` instead.
- Don't commit `audit.md` (or any session-spec scratch file) — it now lives in `.gitignore`. `git add app tests .gitignore scripts` is the safe scope for fix commits.
- Don't `git commit --amend` on `develop` directly. Always work on a `fix/<#>-<slug>` branch.
- **Audit-state alignment (#42):** when a write endpoint audit-logs a state change, it must actually change state. Two patterns that work: (a) persist the value in a small `*_kv` table (e.g. `system_settings_kv` for hostname/timezone), and have the read endpoint fall back to the live OS value when no override exists; (b) for actions that aren't a single value (vpn up/down, mqtt publish), add a write method to the relevant HAL that returns a `simulated: true` envelope with `applied: not self.simulated`, and a `payload_bytes`/`action`/`target` summary so the SPA can show "would apply" vs "applied".
- **`boot_env` rollback is write-only (#151):** the rollback handler writes the before-snapshot to `boot_env_kv` but the SPA's read path still uses `boot()["uboot_env"]`. A rolled-back env var won't visibly revert in the UI without a follow-up issue that overlays `boot_env_kv` on top of the simulated dict — not in scope for #151.
- **Recovering a stray commit on `develop`:** never `git push origin develop`. Reset develop to `origin/develop` only after the fix branch is already pushed and the PR is ready to merge.
- **SQLite `ORDER BY ts DESC` tiebreak is rowid ASC (#42):** when multiple rows are written in the same second (datetime('now') is second-granular), `ORDER BY ts DESC` returns older rows first, not newer. Either add a secondary `id DESC` sort or filter by unique value to find the right row. Don't trust "most recent = index 0" for second-granular audit reads.
- **Raw `psutil.cpu_percent()` returns 0.0 on first call (#46):** the first reading in any process is always 0 (psutil needs a baseline interval). Prime it once at sampler start (`psutil.cpu_percent(percpu=True)` and discard) so the first broadcast isn't a flat-line.
- **Raw `except Exception: ... f"install error: {exc}"` (#47):** strips the traceback. Use `_log.exception("OTA install failed (job_id=%s, filename=%s)", ...)` and surface a generic message to the SPA. The traceback is what makes the failure debuggable from the server log alone.
- **Apply-state `rollback()` audit attribution (#48):** when `apply_state.rollback` is called from a router, the audit row must record the *user who triggered the rollback*, not a hardcoded `system`/`127.0.0.1`. Pass `username: str = "system"` and `ip: str = "127.0.0.1"` kwargs so the router can override; don't reintroduce a hardcoded audit row inside `apply_state.rollback`.
- **Watchdog "present" without an access check (#49):** `has_command` returning True means the binary exists, not that the watchdog device is openable. Open the device `O_RDWR|O_NONBLOCK` (no write — never kick it) and report `accessible` + `access_error` so the UI can show a real status, not a lie.
- **Sysadmin endpoint names must be cross-validated (#50):** `network_link` accepts any string and lets the HAL "bring it down" with no error if the interface doesn't exist. Cross-reference `net_status()["interfaces"]` and 404 with `interface_not_present` so a typo can't silently fail.
- **`cp_block = re.search(...)` regex with literal `$` in raw string (#52):** a Python raw-string regex that contains a literal `$` is `\$`, NOT `\\$`. `\\$` matches a backslash followed by a dollar (which the file does not have). Use `\$SRC_DIR/...` — and verify the regex with a quick `python3 -c` debug before relying on it for tests.
- **Duplicate migration version is a silent skip:** `MIGRATIONS` is a list of `(version, sql)` tuples in `app/db/migrations.py`; `migrate()` iterates and applies any `version > MAX(schema_version)`. If two tuples share the same `version`, the second one is forever masked — first one gets recorded, every later version is greater, second entry never runs. Result: an `ALTER TABLE` that should have added a column never lands and the test that depends on the column fails with `no such column: …`. Always use the next free version (currently 24+); the order of migrations is the order in the list, but the version number must be unique. Verify with `awk '/^    \(/ {print}' app/db/migrations.py` to spot duplicates.
