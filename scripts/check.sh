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

echo "=== Version equality ==="
python -c "from importlib.metadata import version; import scrapefold; assert scrapefold.__version__ == version('scrapefold'), f'drift: {scrapefold.__version__} vs {version(\"scrapefold\")}'; print(f'OK: {scrapefold.__version__}')"

echo "=== All checks passed ==="
