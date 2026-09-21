#!/usr/bin/env bash
# Fails when requirements-release.in and requirements-release.txt disagree (Task 7's sibling check
# for the release pipeline's own tool install -- see requirements-release.in's own comment).
#
# Unlike scripts/verify-action-lock.sh, there is no pyproject.toml cross-check here:
# requirements-release.in pins python-semantic-release, a release-pipeline build tool this project
# does not itself depend on, so it has no corresponding [project.dependencies] entry to compare
# against. This script only does the regenerate-and-diff half of that script's pattern.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

if ! command -v uv >/dev/null 2>&1; then
    echo "verify-release-lock: uv is required but not found on PATH" >&2
    exit 1
fi

echo "verify-release-lock: regenerating the lock in a temporary path and comparing..."

tmp_dir="$(mktemp -d)"
trap 'rm -rf "$tmp_dir"' EXIT

# Seed the temp output with the checked-in lock *before* compiling into it: `uv pip compile`
# treats a pre-existing output file at -o as a preference source and keeps every still-valid pin
# as-is. Compiling into a virgin path instead makes uv re-resolve every loosely-constrained
# transitive package to whatever is newest on PyPI right now -- which would fail this check on any
# unrelated upstream release, with nothing in this repo having changed. Seeding preserves real
# drift detection (a genuine .in change still produces a diff) while eliminating that false
# positive. See scripts/verify-action-lock.sh, which uses the same technique.
cp requirements-release.txt "$tmp_dir/requirements-release.txt"
uv pip compile --generate-hashes --python-version 3.12 \
    -o "$tmp_dir/requirements-release.txt" requirements-release.in >/dev/null

# Skip each file's first 2 header lines: uv embeds the -o path it was given there, which never
# matches the checked-in file's own header even when the locked contents underneath are identical.
if ! diff -u <(tail -n +3 requirements-release.txt) <(tail -n +3 "$tmp_dir/requirements-release.txt"); then
    echo "verify-release-lock: requirements-release.txt is stale -- regenerate it with:" >&2
    echo "  uv pip compile --generate-hashes --python-version 3.12 -o requirements-release.txt requirements-release.in" >&2
    exit 1
fi

echo "verify-release-lock: requirements-release.in and requirements-release.txt agree."
