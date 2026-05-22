---
purpose: "Helper scripts under scripts/ — what each does, when to use."
updated: "2026-05-22"
related:
  - ../workflows/development.md
  - ../conventions/golden-rules.md
---

# Helper scripts

All scripts live under `scripts/`, are executable, and use `set -euo pipefail`.

## `scripts/check.sh`

Pre-commit gate. Runs in order, exits non-zero on first failure:

1. `ruff check src tests`
2. `ruff format --check src tests`
3. `mypy src`
4. `pytest -m "not paid and not network"`

Run before every `git commit`:

```bash
./scripts/check.sh
```

This is exactly what CI runs on every PR (`.github/workflows/ci.yml`).

## `scripts/describe.sh`

Print a project state summary suitable for bootstrapping an AI agent into the repo:

- Directory tree
- Last 5 commits
- Current branch + uncommitted changes
- Failing tests (if any)
- TODO / FIXME / HACK / XXX markers in code

```bash
./scripts/describe.sh
```

## `scripts/quick-test.sh`

Run only tests for files changed since the last commit. Useful during iteration:

```bash
./scripts/quick-test.sh
```

Diff is computed against `HEAD` (staged + unstaged); maps each changed file to its corresponding test via the convention `src/scrapefold/X.py ↔ tests/test_X.py` (or `tests/test_engines/test_X.py` for engines).
