# Exa Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a broad Exa REST engine with LinkedIn-focused public people/company defaults.

**Architecture:** Implement one `ExaEngine` in one engine file, using `httpx.AsyncClient` and `ScrapeOptions.extra["exa_*"]` to select Exa endpoint modes. The engine returns structured Exa payloads in `ScrapeResult.json`, derives text/markdown through the existing JSON converter, and joins LinkedIn profile/company ladders as a paid race member.

**Tech Stack:** Python 3.10+, `httpx`, `pytest`, `pytest-httpx`, existing `scrapefold` engine registry, `ScrapeOptions`, `ScrapeResult`, and `json_to_scrape_text`.

## Global Constraints

- All code under `src/scrapefold/`; tests under `tests/`; docs under `docs/`.
- Async everywhere; use `httpx.AsyncClient`; no `requests`.
- No Exa SDK dependency; do not add `exa-py`.
- No vendor LLM SDK imports anywhere in `src/`.
- Engines are lazy-imported via `src/scrapefold/engines/__init__.py`.
- One file per engine: `src/scrapefold/engines/exa.py`.
- Engines drop unsupported unified options through `SUPPORTED_OPTIONS`; Exa-specific native options go through `opts.extra["exa_*"]`.
- Default tests must be offline and make no live Exa calls.
- Production code must be written after failing tests.

---

## File Structure

- Create `src/scrapefold/engines/exa.py`: Exa REST adapter, payload builders, LinkedIn URL inference, cost extraction, Agent polling.
- Create `tests/test_engine_exa.py`: offline tests for all Exa modes and error cases.
- Modify `src/scrapefold/engines/__init__.py`: lazy-register `exa`.
- Modify `src/scrapefold/ladders.py`: add `exa` to LinkedIn profile/company races.
- Modify `tests/test_ladders.py`: add `exa` to valid engines and assert LinkedIn wiring.
- Modify `tests/test_smoke.py`: assert registry contains `exa`.
- Modify `pyproject.toml`: add empty `exa` extra and include it in `all`.
- Modify docs: `docs/README.md`, `docs/workflows/development.md`, `docs/architecture/overview.md`.

## Interfaces

`src/scrapefold/engines/exa.py` must provide:

```python
class ExaEngine(ScrapeEngine):
    NAME = "exa"

def _infer_mode(url: str, opts: ScrapeOptions) -> str: ...
def _build_payload(mode: str, url: str, opts: ScrapeOptions) -> tuple[str, dict[str, object]]: ...
def _result_from_payload(url: str, payload: dict[str, object]) -> ScrapeResult: ...
```

Mode paths:

```python
{
    "contents": "/contents",
    "search": "/search",
    "find_similar": "/findSimilar",
    "answer": "/answer",
    "agent": "/agent/runs",
}
```

### Task 1: Write Exa Engine Red Tests

**Files:**
- Create: `tests/test_engine_exa.py`

**Interfaces:**
- Consumes: existing `ScrapeOptions`, `EngineError`, and `pytest-httpx`.
- Produces: a failing test suite that defines `ExaEngine` behavior before implementation.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_engine_exa.py` with:

```python
from __future__ import annotations

import json

import httpx
import pytest
from pytest_httpx import HTTPXMock

from scrapefold.engines.base import EngineError
from scrapefold.options import ScrapeOptions

_BASE = "https://api.exa.ai"
_API_KEY = "exa-test-key"


def _engine(api_key: str = _API_KEY):
    from scrapefold.engines.exa import ExaEngine

    return ExaEngine(api_key=api_key)


def _json_body(request: httpx.Request) -> dict:
    return json.loads(request.content.decode())


def _ok_payload() -> dict:
    return {
        "requestId": "req_123",
        "results": [
            {
                "title": "Example",
                "url": "https://example.com",
                "text": "Example text",
                "highlights": ["Example highlight"],
            }
        ],
        "costDollars": {"total": 0.007},
    }


async def test_default_contents_request_uses_top_level_text(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(method="POST", url=f"{_BASE}/contents", json=_ok_payload())

    result = await _engine().scrape("https://example.com/page")

    request = httpx_mock.get_requests()[0]
    body = _json_body(request)
    assert request.headers["x-api-key"] == _API_KEY
    assert body["urls"] == ["https://example.com/page"]
    assert body["text"] is True
    assert "contents" not in body
    assert result.json == _ok_payload()
    assert result.cost_usd == 0.007
    assert result.engine == "exa"
    assert "Example text" in result.text
    assert "Example text" in result.markdown


async def test_contents_all_failed_statuses_raise_engine_error(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        method="POST",
        url=f"{_BASE}/contents",
        json={
            "results": [],
            "statuses": [{"id": "https://bad.example", "status": "error"}],
        },
    )

    with pytest.raises(EngineError) as exc_info:
        await _engine().scrape("https://bad.example")

    assert exc_info.value.engine == "exa"
    assert "no successful contents" in str(exc_info.value)


async def test_search_request_nests_contents_options(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(method="POST", url=f"{_BASE}/search", json=_ok_payload())
    opts = ScrapeOptions(
        extra={
            "exa_mode": "search",
            "exa_query": "latest LLM research",
            "exa_contents": {"text": {"maxCharacters": 500}},
            "exa_numResults": 5,
        }
    )

    await _engine().scrape("https://ignored.example", opts)

    body = _json_body(httpx_mock.get_requests()[0])
    assert body["query"] == "latest LLM research"
    assert body["type"] == "auto"
    assert body["numResults"] == 5
    assert body["contents"] == {"text": {"maxCharacters": 500}}


async def test_linkedin_profile_defaults_to_people_search(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(method="POST", url=f"{_BASE}/search", json=_ok_payload())

    await _engine().scrape("https://www.linkedin.com/in/janedoe/")

    body = _json_body(httpx_mock.get_requests()[0])
    assert body["category"] == "people"
    assert body["contents"] == {"highlights": True}
    assert "linkedin.com/in/janedoe" in body["query"]


async def test_linkedin_company_defaults_to_company_search(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(method="POST", url=f"{_BASE}/search", json=_ok_payload())

    await _engine().scrape("https://www.linkedin.com/company/openai/")

    body = _json_body(httpx_mock.get_requests()[0])
    assert body["category"] == "company"
    assert body["contents"] == {"highlights": True}


async def test_people_category_strips_unsupported_filters(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(method="POST", url=f"{_BASE}/search", json=_ok_payload())
    opts = ScrapeOptions(
        extra={
            "exa_mode": "search",
            "exa_query": "VP Engineering at fintech companies",
            "exa_category": "people",
            "exa_includeDomains": ["linkedin.com"],
            "exa_excludeDomains": ["example.com"],
            "exa_startPublishedDate": "2025-01-01",
            "exa_endPublishedDate": "2025-12-31",
        }
    )

    await _engine().scrape("https://ignored.example", opts)

    body = _json_body(httpx_mock.get_requests()[0])
    assert body["category"] == "people"
    assert "includeDomains" not in body
    assert "excludeDomains" not in body
    assert "startPublishedDate" not in body
    assert "endPublishedDate" not in body


async def test_find_similar_hits_findsimilar_endpoint(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(method="POST", url=f"{_BASE}/findSimilar", json=_ok_payload())
    opts = ScrapeOptions(extra={"exa_mode": "find_similar", "exa_numResults": 3})

    await _engine().scrape("https://example.com/source", opts)

    body = _json_body(httpx_mock.get_requests()[0])
    assert body["url"] == "https://example.com/source"
    assert body["numResults"] == 3
    assert body["contents"] == {"highlights": True}


async def test_answer_hits_answer_endpoint(httpx_mock: HTTPXMock) -> None:
    payload = {"answer": "42", "citations": [], "costDollars": {"total": 0.002}}
    httpx_mock.add_response(method="POST", url=f"{_BASE}/answer", json=payload)
    opts = ScrapeOptions(extra={"exa_mode": "answer", "exa_query": "What is the answer?"})

    result = await _engine().scrape("https://ignored.example", opts)

    body = _json_body(httpx_mock.get_requests()[0])
    assert body == {"query": "What is the answer?", "text": True}
    assert result.json == payload
    assert result.cost_usd == 0.002


async def test_answer_streaming_rejected_before_request() -> None:
    opts = ScrapeOptions(extra={"exa_mode": "answer", "exa_stream": True})

    with pytest.raises(EngineError) as exc_info:
        await _engine().scrape("https://ignored.example", opts)

    assert "streaming" in str(exc_info.value)


async def test_agent_creates_run_and_polls_to_completion(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        method="POST",
        url=f"{_BASE}/agent/runs",
        json={"id": "agent_run_123", "status": "queued"},
    )
    terminal = {
        "id": "agent_run_123",
        "status": "completed",
        "output": {"text": "done", "structured": {"ok": True}},
        "usage": {"agentComputeUnits": 0.1},
    }
    httpx_mock.add_response(method="GET", url=f"{_BASE}/agent/runs/agent_run_123", json=terminal)
    opts = ScrapeOptions(
        extra={
            "exa_mode": "agent",
            "exa_query": "Find AI infra companies",
            "exa_effort": "minimal",
            "exa_poll_interval_ms": 0,
            "exa_max_poll_ms": 1000,
        }
    )

    result = await _engine().scrape("https://ignored.example", opts)

    create_body = _json_body(httpx_mock.get_requests()[0])
    assert create_body["query"] == "Find AI infra companies"
    assert create_body["effort"] == "minimal"
    assert result.json == terminal
    assert "done" in result.text


async def test_http_error_raises_engine_error(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(method="POST", url=f"{_BASE}/contents", status_code=401, json={})

    with pytest.raises(EngineError) as exc_info:
        await _engine().scrape("https://example.com")

    assert exc_info.value.engine == "exa"


def test_is_available_false_without_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EXA_API_KEY", raising=False)
    from scrapefold.engines.exa import ExaEngine

    assert ExaEngine(api_key=None).is_available() is False


def test_api_key_read_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EXA_API_KEY", "env-exa-key")
    from scrapefold.engines.exa import ExaEngine

    assert ExaEngine().api_key == "env-exa-key"


def test_engine_registered() -> None:
    from scrapefold.engines import get_engine, list_engine_names

    assert "exa" in list_engine_names()
    assert get_engine("exa").__name__ == "ExaEngine"
```

- [ ] **Step 2: Run tests to verify RED**

Run:

```bash
rtk pytest tests/test_engine_exa.py -q
```

Expected: failures because `scrapefold.engines.exa` and registry entry do not exist.

### Task 2: Implement Exa Engine and Registry

**Files:**
- Create: `src/scrapefold/engines/exa.py`
- Modify: `src/scrapefold/engines/__init__.py`
- Modify: `pyproject.toml`

**Interfaces:**
- Consumes: tests from Task 1.
- Produces: `ExaEngine`, registered as `exa`, with all requested REST modes.

- [ ] **Step 1: Implement the engine**

Create `src/scrapefold/engines/exa.py` with helpers matching the Task 1 test contract:

```python
"""ExaEngine — paid REST search, contents, answer, and agent workflows."""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any
from urllib.parse import urlparse

import httpx

from scrapefold.engines.base import EngineCapabilities, ScrapeEngine
from scrapefold.html_to_text import json_to_scrape_text
from scrapefold.options import ScrapeOptions, strip_extra_prefix
from scrapefold.result import ScrapeResult

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.exa.ai"
_DEFAULT_COST_USD = 0.005
_TERMINAL_AGENT_STATUSES = frozenset({"completed", "failed", "cancelled"})
_MODE_PATHS = {
    "contents": "/contents",
    "search": "/search",
    "find_similar": "/findSimilar",
    "answer": "/answer",
    "agent": "/agent/runs",
}
_CATEGORY_UNSUPPORTED_FILTERS = frozenset(
    {
        "excludeDomains",
        "startPublishedDate",
        "endPublishedDate",
        "startCrawlDate",
        "endCrawlDate",
    }
)


def _path(url: str) -> str:
    return urlparse(url if "://" in url else f"https://{url}").path


def _host(url: str) -> str:
    return urlparse(url if "://" in url else f"https://{url}").netloc.lower()


def _is_linkedin_profile(url: str) -> bool:
    return "linkedin.com" in _host(url) and "/in/" in _path(url)


def _is_linkedin_company(url: str) -> bool:
    return "linkedin.com" in _host(url) and "/company/" in _path(url)


def _infer_mode(url: str, opts: ScrapeOptions) -> str:
    extra = strip_extra_prefix(opts.extra, "exa_")
    mode = extra.get("mode")
    if mode is not None:
        return str(mode)
    if _is_linkedin_profile(url) or _is_linkedin_company(url):
        return "search"
    return "contents"


def _query_for(url: str, extra: dict[str, Any]) -> str:
    if "query" in extra:
        return str(extra["query"])
    if "linkedin.com" in _host(url):
        return f"public LinkedIn profile or company page {url}"
    return url


def _drop_unsupported_category_filters(payload: dict[str, Any]) -> None:
    category = payload.get("category")
    if category not in {"people", "company"}:
        return
    for key in _CATEGORY_UNSUPPORTED_FILTERS:
        if key in payload:
            logger.debug("exa category=%s dropping unsupported filter=%s", category, key)
            payload.pop(key, None)
    if category == "people" and "includeDomains" in payload:
        logger.debug("exa category=people dropping unsupported filter=includeDomains")
        payload.pop("includeDomains", None)


def _build_contents_payload(url: str, extra: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "urls": extra.get("urls", [url]),
        "text": extra.get("text", True),
    }
    for key in (
        "highlights",
        "summary",
        "subpages",
        "subpageTarget",
        "extras",
        "maxAgeHours",
        "livecrawlTimeout",
    ):
        if key in extra:
            payload[key] = extra[key]
    return payload


def _build_search_payload(url: str, extra: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "query": _query_for(url, extra),
        "type": extra.get("type", "auto"),
        "numResults": int(extra.get("numResults", 10)),
        "contents": extra.get("contents", {"highlights": True}),
    }
    if _is_linkedin_profile(url):
        payload.setdefault("category", "people")
    elif _is_linkedin_company(url):
        payload.setdefault("category", "company")

    for key in (
        "category",
        "includeDomains",
        "excludeDomains",
        "startPublishedDate",
        "endPublishedDate",
        "startCrawlDate",
        "endCrawlDate",
        "additionalQueries",
        "systemPrompt",
        "outputSchema",
        "maxAgeHours",
        "moderation",
    ):
        if key in extra:
            payload[key] = extra[key]
    _drop_unsupported_category_filters(payload)
    return payload


def _build_find_similar_payload(url: str, extra: dict[str, Any]) -> dict[str, Any]:
    return {
        "url": str(extra.get("url", url)),
        "numResults": int(extra.get("numResults", 10)),
        "contents": extra.get("contents", {"highlights": True}),
    }


def _build_answer_payload(url: str, extra: dict[str, Any]) -> dict[str, Any]:
    if extra.get("stream"):
        raise ValueError("exa answer streaming is not supported by ExaEngine yet")
    payload: dict[str, Any] = {"query": _query_for(url, extra), "text": extra.get("text", True)}
    if "outputSchema" in extra:
        payload["outputSchema"] = extra["outputSchema"]
    return payload


def _build_agent_payload(url: str, extra: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {"query": _query_for(url, extra)}
    for key in ("outputSchema", "effort", "input", "previousRunId"):
        if key in extra:
            payload[key] = extra[key]
    return payload


def _build_payload(mode: str, url: str, opts: ScrapeOptions) -> tuple[str, dict[str, Any]]:
    extra = strip_extra_prefix(opts.extra, "exa_")
    extra.pop("mode", None)
    if mode == "contents":
        return _MODE_PATHS[mode], _build_contents_payload(url, extra)
    if mode == "search":
        return _MODE_PATHS[mode], _build_search_payload(url, extra)
    if mode == "find_similar":
        return _MODE_PATHS[mode], _build_find_similar_payload(url, extra)
    if mode == "answer":
        return _MODE_PATHS[mode], _build_answer_payload(url, extra)
    if mode == "agent":
        return _MODE_PATHS[mode], _build_agent_payload(url, extra)
    raise ValueError(f"unknown exa_mode: {mode!r}")


def _cost_from_payload(payload: dict[str, Any]) -> float:
    cost = payload.get("costDollars")
    if isinstance(cost, dict):
        total = cost.get("total")
        if isinstance(total, (int, float)):
            return float(total)
    return _DEFAULT_COST_USD


def _validate_contents_payload(payload: dict[str, Any]) -> None:
    if payload.get("results"):
        return
    statuses = payload.get("statuses")
    if isinstance(statuses, list) and statuses:
        if all(isinstance(s, dict) and s.get("status") != "success" for s in statuses):
            raise ValueError("exa contents returned no successful contents")


def _result_from_payload(url: str, payload: dict[str, Any]) -> ScrapeResult:
    text_out, markdown_out = json_to_scrape_text(payload)
    return ScrapeResult(
        url=url,
        text=text_out,
        markdown=markdown_out,
        html=None,
        json=payload,
        engine=ExaEngine.NAME,
        elapsed_ms=0,
        cost_usd=_cost_from_payload(payload),
        meta={"status_code": 200, "request_id": payload.get("requestId")},
    )


class ExaEngine(ScrapeEngine):
    """Exa REST engine for search, contents, similar pages, answers, and Agent runs."""

    NAME = "exa"
    CAPABILITIES = EngineCapabilities(
        js_rendering=False,
        stealth=True,
        requires_api_key=True,
        estimated_cost_usd=_DEFAULT_COST_USD,
        billing_unit="call",
        proxy_type="residential",
        site_classified=True,
        output_native_markdown=False,
    )
    SUPPORTED_OPTIONS = frozenset({"output_format", "timeout_s", "extra"})

    def __init__(self, api_key: str | None = None) -> None:
        super().__init__(api_key or os.getenv("EXA_API_KEY"))

    async def _fetch(self, url: str, opts: ScrapeOptions) -> ScrapeResult:
        mode = _infer_mode(url, opts)
        path, payload = _build_payload(mode, url, opts)
        headers = {"x-api-key": self.api_key or "", "Content-Type": "application/json"}

        async with httpx.AsyncClient(timeout=float(opts.timeout_s), base_url=_BASE_URL) as client:
            if mode == "agent":
                response = await client.post(path, json=payload, headers=headers)
                response.raise_for_status()
                run = await self._poll_agent_run(client, response.json(), opts, headers)
                return _result_from_payload(url, run)

            response = await client.post(path, json=payload, headers=headers)
            response.raise_for_status()
            data = response.json()
            if mode == "contents":
                _validate_contents_payload(data)
            return _result_from_payload(url, data)

    async def _poll_agent_run(
        self,
        client: httpx.AsyncClient,
        initial: dict[str, Any],
        opts: ScrapeOptions,
        headers: dict[str, str],
    ) -> dict[str, Any]:
        run_id = initial.get("id")
        if not isinstance(run_id, str) or not run_id:
            raise ValueError("exa agent run response did not include an id")

        extra = strip_extra_prefix(opts.extra, "exa_")
        interval_ms = int(extra.get("poll_interval_ms", 1000))
        max_poll_ms = int(extra.get("max_poll_ms", max(opts.timeout_s * 1000, 1000)))
        waited_ms = 0
        current = initial

        while str(current.get("status")) not in _TERMINAL_AGENT_STATUSES:
            if waited_ms >= max_poll_ms:
                raise TimeoutError(f"exa agent run {run_id} did not finish within {max_poll_ms}ms")
            if interval_ms > 0:
                await asyncio.sleep(interval_ms / 1000)
            waited_ms += interval_ms
            response = await client.get(f"/agent/runs/{run_id}", headers=headers)
            response.raise_for_status()
            current = response.json()

        if current.get("status") != "completed":
            raise ValueError(f"exa agent run {run_id} ended with status={current.get('status')}")
        return current


__all__ = [
    "ExaEngine",
    "_build_payload",
    "_infer_mode",
    "_result_from_payload",
]
```

- [ ] **Step 2: Register the engine lazily**

Add to `_REGISTRY` in `src/scrapefold/engines/__init__.py`:

```python
    "exa": lambda: __import__("scrapefold.engines.exa", fromlist=["ExaEngine"]).ExaEngine,
```

- [ ] **Step 3: Add the optional dependency group**

In `pyproject.toml`, add:

```toml
exa          = []
```

Then include `exa` in the `all = [...]` list.

- [ ] **Step 4: Run Task 1 tests to verify GREEN**

Run:

```bash
rtk pytest tests/test_engine_exa.py -q
```

Expected: all tests in `tests/test_engine_exa.py` pass.

- [ ] **Step 5: Commit**

Run:

```bash
rtk git add tests/test_engine_exa.py src/scrapefold/engines/exa.py src/scrapefold/engines/__init__.py pyproject.toml
rtk git commit -m "feat: add exa engine"
```

### Task 3: Add Exa to LinkedIn Ladders

**Files:**
- Modify: `src/scrapefold/ladders.py`
- Modify: `tests/test_ladders.py`
- Modify: `tests/test_smoke.py`

**Interfaces:**
- Consumes: registry name `exa` from Task 2.
- Produces: LinkedIn profile/company auto ladders include `exa`.

- [ ] **Step 1: Write failing ladder/smoke assertions**

Edit `tests/test_ladders.py`:

```python
_VALID_ENGINES = frozenset(
    {
        # ported (10)
        "requests",
        "firecrawl",
        "scrapingbee",
        "scrapingdog",
        "selenium",
        "jina",
        "cloudflare",
        "crawl4ai",
        "outscraper",
        "apify_linkedin",
        # multi-mode distinct names
        "scrapling_stealth",
        "scrapling_fast",
        "brightdata_unlocker_sync",
        "brightdata_unlocker_async",
        "brightdata_browser",
        # new stealth browsers (open-source)
        "obscura",
        "cloakbrowser",
        # new paid SaaS
        "anysite",
        "scrapecreators",
        "exa",
    }
)


def test_linkedin_profile_and_company_ladders_include_exa() -> None:
    for cls in ("linkedin_profile", "linkedin_company"):
        first = get_ladder(cls)[0]
        assert isinstance(first, RaceStep), f"{cls} first step is not a race"
        assert "exa" in step_engines(first)
```

Edit `tests/test_smoke.py`:

```python
def test_engine_registry_contains_exa() -> None:
    """Exa REST engine is registered."""
    assert "exa" in set(list_engine_names())
```

- [ ] **Step 2: Run tests to verify RED**

Run:

```bash
rtk pytest tests/test_ladders.py::test_linkedin_profile_and_company_ladders_include_exa tests/test_smoke.py::test_engine_registry_contains_exa -q
```

Expected: ladder test fails because `exa` is not in the LinkedIn races yet.

- [ ] **Step 3: Update ladders**

Edit `src/scrapefold/ladders.py` first race entries:

```python
"linkedin_profile": (
    _race(
        "apify_linkedin",
        "anysite",
        "scrapingdog",
        "exa",
        budget_accounting="sum_all",
    ),
    _seq("brightdata_unlocker_sync", cost=_HIGH),
),
"linkedin_company": (
    _race("apify_linkedin", "anysite", "scrapingdog", "exa", budget_accounting="sum_all"),
    _seq("brightdata_unlocker_sync", cost=_HIGH),
),
```

- [ ] **Step 4: Run tests to verify GREEN**

Run:

```bash
rtk pytest tests/test_ladders.py tests/test_smoke.py -q
```

Expected: both files pass.

- [ ] **Step 5: Commit**

Run:

```bash
rtk git add src/scrapefold/ladders.py tests/test_ladders.py tests/test_smoke.py
rtk git commit -m "feat: route linkedin profiles through exa"
```

### Task 4: Update Documentation

**Files:**
- Modify: `docs/README.md`
- Modify: `docs/workflows/development.md`
- Modify: `docs/architecture/overview.md`

**Interfaces:**
- Consumes: implemented `exa` engine and LinkedIn ladder changes.
- Produces: docs matching the new registry and env var behavior.

- [ ] **Step 1: Update docs**

Apply these exact content changes:

```markdown
In docs/README.md, change:
`Wraps 14 vendor APIs + local stealth browsers + a baseline `requests` engine behind one async interface.`
to:
`Wraps 15 vendor APIs + local stealth browsers + a baseline `requests` engine behind one async interface.`

In docs/README.md, add this row after PR #3:
`| PR #4 ✅ | Exa engine (Search, Contents, Answer, Agent; LinkedIn people/company defaults, pure-httpx) | Unreleased |`

In docs/workflows/development.md, add this row after `SCRAPECREATORS_API_KEY`:
`| `EXA_API_KEY` | exa |`

In docs/architecture/overview.md, change:
`RaceStep(engines=("apify_linkedin", "anysite", "scrapingdog"),`
to:
`RaceStep(engines=("apify_linkedin", "anysite", "scrapingdog", "exa"),`

In docs/architecture/overview.md, add after the LinkedIn no-plain-HTTP sentence:
`Exa uses public people/company search defaults for LinkedIn profile/company URLs; Sales Navigator remains outside the automatic Exa path.`
```

- [ ] **Step 2: Run docs-adjacent checks**

Run:

```bash
rtk pytest tests/test_ladders.py tests/test_smoke.py -q
```

Expected: pass.

- [ ] **Step 3: Commit**

Run:

```bash
rtk git add docs/README.md docs/workflows/development.md docs/architecture/overview.md
rtk git commit -m "docs: document exa engine"
```

### Task 5: Full Verification

**Files:**
- Verify only.

**Interfaces:**
- Consumes: all prior tasks.
- Produces: evidence that the feature is safe to ship.

- [ ] **Step 1: Run targeted suite**

Run:

```bash
rtk pytest tests/test_engine_exa.py tests/test_ladders.py tests/test_smoke.py -q
```

Expected: pass.

- [ ] **Step 2: Run full project check**

Run:

```bash
rtk ./scripts/check.sh
```

Expected: exit 0.

- [ ] **Step 3: Inspect final diff**

Run:

```bash
rtk git status --short
rtk git diff --stat HEAD
```

Expected: only intentional Exa implementation/docs changes since the last commit.

- [ ] **Step 4: Final commit if any verification cleanup changed files**

If formatting or cleanup changed files, run:

```bash
rtk git status --short
rtk git add src/scrapefold/engines/exa.py src/scrapefold/engines/__init__.py src/scrapefold/ladders.py tests/test_engine_exa.py tests/test_ladders.py tests/test_smoke.py pyproject.toml docs/README.md docs/workflows/development.md docs/architecture/overview.md
rtk git commit -m "chore: polish exa engine"
```

Expected: either no commit needed or a small cleanup commit.
