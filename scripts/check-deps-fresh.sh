#!/usr/bin/env bash
# check-deps-fresh.sh — prints current-pin vs. latest-stable for each
# direct dep in pyproject.toml. NOT a CI failure — a pack-opening nag.
#
# Usage: ./scripts/check-deps-fresh.sh
# Requires: python (uses stdlib only — urllib + tomllib on 3.11+, tomli on 3.10).

set -euo pipefail
cd "$(dirname "$0")/.."

python - <<'PY'
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

def latest_stable(pkg: str) -> str | None:
    """Return the latest non-pre-release version on PyPI, or None on failure."""
    try:
        with urllib.request.urlopen(f"https://pypi.org/pypi/{pkg}/json", timeout=10) as fh:
            data = json.load(fh)
    except (urllib.error.URLError, json.JSONDecodeError):
        return None
    releases = data.get("releases", {})
    stable = []
    for v, files in releases.items():
        if not files:
            continue
        if any(f.get("yanked") for f in files):
            continue
        if re.search(r"(a|b|rc|dev|alpha|beta)\d*", v, re.IGNORECASE):
            continue
        stable.append(v)
    if not stable:
        return None
    from packaging.version import Version, InvalidVersion
    try:
        return str(max((Version(v) for v in stable)))
    except InvalidVersion:
        return None

def minor_distance(pin: str, latest: str) -> int:
    """Approximate minor-version distance. Returns -1 on parse failure."""
    from packaging.version import Version, InvalidVersion
    try:
        a, b = Version(pin), Version(latest)
    except InvalidVersion:
        return -1
    if a.major != b.major:
        return (b.major - a.major) * 100 + (b.minor - a.minor)
    return b.minor - a.minor

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
