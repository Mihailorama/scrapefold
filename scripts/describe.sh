#!/usr/bin/env bash
# Project state snapshot for AI-agent context.
set -euo pipefail

cd "$(dirname "$0")/.."

echo "=== Project structure (depth 3) ==="
if command -v tree >/dev/null 2>&1; then
    tree -L 3 -I "__pycache__|*.egg-info|.venv|.git|.pytest_cache|.mypy_cache|.ruff_cache"
else
    find . -maxdepth 3 -type d \
        \( -name __pycache__ -o -name '*.egg-info' -o -name .venv -o -name .git \
           -o -name .pytest_cache -o -name .mypy_cache -o -name .ruff_cache \) -prune \
        -o -print | sort
fi

echo
echo "=== Current branch ==="
git branch --show-current

echo
echo "=== Last 5 commits ==="
git log --oneline -5 2>/dev/null || echo "(no commits yet)"

echo
echo "=== Uncommitted changes ==="
git diff --stat 2>/dev/null || true
git diff --cached --stat 2>/dev/null || true

echo
echo "=== TODO / FIXME markers ==="
grep -rn --include='*.py' -E 'TODO|FIXME|HACK|XXX' src tests 2>/dev/null | head -20 || true

echo
echo "=== pytest collection (offline) ==="
pytest --collect-only -q -m "not paid and not network" 2>&1 | tail -5 || true
