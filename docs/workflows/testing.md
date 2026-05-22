---
purpose: "How to run tests — markers, layers, fixtures."
updated: "2026-05-22"
related:
  - development.md
  - ../conventions/golden-rules.md
---

# Testing

## Quick start

```bash
pytest -m "not paid and not network"     # default — fast, offline, deterministic
```

This is what CI runs on every PR and what `scripts/check.sh` invokes.

## Test markers

| Marker | Meaning | Default? |
|---|---|---|
| (none) | Unit / fixture-server integration | ✅ run |
| `network` | Hits live internet (e.g. `https://example.com`) | ❌ |
| `paid` | Requires a real vendor API key | ❌ |
| `slow` | > 30 s wall clock | ❌ |
| `integration` | Requires an engine SDK installed | run if extras present |

Run a single layer:

```bash
pytest -m network                    # live-internet smoke
pytest -m paid                       # real vendor calls (uses real keys)
pytest -m "not paid"                 # everything except paid
```

## Layered test plan

1. **Unit** — `pytest-httpx` mocks, no network. Every engine: success, vendor-error, timeout, malformed response, missing key, unsupported-opt-dropped.
2. **Fixture-server integration** — local Starlette/FastAPI app serves a tiny site with sitemap + linked pages. Crawler E2E, router fallback chain, parallel LLM-merge with a stub LLM callable.
3. **Network** — one canonical URL per engine; only runs with `-m network`.
4. **Paid** — only via manual `workflow_dispatch` in CI with secrets.

## Layout

```
tests/
├── conftest.py                 shared fixtures
├── test_smoke.py               public-API imports + dataclass shape
├── test_engine_base.py         ScrapeEngine ABC contract (opt-stripping, errors)
├── test_options.py             (S2+) adapter matrix snapshot
├── test_engines/               one file per engine
│   ├── test_requests.py        (S2)
│   ├── test_firecrawl.py       (S4)
│   └── ...
├── test_router.py              (S7) auto-select + fallback
├── test_parallel.py            (S7) LLM-judge with stub callable
├── test_html_to_text.py        (S2)
├── test_crawler/               (S8) sitemap, BFS, filters, stitcher
├── test_cache.py               (S8)
├── test_cli.py                 (S9)
└── test_mcp_server.py          (S10)
```

## Writing a new engine test

Each engine PR adds `tests/test_engines/test_<name>.py` covering:

1. **Success path** — `pytest-httpx` mock returns a canned response, assert `ScrapeResult` fields.
2. **Vendor error** — mock returns 4xx/5xx, assert `EngineError` is raised.
3. **Timeout** — assert engine respects `opts.timeout_s`.
4. **Missing key** — assert `is_available() is False` and `_fetch` raises a clear error.
5. **Option adapter** — assert one unified opt is translated to the correct native param.

See `tests/test_engine_base.py` for a worked example using `_StubEngine`.
