#!/usr/bin/env bash
# Pre-commit gate. Mirrors what CI runs on every PR.
set -euo pipefail

cd "$(dirname "$0")/.."

PYTHON="${PYTHON:-$(command -v python3 || command -v python || true)}"
if [ -z "$PYTHON" ]; then
    echo "ERROR: no python3 or python found on PATH"
    exit 1
fi

echo "=== Ruff lint ==="
ruff check src tests

echo "=== Ruff format check ==="
ruff format --check src tests

echo "=== Mypy ==="
mypy src

echo "=== Pytest (offline) ==="
pytest -m "not paid and not network" --maxfail=3

echo "=== Version equality ==="
"$PYTHON" -c "from importlib.metadata import version; import scrapefold; assert scrapefold.__version__ == version('scrapefold'), f'drift: {scrapefold.__version__} vs {version(\"scrapefold\")}'; print(f'OK: {scrapefold.__version__}')"

echo "=== All checks passed ==="
