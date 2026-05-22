---
purpose: "Invariants — things contributors and AI agents must never violate."
updated: "2026-05-22"
related:
  - ../architecture/overview.md
  - ../workflows/testing.md
  - ../../CONTRIBUTING.md
---

# Golden rules

These are the non-negotiable invariants of scrapefold. Read before touching code.

### Rule: One unified options schema
- **What:** Every engine accepts the same `ScrapeOptions` dataclass. No engine takes bespoke kwargs.
- **Why:** The whole reason this library exists is to give one parameter surface across 16 vendors. Bespoke kwargs reintroduce the problem we're solving.
- **Do:** `await engine.scrape(url, opts)`
- **Don't:** `await engine.scrape(url, render_js=True, country="ru")`

### Rule: Engines drop unsupported options, never raise
- **What:** If `ScrapeOptions.stealth=True` is passed to Jina (which has no stealth concept), the engine logs DEBUG and proceeds.
- **Why:** Same call site must work for any engine in a fallback chain.
- **Do:** `SUPPORTED_OPTIONS` declares what the engine honors; base class strips the rest.
- **Don't:** `raise ValueError("stealth not supported by Jina")` inside `_fetch`.

### Rule: No vendor LLM SDK in the library
- **What:** scrapefold never imports `openai`, `anthropic`, `litellm`, or similar. LLM judging in `parallel.py` and vision analysis in `vision.py` accept a user-provided async callable.
- **Why:** Provider-agnostic. Users plug in their own LLM (claude-cli subprocess, Anthropic SDK, OpenAI SDK, …).
- **Do:** `async def my_llm(prompt: str) -> str: ...; await scrape(url, llm_judge=my_llm)`
- **Don't:** `import openai` anywhere in `src/scrapefold/`.

### Rule: Engines are lazy-imported
- **What:** Importing `scrapefold` must not fail if Selenium / Scrapling / firecrawl-py are missing.
- **Why:** Users install only the extras they need. A missing extra should only error when that specific engine is requested.
- **Do:** Register engines via `_REGISTRY[name] = lambda: __import__("scrapefold.engines.foo", ...)` in `engines/__init__.py`.
- **Don't:** `from selenium import webdriver` at module top-level of `engines/selenium.py` — it should be inside the class or function.

### Rule: One file per engine
- **What:** Each engine lives in exactly one `src/scrapefold/engines/<name>.py`. No shared "scraping_helpers" file.
- **Why:** Engines are independent units of porting / review / blame. Sharing inhibits this.
- **Do:** Duplicate a 3-line URL-normalization helper inside two engines if needed.
- **Don't:** Create `engines/_shared.py` that several engines import.

### Rule: ScrapeResult is the only return type
- **What:** Engines return `ScrapeResult`. Not dict, not tuple, not raw HTML.
- **Why:** Routers, parallel-orchestrators, and cache assume the shape.
- **Do:** `return ScrapeResult(url=..., text=..., markdown=..., html=..., engine=self.NAME, elapsed_ms=0)` (base class fills elapsed).
- **Don't:** `return text, status_code`.

### Rule: All four format slots are populated when achievable
- **What:** `ScrapeResult.text` and `ScrapeResult.markdown` are **always** non-empty when the scrape succeeds (post-converted from whichever native form). `html` is set when the engine returned HTML. `json` is set only when the engine produced structured data natively (Firecrawl `/extract`, AnySite, Apify actors).
- **Why:** Downstream callers must be able to ask `res.markdown` without having to know which engine ran. The cost of running html2text once inside an engine is negligible compared to a network call.
- **Do:** Inside `_fetch`, if the vendor returned HTML, run `scrapefold.html_to_text.to_markdown(html)` and fill both `text` and `markdown` before constructing the result.
- **Don't:** Return `ScrapeResult(text="", markdown="", html=raw, ...)` and expect the caller to convert.

### Rule: No print(); use the stdlib `logging` module
- **What:** All diagnostics go through `logging.getLogger(__name__)`. CLI output is the one exception (use Typer's `echo`).
- **Why:** Library code is consumed programmatically; print() corrupts stdout for callers.

### Rule: Async-everywhere
- **What:** Engines' `_fetch`, `scrape()`, `crawl_site()` are all `async`. Use `httpx.AsyncClient`, `asyncio.gather`, etc.
- **Why:** Crawl-many-pages performance and parallel-engine orchestration require it.
- **Don't:** `requests.get()` inside an engine. Use `httpx`.

### Rule: Escalate per site class, stop at first good response
- **What:** The router classifies a URL into a `SiteClass` (LinkedIn family, Amazon, Cloudflare-protected, …), then walks the per-class ladder declared in `src/scrapefold/ladders.py`. Each step is either a `SequentialStep` (one engine) or a `RaceStep` (multiple engines, explicit `winner_policy`/`cancel_policy`/`budget_accounting`). It stops escalating as soon as a step returns a non-suspicious response.
- **Why:** A universal T0-T5 chain wastes calls — LinkedIn pages 403 on plain HTTP and need a specialized vendor; CDN-protected pages need a stealth browser before any paid fan-out is worthwhile. Per-class ladders encode that knowledge.
- **Do:** Use the router default — `await scrape(url)`. Per-call overrides via `opts.extra["max_cost_usd"]`, `["max_engines"]`, `["policy"]`.
- **Don't:** Hardcode `opts.engines=["brightdata_unlocker_sync"]` "to be safe" on every URL. That's exactly the overkill pattern per-class ladders exist to prevent.

### Rule: Ladders are data, not router convention
- **What:** Race semantics (winner, cancel, budget accounting) live on `RaceStep` fields, not in the router. The router consumes pure functions `is_step_allowed(step, policy)` and `check_budget(step, walk, ...)` and never inspects step subclass beyond the sum-type dispatch.
- **Why:** Codex round-2 review (R2-C1) flagged ambiguous race semantics when they were router-implicit. Making them data lets reviewers verify ladder behavior by reading the ladder declaration alone.
- **Do:** When a new race needs different budget accounting, set `budget_accounting="sum_all"` on the `RaceStep` and add a test pinning it.
- **Don't:** Add `if step is paid_race_step: bill_all_engines()` to the router. Pin it on the step.

### Rule: Multi-mode engines register as distinct names
- **What:** Engines with multiple operating modes (Bright Data Unlocker async/sync, Scrapling stealth/fast) register as separate engines: `brightdata_unlocker_sync`, `brightdata_unlocker_async`, `scrapling_stealth`, `scrapling_fast`. User-facing aliases (`opts.engines=["scrapling"]`) live in `ENGINE_ALIASES` and resolve at registry lookup.
- **Why:** `WalkBudget.engines_tried` is a set of names — name-based dedup must be unambiguous (Codex R2-NEW-2).
- **Do:** `register("scrapling_stealth", _load); register_alias("scrapling", "scrapling_stealth")`.
- **Don't:** Take a `mode="stealth"` kwarg at engine construction and reuse the same `NAME`.

### Rule: New URL pattern → new `GOLDEN_CORPUS` row
- **What:** Adding an entry to `URL_PATTERNS` in `ladders.py` requires adding at least one matching row to `GOLDEN_CORPUS`. The test `test_every_url_pattern_class_has_corpus_row` enforces coverage; `test_url_classification_golden_corpus` enforces regex-order safety.
- **Why:** Specific patterns (e.g. `linkedin\.com/sales/`) must beat general ones (`linkedin\.com/in/`). A reorder regression breaks one corpus row — the unit-test failure is precise and self-explanatory.
- **Do:** Open `ladders.py`, append a `re.compile(...)` row, append a `(url, class)` row to `GOLDEN_CORPUS`, run tests.
- **Don't:** Add the pattern without a corpus row and rely on manual testing.

### Rule: Suspicious-content detection lives in one place
- **What:** The heuristic "did this scrape actually work?" lives in `scrapefold/detection.py`, not duplicated in every engine.
- **Why:** Engines must not decide on their own whether to escalate. The router owns escalation; engines just report what they got.
- **Do:** Engines return whatever they got, even if it's a captcha page. The router calls `detection.is_suspicious(result)` and decides.
- **Don't:** Add `if "Just a moment" in text: raise` inside an engine's `_fetch`.

### Rule: Tests run offline by default
- **What:** Default `pytest` invocation makes no network calls and uses no real API keys.
- **Why:** CI must be deterministic and free.
- **Do:** Mark live calls with `@pytest.mark.network` or `@pytest.mark.paid`.
- **Don't:** Hit `https://example.com` from a default-marker test.

### Rule: Two Dockerfile pins (downstream consumers) stay in sync
- **What:** If a downstream like downstream-consumer pins scrapefold in multiple Dockerfiles, those pins must match. A `scripts/check.sh` lint enforces this on the downstream side.
- **Why:** Drift between OCR-worker and web-server caused real prod incidents in sibling projects. We avoid the same trap from the start.

### Rule: Never push without tests + lint passing
- **What:** `./scripts/check.sh` exit 0 is a precondition for `git push`.
- **Why:** Trusted-publishing on `v*` tag means a green main branch is what reaches PyPI.
