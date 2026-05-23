#!/usr/bin/env bash
# check-changelog.sh — fails if a PR touches src/scrapefold/ without
# adding a line under [Unreleased] in CHANGELOG.md.
#
# Usage: ./scripts/check-changelog.sh [base-ref]
# Default base-ref is origin/main.

set -euo pipefail
cd "$(dirname "$0")/.."

BASE_REF="${1:-origin/main}"

# Files changed on this branch (added, modified, copied, renamed)
CHANGED=$(git diff --name-only --diff-filter=ACMR "${BASE_REF}...HEAD")

# If no src/ change, we don't require a CHANGELOG entry
if ! echo "$CHANGED" | grep -q '^src/scrapefold/'; then
    echo "check-changelog: no src/scrapefold/ change, nothing to gate"
    exit 0
fi

# Pull the [Unreleased] section diff
DIFF=$(git diff "${BASE_REF}...HEAD" -- CHANGELOG.md)
if ! echo "$DIFF" | grep -q '^+'; then
    echo "check-changelog: FAIL — src/scrapefold/ changed but CHANGELOG.md has no added lines on this branch"
    echo "  Add at least one bullet under ## [Unreleased] in CHANGELOG.md"
    exit 1
fi

# Confirm the added line is inside or below [Unreleased]
# (heuristic: any added '+ - ' or '+### ' line is acceptable)
if ! echo "$DIFF" | grep -qE '^\+(- |### |#### )'; then
    echo "check-changelog: FAIL — CHANGELOG.md has added lines but none look like a release-notes entry"
    echo "  Expected an added line starting with '- ' or '### ' under ## [Unreleased]"
    exit 1
fi

echo "check-changelog: OK — src/ change accompanied by CHANGELOG entry"
