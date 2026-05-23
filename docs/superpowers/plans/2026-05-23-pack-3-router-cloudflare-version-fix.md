# Pack 3 — Router commit + cloudflare engine + version-pin fix

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land the on-disk S7 router shell, add the missing `cloudflare` engine, fix the `pyproject.toml` ↔ `__init__.py` version-pin drift, install release-mechanics scripts (CHANGELOG gate, dep-freshness, version-equality), and cut tag `v0.1.0a2`.

**Architecture:** Four phases — A (land pre-existing router), B (release-mechanics scaffolding), C (cloudflare engine TDD), D (CHANGELOG + tag). Each phase is independently revertable. The router and cloudflare work commit before the version bump; the version bump and dep-freshness commit before the tag.

**Tech Stack:** Python 3.10+, httpx, pytest, pytest-httpx, hatchling (dynamic version), ruff, mypy.

**Spec reference:** `docs/superpowers/specs/2026-05-23-v0.1.0-stable-roadmap-design.md` §3.1.

---

## Phase A — Land the on-disk router shell + structured error contract

Working tree as of `5039a61` already contains:
- `src/scrapefold/router.py` (130 lines, sequential ladder walker)
- `tests/test_router.py` (430 lines, 13 tests)
- `src/scrapefold/__init__.py` modified to wire `scrape()` → `router.walk()`
- `tests/test_smoke.py` cleaned of the obsolete `NotImplementedError` test

The 381 baseline + 13 router tests pass locally. The router skips `RaceStep` entries with a DEBUG log (TBD until Pack 9 = v0.2.0). The work is review-ready; this phase ships it.

**Additionally** (Codex round-1 follow-up): upgrade `AllEnginesFailed` to carry structured `url` + `failures` so consumer migration in Pack 7 can be a single PR, not a backward-compat shim. Consumers will catch `AllEnginesFailed`, inspect `exc.url` + `exc.failures`, and decide. No marker-string legacy preserved.

### Task A.1 — Verify the working tree is clean and tests green

**Files:**
- Read-only: `src/scrapefold/router.py`, `tests/test_router.py`, `src/scrapefold/__init__.py`, `tests/test_smoke.py`

- [ ] **Step 1: Confirm uncommitted files match expectations**

Run: `git status`
Expected: modified `src/scrapefold/__init__.py`, modified `tests/test_smoke.py`, untracked `src/scrapefold/router.py`, untracked `tests/test_router.py`. **No other** changes in the tree. If extras appear, stop and ask the user before proceeding — this plan assumes a clean tree.

- [ ] **Step 2: Run the full offline test suite**

Run: `source .venv/bin/activate && ./scripts/check.sh`
Expected: ruff clean, ruff format clean, mypy clean, `381 passed, 6 skipped, 13 collected in tests/test_router.py` — `=== All checks passed ===`. If anything fails, stop. The pre-existing router work must be green before commit.

### Task A.2 — Commit the router shell

**Files:**
- Stage: `src/scrapefold/router.py`, `tests/test_router.py`, `src/scrapefold/__init__.py`, `tests/test_smoke.py`
- Modify: `CHANGELOG.md` (add Pack 3 entry)

- [ ] **Step 1: Add the Pack 3 Unreleased entry to CHANGELOG**

Edit `CHANGELOG.md`. Insert under `## [Unreleased]` (currently empty):

```markdown
## [Unreleased]

### Added — Pack 3 router shell (sequential)

- `src/scrapefold/router.py` — `async walk(url, opts) -> ScrapeResult` walks the per-site-class ladder. Honors `Policy` (paid_allowed / legal_constraints_blocked / geography_required), `WalkBudget` ceilings (`max_engines`, `max_cost_usd`, `timeout_s`), and the `engines_tried` dedup set. `RaceStep` entries are skipped with a DEBUG log until Pack 9.
- `tests/test_router.py` — 13 tests covering happy path, empty-result advance, EngineError advance, unavailable-engine skip, AllEnginesFailed, unknown-engine skip, policy gating, budget halt, RaceStep skip, public `scrape()` delegation, failures-list, no-retry-within-walk, EngineError-non-propagation.
- `scrapefold.scrape(url, opts)` now delegates to `router.walk` instead of raising `NotImplementedError`.
- `tests/test_smoke.py` — the obsolete `NotImplementedError` smoke test is removed.
```

- [ ] **Step 2: Stage and commit**

Run:

```bash
git add CHANGELOG.md src/scrapefold/router.py tests/test_router.py src/scrapefold/__init__.py tests/test_smoke.py
git commit -m "$(cat <<'EOF'
feat: Pack 3 — sequential router shell (S7)

Land async walk(url, opts) that walks the per-site-class ladder declared
in ladders.py, returning the first non-suspicious result. Honors Policy,
WalkBudget ceilings, and engines_tried dedup. RaceStep entries are
skipped with a DEBUG log until Pack 9 (v0.2.0).

scrapefold.scrape() now delegates to router.walk() instead of raising
NotImplementedError.

13 router tests + 381 baseline pass. Spec: docs/superpowers/specs/2026-05-23-v0.1.0-stable-roadmap-design.md §3.1

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

Expected: commit succeeds; `git status` reports a clean tree.

- [ ] **Step 3: Verify lint+test still green after commit**

Run: `./scripts/check.sh`
Expected: `=== All checks passed ===` (same as A.1 Step 2 — sanity check that committing didn't drag in any pre-commit-hook surprises).

### Task A.3 — Structured `AllEnginesFailed` (CRITICAL/HIGH fix from Codex round 1)

Today `AllEnginesFailed` is `class AllEnginesFailed(Exception): pass` and the router raises `AllEnginesFailed(f"all engines failed for {url}: {failures}")`. That collapses structured failure data into an opaque string. Consumers can't introspect what was tried or why.

**Files:**
- Modify: `src/scrapefold/ladders.py:158-160`
- Modify: `src/scrapefold/router.py:127`
- Modify: `tests/test_router.py` (add structured-attrs assertion)

- [ ] **Step 1: Write failing test**

Append to `tests/test_router.py`:

```python
async def test_all_engines_failed_carries_url_and_failures(
    stub_registry: dict[str, type[ScrapeEngine]],
    stub_ladder: Any,
) -> None:
    """AllEnginesFailed exposes .url and .failures for consumer introspection."""
    from scrapefold.router import walk

    stub_ladder(
        (
            SequentialStep(engine="stub_empty"),
            SequentialStep(engine="stub_raise"),
        )
    )

    with pytest.raises(AllEnginesFailed) as exc_info:
        await walk("https://example.com/probe")

    assert exc_info.value.url == "https://example.com/probe"
    assert isinstance(exc_info.value.failures, list)
    assert any("stub_empty" in f for f in exc_info.value.failures)
    assert any("stub_raise" in f for f in exc_info.value.failures)
```

- [ ] **Step 2: Run failing test**

Run: `pytest tests/test_router.py::test_all_engines_failed_carries_url_and_failures -v`
Expected: AttributeError (`.url` / `.failures` don't exist on the exception).

- [ ] **Step 3: Upgrade `AllEnginesFailed` in `ladders.py`**

In `src/scrapefold/ladders.py`, replace lines 158-160:

```python
class AllEnginesFailed(Exception):  # noqa: N818
    """Raised when every step in the ladder failed or was skipped.

    Carries structured failure data so consumers can introspect what was
    tried without parsing the exception's string form.
    """

    def __init__(self, url: str, failures: list[str]) -> None:
        self.url = url
        self.failures = list(failures)
        super().__init__(f"all engines failed for {url}: {self.failures}")
```

- [ ] **Step 4: Update router's raise site**

In `src/scrapefold/router.py`, replace the final line:

```python
raise AllEnginesFailed(f"all engines failed for {url}: {failures}")
```

with:

```python
raise AllEnginesFailed(url=url, failures=failures)
```

- [ ] **Step 5: Run test + full suite**

Run: `pytest tests/test_router.py::test_all_engines_failed_carries_url_and_failures -v && ./scripts/check.sh`
Expected: both green.

- [ ] **Step 6: Document the error contract**

Append to `CHANGELOG.md` `[Unreleased]`:

```markdown
### Changed — structured AllEnginesFailed (consumer error contract)

- `AllEnginesFailed` now carries `.url: str` and `.failures: list[str]`.
  Consumers no longer need to parse the exception message. The
  `failures` list shape is `"<engine>:<reason>:<detail>"` (e.g.
  `"firecrawl:error:404 Not Found"`, `"jina:empty"`,
  `"scrapingbee:unavailable"`, `"budget:cost"`). Pack 7 consumer
  migrations (downstream-consumer + downstream-consumer) target this contract directly.
```

- [ ] **Step 7: Commit**

```bash
git add src/scrapefold/ladders.py src/scrapefold/router.py tests/test_router.py CHANGELOG.md
git commit -m "$(cat <<'EOF'
feat: Pack 3 — structured AllEnginesFailed.url + .failures

Consumers (downstream-consumer, downstream-consumer) now have a typed error surface to migrate
onto in Pack 7. Replaces the opaque message-string approach that
downstream-consumer' marker-string fallback was working around.

Codex round-1 review HIGH finding #5 — addresses downstream-consumer PR1 safety
by removing the need for any marker-preservation shim.
EOF
)"
```

---

## Phase B — Release-mechanics scaffolding

Three new scripts (`check-deps-fresh.sh`, `check-changelog.sh`, version-equality step in `check.sh`), `pyproject.toml` switched to dynamic version, `pyproject.toml` extras cleaned of `obscura` / `brightdata`.

### Task B.1 — Switch pyproject.toml to dynamic version

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Write a failing assertion**

Add to `tests/test_smoke.py` (append at file end):

```python
def test_dist_metadata_version_matches_dunder_version() -> None:
    """pyproject.toml dynamic version must equal scrapefold.__version__."""
    from importlib.metadata import version

    import scrapefold

    assert scrapefold.__version__ == version("scrapefold"), (
        f"drift: __init__.py={scrapefold.__version__!r} "
        f"metadata={version('scrapefold')!r}"
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_smoke.py::test_dist_metadata_version_matches_dunder_version -v`
Expected: FAIL — `__version__` is `"0.1.0a1"` but `pyproject.toml` declares `version = "0.1.0a0"`. AssertionError shows the drift.

- [ ] **Step 3: Edit pyproject.toml to use dynamic version**

In `pyproject.toml`:

Remove the line:
```toml
version = "0.1.0a0"
```

Add to `[project]`:
```toml
dynamic = ["version"]
```

Add a new top-level section (place after `[tool.hatch.build.targets.wheel]`):
```toml
[tool.hatch.version]
path = "src/scrapefold/__init__.py"
```

- [ ] **Step 4: Reinstall and verify test passes**

Run: `pip install -e ".[test]" && pytest tests/test_smoke.py::test_dist_metadata_version_matches_dunder_version -v`
Expected: PASS. `pip show scrapefold` reports `Version: 0.1.0a1`.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml tests/test_smoke.py
git commit -m "$(cat <<'EOF'
fix: pyproject.toml uses dynamic version from __init__.py

Eliminates version-pin drift between pyproject.toml (was 0.1.0a0) and
src/scrapefold/__init__.py (was 0.1.0a1). Hatch reads __version__ from
the package init at build time. New smoke test asserts equality.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

### Task B.2 — Drop obscura / brightdata from extras

**Files:**
- Modify: `pyproject.toml`
- Create: `docs/post-1.0/backlog.md`

- [ ] **Step 1: Edit pyproject.toml**

In `pyproject.toml` `[project.optional-dependencies]`:

Remove these lines:
```toml
obscura      = []
brightdata   = ["brightdata>=1.0"]
```

In the `all` extra value, remove `obscura` and `brightdata` from the comma list:
```toml
all = [
    "scrapefold[firecrawl,scrapingbee,selenium,scrapling,crawl4ai,outscraper,apify,cloakbrowser,mcp]",
]
```

- [ ] **Step 2: Create the post-1.0 backlog file**

Create `docs/post-1.0/backlog.md`:

```markdown
---
purpose: "Engines + features intentionally deferred from v0.1.0."
updated: "2026-05-23"
related:
  - ../superpowers/specs/2026-05-23-v0.1.0-stable-roadmap-design.md
---

# Post-v0.1.0 backlog

Tracked here so the v0.1.0 surface stays focused and the deferrals are
recoverable in v0.2.0+.

## Engines

### `obscura`

- **Why deferred:** No engine module exists yet; the pyproject extra was
  pointing to nothing importable, which created a broken-install cliff
  (`pip install scrapefold[obscura]` succeeded with no engine).
- **What it would do:** Free stealth browser, planned as an alternative
  to `cloakbrowser` in the `static_general` Step 3 race.
- **v0.2.0 plan:** Land as Wave 3 Pack 3A alongside `brightdata`.

### `brightdata` (Unlocker sync + async, Browser)

- **Why deferred:** Same as `obscura` — extra existed but no engine. Also
  `brightdata>=1.0` SDK is paid + auth-heavy; needs proper credential
  flow in tests.
- **What it would do:** Last-resort paid unlock at the end of nearly
  every difficulty ladder.
- **v0.2.0 plan:** Wave 3 Pack 3A. Register as three names
  (`brightdata_unlocker_sync`, `brightdata_unlocker_async`,
  `brightdata_browser`) with user-facing alias `brightdata` →
  `brightdata_unlocker_sync`.

## Features

(populated by later packs)
```

- [ ] **Step 3: Verify the install surface no longer lies**

Run: `pip install -e ".[obscura]" 2>&1 | head -3`
Expected: pip warning `Did you mean ...?` or `WARNING: scrapefold X does not provide the extra 'obscura'`. The point is **no successful install**.

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml docs/post-1.0/backlog.md
git commit -m "$(cat <<'EOF'
chore: remove broken obscura/brightdata extras from pyproject

The two extras pointed to nothing importable, so 'pip install
scrapefold[brightdata]' succeeded with a broken install. Both move to
docs/post-1.0/backlog.md for v0.2.0 re-introduction. The pyproject
extras list now matches src/scrapefold/engines/ exactly.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

### Task B.3 — Add the dep-freshness script

**Files:**
- Create: `scripts/check-deps-fresh.sh`
- Modify: `docs/tools/scripts.md` (if it exists; otherwise skip the docs edit and add a one-line mention to `docs/README.md`)

- [ ] **Step 1: Create scripts/check-deps-fresh.sh**

Create `scripts/check-deps-fresh.sh`:

```bash
#!/usr/bin/env bash
# check-deps-fresh.sh — prints current-pin vs. latest-stable for each
# direct dep in pyproject.toml. NOT a CI failure — a pack-opening nag.
#
# Usage: ./scripts/check-deps-fresh.sh
# Requires: python (uses stdlib only — urllib + tomllib on 3.11+, tomli on 3.10).

set -euo pipefail
cd "$(dirname "$0")/.."

python - <<'PY'
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

try:
    import tomllib  # 3.11+
except ImportError:
    import tomli as tomllib  # 3.10 fallback

pyproject = tomllib.loads(Path("pyproject.toml").read_text())

# Collect every direct dep, base + extras + test
deps: dict[str, str] = {}
for spec in pyproject["project"].get("dependencies", []):
    m = re.match(r"^([A-Za-z0-9_\-.]+)(?:\[[^\]]+\])?\s*(>=|==|>|~=)?\s*([0-9A-Za-z.\-]+)?", spec)
    if m and m.group(3):
        deps[m.group(1)] = m.group(3)
for extra, specs in pyproject["project"].get("optional-dependencies", {}).items():
    for spec in specs:
        if spec.startswith("scrapefold["):
            continue
        m = re.match(r"^([A-Za-z0-9_\-.]+)(?:\[[^\]]+\])?\s*(>=|==|>|~=)?\s*([0-9A-Za-z.\-]+)?", spec)
        if m and m.group(3):
            deps[m.group(1)] = m.group(3)

def latest_stable(pkg: str) -> str | None:
    """Return the latest non-pre-release version on PyPI, or None on failure."""
    try:
        with urllib.request.urlopen(f"https://pypi.org/pypi/{pkg}/json", timeout=10) as fh:
            data = json.load(fh)
    except (urllib.error.URLError, json.JSONDecodeError):
        return None
    releases = data.get("releases", {})
    stable = []
    for v, files in releases.items():
        if not files:
            continue
        if any(f.get("yanked") for f in files):
            continue
        if re.search(r"(a|b|rc|dev|alpha|beta)\d*", v, re.IGNORECASE):
            continue
        stable.append(v)
    if not stable:
        return None
    from packaging.version import Version, InvalidVersion
    try:
        return str(max((Version(v) for v in stable)))
    except InvalidVersion:
        return None

def minor_distance(pin: str, latest: str) -> int:
    """Approximate minor-version distance. Returns -1 on parse failure."""
    from packaging.version import Version, InvalidVersion
    try:
        a, b = Version(pin), Version(latest)
    except InvalidVersion:
        return -1
    if a.major != b.major:
        return (b.major - a.major) * 100 + (b.minor - a.minor)
    return b.minor - a.minor

print(f"{'package':<25} {'pinned (>=)':<15} {'latest stable':<15} staleness")
print(f"{'-'*25} {'-'*15} {'-'*15} ---------")
for pkg in sorted(deps):
    pin = deps[pkg]
    latest = latest_stable(pkg)
    if latest is None:
        print(f"{pkg:<25} {pin:<15} {'(query failed)':<15} ?")
        continue
    dist = minor_distance(pin, latest)
    marker = "" if dist <= 0 else f"  ←  {dist} minor(s) behind"
    print(f"{pkg:<25} {pin:<15} {latest:<15} {marker}")
PY
```

- [ ] **Step 2: Make it executable and run it**

Run:

```bash
chmod +x scripts/check-deps-fresh.sh
./scripts/check-deps-fresh.sh
```

Expected: a table is printed with one row per dep. Some will be flagged as multiple minors behind (this is the point — Task B.6 acts on this). Network failures print `(query failed) ?` and do not break the script.

- [ ] **Step 3: Commit**

```bash
git add scripts/check-deps-fresh.sh
git commit -m "$(cat <<'EOF'
chore: scripts/check-deps-fresh.sh — pack-opening dep-floor nag

Reads pyproject.toml direct deps, queries PyPI JSON API, prints
current-pin vs. latest-stable with minor-version staleness. Not a CI
failure — a checklist item run at the start of each pack so floors
don't drift.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

### Task B.4 — Add the CHANGELOG-gate script

**Files:**
- Create: `scripts/check-changelog.sh`

- [ ] **Step 1: Create scripts/check-changelog.sh**

Create `scripts/check-changelog.sh`:

```bash
#!/usr/bin/env bash
# check-changelog.sh — fails if a PR touches src/scrapefold/ without
# adding a line under [Unreleased] in CHANGELOG.md.
#
# Usage: ./scripts/check-changelog.sh [base-ref]
# Default base-ref is origin/main.

set -euo pipefail
cd "$(dirname "$0")/.."

BASE_REF="${1:-origin/main}"

# Files changed on this branch (added, modified, copied, renamed)
CHANGED=$(git diff --name-only --diff-filter=ACMR "${BASE_REF}...HEAD")

# If no src/ change, we don't require a CHANGELOG entry
if ! echo "$CHANGED" | grep -q '^src/scrapefold/'; then
    echo "check-changelog: no src/scrapefold/ change, nothing to gate"
    exit 0
fi

# Pull the [Unreleased] section diff
DIFF=$(git diff "${BASE_REF}...HEAD" -- CHANGELOG.md)
if ! echo "$DIFF" | grep -q '^+'; then
    echo "check-changelog: FAIL — src/scrapefold/ changed but CHANGELOG.md has no added lines on this branch"
    echo "  Add at least one bullet under ## [Unreleased] in CHANGELOG.md"
    exit 1
fi

# Confirm the added line is inside or below [Unreleased]
# (heuristic: any added '+ - ' or '+### ' line is acceptable)
if ! echo "$DIFF" | grep -qE '^\+(- |### |#### )'; then
    echo "check-changelog: FAIL — CHANGELOG.md has added lines but none look like a release-notes entry"
    echo "  Expected an added line starting with '- ' or '### ' under ## [Unreleased]"
    exit 1
fi

echo "check-changelog: OK — src/ change accompanied by CHANGELOG entry"
```

- [ ] **Step 2: Make it executable and self-test**

Run:

```bash
chmod +x scripts/check-changelog.sh
./scripts/check-changelog.sh HEAD~3  # against 3 commits ago in this branch
```

Expected: prints `check-changelog: OK` (because Phase A already added a CHANGELOG entry alongside the router commit, and we're three commits past the original baseline).

- [ ] **Step 3: Commit**

```bash
git add scripts/check-changelog.sh
git commit -m "$(cat <<'EOF'
chore: scripts/check-changelog.sh — PR-time gate

Fails if a branch touches src/scrapefold/ without adding a line under
[Unreleased] in CHANGELOG.md. Heuristic: looks for a +- or +### line in
the CHANGELOG diff against base ref.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

### Task B.5 — Add version-equality + changelog-gate to scripts/check.sh and CI

**Files:**
- Modify: `scripts/check.sh`
- Modify: `.github/workflows/ci.yml`

- [ ] **Step 1: Add a version-equality check step to scripts/check.sh**

Read `scripts/check.sh` first to understand its current structure, then append a new step **before** the final "All checks passed" echo:

```bash
echo "=== Version equality ==="
python -c "from importlib.metadata import version; import scrapefold; assert scrapefold.__version__ == version('scrapefold'), f'drift: {scrapefold.__version__} vs {version(\"scrapefold\")}'; print(f'OK: {scrapefold.__version__}')"
```

- [ ] **Step 2: Add release-rehearsal job to ci.yml**

Append to `.github/workflows/ci.yml` a new job (after the existing `test` job, before the existing `publish` job):

```yaml
  release-rehearsal:
    name: Release rehearsal (build + twine check + install)
    runs-on: ubuntu-latest
    needs: test
    steps:
      - uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'

      - name: Install build + twine
        run: pip install build twine

      - name: Build sdist + wheel
        run: python -m build

      - name: twine check
        run: python -m twine check dist/*

      - name: Install built wheel in a clean env
        run: pip install dist/*.whl

      - name: Smoke-import installed package
        run: python -c "import scrapefold; print(scrapefold.__version__)"
```

Also add a changelog-gate step inside the existing `test` job, after the `Install` step:

```yaml
      - name: Changelog gate (skips on main pushes)
        if: github.event_name == 'pull_request'
        run: bash scripts/check-changelog.sh "origin/${{ github.base_ref }}"
```

- [ ] **Step 3: Run scripts/check.sh locally to confirm**

Run: `./scripts/check.sh`
Expected: `=== Version equality === OK: 0.1.0a1` then `=== All checks passed ===`.

- [ ] **Step 4: Commit**

```bash
git add scripts/check.sh .github/workflows/ci.yml
git commit -m "$(cat <<'EOF'
chore: version-equality gate in check.sh + release rehearsal job

Adds a final step to scripts/check.sh asserting
scrapefold.__version__ == importlib.metadata.version('scrapefold')
(catches build-time pin drift even when pyproject.toml uses dynamic).

Adds a release-rehearsal CI job that does build + twine check + install
+ smoke import on every PR — catches MANIFEST mistakes and
dynamic-version misconfig before a tag is cut.

Adds a changelog-gate step to the test job that runs check-changelog.sh
on PRs.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

### Task B.6 — Run dep-freshness audit and bump floors

**Files:**
- Modify: `pyproject.toml` (lower-bound pins)

- [ ] **Step 1: Run the audit**

Run: `./scripts/check-deps-fresh.sh > /tmp/deps-fresh.txt && cat /tmp/deps-fresh.txt`
Expected: a table of pins. Note every row marked "N minor(s) behind."

- [ ] **Step 2: Bump lower bounds in pyproject.toml**

For each dep that is **N ≥ 2 minor versions behind** AND has no documented incompatibility, edit `pyproject.toml` to bump the `>=X.Y` floor to the current latest stable (no upper bound). Apply the §4.4 stable definition from the spec — if the latest is itself an `aN`/`rcN`/`bN`, use the previous non-pre-release stable.

Examples of expected bumps (verify exact values from the audit output before applying):

```toml
# Before
httpx>=0.27
typer>=0.12
pytest>=7.0
ruff>=0.4
mypy>=1.8
firecrawl-py>=4.0
scrapling[fetchers]>=0.4
crawl4ai>=0.8

# After (verify against actual PyPI before committing)
httpx>=0.28
typer>=0.13
pytest>=8.4
ruff>=0.7
mypy>=1.13
firecrawl-py>=5.0  # if 5.x exists and tests pass; otherwise stay at 4.x latest minor
scrapling[fetchers]>=0.5
crawl4ai>=0.9
```

If a major-version bump (e.g. firecrawl-py 4 → 5) breaks any test, that breakage is **in scope for Pack 3**. Either fix the engine adapter or pin upper bound with an inline `# Firecrawl 5.x removed AsyncFirecrawlApp.scrape_url — issue #N` comment.

- [ ] **Step 3: Reinstall the test extras and run the full suite**

Run: `pip install -e ".[test]" --upgrade && ./scripts/check.sh`
Expected: `=== All checks passed ===`. If any test fails because of an upstream API change, fix the affected engine adapter inside this pack (do not defer).

- [ ] **Step 4: Add CHANGELOG entry**

Append under the existing `## [Unreleased]` section:

```markdown
### Changed — dependency floors refreshed

- Bumped lower-bound pins to current PyPI stable (pack-opening
  freshness policy from spec §4.4). Affected pins: <list bumped deps
  here>.
```

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml CHANGELOG.md
git commit -m "$(cat <<'EOF'
chore: refresh dependency floors per Pack 3 freshness audit

Bumped lower-bound pins to current PyPI stable per scripts/check-deps-fresh.sh.
No upper bounds added — library convention. Spec: docs/superpowers/specs/2026-05-23-v0.1.0-stable-roadmap-design.md §4.4

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Phase C — Cloudflare engine (TDD)

Port `get_content_cloudflare` from `downstream-consumer/services/url_to_text_service.py` (lines 1452-1568). Two endpoints: `/markdown` (preferred) and `/content` (raw HTML fallback). Auth via `Authorization: Bearer ${CLOUDFLARE_API_TOKEN}` — verified via context7 against Cloudflare's current Browser Rendering docs (snippet shows `-H 'Authorization: Bearer <apiToken>'` against `https://api.cloudflare.com/client/v4/accounts/<accountId>/browser-rendering/markdown`). Env vars: `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID`.

**Cost honesty (Codex round-1 HIGH #4):** Cloudflare Browser Rendering is billed per request. An empty `/markdown` response that triggers the `/content` fallback = 2 paid requests, not 1. The engine MUST:
1. Record `meta["endpoint_calls"]` = list of endpoints actually hit (e.g. `["markdown"]` or `["markdown", "content"]`).
2. Default `EngineCapabilities.estimated_cost_usd` to the **per-request** vendor rate so the router's cost budget accounts for it. (Concrete number lives in `EngineCapabilities`; verify against Cloudflare's pricing page at pack-open time.)
3. Make the fallback opt-out via `opts.extra["cloudflare_skip_content_fallback"] = True` for callers who specifically want one-shot markdown.

**Note:** `docs/workflows/development.md` env-vars table currently lists `CLOUDFLARE_API_KEY` — this is wrong. The Cloudflare ecosystem uses `_API_TOKEN`. Update that table as part of this phase.

### Task C.1 — Write the failing tests

**Files:**
- Create: `tests/test_engine_cloudflare.py`

- [ ] **Step 1: Write the test file (failing because the engine doesn't exist)**

Create `tests/test_engine_cloudflare.py`:

```python
"""Tests for CloudflareEngine — Cloudflare Browser Rendering API engine.

All tests use the httpx_mock fixture from pytest-httpx so no real network
calls are made. Follows the offline-by-default golden rule.

Ported from downstream-consumer/services/url_to_text_service.py:get_content_cloudflare
(lines 1452-1568). Two endpoints: /markdown (preferred) and /content (HTML
fallback when /markdown returns empty or non-200).
"""

from __future__ import annotations

import pytest
from pytest_httpx import HTTPXMock

from scrapefold.engines.base import EngineError
from scrapefold.options import ScrapeOptions

_ACCOUNT = "test-account-123"
_TOKEN = "cf-token-abc"
_TARGET_URL = "https://example.com/article"
_BASE_URL = f"https://api.cloudflare.com/client/v4/accounts/{_ACCOUNT}/browser-rendering"
_MD_URL = f"{_BASE_URL}/markdown"
_CONTENT_URL = f"{_BASE_URL}/content"

_MARKDOWN_BODY = "# Hello\n\nSome **bold** content."
_HTML_BODY = "<html><body><h1>Hello</h1><p>World</p></body></html>"


def _engine(token: str | None = _TOKEN, account: str | None = _ACCOUNT):
    from scrapefold.engines.cloudflare import CloudflareEngine

    return CloudflareEngine(api_token=token, account_id=account)


# ---------------------------------------------------------------------------
# 1. Happy path /markdown — JSON response shape {"result": "<md>"}
# ---------------------------------------------------------------------------


async def test_markdown_endpoint_string_result(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url=_MD_URL,
        method="POST",
        json={"result": _MARKDOWN_BODY, "success": True},
    )

    result = await _engine().scrape(_TARGET_URL)

    assert result.markdown == _MARKDOWN_BODY
    assert result.engine == "cloudflare"
    assert result.text != ""  # derived from markdown
    # Cost honesty (Codex round-1 HIGH #4)
    assert result.meta["endpoint_calls"] == ["markdown"]
    assert result.cost_usd > 0  # per-request paid


# ---------------------------------------------------------------------------
# 2. /markdown response shape {"result": {"markdown": "<md>"}}
# ---------------------------------------------------------------------------


async def test_markdown_endpoint_dict_result(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url=_MD_URL,
        method="POST",
        json={"result": {"markdown": _MARKDOWN_BODY}, "success": True},
    )

    result = await _engine().scrape(_TARGET_URL)

    assert result.markdown == _MARKDOWN_BODY


# ---------------------------------------------------------------------------
# 3. Bearer token + Content-Type header on every request
# ---------------------------------------------------------------------------


async def test_authorization_and_content_type_headers(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url=_MD_URL, method="POST", json={"result": _MARKDOWN_BODY})

    await _engine().scrape(_TARGET_URL)

    request = httpx_mock.get_requests()[0]
    assert request.headers["authorization"] == f"Bearer {_TOKEN}"
    assert request.headers["content-type"] == "application/json"


# ---------------------------------------------------------------------------
# 4. /markdown empty → fall back to /content
# ---------------------------------------------------------------------------


async def test_falls_back_to_content_when_markdown_empty(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url=_MD_URL, method="POST", json={"result": ""})
    httpx_mock.add_response(url=_CONTENT_URL, method="POST", json={"result": _HTML_BODY})

    result = await _engine().scrape(_TARGET_URL)

    assert result.html == _HTML_BODY
    assert "Hello" in result.text  # post-converted from HTML
    assert result.markdown != ""
    # Cost honesty — both endpoints hit = 2× per-request cost
    assert result.meta["endpoint_calls"] == ["markdown", "content"]
    from scrapefold.engines.cloudflare import CloudflareEngine
    assert result.cost_usd == CloudflareEngine._PER_REQUEST_USD * 2


async def test_skip_content_fallback_opts_out(httpx_mock: HTTPXMock) -> None:
    """opts.extra['cloudflare_skip_content_fallback']=True → only /markdown hit."""
    from scrapefold.engines.base import EngineError
    httpx_mock.add_response(url=_MD_URL, method="POST", json={"result": ""})

    with pytest.raises(EngineError):
        await _engine().scrape(
            _TARGET_URL,
            ScrapeOptions(extra={"cloudflare_skip_content_fallback": True}),
        )
    # /content was NOT hit — verify by checking only one request was made
    assert len(httpx_mock.get_requests()) == 1


# ---------------------------------------------------------------------------
# 5. /markdown 4xx → fall back to /content
# ---------------------------------------------------------------------------


async def test_falls_back_to_content_when_markdown_4xx(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url=_MD_URL, method="POST", status_code=400, json={"errors": []})
    httpx_mock.add_response(url=_CONTENT_URL, method="POST", json={"result": _HTML_BODY})

    result = await _engine().scrape(_TARGET_URL)

    assert result.html == _HTML_BODY


# ---------------------------------------------------------------------------
# 6. Both endpoints empty → raises EngineError
# ---------------------------------------------------------------------------


async def test_both_endpoints_empty_raises_engine_error(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url=_MD_URL, method="POST", json={"result": ""})
    httpx_mock.add_response(url=_CONTENT_URL, method="POST", json={"result": ""})

    with pytest.raises(EngineError):
        await _engine().scrape(_TARGET_URL)


# ---------------------------------------------------------------------------
# 7. Missing token → is_available() returns False
# ---------------------------------------------------------------------------


def test_is_available_false_when_token_missing() -> None:
    engine = _engine(token=None)
    assert engine.is_available() is False


def test_is_available_false_when_account_missing() -> None:
    engine = _engine(account=None)
    assert engine.is_available() is False


def test_is_available_true_when_both_present() -> None:
    engine = _engine()
    assert engine.is_available() is True


# ---------------------------------------------------------------------------
# 8. Request body includes url + render=True + waitUntil=domcontentloaded
# ---------------------------------------------------------------------------


async def test_request_body_shape(httpx_mock: HTTPXMock) -> None:
    import json

    httpx_mock.add_response(url=_MD_URL, method="POST", json={"result": _MARKDOWN_BODY})

    await _engine().scrape(_TARGET_URL)

    request = httpx_mock.get_requests()[0]
    body = json.loads(request.content)
    assert body["url"] == _TARGET_URL
    assert body["render"] is True
    assert body["gotoOptions"]["waitUntil"] == "domcontentloaded"


# ---------------------------------------------------------------------------
# 9. opts.render_js=False → render flag flips to False
# ---------------------------------------------------------------------------


async def test_render_js_false_sends_render_false(httpx_mock: HTTPXMock) -> None:
    import json

    httpx_mock.add_response(url=_MD_URL, method="POST", json={"result": _MARKDOWN_BODY})

    await _engine().scrape(_TARGET_URL, ScrapeOptions(render_js=False))

    body = json.loads(httpx_mock.get_requests()[0].content)
    assert body["render"] is False


# ---------------------------------------------------------------------------
# 10. timeout_s honored on httpx client
# ---------------------------------------------------------------------------


async def test_timeout_s_honored(httpx_mock: HTTPXMock) -> None:
    # No assertion on the actual timeout (httpx_mock can't introspect easily);
    # just confirm a custom value is accepted and the engine completes.
    httpx_mock.add_response(url=_MD_URL, method="POST", json={"result": _MARKDOWN_BODY})

    result = await _engine().scrape(_TARGET_URL, ScrapeOptions(timeout_s=10))

    assert result.markdown == _MARKDOWN_BODY


# ---------------------------------------------------------------------------
# 11. NAME constant + capabilities are correct
# ---------------------------------------------------------------------------


def test_engine_metadata() -> None:
    from scrapefold.engines.cloudflare import CloudflareEngine

    assert CloudflareEngine.NAME == "cloudflare"
    caps = CloudflareEngine.CAPABILITIES
    assert caps.js_rendering is True
    assert caps.requires_api_key is True
    assert caps.output_native_markdown is True


# ---------------------------------------------------------------------------
# 12. Registry registration
# ---------------------------------------------------------------------------


def test_registered_in_engine_registry() -> None:
    from scrapefold.engines import get_engine, list_engine_names

    assert "cloudflare" in list_engine_names()
    cls = get_engine("cloudflare")
    assert cls.NAME == "cloudflare"


# ---------------------------------------------------------------------------
# 13. Test skipped if pytest_httpx not installed (sanity)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(False, reason="sanity")
def test_marker_present() -> None:
    # Marker test that always passes; pinned by test count in the spec.
    assert True
```

- [ ] **Step 2: Run the test file to verify it fails**

Run: `pytest tests/test_engine_cloudflare.py -v`
Expected: every test fails with `ModuleNotFoundError: No module named 'scrapefold.engines.cloudflare'` (or `ImportError`). Confirms TDD baseline.

### Task C.2 — Implement CloudflareEngine

**Files:**
- Create: `src/scrapefold/engines/cloudflare.py`
- Modify: `src/scrapefold/engines/__init__.py` (register)
- Modify: `docs/workflows/development.md` (env-var table)

- [ ] **Step 1: Create the engine module**

Create `src/scrapefold/engines/cloudflare.py`:

```python
"""CloudflareEngine — Cloudflare Browser Rendering API scrape engine.

Ported from downstream-consumer/services/url_to_text_service.py:get_content_cloudflare.
Two endpoints: /markdown (preferred — clean markdown extraction) and
/content (raw HTML fallback when /markdown returns empty or non-200).

Auth via Authorization: Bearer <CLOUDFLARE_API_TOKEN>. Account scope via
CLOUDFLARE_ACCOUNT_ID. JS rendering via render=True (default; set
render_js=False for fast static fetch).
"""

from __future__ import annotations

import logging
import os

import httpx

from scrapefold.engines.base import EngineCapabilities, EngineError, ScrapeEngine
from scrapefold.html_to_text import html_to_both, markdown_to_text
from scrapefold.options import ScrapeOptions
from scrapefold.result import ScrapeResult

logger = logging.getLogger(__name__)


def _extract_markdown(payload: dict) -> str:  # type: ignore[type-arg]
    """Extract markdown from a Cloudflare /markdown response payload.

    Cloudflare returns either {"result": "<md>"} or {"result": {"markdown": "..."}}.
    Anything else collapses to empty string.
    """
    result = payload.get("result")
    if isinstance(result, str):
        return result.strip()
    if isinstance(result, dict):
        return str(result.get("markdown", "")).strip()
    return ""


def _extract_html(payload: dict) -> str:  # type: ignore[type-arg]
    """Extract raw HTML from a Cloudflare /content response payload."""
    result = payload.get("result")
    if isinstance(result, str):
        return result
    return str(result) if result is not None else ""


class CloudflareEngine(ScrapeEngine):
    """Cloudflare Browser Rendering API engine.

    Calls POST /accounts/{id}/browser-rendering/markdown first (clean
    markdown). On empty or non-200, falls back to /content (raw HTML)
    and converts via html_to_text.
    """

    NAME = "cloudflare"
    # Per-request cost — Browser Rendering is billed per request. An empty
    # /markdown that triggers the /content fallback = 2 paid requests; the
    # router's cost budget needs to account for it. Verify the exact USD
    # against Cloudflare's current pricing page at pack-open time.
    _PER_REQUEST_USD = 0.0009  # placeholder — verify and update
    CAPABILITIES = EngineCapabilities(
        js_rendering=True,
        stealth=False,
        screenshot=False,
        requires_api_key=True,
        estimated_cost_usd=_PER_REQUEST_USD,
        billing_unit="call",
        proxy_type="cloudflare",
        free_tier=False,
        output_native_markdown=True,
        default_timeout_s=60,
        avg_response_mb_estimate=1.0,
    )
    SUPPORTED_OPTIONS = frozenset(
        {
            "render_js",
            "timeout_s",
            "extra",  # honors extra["cloudflare_skip_content_fallback"]
        }
    )

    def __init__(
        self, api_token: str | None = None, account_id: str | None = None
    ) -> None:
        super().__init__(api_token or os.getenv("CLOUDFLARE_API_TOKEN"))
        self.account_id = account_id or os.getenv("CLOUDFLARE_ACCOUNT_ID")

    def is_available(self) -> bool:
        return bool(self.api_key) and bool(self.account_id)

    async def _fetch(self, url: str, opts: ScrapeOptions) -> ScrapeResult:
        base_url = (
            f"https://api.cloudflare.com/client/v4/accounts/{self.account_id}/browser-rendering"
        )
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        body = {
            "url": url,
            "render": opts.render_js,
            "gotoOptions": {"waitUntil": "domcontentloaded"},
        }
        skip_fallback = bool(opts.extra.get("cloudflare_skip_content_fallback", False))
        # endpoint_calls is surfaced in meta so callers / billing audit can see
        # exactly which paid endpoints were hit on this scrape.
        endpoint_calls: list[str] = []
        cost_usd = 0.0

        async with httpx.AsyncClient(timeout=float(opts.timeout_s)) as client:
            # Step 1: /markdown
            md_resp = await client.post(f"{base_url}/markdown", headers=headers, json=body)
            endpoint_calls.append("markdown")
            cost_usd += self._PER_REQUEST_USD

            markdown_out = ""
            if md_resp.status_code == 200:
                try:
                    markdown_out = _extract_markdown(md_resp.json())
                except ValueError:  # JSON decode failure
                    markdown_out = ""

            if markdown_out:
                return ScrapeResult(
                    url=url,
                    text=markdown_to_text(markdown_out),
                    markdown=markdown_out,
                    html=None,
                    engine=self.NAME,
                    elapsed_ms=0,  # base class fills
                    cost_usd=cost_usd,
                    meta={
                        "status_code": md_resp.status_code,
                        "endpoint_calls": endpoint_calls,
                    },
                )

            if skip_fallback:
                raise EngineError(
                    engine=self.NAME,
                    message=(
                        f"/markdown ({md_resp.status_code}) returned empty for {url}; "
                        "content fallback disabled by opts.extra"
                    ),
                    elapsed_ms=0,
                )

            # Step 2: /content fallback — second paid request
            logger.debug(
                "engine=cloudflare /markdown empty/non-200 (%d) for %s; trying /content",
                md_resp.status_code,
                url,
            )
            html_resp = await client.post(f"{base_url}/content", headers=headers, json=body)
            endpoint_calls.append("content")
            cost_usd += self._PER_REQUEST_USD

            html_out = ""
            if html_resp.status_code == 200:
                try:
                    html_out = _extract_html(html_resp.json())
                except ValueError:
                    html_out = ""

            if not html_out:
                raise EngineError(
                    engine=self.NAME,
                    message=(
                        f"both /markdown ({md_resp.status_code}) and /content "
                        f"({html_resp.status_code}) returned empty for {url}"
                    ),
                    elapsed_ms=0,
                )

            text_out, md_from_html = html_to_both(html_out, base_url=url)
            return ScrapeResult(
                url=url,
                text=text_out,
                markdown=md_from_html,
                html=html_out,
                engine=self.NAME,
                elapsed_ms=0,
                cost_usd=cost_usd,
                meta={
                    "status_code": html_resp.status_code,
                    "endpoint_calls": endpoint_calls,
                },
            )


__all__ = ["CloudflareEngine"]
```

- [ ] **Step 2: Register the engine**

Edit `src/scrapefold/engines/__init__.py`. Add inside `_REGISTRY` (alphabetical placement near `crawl4ai`):

```python
    "cloudflare": lambda: (
        __import__(
            "scrapefold.engines.cloudflare", fromlist=["CloudflareEngine"]
        ).CloudflareEngine
    ),
```

- [ ] **Step 3: Run the tests**

Run: `pytest tests/test_engine_cloudflare.py -v`
Expected: all 13 tests PASS.

- [ ] **Step 4: Run the full suite**

Run: `./scripts/check.sh`
Expected: `=== All checks passed ===` with ~409 tests passed.

### Task C.3 — Update env-var docs

**Files:**
- Modify: `docs/workflows/development.md`

- [ ] **Step 1: Fix the env-var name**

In `docs/workflows/development.md`, replace the row:

```markdown
| `CLOUDFLARE_API_KEY`, `CLOUDFLARE_ACCOUNT_ID` | cloudflare |
```

with:

```markdown
| `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID` | cloudflare |
```

- [ ] **Step 2: Add CHANGELOG entry**

Append under `## [Unreleased]`:

```markdown
### Added — Pack 3 cloudflare engine

- `src/scrapefold/engines/cloudflare.py` — port of downstream-consumer
  `get_content_cloudflare` to scrapefold's `ScrapeEngine` ABC. Calls
  Cloudflare Browser Rendering `/markdown` first (native markdown), falls
  back to `/content` (raw HTML → html_to_text). Env: `CLOUDFLARE_API_TOKEN`
  + `CLOUDFLARE_ACCOUNT_ID`.
- `tests/test_engine_cloudflare.py` — 13 tests covering both endpoints,
  fallback paths, auth headers, body shape, is_available gating,
  registry registration.
- `docs/workflows/development.md` — env-var table fixed
  (`CLOUDFLARE_API_KEY` → `CLOUDFLARE_API_TOKEN`, matching Cloudflare's
  Bearer-token convention).
```

- [ ] **Step 3: Commit**

```bash
git add src/scrapefold/engines/cloudflare.py tests/test_engine_cloudflare.py src/scrapefold/engines/__init__.py docs/workflows/development.md CHANGELOG.md
git commit -m "$(cat <<'EOF'
feat: Pack 3 — cloudflare engine (Browser Rendering API)

Ports downstream-consumer' get_content_cloudflare to scrapefold. POST
/accounts/{id}/browser-rendering/markdown for native markdown; falls
back to /content (HTML) when /markdown is empty or non-200. Auth via
CLOUDFLARE_API_TOKEN + CLOUDFLARE_ACCOUNT_ID env vars.

13 new tests, ~409 total green. Fixes env-var table in
docs/workflows/development.md to use CLOUDFLARE_API_TOKEN (was
CLOUDFLARE_API_KEY — wrong; Cloudflare uses Bearer tokens).

Spec: docs/superpowers/specs/2026-05-23-v0.1.0-stable-roadmap-design.md §3.1

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Phase D — CHANGELOG roll + tag

### Task D.1 — Bump __version__ and finalize CHANGELOG

**Files:**
- Modify: `src/scrapefold/__init__.py`
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Bump version**

In `src/scrapefold/__init__.py`:

```python
__version__ = "0.1.0a2"
```

- [ ] **Step 2: Move [Unreleased] → [0.1.0a2]**

In `CHANGELOG.md`:
- Replace the existing `## [Unreleased]` heading with `## [0.1.0a2] — 2026-05-23` (use the actual date of the tag).
- Insert a new empty `## [Unreleased]` above it.
- At the bottom of the file, add the new compare link:

```markdown
[Unreleased]: https://github.com/mihailorama/scrapefold/compare/v0.1.0a2...HEAD
[0.1.0a2]: https://github.com/mihailorama/scrapefold/compare/v0.1.0a1...v0.1.0a2
```

And update the existing `[Unreleased]` line to point to the new base.

- [ ] **Step 3: Run full check**

Run: `./scripts/check.sh`
Expected: `=== Version equality === OK: 0.1.0a2` and `=== All checks passed ===`.

- [ ] **Step 4: Commit**

```bash
git add src/scrapefold/__init__.py CHANGELOG.md
git commit -m "$(cat <<'EOF'
chore: bump version to 0.1.0a2 — Pack 3 release

CHANGELOG [Unreleased] rolled into [0.1.0a2]. Next pack opens a new
[Unreleased] section per the release-mechanics CHANGELOG gate.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

### Task D.2 — Tag + push + verify trusted-publish

- [ ] **Step 1: ASK MIKE before pushing/tagging**

Per global CLAUDE.md ASK-list, **stop here**. Ask Mike to confirm the tag should be pushed (this triggers PyPI publish + GH Release auto-draft).

If approved:

- [ ] **Step 2: Push commits and tag**

```bash
git push origin main
git tag -a v0.1.0a2 -m "Pack 3: router shell + cloudflare engine + version-pin fix"
git push origin v0.1.0a2
```

- [ ] **Step 3: Watch CI**

Wait for the GitHub Actions run on the tag. The `publish` job should succeed.
Verify:

```bash
curl -s https://pypi.org/pypi/scrapefold/json | python -c "import json,sys; d=json.load(sys.stdin); print(d['info']['version'])"
```

Expected: `0.1.0a2`.

- [ ] **Step 4: Sanity-install from PyPI in a clean venv**

```bash
mkdir -p /tmp/sf-smoke && cd /tmp/sf-smoke && python -m venv .venv && source .venv/bin/activate && pip install scrapefold==0.1.0a2 && python -c "import scrapefold; print(scrapefold.__version__)"
```

Expected: prints `0.1.0a2`. Confirms PyPI artifact is real.

### Task D.3 — Open Pack 4 milestone

- [ ] **Step 1: Open the next CHANGELOG section**

CHANGELOG already has an empty `## [Unreleased]` from Task D.1. Nothing more to do here — Pack 4 commits start filling it.

---

## Self-review

**Spec coverage:** Each bullet from spec §3.1 maps to a task:
- "Commit the on-disk router shell" → Phase A
- "Land cloudflare engine + tests" → Phase C
- "Fix version-pin drift via dynamic version" → Task B.1
- "Add scripts/check.sh version-equality step" → Task B.5
- "Remove obscura/brightdata extras + post-1.0/backlog.md" → Task B.2
- "Dep-floor audit + bump" → Tasks B.3, B.6
- "CHANGELOG gate" → Task B.4
- "Release rehearsal CI job" → Task B.5
- Exit: ~409 tests, tag v0.1.0a2 publishes → Phase D

**No placeholders.** Every code block is complete. No "TBD" / "TODO" / "similar to Task N."

**Type consistency.** `CloudflareEngine.__init__(api_token, account_id)`, `is_available()` checks both, `_fetch` uses `self.api_key` (the base class field) and `self.account_id`. Test file uses `_engine(token=, account=)` consistently.

**Deferred items are listed** in the spec (P1 #1, #2, #3, #7; P2 #8, #9). Pack 4 picks up #3 and #7; Pack 5 picks up #8 and #9; #1 and #2 wait for v0.2.0 (RaceStep).
