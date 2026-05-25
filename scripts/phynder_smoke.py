"""Live network smoke test of scrapefold against real downstream-consumer targets.

Runs single-URL scrape() and crawl_site() against a curated set of Russian
sports federation / club websites of varying complexity. Writes a markdown
report to /tmp/downstream-consumer_smoke_report.md.

Costs money if engines beyond `requests` get triggered (Firecrawl, ScrapingBee,
etc.). Pass --free-only to constrain to free engines.

Usage:
    python scripts/downstream-consumer_smoke.py [--free-only] [--max-pages N] [--target URL ...]
"""

from __future__ import annotations

import argparse
import asyncio
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path

import scrapefold
from scrapefold import ScrapeOptions

# Targets chosen for architecture/complexity diversity from downstream-consumer's real workload.
TARGETS = [
    # (root_url, label, notes)
    ("https://example.com/", "example-site", "Mid-size football club, likely static CMS"),
    ("https://example.com/", "example-site", "Major hockey league, Cloudflare-protected"),
    ("https://example.com/", "example-site", "Major football club, heavy site"),
    ("https://example.com/", "example-site", "Basketball league, modern SPA"),
    ("https://example.com/", "example-site", "federation"),
    ("https://example.com/", "example-site", "Volleyball federation, well-structured"),
]


@dataclass
class Step:
    label: str
    url: str
    kind: str  # "scrape" | "crawl"
    ok: bool = False
    engine: str | None = None
    elapsed_s: float = 0.0
    bytes_md: int = 0
    pages: int = 0
    failures: list[str] = field(default_factory=list)
    error: str | None = None


async def run_scrape(url: str, label: str, opts: ScrapeOptions, timeout_s: float) -> Step:
    step = Step(label=label, url=url, kind="scrape")
    t0 = time.monotonic()
    try:
        res = await asyncio.wait_for(scrapefold.scrape(url, opts=opts), timeout=timeout_s)
        step.ok = True
        step.engine = res.engine
        step.bytes_md = len((res.markdown or "").encode("utf-8"))
    except asyncio.TimeoutError:
        step.error = f"timeout after {timeout_s}s"
    except Exception as exc:
        step.error = f"{type(exc).__name__}: {exc}"
    step.elapsed_s = time.monotonic() - t0
    return step


async def run_crawl(url: str, label: str, opts: ScrapeOptions, timeout_s: float) -> Step:
    step = Step(label=label, url=url, kind="crawl")
    t0 = time.monotonic()
    try:
        res = await asyncio.wait_for(
            scrapefold.crawl_site(url, opts=opts),
            timeout=timeout_s,
        )
        step.ok = True
        step.pages = len(res.pages)
        step.bytes_md = sum(len((p.markdown or "").encode("utf-8")) for p in res.pages)
        step.failures = list(res.failures)
    except asyncio.TimeoutError:
        step.error = f"timeout after {timeout_s}s"
    except Exception as exc:
        step.error = f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}"
    step.elapsed_s = time.monotonic() - t0
    return step


def _fmt_step(s: Step) -> str:
    if s.ok:
        if s.kind == "scrape":
            return f"OK engine={s.engine} md={s.bytes_md}B time={s.elapsed_s:.1f}s"
        else:
            fails = f" failures={len(s.failures)}" if s.failures else ""
            return f"OK pages={s.pages} md_total={s.bytes_md}B time={s.elapsed_s:.1f}s{fails}"
    return f"FAIL ({s.elapsed_s:.1f}s) — {s.error}"


def write_report(steps: list[Step], path: Path) -> None:
    lines = [
        "# scrapefold ↔ downstream-consumer smoke test",
        "",
        f"Run at {time.strftime('%Y-%m-%d %H:%M:%S')}.",
        "",
        "## Single-URL scrape",
        "",
        "| Target | URL | Result |",
        "|---|---|---|",
    ]
    for s in steps:
        if s.kind == "scrape":
            lines.append(f"| {s.label} | {s.url} | {_fmt_step(s)} |")
    lines += [
        "",
        "## Whole-site crawl",
        "",
        "| Target | Root | Result |",
        "|---|---|---|",
    ]
    for s in steps:
        if s.kind == "crawl":
            lines.append(f"| {s.label} | {s.url} | {_fmt_step(s)} |")

    # Per-target detail section
    lines += ["", "## Per-target detail", ""]
    by_label: dict[str, list[Step]] = {}
    for s in steps:
        by_label.setdefault(s.label, []).append(s)
    for label, group in by_label.items():
        lines.append(f"### {label}")
        for s in group:
            lines.append(f"- **{s.kind}** {_fmt_step(s)}")
            if s.failures:
                for f in s.failures[:5]:
                    lines.append(f"  - failure: {f}")
                if len(s.failures) > 5:
                    lines.append(f"  - ... {len(s.failures) - 5} more")
            if s.error:
                lines.append(f"  - error head: {s.error.splitlines()[0]}")
        lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--free-only", action="store_true", help="Restrict engines to requests-only"
    )
    ap.add_argument("--max-pages", type=int, default=5, help="Pages per crawl (default 5)")
    ap.add_argument(
        "--scrape-timeout", type=float, default=60.0, help="Per-scrape timeout (s)"
    )
    ap.add_argument(
        "--crawl-timeout", type=float, default=300.0, help="Per-crawl timeout (s)"
    )
    ap.add_argument(
        "--target",
        action="append",
        default=None,
        help="Override TARGETS — pass URL repeatedly to test specific sites",
    )
    ap.add_argument(
        "--out",
        type=Path,
        default=Path("/tmp/downstream-consumer_smoke_report.md"),
        help="Markdown report path",
    )
    args = ap.parse_args()

    targets = (
        [(u, u.split("//")[-1].split("/")[0], "custom") for u in args.target]
        if args.target
        else TARGETS
    )

    engines = ("requests",) if args.free_only else None

    print(f"scrapefold {scrapefold.__version__} — {len(targets)} targets")
    print(f"engines={engines or 'auto (full ladder)'}  max_pages={args.max_pages}")
    print(f"report → {args.out}")
    print()

    steps: list[Step] = []
    for root, label, notes in targets:
        print(f"=== {label} ({root}) — {notes}")

        # Single-URL scrape on the root
        scrape_opts = ScrapeOptions(engines=engines)
        s_scrape = await run_scrape(root, label, scrape_opts, args.scrape_timeout)
        steps.append(s_scrape)
        print(f"  scrape: {_fmt_step(s_scrape)}")

        # Whole-site crawl with sitemap → BFS discovery
        crawl_opts = ScrapeOptions(
            engines=engines, max_pages=args.max_pages, max_depth=2
        )
        s_crawl = await run_crawl(root, label, crawl_opts, args.crawl_timeout)
        steps.append(s_crawl)
        print(f"  crawl:  {_fmt_step(s_crawl)}")
        print()

        write_report(steps, args.out)  # incremental: report grows as we go

    n_scrape = sum(1 for s in steps if s.kind == "scrape")
    n_scrape_ok = sum(1 for s in steps if s.kind == "scrape" and s.ok)
    n_crawl = sum(1 for s in steps if s.kind == "crawl")
    n_crawl_ok = sum(1 for s in steps if s.kind == "crawl" and s.ok)
    total_pages = sum(s.pages for s in steps if s.kind == "crawl")

    print()
    print(f"DONE  scrape: {n_scrape_ok}/{n_scrape}  crawl: {n_crawl_ok}/{n_crawl}")
    print(f"      total pages fetched across crawls: {total_pages}")
    print(f"      report: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
