# CI/CD Pipeline

## Pipeline Architecture

| Workflow | File | Trigger | Purpose |
|---|---|---|---|
| CI | `.github/workflows/ci.yml` | every PR, push to `main`, and as a reusable workflow called from Release | lint, typecheck, unit tests, package build, workflow security scan |
| Release | `.github/workflows/release.yml` | push to `main` | run CI, then (if CI passes) version bump, changelog, git tag, floating major tag, PyPI publish, SBOM generation, artifact attestation |
| Security | `.github/workflows/security.yml` | every PR, push to `main`, weekly schedule | dependency-vulnerability scanning (pip-audit), static code analysis (CodeQL: Python + Actions), a second, weekly zizmor pass -- advisory/reporting, not PR-blocking (see below) |

The pre-gating version of both workflows ran green on real GitHub Actions runners, not just
locally (see `docs/test-strategy.md` for the project's general stance on real-vs-mocked
verification). The job split, `workflow_call` gating, and permission scoping described below are
new in this revision and are pending their first live run — see this repository's audit-remediation
plan (Task 6) for the local `zizmor` verification performed instead.

### CI job graph

`ci.yml` splits what used to be one `test` job into five independent jobs — `lint` (`ruff check`),
`typecheck` (`mypy`), `test` (`pytest`), `build` (`python -m build`, package-validation), and
`security` (`zizmor --pedantic --offline` against this repo's own workflow YAML) — plus a final
`required` job that `needs:` all five and fails if any of them failed, were cancelled, or were
skipped. `required` exists purely to give branch protection and Release a single stable status
name, `CI / required`, that doesn't change as jobs are added or removed. Each job declares its own
minimal `permissions:` (`contents: read`); the workflow-level default is `permissions: {}`.

`security` here is narrowly scoped to this repo's own workflow files — it is not dependency
scanning. `pip-audit`, CodeQL, and a scheduled `security.yml` were the separate, later addition
Task 6 anticipated — see "Security workflow: pip-audit, CodeQL, and a second zizmor pass" below.
This job only covers what gates releases today; it remains the one PR-blocking security check.

### CI gates Release: same-workflow dependency, not `workflow_run`

`release.yml` triggers on the same `push: branches: [main]` event as `ci.yml`'s own direct
trigger, and its first job (`ci`) invokes `ci.yml` as a reusable workflow
(`uses: $/.github/workflows/ci.yml`) rather than reacting to `ci.yml`'s completion after the fact.
`release` then `needs: ci`, so a failing or incomplete CI run leaves `release` (and `publish`,
which `needs: release`) skipped — nothing downstream runs.

This was originally built with a `workflow_run` trigger (`on: workflow_run: workflows: ["CI"], …`)
gated by an `if:` checking `conclusion == 'success'`, `head_branch == 'main'`, the triggering
event, and the exact `head_sha`. That is a legitimate, commonly-used pattern in principle, but
`zizmor`'s `dangerous-triggers` audit flags any use of `workflow_run` at high severity
unconditionally — its own documentation states no amount of receiving-side `if:` filtering makes
it safe in zizmor's model, and specifically calls out a `github.repository` check as ineffective
because `workflow_run` always executes in the target repository's context regardless of what
triggered the watched workflow. Since `zizmor --pedantic --offline` with no high-severity result is
this pipeline's static-analysis gate, `workflow_call` composition replaced `workflow_run`: CI runs
inside the same workflow *run* as the release job, on the same commit, with no separate event or
cross-run SHA to validate.

**Trade-off accepted:** because `ci.yml` keeps its own direct `push: branches: [main]` trigger (so
`main` still gets an independent CI signal even if `release.yml` is ever broken or disabled) *and*
`release.yml` calls `ci.yml` again as a reusable workflow, every push to `main` runs the CI job
graph twice — once producing the standalone `CI / required` check, once nested under `Release / ci`
gating the release. This is deliberate: reliability of "is `main` green" outweighs the modest extra
compute for a repository this size.

## Branch Strategy

Single `main` branch. No release branches; every release is cut directly from `main` by
`python-semantic-release` reading Conventional Commits history since the last release tag.

## Release Process

1. A commit lands on `main` (directly, or via a merged PR).
2. `release.yml`'s `ci` job runs the full CI job graph (lint, typecheck, test, build, security)
   against that commit. If any of it fails, nothing below happens — the `release` and `publish`
   jobs are skipped, not just conditioned to no-op.
3. `python-semantic-release` inspects commits since the last release tag:
   - `feat: ...` → minor bump
   - `fix: ...` → patch bump
   - `feat!: ...` or a `BREAKING CHANGE:` footer → major bump
   - `chore:` / `docs:` / `test:` / `ci:` → no release
4. If a release is warranted, it bumps `pyproject.toml`'s `project.version` **and**
   `src/repo_policy/__init__.py`'s `__version__` (via `version_variables` in
   `[tool.semantic_release]` — both must be listed, or they silently diverge; this happened once
   in production, see `docs/test-strategy.md`), regenerates `CHANGELOG.md`, commits as
   `chore(release): {version} [skip ci]`, and tags `v{version}`.
5. Before anything below runs, the workflow diffs that release commit against the pre-release
   commit and fails the job if it touched anything other than `pyproject.toml`,
   `src/repo_policy/__init__.py`, and `CHANGELOG.md` — a check against a compromised or
   misbehaving semantic-release run silently slipping in an unrelated code change.
6. The floating major tag (`v0` until a `1.0.0` ships — see README) is force-moved to point at the
   new release tag.
7. The built sdist/wheel are handed off (via `actions/upload-artifact` / `download-artifact`) to a
   separate `publish` job, and published to PyPI via trusted publishing (OIDC) — no long-lived API
   token stored in the repo.
8. In parallel with `publish`, a separate `provenance` job (Task 8) re-checks out the exact
   released commit (by tag, since `release`'s own push moved `main` past `github.sha`), generates
   a CycloneDX SBOM for the wheel's install environment and one for the Docker Action image
   (built fresh from the release commit's `Dockerfile`), signs GitHub artifact attestations
   (Sigstore-backed build provenance) for the wheel, sdist, both SBOMs, and the Docker image (by
   digest), and attaches the two SBOM files to the GitHub release `release`'s own
   python-semantic-release step already created. See "SBOM and provenance (`provenance` job)"
   below and `SECURITY.md`'s "Verifying release artifacts" for how to check these.

### Permission scoping and concurrency

Each job in `release.yml` carries only the permission its own steps need:

| Job | Permissions | Why |
|---|---|---|
| `ci` | `contents: read` | Only checks out code to lint/type-check/test/build. |
| `release` | `contents: write` | Pushes the semantic-release commit and moves the floating tag. |
| `publish` | `id-token: write` | OIDC trusted publishing only — never checks out the repo, never sees `contents: write`. |
| `provenance` | `contents: write`, `id-token: write`, `attestations: write` | Attaches SBOMs to the GitHub release (`contents: write`) and signs Sigstore-backed attestations (`id-token: write` + `attestations: write`) — a distinct permission combination from both `release` and `publish`, so it's a third job rather than added to either. |

The workflow-level default is `permissions: {}`; nothing falls back to the repository's broader
default token permissions.

The whole pipeline runs under `concurrency: {group: release-main, cancel-in-progress: false}`, so
two rapid pushes to `main` queue and finish in order rather than racing on the same
version/tag/PyPI state — the second run waits for the first to complete before its own `ci` job
even starts, and nothing is cancelled out from under a release in progress.

## A step-ordering bug this pipeline shipped once

Steps 6 and 7 above (tag-move and PyPI publish) are independent — nothing about moving the
floating tag depends on PyPI publishing succeeding, or vice versa. The workflow originally ran
them in the reverse order: when PyPI publish failed (as it did on every release before trusted
publishing was configured), the tag-move was skipped along with it, silently. `uses:
shipsolid/repo-policy@v0` was broken for every consumer across several releases, and nothing in
the workflow's status said so — the job still reported success up to the point PyPI's own step
failed.

**The fix:** run the tag-move step before the PyPI-publish step. Two independent side effects of
one event should never be ordered such that one's failure can silently skip the other.

## PyPI Trusted Publishing

Configured at pypi.org → Publishing → pending/active publisher:

| Field | Value |
|---|---|
| PyPI project name | `repo-policy` |
| Owner | `shipsolid` |
| Repository | `repo-policy` |
| Workflow | `release.yml` |
| Environment | *(none)* |

No PyPI API token is stored anywhere in this repository, by design.

**Status:** working as of v0.1.3. The trusted publisher above wasn't registered on pypi.org until
after v0.1.2 shipped, so `v0.1.0`, `v0.1.1`, and `v0.1.2` each failed at the PyPI-publish step with
`invalid-publisher` (GitHub's OIDC token had no matching publisher to exchange against — trusted
publishing has to be pre-registered on PyPI before the first attempt, it isn't provisioned by
`permissions: id-token: write` alone). Those three git tags and GitHub releases exist but were
never published to PyPI, and — since PyPI never allows re-uploading a consumed version number —
never will be; `pip install repo-policy` starts at `0.1.3`. `v0.1.3` onward publish cleanly.

## SBOM and provenance (`provenance` job)

Added in Task 8, alongside `pip-audit`/CodeQL/`security.yml` — see "Security workflow" below for
those; this section is specifically about what happens per-release.

The `provenance` job (see the permission-scoping table above) runs after `release` succeeds, in
parallel with `publish`:

1. Checks out the exact released commit by tag (`needs.release.outputs.tag`) — not `github.sha`,
   which by this point in the workflow run points at that commit's *parent* (`release`'s own
   version-bump commit already moved `main` forward).
2. Downloads the `dist/` artifact `release` uploaded (the built wheel + sdist).
3. Installs the wheel into a throwaway venv and runs `cyclonedx-py environment` against it —
   `sbom-wheel.cdx.json`, describing exactly what `pip install repo-policy` puts on disk, not just
   the declared dependency ranges.
4. Builds the Docker Action image fresh from the release commit's `Dockerfile` (a single build —
   `ci.yml`'s own `docker` job already proved two-build reproducibility for this exact commit,
   gated by `needs: ci`), extracts its installed-package list (`pip list --format=freeze`, the
   same approach `ci.yml`'s `docker` job uses for its own inventory step), and runs `cyclonedx-py
   requirements` against it — `sbom-docker.cdx.json`.
5. Runs `actions/attest-build-provenance` twice: once (`subject-path`) covering the wheel, sdist,
   and both SBOM files together, and once more (`subject-name` + `subject-digest`) for the Docker
   image, since a file-based and a digest-based subject can't share one call.
6. Uploads both SBOM files to the GitHub release as assets (`gh release upload`) — the release
   object itself already exists by this point, created by `release`'s own
   python-semantic-release step (which creates the VCS release but does not upload `dist/`
   assets to it; PyPI, via the separate `publish` job, remains the actual package-install source).

**Why the Docker image is attested by digest, not by registry reference:** this repository never
pushes the Docker Action image to a registry — `action.yml` builds it fresh from the pinned
`Dockerfile` at consumption time (see Task 7). The attestation therefore records the digest of the
image `provenance` itself built from the release commit, not a `ghcr.io`/Docker Hub tag. Anyone
who wants to independently verify it has to rebuild at the same tag and compare digests, relying
on Task 7's reproducibility guarantee (digest-pinned base image + hash-locked dependencies) rather
than on registry-hosted immutability. See `SECURITY.md`'s "Verifying release artifacts" for the
exact `gh attestation verify` commands.

**Cyclonedx tool note:** the PyPI package is `cyclonedx-bom`; the CLI it installs is `cyclonedx-py`
— a package/command name mismatch worth knowing about when reading the workflow's `pip install
cyclonedx-bom==<version>` step.

## Security workflow: pip-audit, CodeQL, and a second zizmor pass

`.github/workflows/security.yml` (Task 8) is the "separate, dedicated workflow added later" that
`ci.yml`'s own `security` job (Task 6) explicitly deferred to. It runs on pull requests, every push
to `main`, and a weekly schedule (Monday 06:00 UTC) — deliberately **not** added to `ci.yml`'s
`required` job, so it doesn't duplicate that job's PR-blocking role or couple release velocity to,
e.g., CodeQL's own multi-minute run time. Three jobs:

- **`pip-audit`** — scans this project's own dependency set (`pyproject.toml`, via `pip install
  -e ".[dev]"` then `pip-audit` against the resulting environment) and, separately, the Docker
  Action's locked, hash-pinned dependency set (`pip-audit -r requirements-action.txt
  --require-hashes`), since the two are independent and the second is never installed into this
  job's own Python environment.
- **`zizmor`** — `zizmor --pedantic --offline --min-severity high .github/workflows`, the same
  invocation and the same `--min-severity high` threshold as `ci.yml`'s `security` job, for the
  same reason (see that job's own comment): without a floor, `--pedantic`'s advisory-level
  findings (concurrency-limits, artipacked, etc. — see the zizmor findings this repo currently
  tolerates, documented inline in each workflow) would fail this job on every single run. Applying
  the same threshold here, even though this workflow is advisory rather than PR-blocking, avoids
  turning a scheduled security signal into permanent, uninformative noise.
- **`codeql`** — a SHA-pinned advanced workflow (not GitHub's "default setup" repository-settings
  toggle, which is live-repo state outside version control), matrixed over `language: [python,
  actions]` with `build-mode: none` for both (nothing to compile — Python source and GitHub
  Actions workflow YAML are both interpreted/declarative). Job permissions: `contents: read`,
  `security-events: write` (upload SARIF results), `actions: read` (GitHub's own advanced-setup
  template default, lets the CodeQL Action read this workflow run's own context).

`bandit` is run locally as part of this task's own verification (see the audit-remediation plan's
Task 8) but is intentionally not wired into either CI workflow — CodeQL's Python analysis is the
automated static-analysis coverage; bandit's one recurring finding on this codebase (`B506`,
`yaml.load` with a custom Loader) is a documented false positive, not something worth a second,
redundant automated gate for. See `SECURITY.md`'s "Vulnerability Management" section for the
resulting scan cadence, severity response times, and dependency-update policy.

## Rollback

There is no manual rollback step for a bad release — semantic-release doesn't support "undo." To
recover from a bad release:

1. Fix forward with a new `fix:` commit; it produces a new, higher version immediately.
2. If the bad version must not be installed, [yank it on PyPI](https://pypi.org/manage/project/repo-policy/releases/)
   (the version number itself can never be reused, even after yanking).
3. If the floating `v0` tag now points at a bad commit and a fix hasn't shipped yet, it can be
   moved back manually: `git tag -f v0 <last-good-tag> && git push origin v0 --force`.

## Known Platform Constraint: `secrets.GITHUB_TOKEN` Cannot Run This Action

Confirmed against a real workflow run in a real repository: the GitHub Actions auto-generated
`secrets.GITHUB_TOKEN` has no permission scope covering repository administration (branch
protection or rulesets), under any `permissions:` block configuration. Every consumer of
`shipsolid/repo-policy@v0` must supply a real PAT via a custom repository secret and pass it as
`env: GITHUB_TOKEN: ${{ secrets.<YOUR_SECRET_NAME> }}` on the step — see the README's GitHub
Action example. This is a GitHub platform limitation, not something this pipeline or the Action's
`action.yml` can work around.
