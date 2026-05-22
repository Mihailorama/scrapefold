#!/usr/bin/env bash
# Pre-commit gate. Mirrors what CI runs on every PR.
set -euo pipefail

cd "$(dirname "$0")/.."

echo "=== Ruff lint ==="
ruff check src tests

echo "=== Ruff format check ==="
ruff format --check src tests

echo "=== Mypy ==="
mypy src

echo "=== Pytest (offline) ==="
pytest -m "not paid and not network" --maxfail=3

echo "=== All checks passed ==="
