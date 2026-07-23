# Contributing to scrapefold

## Add a new engine — 5-step checklist

1. **Create the module** at `src/scrapefold/engines/<name>.py`:

   ```python
   from __future__ import annotations

   from scrapefold.engines.base import EngineCapabilities, ScrapeEngine
   from scrapefold.options import ScrapeOptions
   from scrapefold.result import ScrapeResult


   class FooEngine(ScrapeEngine):
       NAME = "foo"
       CAPABILITIES = EngineCapabilities(
           js_rendering=True,
           stealth=True,
           cost_per_1k=1.0,
           requires_api_key=True,
       )
       SUPPORTED_OPTIONS = frozenset({
           "language", "country", "render_js", "wait_ms",
           "stealth", "user_agent", "custom_headers", "timeout_s",
       })

       async def _fetch(self, url: str, opts: ScrapeOptions) -> ScrapeResult:
           # 1. Translate opts -> native vendor params via local _adapt(opts)
           # 2. Call vendor (httpx.AsyncClient or vendor SDK, lazy-imported)
           # 3. Convert HTML→markdown if needed via scrapefold.html_to_text
           # 4. Return ScrapeResult with text+markdown+html (+json if structured)
           ...
   ```

2. **Document the native parameter surface** in the module docstring as a table — every vendor parameter you support, what unified option it maps from, defaults. This is the input to the global adapter matrix.

3. **Register it lazily** in `src/scrapefold/engines/__init__.py`:

   ```python
   def _load_foo():
       from scrapefold.engines.foo import FooEngine
       return FooEngine

   register("foo", _load_foo)
   ```

4. **Add tests** under `tests/test_engines/test_foo.py` covering:
   - Success path (vendor mocked via `pytest-httpx`)
   - Vendor error (4xx/5xx → `EngineError`)
   - Timeout (engine respects `opts.timeout_s`)
   - Missing API key (`is_available() is False`)
   - One unified-opt → native-param adapter assertion

5. **Add an extra** in `pyproject.toml` if the engine needs a non-stdlib dependency:

   ```toml
   [project.optional-dependencies]
   foo = ["foo-sdk>=1.0"]
   ```

   And update `all = [...]`.

## Style

- `ruff check` and `ruff format` must be clean.
- `mypy src` must be clean.
- Async everywhere — `async def`, `await`, `httpx.AsyncClient`.
- No top-level imports of optional vendor SDKs. Import inside the class or function.
- No `print()` — use `logging.getLogger(__name__)`.

## Commits

Conventional-ish prefixes: `feat:`, `fix:`, `chore:`, `docs:`, `test:`, `refactor:`, `release:`.

## Release

1. Bump `__version__` in `src/scrapefold/__init__.py` (the single source of
   truth — `pyproject.toml` reads it via hatch's dynamic version).
2. Move the `## [Unreleased]` entries in `CHANGELOG.md` under a new
   `## [X.Y.Z] - YYYY-MM-DD` heading and update the compare links at the
   bottom.
3. Run `./scripts/check.sh` (its version-equality step needs a fresh
   `pip install -e .` so package metadata matches).
4. Merge to `main`, then tag the release commit `vX.Y.Z` and push the tag —
   the `v*` tag build in `ci.yml` publishes to PyPI via trusted publishing.
   - If you cannot push tags directly, dispatch the **Tag release** workflow
     (`.github/workflows/tag-release.yml`) with `tag: vX.Y.Z`; it verifies the
     tag matches `__version__` and pushes it. A tag pushed by that workflow's
     `GITHUB_TOKEN` does not trigger CI automatically — follow up with
     `gh workflow run ci.yml --ref vX.Y.Z`.
5. Verify: PyPI shows the new version and the GitHub Pages landing
   (`docs/`, served at scrapefold.com) redeployed from `main`.
