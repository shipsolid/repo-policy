#!/usr/bin/env bash
# Fails when README.md's or USAGE.md's `shipsolid/repo-policy@<sha> # <tag>` Action-pin examples
# don't match the latest release tag -- the drift AUDIT-GAPS.md's FINDING-001 (stale pin) and
# FINDING-002 (mislabeled pin) found. Nothing else keeps these prose examples in sync:
# release.yml's automated version-bump commit only ever touches pyproject.toml,
# src/repo_policy/__init__.py, and CHANGELOG.md.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

# Not `git describe --tags --abbrev=0`: this repo's floating `v0` major-version tag (README's
# "Convenience alternative -- @v0") points at the exact same tag object as the latest `v<semver>`
# release tag once a release lands. `git describe` then treats the two as ambiguous ("warning: tag
# 'v0' is externally known as 'v0.5.0'") and silently stops honoring --abbrev=0, always appending
# the full -N-g<hash> suffix even when HEAD is more than one release-plus-later-commits from a
# clean checkout -- unusable as "the latest release tag" here. Listing only tags matching the full
# `v<major>.<minor>.<patch>` glob and version-sorting them sidesteps the ambiguity entirely: `v0`
# has no dots, so it never matches the pattern.
latest_tag="$(git tag -l 'v[0-9]*.[0-9]*.[0-9]*' --sort=-v:refname | head -1)"
if [ -z "$latest_tag" ]; then
    echo "verify-release-pins: no v<major>.<minor>.<patch> tag found" >&2
    exit 1
fi
expected_sha="$(git rev-parse "${latest_tag}^{commit}")"

echo "verify-release-pins: latest tag is ${latest_tag} (${expected_sha})"

if ! python3 - "$latest_tag" "$expected_sha" README.md USAGE.md <<'PY'
import re
import sys

latest_tag, expected_sha, *files = sys.argv[1:]
pattern = re.compile(r"shipsolid/repo-policy@([0-9a-f]{40}) # (v\d+\.\d+\.\d+)")

failures = []
found_any = False
for path in files:
    text = open(path, encoding="utf-8").read()
    for match in pattern.finditer(text):
        found_any = True
        sha, tag = match.group(1), match.group(2)
        if sha != expected_sha or tag != latest_tag:
            line = text[: match.start()].count("\n") + 1
            failures.append(
                f"{path}:{line}: pinned to {sha[:12]}... # {tag}, "
                f"expected {expected_sha[:12]}... # {latest_tag}"
            )

if not found_any:
    failures.append(
        "no `shipsolid/repo-policy@<sha> # <tag>` pin example found in README.md or "
        "USAGE.md -- pattern may have changed"
    )

if failures:
    for f in failures:
        print(f, file=sys.stderr)
    sys.exit(1)
PY
then
    echo "verify-release-pins: stale or mismatched Action-pin example(s) found -- update them to:" >&2
    echo "  shipsolid/repo-policy@${expected_sha} # ${latest_tag}" >&2
    exit 1
fi

echo "verify-release-pins: README.md and USAGE.md's Action-pin examples match ${latest_tag}."
