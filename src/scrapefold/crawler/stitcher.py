"""Stitcher — write a list of ScrapeResults into a single multi-section .md file."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import yaml

from scrapefold.result import ScrapeResult


def _front_matter(result: ScrapeResult) -> str:
    meta = {
        "url": result.url,
        "engine": result.engine,
        "elapsed_ms": result.elapsed_ms,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }
    return "---\n" + yaml.safe_dump(meta, sort_keys=True) + "---\n"


def write_stitched(results: list[ScrapeResult], output: Path) -> Path:
    """Write *results* to *output* as one markdown file with per-page front-matter.

    Empty results produce an empty file. Parent directories are created as
    needed. Returns the output path.
    """
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)

    if not results:
        output.write_text("")
        return output

    sections: list[str] = []
    for r in results:
        sections.append(_front_matter(r) + "\n" + r.markdown.rstrip() + "\n")

    output.write_text("\n".join(sections))
    return output


__all__ = ["write_stitched"]
