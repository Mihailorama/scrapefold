"""Live parity check: scrapefold engines versus a saved ai-utils output (AIDE-164).

Runs the scrapefold engine that replaces each ai-utils scraper, with the options
that reproduce ai-utils' request, and prints a field-by-field comparison against
what ai-utils returned for the same URL.

Costs money: it calls the paid vendors whose keys are in the environment
(SCRAPINGDOG_API_KEY, FIRECRAWL_API_KEY, JINA_API_KEY or JINAAI_API_KEY,
CLOUDFLARE_API_TOKEN + CLOUDFLARE_ACCOUNT_ID). Scrapling and Crawl4AI need their
extras installed (``pip install -e ".[scrapling,crawl4ai,firecrawl]"``).

1. Save ai-utils' output, one record per URL, as a JSON list in a file::

       [{"url": "https://www.linkedin.com/in/john-doe/", "engine": "scrapingdog",
         "output": "<the string ai-utils returned>"},
        {"url": "https://example.com/", "engine": "firecrawl", "whole_site": true,
         "limit": 10, "output": "..."}]

   ``engine`` is the ai-utils scraper: scrapingdog, jina, cloudflare, scrapling,
   crawl4ai or firecrawl. ``output`` is the return value of the matching
   ``services.url_to_text_service.get_content_<engine>(url)`` call (for
   Scrapingdog's LinkedIn/Amazon/Twitter endpoints that is ``str(dict)``, which
   this script parses back). From the ai-utils checkout, for example::

       python -c "import asyncio, json; from services import url_to_text_service as s; \\
       u='https://example.com/'; \\
       print(json.dumps([{'url': u, 'engine': 'jina', 'output': asyncio.run(s.get_content_jina(u))}]))" \\
       > ai_utils_output.json

2. Run, with the vendor keys exported::

       python scripts/parity_ai_utils.py ai_utils_output.json [--url URL ...] [--json]

``--url`` limits the run to those records. Exit code 1 if any record differs.
"""

from __future__ import annotations

import argparse
import ast
import asyncio
import difflib
import json
import re
import sys
from pathlib import Path
from typing import Any

from scrapefold import ScrapeOptions, crawl_site
from scrapefold.engines import get_engine
from scrapefold.ladders import classify_url

_DEDICATED = {"linkedin_profile", "linkedin_company", "linkedin_job", "amazon_product", "twitter"}
_TEXT_MATCH = 0.9  # similarity ratio at or above which two texts count as matching


def engine_and_opts(record: dict[str, Any]) -> tuple[str, ScrapeOptions]:
    """The scrapefold engine and options that reproduce ai-utils' request."""
    engine, url = record["engine"], record["url"]
    if engine == "scrapingdog":
        if classify_url(url) in _DEDICATED:
            return "scrapingdog_dedicated", ScrapeOptions()
        # ai-utils /scrape: dynamic=true, wait=5000, premium=true, country by TLD.
        return "scrapingdog", ScrapeOptions(premium_proxy=True, country=record.get("country"))
    if engine == "scrapling":
        return "scrapling_stealth", ScrapeOptions(timeout_s=30)
    if engine == "jina":
        return "jina", ScrapeOptions(timeout_s=30)
    if engine == "firecrawl" and record.get("whole_site"):
        return "firecrawl", ScrapeOptions(
            engines=("firecrawl",),
            max_pages=int(record.get("limit", 100)),
            max_depth=5,
            main_content=True,
            wait_ms=4000,
            strip_boilerplate=False,  # ai-utils does no cross-page dedup
        )
    if engine in ("cloudflare", "crawl4ai", "firecrawl"):
        return engine, ScrapeOptions()
    raise ValueError(f"unknown ai-utils engine {engine!r}")


def parse_structured(value: Any) -> Any:
    """ai-utils output as JSON data, or ``None`` when it is plain text."""
    if isinstance(value, (dict, list)):
        return value
    if not isinstance(value, str) or not value.lstrip().startswith(("{", "[")):
        return None
    for parse in (json.loads, ast.literal_eval):
        try:
            return parse(value)
        except (ValueError, SyntaxError):
            continue
    return None


def _first(data: Any) -> Any:
    return data[0] if isinstance(data, list) and data else data


def compare_fields(theirs: Any, ours: Any) -> list[dict[str, Any]]:
    """Top-level field comparison of two JSON payloads (a list compares its first item)."""
    a, b = _first(theirs), _first(ours)
    if not isinstance(a, dict) or not isinstance(b, dict):
        same = a == b
        return [{"field": "<value>", "status": "same" if same else "differs"}]
    rows = []
    for key in sorted(set(a) | set(b)):
        if key not in b:
            status = "only ai-utils"
        elif key not in a:
            status = "only scrapefold"
        else:
            status = "same" if a[key] == b[key] else "differs"
        rows.append({"field": key, "status": status})
    if isinstance(theirs, list) and isinstance(ours, list) and len(theirs) != len(ours):
        rows.append({"field": "<items>", "status": f"differs ({len(theirs)} vs {len(ours)})"})
    return rows


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def compare_text(theirs: str, ours: str) -> list[dict[str, Any]]:
    """Length, similarity and line overlap of two text outputs."""
    a, b = _norm(theirs), _norm(ours)
    ratio = difflib.SequenceMatcher(None, a, b, autojunk=False).ratio() if a or b else 1.0
    lines_a = {line.strip() for line in theirs.splitlines() if line.strip()}
    lines_b = {line.strip() for line in ours.splitlines() if line.strip()}
    union = lines_a | lines_b
    overlap = len(lines_a & lines_b) / len(union) if union else 1.0
    return [
        {"field": "length", "ai_utils": len(theirs), "scrapefold": len(ours)},
        {
            "field": "similarity",
            "value": round(ratio, 3),
            "status": "same" if ratio >= _TEXT_MATCH else "differs",
        },
        {"field": "line_overlap", "value": round(overlap, 3)},
    ]


async def run_record(record: dict[str, Any]) -> dict[str, Any]:
    url = record["url"]
    engine, opts = engine_and_opts(record)
    report: dict[str, Any] = {"url": url, "ai_utils_engine": record["engine"], "engine": engine}
    try:
        if record["engine"] == "firecrawl" and record.get("whole_site"):
            crawl = await crawl_site(url, opts)
            ours: Any = "".join(page.markdown for page in crawl.pages)
            ours_json = None
        else:
            result = await get_engine(engine)().scrape(url, opts)
            ours, ours_json = result.markdown, result.json
    except Exception as exc:  # report every failure, keep going
        report.update(error=f"{type(exc).__name__}: {exc}", rows=[], match=False)
        return report

    theirs_json = parse_structured(record["output"])
    if theirs_json is not None or ours_json is not None:
        rows = compare_fields(theirs_json, ours_json)
    else:
        rows = compare_text(str(record["output"]), ours)
    report["rows"] = rows
    report["match"] = all(r.get("status", "same") == "same" for r in rows)
    return report


def _print(report: dict[str, Any]) -> None:
    verdict = "MATCH" if report["match"] else "DIFF"
    print(
        f"\n[{verdict}] {report['url']}  ai-utils={report['ai_utils_engine']} -> {report['engine']}"
    )
    if "error" in report:
        print(f"  error: {report['error']}")
    for row in report["rows"]:
        rest = ", ".join(f"{k}={v}" for k, v in row.items() if k != "field")
        print(f"  {row['field']}: {rest}")


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("ai_utils_output", type=Path, help="saved ai-utils output JSON list")
    parser.add_argument("--url", action="append", help="only these URLs (repeatable)")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args()

    records = json.loads(args.ai_utils_output.read_text(encoding="utf-8"))
    if args.url:
        records = [r for r in records if r["url"] in set(args.url)]
    if not records:
        print("no records to compare", file=sys.stderr)
        return 2

    reports = [await run_record(r) for r in records]
    if args.json:
        print(json.dumps(reports, ensure_ascii=False, indent=2))
    else:
        for report in reports:
            _print(report)
    return 0 if all(r["match"] for r in reports) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
