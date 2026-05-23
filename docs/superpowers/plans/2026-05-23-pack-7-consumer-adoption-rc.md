# Pack 7 — Consumer adoption RC (cross-repo)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to coordinate the cross-repo migration. Pack 7 is mostly checklist + parity testing, not new code in scrapefold itself.

**Goal:** Tag `v0.1.0rc1`, then migrate `downstream-consumer` and `downstream-consumer` onto it. Run a one-week soak window. Bugfix-only Pack 7.x → `rc2`, `rc3` as consumer regressions land. Exit when 7 calendar days pass with zero scrapefold-attributable issues OR both consumer maintainers explicitly approve.

**Architecture:** Three sub-phases. **A.** Tag `v0.1.0rc1` from the head of Pack 6. **B.** Migrate `downstream-consumer` (3 PRs). **C.** Migrate `downstream-consumer` (4 PRs). Each consumer PR commits a parity-replay test corpus before deletion of old code. Issues found land as Pack 7.x patches in scrapefold and bump the rc.

**Spec reference:** `docs/superpowers/specs/2026-05-23-v0.1.0-stable-roadmap-design.md` §3.5 + §5.

---

## Phase A — Tag `v0.1.0rc1`

### Task A.1 — Cut the rc

- [ ] **Step 1:** In `scrapefold`, bump `__version__` to `0.1.0rc1`.
- [ ] **Step 2:** Move `[Unreleased]` → `[0.1.0rc1] — <date>` in `CHANGELOG.md`. Open a new empty `[Unreleased]`. Add compare-link.
- [ ] **Step 3:** Run `./scripts/check.sh` → green.
- [ ] **Step 4:** Commit:

```bash
git add src/scrapefold/__init__.py CHANGELOG.md
git commit -m "chore: cut v0.1.0rc1 — consumer adoption window opens"
```

- [ ] **Step 5: ASK Mike** before pushing + tagging. If approved:

```bash
git push origin main
git tag -a v0.1.0rc1 -m "v0.1.0 release candidate 1 — consumer adoption"
git push origin v0.1.0rc1
```

Wait for trusted-publishing CI to push to PyPI. Verify with:

```bash
pip index versions scrapefold
```

Expected: `0.1.0rc1` listed.

---

## Phase B — downstream-consumer migration (3 PRs)

**Repo:** `/Users/m/code/mihailorama/downstream-consumer` (separate from scrapefold).

### Task B.1 — Pin and parity corpus

- [ ] **Step 1:** In `downstream-consumer/requirements.txt` (or `pyproject.toml`), pin `scrapefold==0.1.0rc1` (exact, no caret).
- [ ] **Step 2:** Create `downstream-consumer/tests/scrapefold_parity/` directory.
- [ ] **Step 3:** Create `downstream-consumer/tests/scrapefold_parity/corpus.txt` — 200 URLs from the sponsorship audit corpus (collect via `git log -p --name-only` on `enrich_from_site.py` history or recent batch runs).
- [ ] **Step 4:** Write `downstream-consumer/tests/scrapefold_parity/test_parity.py` that runs each URL through (a) the **current** `markdown_fetch.fetch_markdown` and (b) `scrapefold.scrape(...)`, asserts `extract_role_pairs(md_old) == extract_role_pairs(md_new)`, and measures cache-hit-rate over a second pass.
- [ ] **Step 5:** Commit on a branch `migration/scrapefold-rc1`.

### Task B.2 — PR 1: `markdown_fetch.fetch_markdown` body replacement

- [ ] **Step 1:** Replace the body of `downstream-consumer/scripts/sponsorship_audit/markdown_fetch.py:fetch_markdown` with:

```python
from scrapefold import scrape, ScrapeOptions, AllEnginesFailed

async def fetch_markdown(url, cache_dir, timeout=60, render_js=True, logger=None):
    opts = ScrapeOptions(
        render_js=render_js,
        timeout_s=timeout,
        extra={"cache_dir": str(cache_dir), "cache_ttl_days": 7},
    )
    try:
        result = await scrape(url, opts)
    except AllEnginesFailed as exc:
        if logger:
            logger.warning("scrapefold fetch(%s) failed: %s", url, exc)
        return None
    return result.markdown
```

(Outer function signature is preserved — callers don't change. If the existing function is sync, wrap with `asyncio.run` or refactor callers to await; pick one based on the codebase's current async posture.)

- [ ] **Step 2:** Run `downstream-consumer/tests/scrapefold_parity/test_parity.py` — assert ≤ 5% delta on `extract_role_pairs` tuples.
- [ ] **Step 3:** Open PR 1 with the parity test green, request review.

### Task B.3 — PR 2: og:meta fallback removal

- [ ] **Step 1:** In `downstream-consumer/scripts/sponsorship_audit/enrich_from_site.py`, delete `_og_scrape(url)` and the `OG_ONLY_HOSTS` gate.
- [ ] **Step 2:** Replace its call sites with a direct `scrapefold.scrape(url, opts)` call.
- [ ] **Step 3:** Add Telegram (`t.me/spartakmoscow`) and VK (`vk.com/cska_official`) to the parity corpus. Run parity tests.
- [ ] **Step 4:** If `scrapefold.classify_url("t.me/...")` returns the wrong class, **stop**: file a scrapefold issue and a Pack 7.x patch. Do NOT add a downstream-consumer-side workaround.
- [ ] **Step 5:** Open PR 2 after corpus is green.

### Task B.4 — PR 3: Cache code removal

- [ ] **Step 1:** Delete `_cache_path`, `_load_cached`, `_save_cached` from both `markdown_fetch.py` and `enrich_from_site.py`.
- [ ] **Step 2:** Cache is now scrapefold's job (configured via `opts.extra["cache_dir"]`).
- [ ] **Step 3:** Run parity tests — cache-hit-rate delta should be within 5%.
- [ ] **Step 4:** Open PR 3.

### Task B.5 — downstream-consumer soak

- [ ] **Step 1:** All 3 PRs merged.
- [ ] **Step 2:** Run downstream-consumer in its normal workload for 1 week.
- [ ] **Step 3:** Track any scrapefold-attributable regression. File as scrapefold issue → land Pack 7.x patch → rc bump → downstream-consumer re-pin.

---

## Phase C — downstream-consumer migration (4 PRs)

**Repo:** `/Users/m/code/downstream-consumer/downstream-consumer`. Larger surface; expect 12+ call sites touched.

### Task C.1 — Pin and parity corpus

- [ ] **Step 1:** Pin `scrapefold==0.1.0rc1` (or current rc).
- [ ] **Step 2:** Create `downstream-consumer/tests/scrapefold_parity/` with a 50-page-crawl corpus (Firecrawl `entireWebsite=True` recent runs).
- [ ] **Step 3:** Write a parity harness that compares old code vs. new code for both single-URL and multi-page-crawl paths.

### Task C.2 — PR 1: Collapse 8 `get_content_X` functions

- [ ] **Step 1:** In `services/url_to_text_service.py`, replace the bodies of `get_content_firecrawl`, `get_content_scrapingbee`, `get_content_selenium`, `get_content_jina`, `get_content_scrapingdog`, `get_content_cloudflare`, `get_content_scrapling`, `get_content_crawl4ai`, `get_content_outscraper` with one shared implementation:

```python
async def get_content_via_scrapefold(url, engine_name, sourceUrl="", include_external_links=False, **engine_specific_kwargs):
    from scrapefold import scrape, ScrapeOptions, AllEnginesFailed
    opts = ScrapeOptions(engines=[engine_name], extra={"source_url": sourceUrl})
    try:
        result = await scrape(url, opts)
    except AllEnginesFailed:
        return ""  # marker-string purge happens in PR 2
    return result.markdown
```

- [ ] **Step 2:** Each `get_content_X` becomes a one-liner: `return await get_content_via_scrapefold(url, "<engine>", ...)`. Vendor-specific kwargs (e.g. `take_screenshot`) flow through `opts.extra` until v0.2.0 ships dedicated options.
- [ ] **Step 3:** Run parity harness; assert ≤ 5% byte-delta on `result.text`. Open PR 1.

### Task C.3 — PR 2: Marker-string purge

- [ ] **Step 1:** In `services/process_file_to_array_service.py`, delete the 8 marker constants (lines 39-43) and `_is_scraping_failed()` helper.
- [ ] **Step 2:** Replace the chained `if html_text == "Frc failed": …` cascade (lines 448-465) with:

```python
from scrapefold import scrape, ScrapeOptions, AllEnginesFailed

try:
    result = await scrape(url, ScrapeOptions())
    html_text = result.markdown
except AllEnginesFailed:
    html_text = None
```

- [ ] **Step 3:** Update any caller that branched on marker-string values to branch on `None` instead.
- [ ] **Step 4:** Open PR 2 with parity green.

### Task C.4 — PR 3: Per-domain mapping review

- [ ] **Step 1:** Review `website_scraper_mapping` dict entry by entry.
- [ ] **Step 2:** For each entry:
  - Add the URL to scrapefold's `GOLDEN_CORPUS` (via a scrapefold patch PR).
  - Run `scrapefold.classify_url(url)` → verify the routed engine matches what the mapping was forcing.
  - If yes → **delete the entry**.
  - If no → **keep as one-line override**: `opts.engines=["forced_engine"]` with a comment.
- [ ] **Step 3:** Hard cap: ≤ 3 surviving overrides. If more, that's a ladder bug → patch scrapefold (Pack 7.x).
- [ ] **Step 4:** Open PR 3 with parity green.

### Task C.5 — PR 4: `crawl_site` adoption

- [ ] **Step 1:** Replace the `entireWebsite=True` Firecrawl path in `process_file_to_array_service.py` with:

```python
from scrapefold import crawl_site, ScrapeOptions

result_path = await crawl_site(
    url,
    opts=ScrapeOptions(max_pages=urls_limiter_firecrawl),
    output=Path(local_output_dir) / "site.md",
)
extracted_text = result_path.read_text()
```

- [ ] **Step 2:** Run 50-page-crawl parity corpus.
- [ ] **Step 3:** Open PR 4.

### Task C.6 — downstream-consumer soak

- [ ] All 4 PRs merged → downstream-consumer runs on rc1 for 1 calendar week at normal request volume.
- [ ] Track scrapefold-attributable regressions → Pack 7.x patches.

---

## Phase D — Pack 7.x bugfix loop (scrapefold side)

Whenever a consumer regression lands:

- [ ] **Step 1:** File the issue in scrapefold with reproducing test case.
- [ ] **Step 2:** Write the failing test (add to `tests/<area>.py`).
- [ ] **Step 3:** Land the fix as a new commit on `main`.
- [ ] **Step 4:** Bump `__version__` to next rc (`0.1.0rc2`, `0.1.0rc3`, …).
- [ ] **Step 5:** Roll CHANGELOG (`[0.1.0rcN] — <date>` with one-bullet description).
- [ ] **Step 6:** ASK Mike → push + tag → consumers re-pin.

**No new features in this window.** Anything that isn't "consumer-blocking regression fix" goes on the v0.2.0 milestone.

---

## Phase E — Soak exit criteria

Pack 7 closes when **either**:

- **(a)** 7 calendar days pass with zero scrapefold-attributable regressions in either consumer, **or**
- **(b)** both consumer maintainers explicitly approve the current rc as "production-quality."

When closed:
- [ ] Tag `v0.1.0rc<N>` is identified as the "release candidate."
- [ ] Pack 8 opens.

---

## Self-review

**Spec coverage** (§3.5 + §5):
- Tag `v0.1.0rc1` after Pack 6 → Phase A ✅
- downstream-consumer migration PR 1/2/3 → Tasks B.2/3/4 ✅
- downstream-consumer migration PR 1/2/3/4 → Tasks C.2/3/4/5 ✅
- 1-week soak per consumer → Tasks B.5, C.6 ✅
- Bugfix-only Pack 7.x → rc bumps → Phase D ✅
- ≤ 3 surviving per-domain overrides hard cap → Task C.4 ✅
- Per-domain entries added to GOLDEN_CORPUS → Task C.4 ✅
- Exit: 7 days zero regressions OR maintainer approval → Phase E ✅

**No placeholders.** Cross-repo PR code blocks show actual replacement code; parity tests are concretely defined.

**Type consistency.** `scrapefold.scrape(url, opts)` → `ScrapeResult` with `.markdown`. `scrapefold.crawl_site(url, opts, output)` → `Path`. `AllEnginesFailed` is the documented exception. All match the spec and Packs 3/4/5/6.

**Deferred to v0.2.0** (per spec §5.2):
- Selenium pagination (`max_iterations`) — downstream-consumer keeps its standalone code.
- Screenshot capture in `ScrapeOptions`.
- Parallel LLM-judge merge (`opts.parallel=True`) — downstream-consumer keeps its current code.
- ScrapingBee `extract_rules` link extraction — downstream-consumer does it via `result.html` + bs4 in caller.
