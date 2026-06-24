# Low-Impact Web — developer & CI entry point.
#
# This Makefile is the single source of truth for every common task. Each
# target is small and documented so it is easy to read and extend: to add a new
# task, copy a target, give it a `## comment` (it shows up in `make help`) and
# add it to .PHONY.
#
# Quick start:
#   make install     # create the venv and install everything
#   make run          # start the dashboard on http://localhost:8000
#   make validate     # lint + typecheck + tests + coverage gate (CI gate)

# Use bash with strict flags for every recipe line.
SHELL := /bin/bash
.SHELLFLAGS := -eu -o pipefail -c
.DEFAULT_GOAL := help

VENV    := .venv
PY      := $(VENV)/bin/python
PIP     := $(VENV)/bin/pip
APP     := app
PKG     := app

.PHONY: help
help: ## Show this help.
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| sort \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

# --- environment -----------------------------------------------------------

$(VENV): ## Create the virtual environment.
	python3 -m venv $(VENV)
	$(PIP) install --upgrade pip

.PHONY: install
install: $(VENV) ## Install runtime + dev dependencies into the venv.
	$(PIP) install -r requirements-dev.txt

.PHONY: install-prod
install-prod: $(VENV) ## Install only runtime dependencies.
	$(PIP) install -r requirements.txt

# --- run -------------------------------------------------------------------

.PHONY: run
run: ## Run the dashboard (http://localhost:8000).
	$(PY) -m app.main

.PHONY: dev
dev: ## Run with autoreload for development.
	$(VENV)/bin/uvicorn app.main:create_app --factory --reload --port 8000

# --- vendoring (air-gap) ---------------------------------------------------

.PHONY: fetch-vendor
fetch-vendor: ## Re-download and rebuild self-hosted frontend vendor assets.
	bash deploy/fetch-vendor.sh

# --- quality gates ---------------------------------------------------------

.PHONY: lint
lint: ## Lint with ruff.
	$(VENV)/bin/ruff check $(APP) tests
	$(VENV)/bin/ruff format --check $(APP) tests

.PHONY: format
format: ## Auto-format with ruff.
	$(VENV)/bin/ruff format $(APP) tests
	$(VENV)/bin/ruff check --fix $(APP) tests

.PHONY: typecheck
typecheck: ## Static type-check the app with mypy --strict.
	$(VENV)/bin/mypy $(APP)

.PHONY: test
test: ## Run the unit/integration suite with coverage.
	$(VENV)/bin/pytest --cov=$(PKG) --cov-report=term-missing

.PHONY: test-e2e
test-e2e: ## Run Playwright browser tests (skipped if browsers absent).
	@if [ -d "$(HOME)/.cache/ms-playwright" ] || $(PY) -c "import playwright" 2>/dev/null; then \
		$(VENV)/bin/pytest tests/e2e -q || true; \
	else \
		echo "Playwright not installed — skipping e2e (run: $(VENV)/bin/playwright install chromium)"; \
	fi

.PHONY: validate
validate: lint typecheck test ## Full CI gate: lint + typecheck + tests + coverage.
	@echo "validate: all checks passed."

# --- housekeeping ----------------------------------------------------------

.PHONY: clean
clean: ## Remove caches, coverage and build artifacts (keeps the venv).
	rm -rf .pytest_cache .mypy_cache .ruff_cache .coverage htmlcov \
		test-results playwright-report
	find . -type d -name __pycache__ -prune -exec rm -rf {} +

.PHONY: distclean
distclean: clean ## Also remove the virtual environment.
	rm -rf $(VENV)
