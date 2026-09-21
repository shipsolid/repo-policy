# Contributing

## Prerequisites

- Python 3.10+
- No other runtime dependency — the test suite mocks every GitHub API call (`respx`); you do not
  need a real GitHub token to develop or run tests.
- `docker` only if you're changing `Dockerfile`/`action.yml` and want to build the Action image
  locally.

## Setup

```bash
pip install -e ".[dev]"
```

## Repository Structure

```
src/repo_policy/       the package — see ARCHITECTURE.md for what each module owns
tests/                 one test file per source module, plus test_idempotency.py and
                       test_policies_parity.py (cross-cutting regression guards)
docs/adrs/             why key decisions were made
docs/ci-cd.md          release pipeline
docs/test-strategy.md  what's tested and why
docs/troubleshooting.md
action.yml, Dockerfile the GitHub Action
.github/workflows/     ci.yml (lint, typecheck, a test matrix across every supported Python
                       version with coverage/format-check enforcement, package build, a wheel-
                       install smoke test, self-policy validation, workflow security scan, Docker
                       image build+scan); release.yml (semantic-release, signed release tag, PyPI
                       publish, SBOM + attestation); security.yml (pip-audit, CodeQL, a second
                       zizmor pass); policy-audit.yml (self-governance, read-only); e2e.yml
                       (nightly/manual `pytest -m e2e` against the live fixture repo)
```

## Before opening a PR

```bash
ruff format --check src tests
ruff check src tests
mypy src
pytest --cov=repo_policy --cov-branch --cov-report=term-missing --cov-fail-under=95
```

CI runs this same test suite on every Python version `repo-policy` claims to support (3.10, 3.11,
3.12, 3.13, 3.14 -- see `requires-python` in `pyproject.toml`), plus a wheel-install smoke test on
the oldest and newest of those. Total branch coverage must stay at or above 95%
(`--cov-fail-under=95`), and any formatting drift from `ruff format --check` blocks merging -- run
`ruff format src tests` to fix it before committing.

## Commit messages

This project uses [Conventional Commits](https://www.conventionalcommits.org/) — releases and
version bumps are automated by `python-semantic-release` from commit history:

- `feat: ...` → minor version bump
- `fix: ...` → patch version bump
- `feat!: ...` or a `BREAKING CHANGE:` footer → major version bump
- `chore:`, `docs:`, `test:`, `ci:` → no release

## Verifying a Release

Before trusting a downloaded release artifact (a wheel/sdist from PyPI, or the Docker Action
image), you can independently check each of the following. `SECURITY.md`'s "Verifying release
artifacts" and "Release Signing" sections have the full reasoning and threat model behind each
command; this is the quick-reference command list.

```bash
# 0. Fetch what was actually published — never a fresh local rebuild. Wheel/sdist builds are not
#    proven byte-reproducible here (hatchling's own `Generator:` metadata line alone varies by
#    version), and comparing a local rebuild's hash against a published digest is exactly the
#    "rebuild and diff a hash" anti-pattern SECURITY.md's own Docker-digest discussion warns
#    against — it produces false "tampering" conclusions from ordinary build-tool variance, not a
#    real integrity signal. The wheel/sdist live on PyPI; the two SBOMs are GitHub release assets.
mkdir -p /tmp/repo-policy-verify && cd /tmp/repo-policy-verify
curl -s https://pypi.org/pypi/repo-policy/<version>/json | jq -r '.urls[].url' | xargs -n1 curl -sLO
gh release download v<version> --repo shipsolid/repo-policy   # the two SBOM files

# 1. Release signature — the release tag itself, will carry a verifiable SSH signature from the
#    dedicated release-bot identity once Task 10's signing pipeline is live (one-time: register the
#    bot's public key as a trusted signer for its committer email). Runs in its own clone, not the
#    /tmp/repo-policy-verify scratch dir above, since verifying a tag needs an actual repository.
git clone --quiet https://github.com/shipsolid/repo-policy.git /tmp/repo-policy-verify-git
cd /tmp/repo-policy-verify-git
git verify-tag v<version>
cd /tmp/repo-policy-verify

# 2. GitHub artifact attestations (Sigstore-backed build provenance), against the files fetched in
#    step 0 above — never a local rebuild
gh attestation verify repo_policy-<version>-py3-none-any.whl --owner shipsolid
gh attestation verify repo_policy-<version>.tar.gz --owner shipsolid
gh attestation verify sbom-wheel.cdx.json --owner shipsolid
gh attestation verify sbom-docker.cdx.json --owner shipsolid

# 3. Wheel digest — confirm the download itself wasn't corrupted or tampered with in transit, by
#    comparing it against the digest PyPI's own JSON API reports for that exact file (a
#    transit-integrity check on a downloaded artifact, not a build-reproducibility claim)
sha256sum repo_policy-<version>-py3-none-any.whl
curl -s https://pypi.org/pypi/repo-policy/<version>/json | jq -r '.urls[].digests.sha256'

# 4. Container digest — the Docker Action image's attestation is keyed by digest, not a registry
#    reference (this project never pushes the image to a registry); see SECURITY.md for why the
#    digest itself isn't reproducible across rebuilds and what a digest-based attestation does and
#    doesn't prove. Confirm the attestation exists for the digest you built:
gh api repos/shipsolid/repo-policy/attestations/<digest>

# 5. SBOM presence — both CycloneDX SBOMs are attached to the GitHub release
gh release view v<version> --json assets --jq '.assets[].name' | grep -E 'sbom-(wheel|docker)\.cdx\.json'
```

## Adding a new policy field

1. Add the field itself to the relevant model (`BranchPolicy`, `PullRequestPolicy`,
   `StatusChecksPolicy`, or `RepoSettingsPolicy`) in `src/repo_policy/models.py`.
2. For a `BranchPolicy` field, add one `FieldSpec` entry to `FIELD_SPECS` in
   `src/repo_policy/models.py` — its permissive (no-op) default, whether its boolean polarity is
   inverted, whether GitHub Rulesets can represent it at all (`ruleset_supported`), and its
   display label. This is the single source of truth: `diff._FIELDS`/`_SCHEMA_DEFAULTS`/
   `_INVERTED_FIELDS`, `models._RULESET_UNSUPPORTED_FIELDS`, `render._LABELS`, and
   `tests/test_policies_parity.py`'s ruleset-unsupported set are all *derived* from `FIELD_SPECS` —
   there is no second table to hand-edit for any of those anymore (see `FieldSpec`'s own
   docstring for why that consolidation exists).
3. Map it to both backends in `src/repo_policy/policies/branch_protection.py` and
   `src/repo_policy/policies/rulesets.py` — unless `ruleset_supported=False`, in which case the
   ruleset backend only needs it threaded through `_RULESET_UNSUPPORTED_FIELDS` (already derived
   from step 2) rather than a real translation.
4. Add coverage in each affected test file — the diff engine (`tests/test_diff.py`), both
   translators, the idempotency test (`tests/test_idempotency.py`), and a `RESTRICTIVE_VALUES`
   entry for the new field in `tests/test_policies_parity.py`.
