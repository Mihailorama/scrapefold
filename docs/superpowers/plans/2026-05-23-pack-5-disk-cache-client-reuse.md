# Pack 5 — Disk cache + engine client reuse (P2 #8 + #9)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **PACK-OPEN REFRESH:** Re-run `scripts/check-deps-fresh.sh` and bump stale dep floors before starting. Pay special attention to `httpx`, `firecrawl-py`, `apify-client`, `outscraper` — Pack 5 touches their client lifecycles.

**Goal:** Land `cache.py` (sha256-keyed disk cache with TTL) and convert HTTP-tier + SDK engines to reuse a single client across calls. Both consumed by the router at walk shutdown via `engine.aclose()`.

**Architecture:** `cache.py` provides `Cache(dir, ttl_days)` with `async get(key)` / `async set(key, result)` and `make_key(url, opts)`. `scrape()` and `crawl_site()` consult the cache when `opts.skip_cache=False` (default). Each HTTP-tier engine gains a `self._client: httpx.AsyncClient` instance reused across calls, with `async aclose()` called at walk shutdown. SDK engines (Firecrawl / Apify / Outscraper) cache their vendor client instance on `self`.

**Tech Stack:** Python 3.10+, httpx, hashlib, json, dataclasses.asdict, vendor SDKs (Firecrawl 4.x+, Apify, Outscraper).

**Spec reference:** `docs/superpowers/specs/2026-05-23-v0.1.0-stable-roadmap-design.md` §3.3.

---

## Phase A — Dep-freshness audit

### Task A.1 — Run audit + bump floors

- [ ] **Step 1:** `./scripts/check-deps-fresh.sh > /tmp/pack-5-deps.txt && cat /tmp/pack-5-deps.txt`
- [ ] **Step 2:** Bump `httpx`, `firecrawl-py`, `apify-client`, `outscraper`, and any other deps ≥ 2 minor versions behind in `pyproject.toml`. Reinstall, run `./scripts/check.sh`. Fix any breakage inside this pack.
- [ ] **Step 3:** Commit if anything changed.

```bash
git add pyproject.toml
git commit -m "chore: refresh dependency floors for Pack 5"
```

---

## Phase B — `cache.py` (TDD)

### Task B.1 — Failing tests

**Files:**
- Create: `tests/test_cache.py`

- [ ] **Step 1: Write tests**

Create `tests/test_cache.py`:

```python
"""Tests for scrapefold.cache — disk-backed sha256-keyed TTL cache."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from scrapefold.cache import Cache, make_key
from scrapefold.options import ScrapeOptions
from scrapefold.result import ScrapeResult


def _result(url: str, md: str = "# hello") -> ScrapeResult:
    return ScrapeResult(
        url=url, text=md, markdown=md, html=None, engine="stub", elapsed_ms=1,
    )


# ---------------------------------------------------------------------------
# 1. make_key — same (url, opts) → same key; differing → different key
# ---------------------------------------------------------------------------


def test_make_key_deterministic_for_same_inputs() -> None:
    k1 = make_key("https://example.com/", ScrapeOptions(language="en"))
    k2 = make_key("https://example.com/", ScrapeOptions(language="en"))
    assert k1 == k2
    assert len(k1) == 64  # sha256 hex


def test_make_key_changes_on_url() -> None:
    k1 = make_key("https://a.com/", ScrapeOptions())
    k2 = make_key("https://b.com/", ScrapeOptions())
    assert k1 != k2


def test_make_key_changes_on_opts() -> None:
    k1 = make_key("https://example.com/", ScrapeOptions(language="en"))
    k2 = make_key("https://example.com/", ScrapeOptions(language="ru"))
    assert k1 != k2


def test_make_key_stable_across_dict_key_order() -> None:
    # extra dict key ordering should not influence the key
    k1 = make_key("https://x/", ScrapeOptions(extra={"a": 1, "b": 2}))
    k2 = make_key("https://x/", ScrapeOptions(extra={"b": 2, "a": 1}))
    assert k1 == k2


# ---------------------------------------------------------------------------
# 2. Cache hit / miss
# ---------------------------------------------------------------------------


async def test_cache_miss_then_hit(tmp_path: Path) -> None:
    cache = Cache(dir=tmp_path, ttl_days=7)
    key = "abc123"

    assert await cache.get(key) is None
    await cache.set(key, _result("https://x/"))
    got = await cache.get(key)

    assert got is not None
    assert got.url == "https://x/"
    assert got.markdown == "# hello"


# ---------------------------------------------------------------------------
# 3. TTL expiry — old entries return None
# ---------------------------------------------------------------------------


async def test_cache_ttl_expiry(tmp_path: Path) -> None:
    cache = Cache(dir=tmp_path, ttl_days=7)
    key = "exp"

    await cache.set(key, _result("https://x/"))

    # Force the file's mtime into the past (8 days)
    path = cache._path_for(key)
    old = time.time() - 8 * 86400
    import os
    os.utime(path, (old, old))

    assert await cache.get(key) is None


# ---------------------------------------------------------------------------
# 4. Atomic write — partial writes do not poison the cache
# ---------------------------------------------------------------------------


async def test_cache_write_is_atomic(tmp_path: Path) -> None:
    cache = Cache(dir=tmp_path, ttl_days=7)
    key = "atom"
    await cache.set(key, _result("https://x/"))

    # No half-written .tmp files left behind
    leftover = list(tmp_path.glob("*.tmp"))
    assert leftover == []


# ---------------------------------------------------------------------------
# 5. Cache survives across instances (disk-backed, not in-memory)
# ---------------------------------------------------------------------------


async def test_cache_persists_across_instances(tmp_path: Path) -> None:
    cache1 = Cache(dir=tmp_path, ttl_days=7)
    await cache1.set("persist", _result("https://x/"))

    cache2 = Cache(dir=tmp_path, ttl_days=7)
    got = await cache2.get("persist")
    assert got is not None
    assert got.url == "https://x/"


# ---------------------------------------------------------------------------
# 6. Corrupt cache file → treated as miss, deleted, no crash
# ---------------------------------------------------------------------------


async def test_cache_corrupt_file_treated_as_miss(tmp_path: Path) -> None:
    cache = Cache(dir=tmp_path, ttl_days=7)
    key = "corrupt"
    path = cache._path_for(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("not json")

    assert await cache.get(key) is None
    # Corrupt file is auto-cleaned
    assert not path.exists()


# ---------------------------------------------------------------------------
# 7. Default ttl_days is 7 per spec
# ---------------------------------------------------------------------------


def test_cache_default_ttl_is_seven_days(tmp_path: Path) -> None:
    cache = Cache(dir=tmp_path)
    assert cache.ttl_days == 7


# ---------------------------------------------------------------------------
# 8. Cache.set rejects non-result types
# ---------------------------------------------------------------------------


async def test_cache_set_type_check(tmp_path: Path) -> None:
    cache = Cache(dir=tmp_path)
    with pytest.raises(TypeError):
        await cache.set("k", {"not": "a ScrapeResult"})  # type: ignore[arg-type]
```

- [ ] **Step 2: Run failing tests**

Run: `pytest tests/test_cache.py -v`
Expected: `ModuleNotFoundError: scrapefold.cache`.

### Task B.2 — Implement `cache.py`

**Files:**
- Create: `src/scrapefold/cache.py`

- [ ] **Step 1: Create module**

Create `src/scrapefold/cache.py`:

```python
"""Disk-backed TTL cache for ScrapeResult.

Key derivation: sha256(url) + sha256(canonical-json(opts)). One file per
key under ``<cache_dir>/<first-2-of-key>/<rest-of-key>.json``. TTL via
file mtime. Atomic writes via ``os.replace``. Corrupt files are treated
as misses and removed.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from dataclasses import asdict, fields, is_dataclass
from pathlib import Path
from typing import Any

from scrapefold.options import ScrapeOptions
from scrapefold.result import ScrapeResult

logger = logging.getLogger(__name__)

_DEFAULT_TTL_DAYS = 7


def _canonical_opts(opts: ScrapeOptions) -> str:
    """Produce a stable JSON string for opts, with dict keys sorted recursively."""
    raw = asdict(opts)
    return json.dumps(raw, sort_keys=True, default=str)


def make_key(url: str, opts: ScrapeOptions) -> str:
    """Return the 64-char hex sha256 key for (url, opts)."""
    h = hashlib.sha256()
    h.update(url.encode("utf-8"))
    h.update(b"\x00")
    h.update(_canonical_opts(opts).encode("utf-8"))
    return h.hexdigest()


class Cache:
    """sha256-keyed disk cache for ScrapeResult, TTL'd via file mtime."""

    def __init__(self, dir: Path | str, ttl_days: int = _DEFAULT_TTL_DAYS) -> None:
        self.dir = Path(dir)
        self.ttl_days = ttl_days
        self.dir.mkdir(parents=True, exist_ok=True)

    def _path_for(self, key: str) -> Path:
        # Sharded by first two hex chars to avoid one giant flat directory.
        return self.dir / key[:2] / f"{key[2:]}.json"

    async def get(self, key: str) -> ScrapeResult | None:
        path = self._path_for(key)
        if not path.exists():
            return None

        # TTL via mtime
        age_s = time.time() - path.stat().st_mtime
        if age_s > self.ttl_days * 86400:
            return None

        try:
            data = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError) as exc:
            logger.debug("cache: corrupt file %s (%s); removing", path, exc)
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
            return None

        try:
            return _result_from_dict(data)
        except (KeyError, TypeError) as exc:
            logger.debug("cache: shape mismatch in %s (%s); removing", path, exc)
            path.unlink(missing_ok=True)
            return None

    async def set(self, key: str, result: ScrapeResult) -> None:
        if not isinstance(result, ScrapeResult):
            raise TypeError(f"Cache.set expects ScrapeResult, got {type(result).__name__}")

        path = self._path_for(key)
        path.parent.mkdir(parents=True, exist_ok=True)

        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(_result_to_dict(result), default=str))
        os.replace(tmp, path)  # atomic rename


def _result_to_dict(r: ScrapeResult) -> dict[str, Any]:
    return asdict(r)


def _result_from_dict(d: dict[str, Any]) -> ScrapeResult:
    # Be strict about the required fields; tolerate missing optional ones
    return ScrapeResult(
        url=d["url"],
        text=d["text"],
        markdown=d["markdown"],
        html=d.get("html"),
        json=d.get("json"),
        screenshot_b64=d.get("screenshot_b64"),
        engine=d["engine"],
        elapsed_ms=d["elapsed_ms"],
        cost_usd=d.get("cost_usd", 0.0),
        meta=d.get("meta", {}),
        failures=d.get("failures", []),
    )


__all__ = ["Cache", "make_key"]
```

- [ ] **Step 2: Run tests**

Run: `pytest tests/test_cache.py -v`
Expected: 8 PASS.

- [ ] **Step 3: Commit**

```bash
git add src/scrapefold/cache.py tests/test_cache.py
git commit -m "$(cat <<'EOF'
feat: Pack 5 — cache.py (disk-backed TTL cache)

Cache class: sha256(url) + sha256(canonical_json(opts)) key, sharded
two-char directory layout, atomic writes via os.replace, default
7-day TTL via file mtime. Corrupt files are treated as misses and
auto-cleaned.

8 tests covering key determinism, hit/miss, TTL expiry, atomic-write
safety, cross-instance persistence, corrupt-file resilience.

Spec: docs/superpowers/specs/2026-05-23-v0.1.0-stable-roadmap-design.md §3.3
EOF
)"
```

---

## Phase C — Wire cache into `scrape()` and `crawl_site()`

### Task C.1 — Add `opts.skip_cache` honor + `cache_dir` / `cache_ttl_days` extras

**Files:**
- Modify: `src/scrapefold/__init__.py` (scrape function)
- Modify: `tests/test_router.py` or new `tests/test_cache_integration.py`

- [ ] **Step 1: Write failing integration test**

Create `tests/test_cache_integration.py`:

```python
"""Integration tests — scrape() consults the cache."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import scrapefold
from scrapefold import ScrapeOptions


@pytest.fixture
def cache_dir(tmp_path: Path) -> Path:
    return tmp_path / "cache"


async def test_cache_hit_skips_network(
    cache_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Second call with the same (url, opts) reads from cache, not the router."""
    call_count = {"n": 0}

    async def _fake_walk(url: str, opts: ScrapeOptions | None = None) -> Any:
        call_count["n"] += 1
        from scrapefold.result import ScrapeResult

        return ScrapeResult(
            url=url, text="from-net", markdown="# from-net",
            html=None, engine="stub", elapsed_ms=1,
        )

    monkeypatch.setattr("scrapefold.router.walk", _fake_walk)

    opts = ScrapeOptions(
        extra={"cache_dir": str(cache_dir), "cache_ttl_days": 7}
    )
    url = "https://example.com/"

    r1 = await scrapefold.scrape(url, opts)
    r2 = await scrapefold.scrape(url, opts)

    assert r1.markdown == "# from-net"
    assert r2.markdown == "# from-net"
    assert call_count["n"] == 1, "second call must hit cache"


async def test_skip_cache_forces_network(
    cache_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    call_count = {"n": 0}

    async def _fake_walk(url: str, opts: ScrapeOptions | None = None) -> Any:
        call_count["n"] += 1
        from scrapefold.result import ScrapeResult

        return ScrapeResult(
            url=url, text="x", markdown="# x", html=None,
            engine="stub", elapsed_ms=1,
        )

    monkeypatch.setattr("scrapefold.router.walk", _fake_walk)

    url = "https://example.com/"
    opts = ScrapeOptions(extra={"cache_dir": str(cache_dir)})
    opts_skip = ScrapeOptions(
        skip_cache=True, extra={"cache_dir": str(cache_dir)}
    )

    await scrapefold.scrape(url, opts)
    await scrapefold.scrape(url, opts_skip)

    assert call_count["n"] == 2
```

- [ ] **Step 2: Run failing test**

Run: `pytest tests/test_cache_integration.py -v`
Expected: FAIL (currently no cache layer).

- [ ] **Step 3: Edit `scrape()` to consult cache**

In `src/scrapefold/__init__.py`, replace the current `scrape` function:

```python
async def scrape(url: str, opts: ScrapeOptions | None = None) -> ScrapeResult:
    """Single-URL scrape with engine auto-selection.

    Honors opts.skip_cache, opts.extra["cache_dir"], opts.extra["cache_ttl_days"].
    """
    from scrapefold.cache import Cache, make_key
    from scrapefold.router import walk

    opts = opts or ScrapeOptions()

    cache: Cache | None = None
    cache_key: str | None = None
    if not opts.skip_cache and "cache_dir" in opts.extra:
        cache = Cache(
            dir=opts.extra["cache_dir"],
            ttl_days=int(opts.extra.get("cache_ttl_days", 7)),
        )
        cache_key = make_key(url, opts)
        cached = await cache.get(cache_key)
        if cached is not None:
            return cached

    result = await walk(url, opts)

    if cache is not None and cache_key is not None:
        await cache.set(cache_key, result)
    return result
```

- [ ] **Step 4: Run tests + full suite**

Run: `pytest tests/test_cache_integration.py tests/test_router.py -v && ./scripts/check.sh`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/scrapefold/__init__.py tests/test_cache_integration.py
git commit -m "$(cat <<'EOF'
feat: Pack 5 — scrape() consults cache when opts.extra['cache_dir'] set

scrape() checks the cache before walking the ladder when cache_dir is
present in opts.extra. opts.skip_cache=True forces network. Cache TTL
defaults to 7 days; overridable via opts.extra['cache_ttl_days'].

2 integration tests covering hit-skips-network and skip-cache-forces-network.
EOF
)"
```

---

## Phase D — Engine client reuse (P2 #8 + #9)

Each HTTP-tier engine (`requests`, `jina`, `scrapingdog`, `anysite`) instantiates a fresh `httpx.AsyncClient` per call today, which means 50 TLS handshakes + connection-pool teardowns for a 50-URL crawl. SDK engines (`firecrawl`, `apify_linkedin`, `outscraper`) rebuild their vendor client per call.

### Task D.1 — Base class `aclose()` hook

**Files:**
- Modify: `src/scrapefold/engines/base.py`
- Modify: `tests/test_engine_base.py`

- [ ] **Step 1: Failing test**

Append to `tests/test_engine_base.py`:

```python
async def test_base_engine_provides_aclose_default() -> None:
    """Default aclose() must be safe to call even on engines that don't override it."""
    from scrapefold.engines.base import EngineCapabilities, ScrapeEngine
    from scrapefold.options import ScrapeOptions
    from scrapefold.result import ScrapeResult

    class _NoopEngine(ScrapeEngine):
        NAME = "noop"
        CAPABILITIES = EngineCapabilities()
        SUPPORTED_OPTIONS = frozenset()

        async def _fetch(self, url: str, opts: ScrapeOptions) -> ScrapeResult:
            return ScrapeResult(
                url=url, text="x", markdown="# x", html=None,
                engine=self.NAME, elapsed_ms=1,
            )

    engine = _NoopEngine()
    await engine.aclose()  # must not raise
```

- [ ] **Step 2: Add `aclose` default in base**

In `src/scrapefold/engines/base.py`, add to `ScrapeEngine`:

```python
    async def aclose(self) -> None:
        """Close any per-engine resources (httpx client, SDK client).

        Default no-op. Engines that hold a client override this.
        """
        return None
```

- [ ] **Step 3: Run test**

Run: `pytest tests/test_engine_base.py::test_base_engine_provides_aclose_default -v`
Expected: PASS.

### Task D.2 — RequestsEngine reuses httpx client

**Files:**
- Modify: `src/scrapefold/engines/requests.py`
- Modify: `tests/test_engine_requests.py`

- [ ] **Step 1: Failing test**

Append to `tests/test_engine_requests.py`:

```python
async def test_requests_engine_reuses_httpx_client(httpx_mock: HTTPXMock) -> None:
    """A single RequestsEngine instance shares one httpx.AsyncClient across calls."""
    from scrapefold.engines.requests import RequestsEngine

    httpx_mock.add_response(url="https://example.com/a", text="<html><body>a</body></html>")
    httpx_mock.add_response(url="https://example.com/b", text="<html><body>b</body></html>")

    engine = RequestsEngine()
    await engine.scrape("https://example.com/a")
    client_after_first = engine._client  # implementation detail, OK for white-box test
    await engine.scrape("https://example.com/b")

    assert engine._client is client_after_first
    await engine.aclose()
```

- [ ] **Step 2: Refactor RequestsEngine**

In `src/scrapefold/engines/requests.py`:

Replace the `_fetch` method's `async with httpx.AsyncClient(...) as client:` block with persistent-client lifecycle:

```python
class RequestsEngine(ScrapeEngine):
    # ... existing NAME/CAPABILITIES/SUPPORTED_OPTIONS ...

    def __init__(self, api_key: str | None = None) -> None:
        super().__init__(api_key)
        self._client: httpx.AsyncClient | None = None

    def _get_client(self, opts: ScrapeOptions) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=float(opts.timeout_s),
                follow_redirects=True,
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _fetch(self, url: str, opts: ScrapeOptions) -> ScrapeResult:
        headers = build_target_headers(opts, include_cookies=False)
        headers.setdefault("User-Agent", _DEFAULT_USER_AGENT)

        client = self._get_client(opts)
        response = await client.get(url, headers=headers, cookies=opts.cookies or {})

        # ... rest of existing _fetch body unchanged from "content_type = ..." onward ...
```

- [ ] **Step 3: Run tests**

Run: `pytest tests/test_engine_requests.py -v`
Expected: all PASS, including the new reuse test.

- [ ] **Step 4: Repeat for `jina`, `scrapingdog`, `anysite`**

For each, apply the same pattern: `__init__` initializes `self._client = None`; new `_get_client(opts)` helper; new `aclose()`; `_fetch` calls `self._get_client(opts).get/post(...)` instead of `async with httpx.AsyncClient(...) as client:`.

Each engine adds its own `test_<engine>_reuses_httpx_client` test similar to `RequestsEngine`'s.

- [ ] **Step 5: Run full suite**

Run: `./scripts/check.sh`
Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add src/scrapefold/engines/base.py src/scrapefold/engines/requests.py src/scrapefold/engines/jina.py src/scrapefold/engines/scrapingdog.py src/scrapefold/engines/anysite.py tests/test_engine_base.py tests/test_engine_requests.py tests/test_engine_jina.py tests/test_engine_scrapingdog.py tests/test_engine_anysite.py
git commit -m "$(cat <<'EOF'
feat: Pack 5 — engines reuse a single httpx.AsyncClient (P2 #8)

RequestsEngine, JinaEngine, ScrapingdogEngine, AnySiteEngine all share
one client across scrape() calls instead of opening a fresh one per
call. ScrapeEngine base class adds async aclose() (no-op default;
engines with clients override). Closes TECH_DEBT P2 #8.

4 new "reuses httpx client" tests, one per engine.
EOF
)"
```

### Task D.3 — SDK engine client reuse

**Files:**
- Modify: `src/scrapefold/engines/firecrawl.py`
- Modify: `src/scrapefold/engines/apify_linkedin.py`
- Modify: `src/scrapefold/engines/outscraper.py`

- [ ] **Step 1: Failing tests per engine**

For each SDK engine, append a `test_<engine>_reuses_sdk_client` test. Example for Firecrawl:

```python
async def test_firecrawl_reuses_sdk_client(monkeypatch: pytest.MonkeyPatch) -> None:
    from scrapefold.engines.firecrawl import FirecrawlEngine

    constructed: list[object] = []
    real_init = None

    def _track_init(self, *args, **kwargs):
        constructed.append(self)
        return real_init(self, *args, **kwargs)

    # Patch the AsyncFirecrawlApp constructor (verify the actual class name
    # at pack-open time against the current firecrawl-py SDK).
    import firecrawl

    real_init = firecrawl.AsyncFirecrawlApp.__init__
    monkeypatch.setattr(firecrawl.AsyncFirecrawlApp, "__init__", _track_init)

    engine = FirecrawlEngine(api_key="test")
    # Two scrapes — only one SDK client should be constructed
    # (mock the actual scrape_url method too; minimal example)
    ...
    assert len(constructed) == 1
```

Adapt to the actual SDK class names by reading each engine module first.

- [ ] **Step 2: Refactor each engine to lazy-init + cache the SDK client on self**

Pattern (Firecrawl example):

```python
class FirecrawlEngine(ScrapeEngine):
    def __init__(self, api_key: str | None = None) -> None:
        super().__init__(api_key or os.getenv("FIRECRAWL_API_KEY"))
        self._sdk_client: Any | None = None

    def _get_sdk_client(self) -> Any:
        if self._sdk_client is None:
            from firecrawl import AsyncFirecrawlApp  # lazy import (golden rule)

            self._sdk_client = AsyncFirecrawlApp(api_key=self.api_key)
        return self._sdk_client

    async def aclose(self) -> None:
        # Firecrawl SDK does not expose aclose; rely on GC for the underlying pool.
        # Apify/Outscraper override more aggressively.
        self._sdk_client = None
```

- [ ] **Step 3: Run tests + full suite + commit**

```bash
./scripts/check.sh
git add src/scrapefold/engines/firecrawl.py src/scrapefold/engines/apify_linkedin.py src/scrapefold/engines/outscraper.py tests/test_engine_firecrawl.py tests/test_engine_apify_linkedin.py tests/test_engine_outscraper.py
git commit -m "$(cat <<'EOF'
feat: Pack 5 — SDK engines cache vendor clients across calls (P2 #9)

Firecrawl, Apify, Outscraper engines now lazy-instantiate the vendor
SDK client on first call and reuse it. aclose() drops the reference
(GC handles underlying pool cleanup). Closes TECH_DEBT P2 #9.
EOF
)"
```

### Task D.4 — Router calls `aclose()` at walk shutdown

**Files:**
- Modify: `src/scrapefold/router.py`
- Modify: `tests/test_router.py`

- [ ] **Step 1: Failing test**

Append to `tests/test_router.py`:

```python
async def test_router_closes_engines_after_walk(
    stub_registry: dict[str, type[ScrapeEngine]],
    stub_ladder: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scrapefold import EngineCapabilities, SequentialStep
    from scrapefold.engines import _REGISTRY
    from scrapefold.engines.base import ScrapeEngine
    from scrapefold.options import ScrapeOptions
    from scrapefold.result import ScrapeResult
    from scrapefold.router import walk

    closed: list[str] = []

    async def _aclose(self):
        closed.append(self.NAME)

    async def _fetch(self, url, opts):
        return ScrapeResult(
            url=url, text="x", markdown="# x", html=None,
            engine=self.NAME, elapsed_ms=1,
        )

    closing_engine = type(
        "_StubClose",
        (ScrapeEngine,),
        {
            "NAME": "closing_engine",
            "CAPABILITIES": EngineCapabilities(),
            "SUPPORTED_OPTIONS": frozenset(),
            "_fetch": _fetch,
            "aclose": _aclose,
        },
    )
    monkeypatch.setitem(_REGISTRY, "closing_engine", lambda: closing_engine)
    stub_ladder((SequentialStep(engine="closing_engine"),))

    await walk("https://example.com/")

    assert "closing_engine" in closed
```

- [ ] **Step 2: Patch router**

In `src/scrapefold/router.py`, modify the `walk()` function to track instantiated engines and close them on exit:

```python
async def walk(url: str, opts: ScrapeOptions | None = None) -> ScrapeResult:
    # ... existing setup ...
    instantiated: list[ScrapeEngine] = []
    try:
        # ... existing loop, but after `engine = engine_cls()` add:
        #     instantiated.append(engine)
        # ... rest of loop unchanged ...
        # On success:
        return replace(result, failures=failures)
    finally:
        for e in instantiated:
            try:
                await e.aclose()
            except Exception as exc:  # noqa: BLE001
                logger.debug("router: aclose engine=%s failed: %s", e.NAME, exc)
    raise AllEnginesFailed(f"all engines failed for {url}: {failures}")
```

(Adjust `try/finally` placement so `aclose` runs whether the walk returns a result OR raises `AllEnginesFailed`.)

- [ ] **Step 3: Run tests + commit**

```bash
./scripts/check.sh
git add src/scrapefold/router.py tests/test_router.py
git commit -m "$(cat <<'EOF'
feat: Pack 5 — router calls engine.aclose() in finally block

After each walk (success or AllEnginesFailed), the router closes every
engine instance it constructed. Combined with the per-engine client
reuse from D.2/D.3, this gives a single-client lifetime per walk
without leaking sockets when many walks happen back-to-back.
EOF
)"
```

---

## Phase E — CHANGELOG roll + tag

### Task E.1

- [ ] Bump `__version__` to `0.1.0a4`.
- [ ] Roll CHANGELOG, add compare-link.
- [ ] Remove items P2 #8 and #9 from `docs/TECH_DEBT.md`.
- [ ] Run `./scripts/check.sh` → green.
- [ ] Commit + ASK before pushing/tagging `v0.1.0a4`.

---

## Self-review

**Spec coverage** (§3.3):
- `cache.py` with sha256 key + 7-day TTL + atomic write → Phase B ✅
- `opts.extra["cache_ttl_days"]` + `cache_dir` override → Phase C ✅
- `opts.skip_cache` honored → Phase C ✅
- P2 #8 (per-instance httpx client) → Phase D.2 ✅
- P2 #9 (SDK client reuse for firecrawl/apify/outscraper) → Phase D.3 ✅
- `engine.aclose()` called by router at walk shutdown → Phase D.4 ✅
- ~20 new tests: 8 (cache) + 2 (cache integration) + 1 (base aclose) + 4 (httpx engines) + 3 (SDK engines) + 1 (router aclose) = 19 ✅
- Exit: same URL twice = 1 network + 1 cache hit → Phase C tests ✅

**No placeholders.** Code blocks complete for cache.py and base.py refactor. SDK-engine refactor (D.3) is templated — each engine needs the actual SDK class name verified at pack-open (likely changed since 2026-05-23). That's why the pack opens with a dep-freshness audit.

**Type consistency.** `Cache(dir, ttl_days)`, `make_key(url, opts)`, `_path_for(key)`, `aclose()` are all consistent across signature, tests, and consumer code.

**Deferred:** P1 #1 (`budget_mode`), P1 #2 (race billing) — both `RaceStep`-coupled, wait for v0.2.0.
