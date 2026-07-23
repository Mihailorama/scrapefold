"""Self-update helpers — ``scrapefold update``.

Keeps installs current without users tracking PyPI: ``--check`` compares the
installed version against the latest release, the default action upgrades in
place via the running interpreter's pip. Pure helpers live here so they are
unit-testable offline; the Typer wiring lives in :mod:`scrapefold.cli`.
"""

from __future__ import annotations

import re
import sys

import httpx

PYPI_JSON_URL = "https://pypi.org/pypi/scrapefold/json"


def latest_version(timeout: float = 10.0) -> str:
    """Return the latest scrapefold version published on PyPI."""
    response = httpx.get(PYPI_JSON_URL, timeout=timeout, follow_redirects=True)
    response.raise_for_status()
    return str(response.json()["info"]["version"])


def _version_tuple(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in re.findall(r"\d+", version)[:3])


def is_newer(latest: str, current: str) -> bool:
    """True when *latest* is strictly newer than *current* (numeric compare)."""
    return _version_tuple(latest) > _version_tuple(current)


def build_update_argv(extras: str | None = None) -> list[str]:
    """pip command that upgrades scrapefold in the running interpreter.

    ``extras`` is a comma-separated list (e.g. ``"mcp,firecrawl"``) baked
    into the requirement as ``scrapefold[mcp,firecrawl]``.
    """
    target = "scrapefold"
    if extras:
        cleaned = ",".join(e.strip() for e in extras.split(",") if e.strip())
        if cleaned:
            target = f"scrapefold[{cleaned}]"
    return [sys.executable, "-m", "pip", "install", "--upgrade", target]
