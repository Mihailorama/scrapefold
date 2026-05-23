#!/usr/bin/env bash
# check-deps-fresh.sh — prints current-pin vs. latest-stable for each
# direct dep in pyproject.toml. NOT a CI failure — a pack-opening nag.
#
# Usage: ./scripts/check-deps-fresh.sh
# Requires: python (uses stdlib only — urllib + tomllib on 3.11+, tomli on 3.10).

set -euo pipefail
cd "$(dirname "$0")/.."

python3 - <<'PY'
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

try:
    import tomllib  # 3.11+
except ImportError:
    import tomli as tomllib  # 3.10 fallback

pyproject = tomllib.loads(Path("pyproject.toml").read_text())

# Collect every direct dep, base + extras + test
deps: dict[str, str] = {}
for spec in pyproject["project"].get("dependencies", []):
    m = re.match(r"^([A-Za-z0-9_\-.]+)(?:\[[^\]]+\])?\s*(>=|==|>|~=)?\s*([0-9A-Za-z.\-]+)?", spec)
    if m and m.group(3):
        deps[m.group(1)] = m.group(3)
for extra, specs in pyproject["project"].get("optional-dependencies", {}).items():
    for spec in specs:
        if spec.startswith("scrapefold["):
            continue
        m = re.match(r"^([A-Za-z0-9_\-.]+)(?:\[[^\]]+\])?\s*(>=|==|>|~=)?\s*([0-9A-Za-z.\-]+)?", spec)
        if m and m.group(3):
            deps[m.group(1)] = m.group(3)


def _parse_version(v: str) -> tuple[int, ...]:
    """Parse a version string into a tuple of ints, ignoring non-integer segments.

    Examples:
        "1.2.3"    -> (1, 2, 3)
        "1.2.3a1"  -> ()  — pre-release, caller should discard
        "2.0"      -> (2, 0)
    """
    parts = v.split(".")
    result = []
    for part in parts:
        if part.isdigit():
            result.append(int(part))
        else:
            # Any non-pure-integer segment (rc1, a1, dev0, post1 …) stops parsing
            # but we still return what we have so far only if nothing odd preceded.
            # Simpler: return empty to signal "not a clean release".
            return ()
    return tuple(result)


def _is_stable(v: str) -> bool:
    """Return True if the version string contains no pre/dev/post markers."""
    return not re.search(r"(a|b|rc|dev|alpha|beta|post)\d*", v, re.IGNORECASE)


def latest_stable(pkg: str) -> str | None:
    """Return the latest non-pre-release version on PyPI, or None on failure."""
    try:
        with urllib.request.urlopen(f"https://pypi.org/pypi/{pkg}/json", timeout=10) as fh:
            data = json.load(fh)
    except (urllib.error.URLError, json.JSONDecodeError):
        return None
    releases = data.get("releases", {})
    stable: list[tuple[tuple[int, ...], str]] = []
    for v, files in releases.items():
        if not files:
            continue
        if any(f.get("yanked") for f in files):
            continue
        if not _is_stable(v):
            continue
        parsed = _parse_version(v)
        if not parsed:
            continue
        stable.append((parsed, v))
    if not stable:
        return None
    # max() on (tuple, str) compares tuples lexicographically — correct for semver
    return max(stable)[1]


def minor_distance(pin: str, latest: str) -> int:
    """Approximate minor-version distance. Returns -1 on parse failure."""
    a = _parse_version(pin)
    b = _parse_version(latest)
    if not a or not b:
        return -1
    # Pad to equal length
    length = max(len(a), len(b))
    a = a + (0,) * (length - len(a))
    b = b + (0,) * (length - len(b))
    if a[0] != b[0]:
        # Cross-major: express as 100*major_diff + minor_diff
        minor_a = a[1] if len(a) > 1 else 0
        minor_b = b[1] if len(b) > 1 else 0
        return (b[0] - a[0]) * 100 + (minor_b - minor_a)
    if len(a) < 2:
        return 0
    return b[1] - a[1]


print(f"{'package':<25} {'pinned (>=)':<15} {'latest stable':<15} staleness")
print(f"{'-'*25} {'-'*15} {'-'*15} ---------")
for pkg in sorted(deps):
    pin = deps[pkg]
    latest = latest_stable(pkg)
    if latest is None:
        print(f"{pkg:<25} {pin:<15} {'(query failed)':<15} ?")
        continue
    dist = minor_distance(pin, latest)
    marker = "" if dist <= 0 else f"  ←  {dist} minor(s) behind"
    print(f"{pkg:<25} {pin:<15} {latest:<15} {marker}")
PY
