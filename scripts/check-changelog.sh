#!/usr/bin/env bash
# check-changelog.sh — fails if a PR touches src/scrapefold/ without
# adding at least one line under the ## [Unreleased] section of CHANGELOG.md.
#
# Only additions inside [Unreleased] count.  Bullets added to old release
# sections are ignored — they cannot satisfy this gate.
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

# Helper: extract the lines belonging to the ## [Unreleased] section.
# Starts on the line after "## [Unreleased]", stops before the next "## [" heading.
extract_unreleased() {
    awk '/^## \[Unreleased\]/{flag=1; next} /^## \[/{flag=0} flag' "$1"
}

# Compare the [Unreleased] section between the base and HEAD.
# We write to temp files because process substitution can't be used as awk args.
TMP_BEFORE=$(mktemp)
TMP_AFTER=$(mktemp)
trap 'rm -f "$TMP_BEFORE" "$TMP_AFTER"' EXIT

git show "${BASE_REF}:CHANGELOG.md" 2>/dev/null | extract_unreleased /dev/stdin > "$TMP_BEFORE" \
    || extract_unreleased /dev/stdin < <(echo "") > "$TMP_BEFORE"
extract_unreleased CHANGELOG.md > "$TMP_AFTER"

# Look for lines added to the [Unreleased] section (bullet, sub-heading, or heading).
ADDED=$(diff "$TMP_BEFORE" "$TMP_AFTER" | grep -E '^> (- |### |#### )' || true)

if [ -z "$ADDED" ]; then
    echo "check-changelog: FAIL — src/scrapefold/ changed but no new entry found under ## [Unreleased] in CHANGELOG.md"
    echo "  Add at least one bullet under ## [Unreleased] in CHANGELOG.md"
    exit 1
fi

echo "check-changelog: OK — src/ change accompanied by CHANGELOG entry under [Unreleased]"
