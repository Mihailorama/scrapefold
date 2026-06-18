---
title: Exa engine design
date: 2026-06-18
status: approved-in-chat
related:
  - ../../README.md
  - ../../conventions/golden-rules.md
  - ../../../CONTRIBUTING.md
  - ../../../src/scrapefold/engines/scrapecreators.py
  - ../../../src/scrapefold/ladders.py
---

# Exa engine design

## Goal

Add Exa as a paid, API-key-backed `scrapefold` engine with broad coverage of Exa's current public REST surface, with first-class LinkedIn-focused behavior.

The implementation should expose one canonical engine name, `exa`, rather than separate registry entries per Exa endpoint. This keeps the vendor integration aligned with the repository's "one file per engine" rule while still supporting multiple Exa modes through `ScrapeOptions.extra`.

## Sources Checked

- Exa Search: `POST https://api.exa.ai/search`
- Exa Contents: `POST https://api.exa.ai/contents`
- Exa Answer: `POST https://api.exa.ai/answer`
- Exa Agent API: `POST /agent/runs`, `GET /agent/runs/{id}`
- Exa people vertical: `category: "people"` for public professional profiles
- Exa current docs note that legacy research is superseded by Agent API for long-running research workflows.

## Engine Shape

Create `src/scrapefold/engines/exa.py`:

- Class: `ExaEngine`
- Registry name: `exa`
- Auth: `EXA_API_KEY` environment variable or constructor `api_key`
- Transport: pure `httpx.AsyncClient`, no `exa-py` dependency
- Headers: `x-api-key: <key>`, `Content-Type: application/json`
- Return type: always `ScrapeResult`
- Structured payload: always stored in `ScrapeResult.json`
- Text/markdown: generated from JSON using existing `json_to_scrape_text`

`CAPABILITIES` should declare:

- `requires_api_key=True`
- `site_classified=True`
- `output_native_markdown=False`
- `billing_unit="call"`
- `estimated_cost_usd` as a conservative low paid-call estimate for budget gates

The result `cost_usd` should prefer `payload["costDollars"]["total"]` when present. If the field is absent, fall back to the capability estimate.

## Modes

The engine accepts mode through `opts.extra["exa_mode"]`.

### `contents`

Default mode for normal `scrape(url)` calls, except LinkedIn profile/company URLs. LinkedIn profile/company URLs default to `search` because Exa's strongest LinkedIn value is its public people/company indexes, not raw auth-gated page retrieval.

Request:

```json
{
  "urls": ["<target url>"],
  "text": true
}
```

Supported extra overrides:

- `exa_urls`: replace the single target URL with a list of URLs
- `exa_text`
- `exa_highlights`
- `exa_summary`
- `exa_subpages`
- `exa_subpageTarget`
- `exa_extras`
- `exa_maxAgeHours`
- `exa_livecrawlTimeout`

Important adapter rule: `/contents` takes `text`, `highlights`, and `summary` as top-level fields. Do not nest them under `contents`.

### `search`

Search the web and optionally retrieve result content.

Request base:

```json
{
  "query": "<query>",
  "type": "auto",
  "numResults": 10,
  "contents": {"highlights": true}
}
```

Query source:

1. `opts.extra["exa_query"]`
2. If absent and the target is a LinkedIn URL, derive a URL-oriented query from the URL.
3. Otherwise use the target URL string as the query.

Supported extra overrides:

- `exa_query`
- `exa_type`
- `exa_numResults`
- `exa_category`
- `exa_contents`
- `exa_includeDomains`
- `exa_excludeDomains`
- `exa_startPublishedDate`
- `exa_endPublishedDate`
- `exa_startCrawlDate`
- `exa_endCrawlDate`
- `exa_additionalQueries`
- `exa_systemPrompt`
- `exa_outputSchema`
- `exa_maxAgeHours`
- `exa_moderation`

People/company restriction: when `category` is `people` or `company`, strip unsupported filters that Exa rejects, including date filters and `excludeDomains`. For `people`, also strip `includeDomains` unless a caller explicitly passes a supported profile domain policy later. Log dropped fields at debug level rather than raising.

### `find_similar`

Call `POST /findSimilar`.

Request base:

```json
{
  "url": "<target url>",
  "numResults": 10,
  "contents": {"highlights": true}
}
```

Use this only when explicitly requested through `exa_mode`; do not put it into automatic ladders.

### `answer`

Call `POST /answer`.

Request base:

```json
{
  "query": "<query>",
  "text": true
}
```

Do not support streaming in the first implementation. If `exa_stream` is true, raise a clear `ValueError` before making the request so the base class wraps it as `EngineError`.

### `agent`

Call Exa Agent for long-running research/list-building/enrichment.

Initial implementation should support the synchronous wrapper shape:

1. `POST /agent/runs`
2. Poll `GET /agent/runs/{id}` until `completed`, `failed`, or `cancelled`
3. Return the terminal run object as JSON

Supported extras:

- `exa_query`
- `exa_outputSchema`
- `exa_effort`
- `exa_input`
- `exa_previousRunId`
- `exa_poll_interval_ms`
- `exa_max_poll_ms`

Do not add `agent` mode to automatic ladders because it can be high-cost and long-running.

## LinkedIn Behavior

LinkedIn focus means Exa participates in public-profile/company enrichment without pretending to be an auth-gated LinkedIn scraper.

Ladder changes:

- Add `exa` to the first paid race for `linkedin_profile`
- Add `exa` to the first paid race for `linkedin_company`
- Do not add Exa to `linkedin_sales_navigator` first step. Sales Navigator is auth-gated and Exa's public people/company indexes are not equivalent to a logged-in Sales Navigator session.
- Leave `linkedin_post` and `linkedin_job` out of the first Exa pass unless tests or docs show reliable mode-specific behavior. Exa can still be used explicitly with `opts.engines=("exa",)`.

Mode/category defaults for LinkedIn:

- `linkedin_profile` URLs with no explicit `exa_mode` should use `search` with `category: "people"` and `contents: {"highlights": true}`.
- `linkedin_company` URLs with no explicit `exa_mode` should use `search` with `category: "company"` and `contents: {"highlights": true}`.
- Callers that specifically want raw URL retrieval for a LinkedIn URL can force `opts.extra["exa_mode"] = "contents"`.

Because current router steps do not carry per-engine option overrides, the implementation should infer these LinkedIn defaults from the target URL whenever `exa_mode` is absent. This keeps the ladder data simple and avoids adding router-specific option mutation.

## Error Handling

- HTTP 4xx/5xx should call `raise_for_status()` and be wrapped by the base engine as `EngineError`.
- `/contents` may return HTTP 200 with per-URL failures in `statuses`. If every status is non-success or there are no results, raise `ValueError` with a concise message. If at least one result is present, return the payload and include `statuses` in JSON.
- Missing API key should make `is_available()` return `False`.
- Unknown `exa_mode` should raise `ValueError`.
- Streaming modes are out of scope for the first pass.

## Tests

Write tests before production code.

Add `tests/test_engine_exa.py` covering:

- Missing key: `is_available() is False`
- Env key: `EXA_API_KEY` is read
- Registry contains `exa`
- Default `contents` request hits `/contents` with top-level `text`
- `search` request nests content options under `contents`
- LinkedIn profile automatic request uses `category: "people"`
- LinkedIn company automatic request uses `category: "company"`
- People/company category strips unsupported filters instead of sending a request that Exa rejects
- `find_similar` request hits `/findSimilar`
- `answer` request hits `/answer`
- `agent` creates a run, polls to completion, and returns terminal JSON
- `costDollars.total` maps to `ScrapeResult.cost_usd`
- HTTP error becomes `EngineError`
- `/contents` all-failed statuses become `EngineError`

Update existing tests:

- `tests/test_ladders.py`: add `exa` to `_VALID_ENGINES`; assert LinkedIn profile/company first races contain `exa`; preserve `sum_all` billing.
- `tests/test_smoke.py`: assert registry includes `exa`.

## Documentation Updates

Update:

- `docs/README.md` engine/status tables
- `docs/workflows/development.md` environment variables table with `EXA_API_KEY`
- `docs/architecture/overview.md` LinkedIn ladder example
- `pyproject.toml` optional dependency group `exa = []` and include it in `all`

No new third-party dependency should be added.

## Non-Goals

- No Exa SDK dependency.
- No streaming support in the first pass.
- No legacy `/research/v1` support.
- No automatic Agent mode in ladders.
- No special handling for private/auth-gated LinkedIn beyond returning Exa's public search/enrichment data.

## Acceptance Criteria

- `pytest tests/test_engine_exa.py tests/test_ladders.py tests/test_smoke.py` passes.
- `./scripts/check.sh` passes before push.
- Importing `scrapefold` still works without any Exa package installed.
- Default offline tests make no live Exa API calls.
- LinkedIn profile/company ladders include Exa but still never start with `requests`.
