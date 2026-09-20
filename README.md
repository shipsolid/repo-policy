# repo-policy

Lightweight, declarative repository governance for GitHub. Define expected branch protection
and ruleset configuration in YAML; audit, preview, and apply it locally or in CI.

Not a Terraform replacement — no state file, no backend. `repo-policy` is safe to adopt
incrementally on a live repository: by default it only ever touches branches you declare, and
never deletes anything you didn't ask it to manage.

## Install

```bash
pip install repo-policy
```

## Quick start

```yaml
# policy.yml
version: 1

branches:
  main:
    pull_requests:
      required: true
      approvals: 2
      code_owner_review: true
    status_checks:
      required: [build, test]
    signed_commits: true
    linear_history: true
    allow_force_push: false
    allow_deletion: false
```

```bash
repo-policy validate
repo-policy audit --repo acme/widgets
repo-policy plan --repo acme/widgets
repo-policy apply --repo acme/widgets
```

## CLI Reference

| Command | Flags | Behavior | Exit codes |
|---|---|---|---|
| `validate` | `--config` (default `policy.yml`) | Parses and schema-validates the policy file only; no network calls | 0 valid, 2 invalid |
| `audit` | `--config`, `--repo`, `--token` | Read-only compliance check | 0 compliant, 1 drift found, 2 invalid config, 3 API/auth error |
| `plan` | `--config`, `--repo`, `--token` | Same diff engine as `audit`, renders a human-readable +/-/~/✓ preview | same as `audit` |
| `apply` | `--config`, `--repo`, `--token` | Executes only the changes `plan` would show | 0 success (incl. no-op), 2 invalid config, 3 API/auth error |

**Token resolution order:** `--token` → `GITHUB_TOKEN` → `GH_TOKEN`. **Repo resolution order:**
`--repo owner/name` → `$GITHUB_REPOSITORY` → the local git `origin` remote.

## GitHub Action

```yaml
- uses: shipsolid/repo-policy@v0
  env:
    GITHUB_TOKEN: ${{ secrets.REPO_POLICY_TOKEN }}
  with:
    config: .github/repository-policy.yml
    mode: audit
```

**`secrets.GITHUB_TOKEN` will not work here, in any workflow, no matter what `permissions:` you
grant it** — confirmed against a real repo. GitHub Actions' automatically-generated token has no
permission scope covering branch protection or ruleset administration; that's a platform
constraint, not something a workflow can opt into. Create a PAT with `repo` scope (classic) or
`Administration: Read and write` (fine-grained), store it as a repository secret — `REPO_POLICY_TOKEN`
above is just an example name — and reference that secret instead.

The floating tag tracks the current major version (`v0` until a `1.0.0` release ships), the same
convention `actions/checkout` and similar Actions use.

## Self-governance

This repository governs itself with its own tool: [`.github/repository-policy.yml`](.github/repository-policy.yml)
declares `main`'s branch protection and repo security settings, and
[`.github/workflows/policy-audit.yml`](.github/workflows/policy-audit.yml) runs `repo-policy audit`
against it on a daily schedule, on every change to the policy file or that workflow, and on demand.

- The audit workflow is read-only (`mode: audit`) and needs a `POLICY_AUDIT_TOKEN` repository
  secret — a fine-grained PAT scoped to this repository only, with `Administration: Read`. That
  secret does not exist yet; creating it is a live-repo setup step for whoever holds admin access.
- `.github/repository-policy.yml` documents the *intended* branch protection for this repository —
  it has not yet been applied. Until `repo-policy apply` runs against the live repository (a manual,
  reviewed step — see [SECURITY.md](SECURITY.md)'s Threat Model for why `apply` is never run
  unattended against a real repo from an untrusted trigger), it does not reflect live GitHub state.
- If a change to `main`'s required status check ever leaves it unable to produce a passing
  `CI / required` result — blocking the very fix that would repair it — see SECURITY.md's
  "Emergency Recovery" for the documented, auditable bypass procedure.

## How it works

Every declared branch is diffed against live GitHub state and reconciled through one of two
backends, selected per branch with `enforcement: branch_protection | ruleset` (default
`branch_protection`). See [ARCHITECTURE.md](ARCHITECTURE.md) for the full data flow, safety model,
and known v1 limitations, and [docs/adrs/](docs/adrs/) for why it's built this way.

## Exit codes

| Code | Meaning |
| ---- | ------- |
| 0 | Success / compliant / no-op |
| 1 | Drift detected (`audit`/`plan`) |
| 2 | Invalid `policy.yml` |
| 3 | GitHub API or auth error |

## More docs

- [ARCHITECTURE.md](ARCHITECTURE.md) — design, data flow, safety model
- [docs/adrs/](docs/adrs/) — why the key decisions were made
- [docs/ci-cd.md](docs/ci-cd.md) — release pipeline, PyPI publishing, the `v0` tag
- [docs/test-strategy.md](docs/test-strategy.md) — what's tested, and what mocking alone can't catch
- [docs/troubleshooting.md](docs/troubleshooting.md) · [FAQ.md](FAQ.md) · [SUPPORT.md](SUPPORT.md)
- [SECURITY.md](SECURITY.md) — token permissions and threat model
- [CONTRIBUTING.md](CONTRIBUTING.md) · [ROADMAP.md](ROADMAP.md) · [CHANGELOG.md](CHANGELOG.md)

## License

MIT
