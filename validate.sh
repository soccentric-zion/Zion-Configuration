#!/usr/bin/env bash
#
# validate.sh — one-command validation, mirrors `make validate`.
#
# Useful where `make` is unavailable. Runs lint, strict type-checking and the
# test suite (with the coverage gate). Exits non-zero on the first failure.

set -euo pipefail
cd "$(dirname "$0")"

VENV="${VENV:-.venv}"
BIN="$VENV/bin"

if [[ ! -x "$BIN/python" ]]; then
	echo "No virtualenv at $VENV. Run: make install" >&2
	exit 1
fi

echo "==> ruff (lint + format check)"
"$BIN/ruff" check app tests
"$BIN/ruff" format --check app tests

echo "==> mypy (strict)"
"$BIN/mypy" app

echo "==> pytest (with coverage gate)"
"$BIN/pytest" --cov=app --cov-report=term-missing

echo "==> validate: all checks passed."
