# Security Policy

## Reporting a Vulnerability

Please report security issues privately via GitHub's
["Report a vulnerability"](https://github.com/shipsolid/repo-policy/security/advisories/new)
form rather than opening a public issue. We'll acknowledge within 5 business days.

## Scope

`repo-policy` requires a GitHub token with `repo` (or fine-grained `administration:write`)
permissions to manage branch protection and rulesets. Treat that token with the same care as any
credential capable of changing repository security settings. The tool never stores the token —
it is read once per invocation from `--token`, `GITHUB_TOKEN`, or `GH_TOKEN`.

**In GitHub Actions, this must be a real PAT stored as a repository secret — never the
automatically-generated `secrets.GITHUB_TOKEN`.** Confirmed against a real workflow run:
`GITHUB_TOKEN`'s permission scopes don't include repository administration under any
`permissions:` configuration, so it always fails with `403 Resource not accessible by
integration` on branch protection/ruleset endpoints, regardless of what the workflow grants it.

## Threat Model

| Actor | Attack Vector | Impact | Mitigation |
|---|---|---|---|
| Attacker with repo write access but not admin | Modify `policy.yml` to weaken branch protection (e.g. drop required approvals), merge it, wait for `apply` to run in CI | Branch protection silently weakened | Require review on changes to `policy.yml` itself via branch protection on the repo that runs repo-policy — repo-policy cannot protect its own config file from a compromised reviewer |
| Attacker who obtains the CI-stored PAT | Full read/write on whatever the PAT's scope covers — not limited to what `policy.yml` declares | Arbitrary branch protection/ruleset changes, or broader if the PAT has full `repo` scope | Scope the PAT as narrowly as GitHub allows (fine-grained PAT, `Administration` permission only, single-repository access); rotate it; never log it (repo-policy never prints the token) |
| Malicious `policy.yml` in a fork's PR, run via `pull_request_target` | Attacker-controlled config gets a privileged token via workflow misconfiguration | Same blast radius as PAT compromise above | Never run repo-policy's `apply` mode on `pull_request_target` against untrusted input; `audit`/`plan` read-only are lower risk but still exercise real API calls with the token |
| Naming collision: something else creates a ruleset named `repo-policy:<branch>` | `strict` mode's prune logic would treat it as repo-policy-owned and could delete it | Loss of an unrelated ruleset | The naming convention is a documented hard constraint (see `docs/adrs/0004-*`) — don't create rulesets with that prefix outside repo-policy |

## Authentication

repo-policy authenticates to the GitHub REST API with a single bearer token, resolved in order
from `--token`, `GITHUB_TOKEN`, then `GH_TOKEN` (`cli._resolve_token`). There is no OAuth flow, no
session, and no credential caching — the token lives only in the process's memory for the
duration of one invocation.

## Authorization

repo-policy performs no authorization of its own; it relies entirely on GitHub's own permission
model for the token it's given. Whatever the token can do, repo-policy can do — it does not
restrict itself to a subset. This is why token scoping (above) is the primary control.

## Secrets Management

- The token is never written to disk, logged, or included in any error message.
- `policy.yml` is not an appropriate place to store secrets and repo-policy never expects one
  there — it contains only declarative policy, no credentials.
- In CI, store the PAT as an encrypted repository (or organization) secret; never as a plaintext
  workflow env default or a committed file.
- A second, narrower-scoped PAT (`REPO_POLICY_E2E_TOKEN`) exists for the automated E2E suite
  (`tests/e2e/`, `.github/workflows/e2e.yml`): fine-grained, `Administration: Read and write`,
  restricted to the single disposable fixture repo (`shipsolid/repo-policy-e2e-fixture`). Its
  blast radius is bounded to that one repo — a concrete instance of the "scope the PAT as
  narrowly as GitHub allows" mitigation already listed in the Threat Model below, not a new
  category of risk.

## Vulnerability Management

### Scan cadence

| Check | Tool | Runs | Scope |
|---|---|---|---|
| Dependency vulnerabilities | `pip-audit` | Every PR, every push to `main`, weekly (`.github/workflows/security.yml`) | This project's own dependencies (`pyproject.toml`) and the Docker Action's locked, hash-pinned dependency set (`requirements-action.txt`) |
| Static code analysis | CodeQL (`python`, `actions`) | Every PR, every push to `main`, weekly | `src/`, `tests/`, and `.github/workflows/*.yml` |
| Workflow YAML security | `zizmor --pedantic` | Every PR, every push to `main` (`ci.yml`'s `security` job, PR-blocking), weekly again (`security.yml`, advisory) | `.github/workflows/*.yml` |
| Container image vulnerabilities | Trivy (Task 7) | Every PR, every push to `main` (`ci.yml`'s `docker` job) | The Docker Action image, `CRITICAL` blocking / `CRITICAL,HIGH` reported |
| Dependency update proposals | Dependabot (`.github/dependabot.yml`) | Weekly, grouped per ecosystem (`pip`, `github-actions`, `docker`), with a 7-day cooldown before a newly-published version is proposed | Every dependency this project or its Docker image declares |

`security.yml`'s pip-audit/zizmor/CodeQL jobs are advisory, not PR-blocking -- they are not part of
`ci.yml`'s `required` check. `ci.yml`'s own narrower `security` job (zizmor over workflow YAML
only, `--min-severity high`) remains the one PR-blocking security gate, unchanged by this. See
`docs/ci-cd.md` for the full pipeline architecture and the reasoning behind that split.

### Severity response times

These are target response times for a finding surfaced by any of the scans above, not a
contractual SLA:

| Severity | Response |
|---|---|
| Critical / High | Triaged within 5 business days; fixed or explicitly risk-accepted (with reasoning recorded here or in the relevant PR) before the next release |
| Medium | Triaged within 2 weeks; fixed opportunistically, typically bundled with the next `fix:`/`chore:` commit touching the same area |
| Low / Informational | Reviewed on a best-effort basis; commonly tolerated as documented, known findings (see `docs/ci-cd.md`'s zizmor discussion) rather than fixed individually |

### Dependency-update policy

- Dependency upper bounds are pinned in `pyproject.toml` to bound the blast radius of an
  unreviewed transitive upgrade; Dependabot's weekly, grouped PRs are the mechanism that actually
  proposes moving those pins forward, rather than upper bounds alone going stale indefinitely.
- The 7-day cooldown (`dependabot.yml`'s `cooldown.default-days`) means a newly-published package
  version isn't proposed the same day it lands on PyPI/the Actions marketplace/Docker Hub -- a
  deliberate window against a compromised-release supply-chain attack landing in a merged PR
  before it's been caught and yanked upstream.
- A Dependabot PR against `requirements-action.txt` specifically may fail CI's
  `verify-action-lock` check even when the underlying version bump is legitimate, because that
  file is a `uv --generate-hashes` lockfile, not one Dependabot's pip-compile-aware update path
  recognizes (see `dependabot.yml`'s own comment). Regenerate it by hand with the command
  `verify-action-lock.sh`'s failure output prints; don't merge Dependabot's version of that file
  directly.

### Verifying release artifacts

Starting with the first release cut after this SBOM/attestation tooling merged (check
`CHANGELOG.md` for the earliest entry mentioning SBOMs, or look for a release whose assets include
`sbom-wheel.cdx.json`/`sbom-docker.cdx.json` — as of this writing that tooling has not yet shipped
in a release; the current in-development version is `0.4.7`), every release publishes, alongside
the PyPI package: a CycloneDX SBOM for the wheel's install environment and one for the Docker
Action image (both attached to the GitHub release), plus GitHub artifact attestations
(Sigstore-backed build provenance) for the wheel, sdist, both SBOMs, and the Docker Action image
(by digest). To verify a downloaded artifact's provenance:

```bash
# Verify the wheel/sdist/SBOM attestations (requires the GitHub CLI, `gh`, and repo read access)
gh attestation verify dist/repo_policy-<version>-py3-none-any.whl --owner shipsolid
gh attestation verify dist/repo_policy-<version>.tar.gz --owner shipsolid
gh attestation verify sbom-wheel.cdx.json --owner shipsolid
gh attestation verify sbom-docker.cdx.json --owner shipsolid
```

The SBOMs themselves (`sbom-wheel.cdx.json`, `sbom-docker.cdx.json`) are downloadable from each
GitHub release's assets and are valid CycloneDX 1.6 JSON -- validate structurally with any
CycloneDX-compliant tool, e.g. `cyclonedx-py`'s own `--validate` (on by default) or
[cyclonedx-cli](https://github.com/CycloneDX/cyclonedx-cli) `validate --input-file
sbom-wheel.cdx.json`.

**Known limitation -- the Docker Action image's attestation cannot currently be verified with a
single command, and its digest is not reproducible across rebuilds.** `gh attestation verify`
requires either a local file path or a registry-resolvable `oci://` reference to recompute the
subject's digest and compare it against the signed record (confirmed against `gh`'s own
documentation). This repository never pushes the Docker Action image to a registry -- `action.yml`
builds it fresh from the pinned `Dockerfile` at consumption time (see Task 7) -- so there is no
`oci://` reference to verify against.

It's also not enough to just rebuild locally and compare digests: **the image's digest is not
reproducible across independent builds**, confirmed by building this exact commit twice
(`docker build --no-cache`) and comparing `docker inspect --format='{{.Id}}'` output -- the two
builds produced different image IDs, with different `RootFS.Layers` digests on every layer that
touches `src/`, `requirements-action.txt`, or either `pip install` step. Only the base-image layers
(digest-pinned in the `Dockerfile`) matched. This is consistent with what Task 7's own CI check
(`ci.yml`'s `docker` job) actually verifies: it diffs **package and OS inventories** (`pip list
--format=freeze`, `dpkg -l`) between two clean builds, not image digests -- Task 7 established
"the same packages, at the same versions, every time," not "byte-identical image layers." Treating
the two as equivalent (an earlier draft of this document did) is wrong and would send anyone who
tried to verify a release's image digest straight into a false "tampering" conclusion.

What you *can* verify for a specific release's Docker image, without trusting anything blindly:

1. **The attestation is a real, auditable record.** It's visible under the repository's
   Attestations tab on GitHub, and fetchable directly by digest via `GET
   /repos/shipsolid/repo-policy/attestations/<digest>` -- it proves *some* GitHub Actions run in
   this repository, at this commit, produced an image with that exact digest, signed via Sigstore.
   Treat it as an audit trail, not as something you locally re-derive.
2. **The SBOM's component list is reproducible even though the image digest isn't.** Rebuild the
   image from the same release tag's `Dockerfile`, extract its package list (`docker run --rm
   --entrypoint pip <image> list --format=freeze`), and diff it against the release's
   `sbom-docker.cdx.json` components -- the *packages and versions* installed are pinned by
   `requirements-action.txt`'s hashes and are what should match, not the image digest.
3. Turning digest-level verification into a real one-command check would require either making the
   build byte-for-byte reproducible (e.g. `SOURCE_DATE_EPOCH` pinned to the commit timestamp plus
   auditing every remaining source of build-time nondeterminism, then proving it with repeated
   `--no-cache` builds) or publishing the image to a registry (`ghcr.io`/Docker Hub) so `gh
   attestation verify oci://...` has something to resolve against. Both are real scope beyond this
   SBOM/attestation-plumbing task and are open follow-up items, not done here.

## Security Baseline

- Dependencies pinned with upper bounds (`pyproject.toml`).
- PyPI publishing uses trusted publishing (OIDC) — no long-lived PyPI API token stored anywhere.
- No secrets, tokens, or credentials are ever persisted by repo-policy itself.
- Automated dependency-vulnerability scanning (`pip-audit`), static analysis (CodeQL), and workflow
  security linting (`zizmor`) run on every PR, every push to `main`, and weekly
  (`.github/workflows/security.yml`); Dependabot proposes grouped, weekly dependency updates with a
  7-day cooldown (`.github/dependabot.yml`). See "Vulnerability Management" above.
- Every release publishes CycloneDX SBOMs (wheel + Docker Action image) and GitHub artifact
  attestations (Sigstore-backed build provenance) for the wheel, sdist, both SBOMs, and the Docker
  image. See "Verifying release artifacts" above.

## Repository Settings Not Yet Enabled

The following are GitHub repository-settings toggles (Settings → Code security), not something
expressible in a workflow file — recorded here as an open action item rather than silently
skipped:

- **Secret scanning** — Settings → Code security → Secret scanning → Enable. Flags secrets
  matching known provider patterns that get committed to the repository.
- **Push protection** — same page, enabled after secret scanning is on. Blocks a `git push`
  containing a detected secret before it ever lands in the repository's history, rather than only
  flagging it after the fact.

Both are available on public repositories at no cost, and on private repositories with GitHub
Advanced Security. Neither is enabled by anything in this repository's version-controlled
configuration — enabling them requires repository-admin access to the GitHub UI (or the REST API's
`PATCH /repos/{owner}/{repo}` `security_and_analysis` field, the same endpoint `repo-policy`
itself already manages other `security_and_analysis` sub-settings through — see
`github_client.py`'s `update_security_and_analysis`). Turning these two on for
`shipsolid/repo-policy` itself is an action item for whoever holds admin access, not something
this codebase change can complete.

## Known Limitations

- No mTLS or certificate-based auth path — token-based auth only.
- No built-in secret scanning of `policy.yml` — a user could technically put a secret in a custom
  field extension in the future; the current schema has no such field, so this is currently moot.
- See the Threat Model above for the PAT-scope and `policy.yml`-review gaps, which are
  organizational controls repo-policy cannot enforce on your behalf.
- `bandit -q -r src` reports one Medium-severity finding, `B506` (`yaml_load`) at
  `config.py:103`, on `yaml.load(raw_text, Loader=_StrictLoader)`. This is a documented false
  positive: `_StrictLoader` (`config.py:17`) is a subclass of `yaml.SafeLoader`, not `yaml.Loader`
  — it only narrows two of SafeLoader's own implicit-resolver surprises (the yes/no/on/off bool
  words, and octal/sexagesimal ints) and rejects duplicate mapping keys; it never adds a
  constructor capable of instantiating arbitrary Python objects, so it carries exactly the same
  safety guarantee as `yaml.safe_load()` itself. Bandit's `B506` check flags any `yaml.load(...,
  Loader=...)` call pattern regardless of which Loader class is actually passed, so it can't
  distinguish this from a genuinely unsafe `Loader=yaml.Loader`. Not suppressed with `# nosec`
  in-source because `bandit` isn't wired into either CI workflow (see `docs/ci-cd.md`'s "Security
  workflow" section for why CodeQL's Python analysis is the automated static-analysis coverage
  instead) — there is no gate for a source-level suppression to silence, only this note for the
  next person who runs `bandit` locally and sees the same finding.
