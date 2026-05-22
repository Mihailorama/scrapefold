"""scrapefold MCP server entry point.

Scaffold (S1) — full tool/resource implementation lands in S10. Requires
the ``mcp`` optional extra:

    pip install "scrapefold[mcp]"

Exposed once implemented:
    - tool: scrape_url
    - tool: crawl_site
    - tool: list_engines
    - tool: inspect_options
    - resource: scrapefold://cache/{sha}
    - resource: scrapefold://engines
"""

from __future__ import annotations

import sys


def main() -> None:
    """Console-script entry point for ``scrapefold-mcp``."""
    print(
        "scrapefold-mcp: scaffold only — full server lands in S10 "
        "(see docs/architecture/overview.md).",
        file=sys.stderr,
    )
    sys.exit(2)


if __name__ == "__main__":
    main()
