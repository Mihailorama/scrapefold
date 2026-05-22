#!/usr/bin/env bash
# Run tests only for files changed since HEAD.
set -euo pipefail

cd "$(dirname "$0")/.."

# Collect changed Python files in src/ and tests/
mapfile -t CHANGED < <(git diff --name-only HEAD -- 'src/*.py' 'tests/*.py' 2>/dev/null | sort -u)

if [ ${#CHANGED[@]} -eq 0 ]; then
    echo "No changed Python files since HEAD. Running full offline suite."
    exec pytest -m "not paid and not network" --maxfail=3
fi

# Map each src/scrapefold/X/Y.py -> tests/test_<stem>.py if it exists.
TARGETS=()
for f in "${CHANGED[@]}"; do
    if [[ "$f" == tests/* ]]; then
        TARGETS+=("$f")
        continue
    fi
    stem="$(basename "${f%.py}")"
    # Look for matching test file anywhere under tests/
    while IFS= read -r match; do
        TARGETS+=("$match")
    done < <(find tests -type f -name "test_${stem}.py" 2>/dev/null)
done

if [ ${#TARGETS[@]} -eq 0 ]; then
    echo "No mapped tests for changes. Running full offline suite."
    exec pytest -m "not paid and not network" --maxfail=3
fi

# De-dup
mapfile -t TARGETS < <(printf "%s\n" "${TARGETS[@]}" | sort -u)

echo "Running ${#TARGETS[@]} targeted test file(s):"
printf '  %s\n' "${TARGETS[@]}"
pytest -m "not paid and not network" --maxfail=3 "${TARGETS[@]}"
