# SoCcentric WebConfig — top-level Makefile.
#
# Per SoCcentric global CLAUDE.md §12, the repo-root Makefile is the single
# entry point for build/test/lint, wrapping the underlying tool (cargo). The
# git workflows (§9 step 6) run `make build && make test && make lint`.
#
# Targets:
#   make toolchain  — install rustup + the exact pinned toolchain (one-time
#                     host setup; see rust-toolchain.toml)
#   make build      — debug build of the daemon (host target)
#   make release    — release build with the exact spec §2 profile
#   make test       — engine + HTTP integration tests (fake sysroot, no HW)
#   make lint       — clippy + rustfmt --check
#   make fmt        — format in place
#   make vendor     — (re)generate the vendored offline crate tarball
#   make cross      — build all three cross-targets (CI parity)
#   make run        — run the daemon against the real host (live /proc, real
#                     backends); persistent state in ./data (dev)
#   make run-mock   — run against a tempdir fake sysroot (mock backends, no HW)
#   make stop       — stop the process listening on the WebConfig TCP port
#   make clean      — remove target/

# Install rustup (if missing) plus the exact pinned toolchain, components,
# and cross targets declared in rust-toolchain.toml (spec §6 host/CI parity).
# Idempotent — safe to re-run. Needs network access to static.rust-lang.org /
# sh.rustup.rs; not usable in network-restricted sandboxes.
toolchain: ## Install rustup + the exact pinned toolchain (one-time host setup)
	@set -e; \
	export PATH="$$HOME/.cargo/bin:$$PATH"; \
	if ! command -v rustup >/dev/null 2>&1; then \
		echo "==> rustup not found; installing"; \
		curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs \
			| sh -s -- -y --default-toolchain none --profile minimal; \
	fi; \
	echo "==> installing toolchain $(RUST_CHANNEL) (rustfmt, clippy)"; \
	rustup toolchain install $(RUST_CHANNEL) --profile minimal \
		--component rustfmt --component clippy; \
	echo "==> adding cross targets: $(TARGETS)"; \
	rustup target add --toolchain $(RUST_CHANNEL) $(TARGETS); \
	echo "==> ready: $$(rustup run $(RUST_CHANNEL) rustc --version)"

# Prefer the standard rustup installation even when the caller's shell has not
# sourced ~/.cargo/env; an explicit CARGO override still takes precedence.
CARGO ?= $(if $(wildcard $(HOME)/.cargo/bin/cargo),$(HOME)/.cargo/bin/cargo,cargo)
WEBCONFIG_PORT ?= 8443
export WEBCONFIG_PORT
TARGETS := x86_64-unknown-linux-gnu aarch64-unknown-linux-gnu armv7-unknown-linux-gnueabihf
# Pinned channel, read straight from rust-toolchain.toml so this target can't
# drift from the pin.
RUST_CHANNEL := $(shell grep -m1 '^channel' rust-toolchain.toml | sed -E 's/.*"([^"]*)".*/\1/')

.PHONY: help build release test lint fmt vendor release-artifacts cross run run-mock stop clean check toolchain

# Bare `make` prints help rather than building, so a stray invocation never
# kicks off a network install or a build by surprise.
.DEFAULT_GOAL := help

help: ## Show this help
	@echo "SoCcentric WebConfig — available targets:"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

build: ## Debug build of the daemon (host target)
	$(CARGO) build --workspace

release: ## Release build, exact spec §2 profile (opt-level=z, lto, stripped)
	$(CARGO) build --workspace --release

test: ## Engine + HTTP integration tests (fake sysroot, no hardware)
	$(CARGO) test --workspace --all-targets

lint: ## clippy -D warnings + rustfmt --check
	$(CARGO) clippy --workspace --all-targets -- -D warnings
	$(CARGO) fmt --all -- --check

fmt: ## Format the workspace in place
	$(CARGO) fmt --all

# Regenerate vendored sources for offline builds (spec §2). Run after any
# dependency change; commit the result.
vendor: ## Regenerate the vendored offline crate tarball
	$(CARGO) vendor --versioned-dirs vendor

# Package the release artifacts webconfigd.bb fetches (source + vendor
# tarballs) and print their real SHA-256 checksums (Checkpoint 3.2). Requires
# `make vendor` to have been run at least once.
release-artifacts: ## Package release tarballs + compute real checksums for webconfigd.bb
	@./tools/release-artifacts.sh

# Cross-compile parity check for CI (spec §6).
cross: ## Build all three cross-targets (x86_64/aarch64/armv7), CI parity
	@for t in $(TARGETS); do \
		echo "==> building $$t"; \
		$(CARGO) build --workspace --release --target $$t || exit 1; \
	done

# Dev run against the REAL host sysroot (spec §6): no WEBCONFIG_SYSROOT is
# set, so resolve_sysroot() uses "/" and is_real_root() is true — the daemon
# reads live /proc + /sys and uses the real systemd/update backends (mock
# backends are only selected for a tempdir fake sysroot). State (config.db,
# cert.pem, key.pem) is redirected to a persistent, gitignored ./data so the
# first-boot admin and config survive restarts, mirroring a device's /data
# partition. Wipe ./data to reset to first-boot. For the no-hardware mock
# mode, use `make run-mock`.
run: ## Run the daemon against the real host (live /proc, real backends)
	@mkdir -p data; \
	WEBCONFIG_DATA_DIR="$$(pwd)/data" \
	RUST_LOG=info \
	$(CARGO) run -p webconfigd -- --

# Mock mode: run against a tempdir fake sysroot so no hardware is required and
# no host side effects occur (spec §6). Vitals read as zeros; backends are
# mocks. The tempdir is wiped by the OS on reboot; state does not persist.
run-mock: ## Run against a tempdir fake sysroot (mock backends, no hardware)
	WEBCONFIG_SYSROOT=$$(mktemp -d) \
	WEBCONFIG_DATA_DIR=$$WEBCONFIG_SYSROOT/data \
	RUST_LOG=info \
	$(CARGO) run -p webconfigd -- --

# Stop the process holding the default daemon port, or an overridden
# WEBCONFIG_PORT. A clear port is already in the desired state.
stop: ## Stop the process listening on WEBCONFIG_PORT (default: 8443)
	@case "$$WEBCONFIG_PORT" in \
		[1-9]|[1-9][0-9]|[1-9][0-9][0-9]|[1-9][0-9][0-9][0-9]|[1-9][0-9][0-9][0-9][0-9]) ;; \
		*) echo "WEBCONFIG_PORT must be an integer between 1 and 65535" >&2; exit 2 ;; \
	esac; \
	if [ "$$WEBCONFIG_PORT" -lt 1 ] || [ "$$WEBCONFIG_PORT" -gt 65535 ]; then \
		echo "WEBCONFIG_PORT must be an integer between 1 and 65535" >&2; exit 2; \
	fi; \
	fuser "$$WEBCONFIG_PORT/tcp" >/dev/null 2>&1; status=$$?; \
	if [ $$status -eq 0 ]; then \
		fuser -k -TERM "$$WEBCONFIG_PORT/tcp" >/dev/null || exit $$?; \
		for attempt in 1 2 3 4 5; do \
			if ! fuser "$$WEBCONFIG_PORT/tcp" >/dev/null 2>&1; then \
				echo "Stopped process listening on port $$WEBCONFIG_PORT"; exit 0; \
			fi; \
			sleep 1; \
		done; \
		echo "Port $$WEBCONFIG_PORT is still in use after SIGTERM" >&2; exit 1; \
	fi; \
	if [ $$status -eq 1 ]; then \
		echo "No process is listening on port $$WEBCONFIG_PORT"; \
	else \
		echo "Unable to inspect port $$WEBCONFIG_PORT" >&2; exit $$status; \
	fi

clean: ## Remove target/
	$(CARGO) clean

# Convenience alias: full validation gate (§9 step 6).
check: build test lint ## Full validation gate: build && test && lint