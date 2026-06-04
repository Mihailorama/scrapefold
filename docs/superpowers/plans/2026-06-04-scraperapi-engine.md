# ScraperAPI Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `scraperapi` scrape engine (standard scrape + AI-Parser structured `json`) plus README/backlog/competitive-intel docs.

**Architecture:** Pure-`httpx` REST engine mirroring `engines/scrapingdog.py` — no vendor SDK. `_adapt()` maps unified `ScrapeOptions` to ScraperAPI query params; `_fetch()` branches on response content-type to fill the right `ScrapeResult` slots (native markdown, AI-Parser JSON, or HTML→both). Registered lazily; selectable via `opts.engines`, not wired into the default ladder.

**Tech Stack:** Python 3.11+, httpx, pytest + pytest-httpx (offline mocks), pytest-asyncio.

**Spec:** `docs/superpowers/specs/2026-06-04-scraperapi-engine-design.md`

---

## File structure

- Create `src/scrapefold/engines/scraperapi.py` — the engine (one responsibility: ScraperAPI REST).
- Create `tests/test_engine_scraperapi.py` — offline test suite.
- Modify `src/scrapefold/engines/__init__.py` — lazy registry entry.
- Modify `pyproject.toml` — `scraperapi = []` extra + `all` aggregate.
- Modify `README.md` — three table rows.
- Modify `docs/post-1.0/backlog.md` — AI Parser lifecycle follow-up.
- Create `docs/competitive-intel.md` — competitor change log.

Reference helpers (already exist, do not modify):
- `ScrapeResult(url, text, markdown, html, engine, elapsed_ms, cost_usd=0.0, json=None, screenshot_b64=None, meta={}, failures=[])` — `scrapefold.result`.
- `html_to_both(html, *, base_url=None) -> tuple[str, str]` — `scrapefold.html_to_text`.
- `build_target_headers(opts) -> dict[str,str]` — maps language→Accept-Language, user_agent→User-Agent, cookies→Cookie, custom_headers last-wins. `scrapefold.options`.
- `strip_extra_prefix(extra, prefix) -> dict` — `scrapefold.options`.

---

## Task 1: Engine module + core 200-HTML path (TDD)

**Files:**
- Test: `tests/test_engine_scraperapi.py`
- Create: `src/scrapefold/engines/scraperapi.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_engine_scraperapi.py`:

```python
"""Tests for ScraperApiEngine. Offline — pytest-httpx, no real network."""

from __future__ import annotations

import pytest
from pytest_httpx import HTTPXMock

from scrapefold.engines.base import EngineError
from scrapefold.engines.scraperapi import ScraperApiEngine, _adapt
from scrapefold.options import ScrapeOptions

_ENDPOINT = "https://api.scraperapi.com/"
_API_KEY = "test-api-key-123"
_HTML = (
    "<html><head><title>ScraperAPI Test</title></head>"
    "<body><h1>Hello</h1><p>World</p></body></html>"
)


def _engine(api_key: str = _API_KEY) -> ScraperApiEngine:
    return ScraperApiEngine(api_key=api_key)


async def test_html_200_populates_all_slots(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        status_code=200,
        headers={"content-type": "text/html; charset=utf-8"},
        text=_HTML,
    )
    result = await _engine().scrape("https://example.com/")

    assert result.html == _HTML
    assert "Hello" in result.text
    assert "Hello" in result.markdown
    assert result.cost_usd == 0.00049
    assert result.engine == "scraperapi"
    assert result.meta["status_code"] == 200
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_engine_scraperapi.py::test_html_200_populates_all_slots -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'scrapefold.engines.scraperapi'`

- [ ] **Step 3: Write the full engine implementation**

Create `src/scrapefold/engines/scraperapi.py`:

```python
"""ScraperApiEngine — paid REST scraping via api.scraperapi.com.

Pure REST, no SDK. Supports JS rendering, country routing, premium proxies,
native markdown output, and AI-Parser structured extraction (``json`` slot).

Cost model: 1 credit per base request (~$0.00049 on the entry plan). JS
``render`` costs ~10 credits and ultra-premium more; ``estimated_cost_usd``
reflects the base credit and is a floor, not a ceiling.

AI Parser:
- ``extra["scraperapi_autoparse"] = "true"`` enables ScraperAPI's built-in
  domain parsers (Amazon, Google, …) → JSON response → ``json`` slot.
- ``extra["scraperapi_output_format"] = "json"`` (or a custom-parser param via
  the same ``scraperapi_`` passthrough) routes a custom AI Parser. Creating /
  editing parsers (30k credits each) is out of scope — see backlog.
"""

from __future__ import annotations

import json as _json
import logging
import os

import httpx

from scrapefold.engines.base import EngineCapabilities, ScrapeEngine
from scrapefold.html_to_text import html_to_both
from scrapefold.options import ScrapeOptions, build_target_headers, strip_extra_prefix
from scrapefold.result import ScrapeResult

logger = logging.getLogger(__name__)

_ENDPOINT = "https://api.scraperapi.com/"
_COST_USD = 0.00049


def _adapt(opts: ScrapeOptions, api_key: str, url: str) -> dict[str, str]:
    """Map unified ScrapeOptions to ScraperAPI query params.

    Booleans serialize as ``"true"`` / ``"false"`` strings. ``language``,
    ``user_agent``, ``cookies`` and ``custom_headers`` are request *headers*,
    handled by ``build_target_headers`` — not query params.
    """
    params: dict[str, str] = {
        "api_key": api_key,
        "url": url,
        "render": "true" if opts.render_js else "false",
    }
    if opts.country:
        params["country_code"] = opts.country
    if opts.premium_proxy:
        params["premium"] = "true"
    if opts.wait_for_selector:
        params["wait_for_selector"] = opts.wait_for_selector
    if opts.output_format in ("markdown", "auto"):
        params["output_format"] = "markdown"
    for key, value in strip_extra_prefix(opts.extra, "scraperapi_").items():
        params[key] = str(value)
    return params


def _is_json_response(response: httpx.Response, params: dict[str, str]) -> bool:
    """True when ScraperAPI returned structured JSON (AI Parser / autoparse)."""
    ctype = response.headers.get("content-type", "")
    if "application/json" in ctype:
        return True
    return params.get("autoparse") == "true" or params.get("output_format") == "json"


class ScraperApiEngine(ScrapeEngine):
    """Scraping engine backed by the ScraperAPI REST API.

    API key from the constructor argument or the ``SCRAPERAPI_API_KEY``
    environment variable. ``is_available()`` returns ``False`` when neither
    is set.
    """

    NAME = "scraperapi"
    CAPABILITIES = EngineCapabilities(
        js_rendering=True,
        stealth=False,
        screenshot=False,
        estimated_cost_usd=_COST_USD,
        billing_unit="call",
        requires_api_key=True,
        proxy_type="datacenter",
        output_native_markdown=True,
        default_timeout_s=60,
    )
    SUPPORTED_OPTIONS = frozenset(
        {
            "language",
            "country",
            "render_js",
            "wait_for_selector",
            "premium_proxy",
            "user_agent",
            "custom_headers",
            "cookies",
            "output_format",
            "timeout_s",
            "extra",
        }
    )

    def __init__(self, api_key: str | None = None) -> None:
        super().__init__(api_key or os.getenv("SCRAPERAPI_API_KEY"))

    async def _fetch(self, url: str, opts: ScrapeOptions) -> ScrapeResult:
        params = _adapt(opts, self.api_key or "", url)
        headers = build_target_headers(opts)

        async with httpx.AsyncClient(timeout=float(opts.timeout_s)) as client:
            response = await client.get(_ENDPOINT, params=params, headers=headers)

        raw = response.text
        json_data: object | None = None
        html: str | None = None

        if _is_json_response(response, params):
            try:
                json_data = response.json()
            except ValueError:
                json_data = None
            pretty = _json.dumps(json_data, indent=2, ensure_ascii=False) if json_data is not None else raw
            text, markdown = pretty, pretty
        elif params.get("output_format") == "markdown":
            # Native markdown — do not re-derive from HTML.
            markdown = raw
            text = raw
        else:
            text, markdown = html_to_both(raw, base_url=url)
            html = raw

        meta: dict[str, object] = {"status_code": response.status_code}
        target_status = response.headers.get("sa-statuscode")
        if target_status is not None:
            meta["scraperapi_target_status"] = target_status
        credit_cost = response.headers.get("sa-credit-cost")
        if credit_cost is not None:
            meta["scraperapi_credit_cost"] = credit_cost

        return ScrapeResult(
            url=url,
            text=text,
            markdown=markdown,
            html=html,
            json=json_data,
            engine=self.NAME,
            elapsed_ms=0,  # base class fills this in
            cost_usd=_COST_USD,
            meta=meta,
        )


__all__ = ["ScraperApiEngine"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_engine_scraperapi.py::test_html_200_populates_all_slots -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/scrapefold/engines/scraperapi.py tests/test_engine_scraperapi.py
git commit -m "feat(engines): add ScraperAPI engine — core HTML path"
```

---

## Task 2: Param-mapping tests

**Files:**
- Test: `tests/test_engine_scraperapi.py`

- [ ] **Step 1: Append the failing tests**

Append to `tests/test_engine_scraperapi.py`:

```python
async def test_render_js_true_sends_render_true_string(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(status_code=200, text=_HTML)
    await _engine().scrape("https://example.com/", ScrapeOptions(render_js=True))
    req = httpx_mock.get_requests()[0]
    assert req.url.params.get("render") == "true"


async def test_render_js_false_sends_render_false_string(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(status_code=200, text=_HTML)
    await _engine().scrape("https://example.com/", ScrapeOptions(render_js=False))
    req = httpx_mock.get_requests()[0]
    assert req.url.params.get("render") == "false"


async def test_country_maps_to_country_code(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(status_code=200, text=_HTML)
    await _engine().scrape("https://example.com/", ScrapeOptions(country="ru"))
    req = httpx_mock.get_requests()[0]
    assert req.url.params.get("country_code") == "ru"


async def test_premium_proxy_sends_premium_true(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(status_code=200, text=_HTML)
    await _engine().scrape("https://example.com/", ScrapeOptions(premium_proxy=True))
    req = httpx_mock.get_requests()[0]
    assert req.url.params.get("premium") == "true"


async def test_wait_for_selector_forwarded(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(status_code=200, text=_HTML)
    await _engine().scrape("https://example.com/", ScrapeOptions(wait_for_selector="#main"))
    req = httpx_mock.get_requests()[0]
    assert req.url.params.get("wait_for_selector") == "#main"


async def test_language_sets_accept_language_header(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(status_code=200, text=_HTML)
    await _engine().scrape("https://example.com/", ScrapeOptions(language="ru"))
    req = httpx_mock.get_requests()[0]
    assert req.headers.get("accept-language") == "ru"
    assert "language" not in req.url.params


async def test_user_agent_sets_header_not_param(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(status_code=200, text=_HTML)
    await _engine().scrape("https://example.com/", ScrapeOptions(user_agent="MyBot/2.0"))
    req = httpx_mock.get_requests()[0]
    assert req.headers.get("user-agent") == "MyBot/2.0"
    assert "user_agent" not in req.url.params


async def test_cookies_become_cookie_header(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(status_code=200, text=_HTML)
    await _engine().scrape("https://example.com/", ScrapeOptions(cookies={"session": "abc"}))
    req = httpx_mock.get_requests()[0]
    assert "session=abc" in req.headers.get("cookie", "")


async def test_custom_headers_override_derived(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(status_code=200, text=_HTML)
    opts = ScrapeOptions(language="en", custom_headers={"X-Custom": "yes", "Accept-Language": "fr"})
    await _engine().scrape("https://example.com/", opts)
    req = httpx_mock.get_requests()[0]
    assert req.headers.get("x-custom") == "yes"
    assert req.headers.get("accept-language") == "fr"


async def test_api_key_and_url_in_params(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(status_code=200, text=_HTML)
    target = "https://target.example.com/page"
    await _engine().scrape(target)
    req = httpx_mock.get_requests()[0]
    assert req.url.params.get("api_key") == _API_KEY
    assert req.url.params.get("url") == target


async def test_extra_scraperapi_prefix_forwarded(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(status_code=200, text=_HTML)
    await _engine().scrape("https://example.com/", ScrapeOptions(extra={"scraperapi_session_number": "5"}))
    req = httpx_mock.get_requests()[0]
    assert req.url.params.get("session_number") == "5"


def test_adapt_ignores_non_scraperapi_extra_keys() -> None:
    opts = ScrapeOptions(extra={"firecrawl_foo": "bar", "unrelated": "val"})
    params = _adapt(opts, api_key="k", url="https://x.com")
    assert "firecrawl_foo" not in params
    assert "unrelated" not in params
```

- [ ] **Step 2: Run tests**

Run: `pytest tests/test_engine_scraperapi.py -v -k "render or country or premium or wait_for or language or user_agent or cookies or custom_headers or api_key or extra or adapt"`
Expected: all PASS (engine already implements these).

- [ ] **Step 3: Commit**

```bash
git add tests/test_engine_scraperapi.py
git commit -m "test(engines): ScraperAPI param mapping coverage"
```

---

## Task 3: Native markdown + AI-Parser JSON + meta (TDD)

**Files:**
- Test: `tests/test_engine_scraperapi.py`

- [ ] **Step 1: Append the failing tests**

```python
async def test_output_format_markdown_sets_native_markdown(httpx_mock: HTTPXMock) -> None:
    md = "# Title\n\nbody text"
    httpx_mock.add_response(status_code=200, headers={"content-type": "text/markdown"}, text=md)
    result = await _engine().scrape("https://example.com/", ScrapeOptions(output_format="markdown"))
    req = httpx_mock.get_requests()[0]
    assert req.url.params.get("output_format") == "markdown"
    assert result.markdown == md  # not re-derived from HTML
    assert result.html is None


async def test_autoparse_json_fills_json_slot(httpx_mock: HTTPXMock) -> None:
    payload = {"name": "Widget", "price": 9.99}
    httpx_mock.add_response(
        status_code=200,
        headers={"content-type": "application/json"},
        json=payload,
    )
    opts = ScrapeOptions(extra={"scraperapi_autoparse": "true"})
    result = await _engine().scrape("https://example.com/", opts)
    req = httpx_mock.get_requests()[0]
    assert req.url.params.get("autoparse") == "true"
    assert result.json == payload
    assert "Widget" in result.markdown  # best-effort pretty JSON


async def test_json_content_type_without_autoparse_still_parsed(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        status_code=200, headers={"content-type": "application/json"}, json={"a": 1}
    )
    result = await _engine().scrape("https://example.com/")
    assert result.json == {"a": 1}


async def test_credit_cost_header_stored_in_meta(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(status_code=200, headers={"sa-credit-cost": "10"}, text=_HTML)
    result = await _engine().scrape("https://example.com/")
    assert result.meta.get("scraperapi_credit_cost") == "10"
```

- [ ] **Step 2: Run tests**

Run: `pytest tests/test_engine_scraperapi.py -v -k "output_format or autoparse or json_content or credit_cost"`
Expected: all PASS.

> If `test_output_format_markdown_sets_native_markdown` fails because `output_format="markdown"` is the requested format but the response content-type triggers the JSON branch — confirm the JSON branch only fires on `application/json` ctype or explicit `autoparse`/`output_format=json` params, never on `output_format=markdown`. The implementation in Task 1 already orders the markdown branch after the JSON guard and `output_format=markdown` is excluded from `_is_json_response`.

- [ ] **Step 3: Commit**

```bash
git add tests/test_engine_scraperapi.py
git commit -m "test(engines): ScraperAPI native markdown + AI Parser JSON"
```

---

## Task 4: Availability, error handling, unsupported-option stripping (TDD)

**Files:**
- Test: `tests/test_engine_scraperapi.py`

- [ ] **Step 1: Append the failing tests**

```python
def test_is_available_true_with_key() -> None:
    assert _engine(api_key="key-abc").is_available() is True


def test_is_available_false_without_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SCRAPERAPI_API_KEY", raising=False)
    assert ScraperApiEngine(api_key=None).is_available() is False


async def test_network_error_raises_engine_error(httpx_mock: HTTPXMock) -> None:
    import httpx as _httpx

    httpx_mock.add_exception(_httpx.ConnectError("connection refused"))
    with pytest.raises(EngineError) as exc_info:
        await _engine().scrape("https://example.com/")
    assert exc_info.value.engine == "scraperapi"


async def test_unsupported_wait_ms_dropped_not_forwarded(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(status_code=200, text=_HTML)
    # wait_ms is NOT in SUPPORTED_OPTIONS — base class strips it, no "wait" param.
    await _engine().scrape("https://example.com/", ScrapeOptions(wait_ms=9000))
    req = httpx_mock.get_requests()[0]
    assert "wait" not in req.url.params
    assert "wait_ms" not in req.url.params


async def test_unsupported_options_do_not_break_call(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(status_code=200, text=_HTML)
    result = await _engine().scrape("https://example.com/", ScrapeOptions(stealth=True))
    assert result.engine == "scraperapi"
```

- [ ] **Step 2: Run the full file**

Run: `pytest tests/test_engine_scraperapi.py -v`
Expected: all PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/test_engine_scraperapi.py
git commit -m "test(engines): ScraperAPI availability + error + option stripping"
```

---

## Task 5: Registration (registry + pyproject extra)

**Files:**
- Modify: `src/scrapefold/engines/__init__.py`
- Modify: `pyproject.toml`
- Test: `tests/test_engine_scraperapi.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_engine_scraperapi.py`:

```python
def test_registered_in_registry() -> None:
    from scrapefold.engines import get_engine

    cls = get_engine("scraperapi")
    assert cls is ScraperApiEngine
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_engine_scraperapi.py::test_registered_in_registry -v`
Expected: FAIL — `unknown engine: 'scraperapi'`.

- [ ] **Step 3: Add the registry entry**

In `src/scrapefold/engines/__init__.py`, inside `_REGISTRY`, after the
`"scrapingdog"` entry, add:

```python
    "scraperapi": lambda: (
        __import__(
            "scrapefold.engines.scraperapi", fromlist=["ScraperApiEngine"]
        ).ScraperApiEngine
    ),
```

- [ ] **Step 4: Add the pyproject extra**

In `pyproject.toml`, after line `scrapingdog  = []`, add:

```toml
scraperapi   = []
```

And add `scraperapi` to the `all` aggregate list:

```toml
all = [
    "scrapefold[firecrawl,scrapingbee,scrapingdog,scraperapi,selenium,scrapling,crawl4ai,outscraper,apify,cloakbrowser,mcp]",
]
```

> Note: the existing `all` omits some no-dep extras (scrapingdog, oxylabs,
> jina, cloudflare, anysite) because they pull no packages. Adding
> `scraperapi` (also no-dep) is harmless but optional — include it so the
> extra is discoverable. Keep `scrapingdog` if you add it; do not remove
> existing names.

- [ ] **Step 5: Run the test + full suite**

Run: `pytest tests/test_engine_scraperapi.py -v`
Expected: all PASS including `test_registered_in_registry`.

- [ ] **Step 6: Commit**

```bash
git add src/scrapefold/engines/__init__.py pyproject.toml tests/test_engine_scraperapi.py
git commit -m "feat(engines): register scraperapi + add pyproject extra"
```

---

## Task 6: Docs (README rows, backlog, competitive-intel)

**Files:**
- Modify: `README.md`
- Modify: `docs/post-1.0/backlog.md`
- Create: `docs/competitive-intel.md`

- [ ] **Step 1: README — Engine Comparison table**

In `README.md`, after the Oxylabs row in the `## Engine Comparison` table
(currently `README.md:72`), add:

```markdown
| [**ScraperAPI**](https://www.scraperapi.com/) | ✅ | SaaS | Paid | ★★★ | ★★★ | ★★★ | Fast | $ |
```

- [ ] **Step 2: README — engine roster table**

After the Oxylabs roster row (currently `README.md:154`), add:

```markdown
| [**ScraperAPI**](https://www.scraperapi.com/) | SaaS | Paid | Proxy + JS render, native markdown, AI Parser (`json`) | `pip install scrapefold[scraperapi]` |
```

- [ ] **Step 3: README — detailed $/1k table**

In the `## Comparison` table (the one with header `Engine | Type | JS |
Stealth | Screenshot | Native MD | Proxy | Needs key | Free tier | Est.
$/1k`), add after the `scrapingdog` row:

```markdown
| `scraperapi` | SaaS | ✓ | — | — | ✓ | datacenter | ✓ | ✓ | $0.49 |
```

- [ ] **Step 4: Backlog — AI Parser lifecycle follow-up**

In `docs/post-1.0/backlog.md`, under `## Features`, add a new entry:

```markdown
### ScraperAPI AI Parser lifecycle

- **Why deferred:** The `scraperapi` engine *uses* a parser by reference
  (`extra["scraperapi_autoparse"]` for built-in domains, or a custom
  parser id). It does not create/edit/delete parsers. ScraperAPI's AI
  Parser went GA 2026-06-04 charging 30,000 credits per parser create
  **and** per edit (first parser free) — a costly, stateful operation
  that doesn't fit the stateless per-call engine model.
- **v0.2.0 plan:** A separate management helper (not a scrape engine) that
  wraps the AI Parser CRUD API, with credit-cost guards before any
  create/edit call. Tracked in `docs/competitive-intel.md`.
```

- [ ] **Step 5: Create competitive-intel doc**

Create `docs/competitive-intel.md`:

```markdown
---
purpose: "Dated log of competitor pricing / feature changes that affect scrapefold's positioning or engine roadmap."
updated: "2026-06-04"
related:
  - post-1.0/backlog.md
  - README.md
---

# Competitive intel

Append-only log of notable pricing / feature shifts among the SaaS scraping
vendors scrapefold wraps or competes with. Newest first. Each row should
link a source and state the concrete impact on scrapefold (engine work,
docs, positioning).

| Date | Vendor | Change | Source | Impact on scrapefold |
|---|---|---|---|---|
| 2026-06-04 | ScraperAPI | **AI Parser GA.** LLM-based structured extraction leaves beta. New pricing: 30,000 credits per parser **create** and per **edit**; first parser free; existing beta parsers grandfathered (no retroactive charges). | Product email "AI Parser is Now Live \| Your Beta Parsers Are Safe" (noreply@scraperapi.com, 2026-06-04) | Added `scraperapi` engine (uses parsers by reference). Parser **lifecycle** management deferred — see [backlog](post-1.0/backlog.md) "ScraperAPI AI Parser lifecycle". |
```

- [ ] **Step 6: Verify links + commit**

Run: `pytest -m "not paid and not network" -q && ./scripts/check.sh`
Expected: green.

```bash
git add README.md docs/post-1.0/backlog.md docs/competitive-intel.md
git commit -m "docs: add ScraperAPI to comparison tables + competitive-intel log"
```

---

## Final verification

- [ ] Run `./scripts/check.sh` — lint + type-check + offline tests all pass.
- [ ] Run `pytest tests/test_engine_scraperapi.py -v` — full new suite green.
- [ ] Confirm `python -c "from scrapefold.engines import get_engine; print(get_engine('scraperapi'))"` prints the class.
