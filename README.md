# repo-policy

Lightweight, declarative repository governance for GitHub. Define expected branch protection and
ruleset configuration in YAML; audit, preview, and apply it locally or in CI.

Not a Terraform replacement — no state file, no backend. `repo-policy` is safe to adopt
incrementally on a live repository: by default it only ever touches branches you declare, and never
deletes anything you didn't ask it to manage.

## Features

**Status legend:** ✅ shipped, available today · 🚧 built, not yet live · 🔜 planned next · 💡
planned later, unscheduled.

| #   | Status | Feature                                       | Description                                                                                                                                                                               |
| --- | ------ | --------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1   | ✅     | Declarative `policy.yml`                      | Define branch protection and repository governance rules in versioned YAML instead of GitHub's settings UI.                                                                               |
| 2   | ✅     | `validate` command                            | Offline schema validation of `policy.yml`; zero network calls.                                                                                                                            |
| 3   | ✅     | `audit` command                               | Read-only compliance check — reports drift against live GitHub state without changing anything.                                                                                           |
| 4   | ✅     | `plan` command                                | Human-readable, field-by-field preview (`+`/`-`/`~`/`✓`) of exactly what `apply` would change.                                                                                            |
| 5   | ✅     | `apply` command                               | Reconciles GitHub to match `policy.yml`, then independently re-verifies live state before reporting success.                                                                              |
| 6   | ✅     | Managed-scope default (no state file)         | Only touches branches and fields you explicitly declare; safe to adopt incrementally on an already-live repository.                                                                       |
| 7   | ✅     | Strict mode (opt-in)                          | Full desired-state enforcement, settable at the top level or overridden per branch.                                                                                                       |
| 8   | ✅     | Dual enforcement backends                     | Classic branch protection and GitHub Rulesets, selected per branch via `enforcement:`.                                                                                                    |
| 9   | ✅     | Ruleset ownership convention                  | Repo-policy-managed rulesets are named `repo-policy:<branch>`; strict mode prunes only the orphaned ones it owns.                                                                         |
| 10  | ✅     | Stale-protection detection                    | Flags leftover classic branch protection when a branch moves from `branch_protection` to `ruleset` enforcement.                                                                           |
| 11  | ✅     | Pull request policy controls                  | Required reviews, approval count (0–6), code owner review, dismiss stale reviews, require last push approval.                                                                             |
| 12  | ✅     | Required status checks                        | Declare the exact CI check names that must pass before a branch can merge.                                                                                                                |
| 13  | ✅     | Branch hygiene controls                       | Signed commits, linear history, force-push/deletion protection, admin enforcement, conversation resolution, branch lock, fork syncing, push-restriction clearing.                         |
| 14  | ✅     | Repo-level settings                           | Delete-branch-on-merge, allow-update-branch, Dependabot alerts/security updates, private vulnerability reporting, secret scanning + push protection.                                      |
| 15  | ✅     | Fail-closed schema validation                 | Rejects unknown fields, coerced types, duplicate YAML keys, and invalid cross-field combinations before any API call.                                                                     |
| 16  | ✅     | GitHub Action                                 | Docker-based Action wrapping the full CLI (`validate`/`audit`/`plan`/`apply`) via `config`/`mode` inputs.                                                                                 |
| 17  | ✅     | Consistent exit-code contract                 | Shared `0`/`1`/`2`/`3` exit codes across every command, safe for CI branching.                                                                                                            |
| 18  | ✅     | Partial-apply journaling                      | Line-by-line record of exactly what succeeded before a mutation failure, so re-running is always safe.                                                                                    |
| 19  | ✅     | Proxy / SOCKS support                         | Honors `HTTP_PROXY`/`HTTPS_PROXY`/`ALL_PROXY`/`NO_PROXY`, including `socks5`/`socks5h` schemes, out of the box.                                                                           |
| 20  | ✅     | Self-governance                               | This repository audits its own branch protection daily, using its own tool (`.github/workflows/policy-audit.yml`).                                                                        |
| 21  | 🚧     | Signed, verifiable release tags               | SSH-signed annotated release tags via a dedicated release-bot identity; code-complete, pending final live-repo setup (see [SECURITY.md](SECURITY.md)'s Release Pipeline Setup Checklist). |
| 22  | 🔜     | CODEOWNERS / multi-maintainer ownership       | Blocked on a second regular contributor joining the project.                                                                                                                              |
| 23  | 💡     | Org-wide policy inheritance                   | A default policy that an org's repositories inherit unless explicitly overridden.                                                                                                         |
| 24  | 💡     | Multi-repository orchestration                | `repo-policy apply` across a list of repositories in a single invocation.                                                                                                                 |
| 25  | 💡     | GitHub App authentication                     | Alternative to a PAT; would resolve the `GITHUB_TOKEN` platform limitation more elegantly.                                                                                                |
| 26  | 💡     | Full branch-protection release in strict mode | A way to fully "unprotect" a `branch_protection`-backed branch removed from `policy.yml` (currently a documented v1 limitation).                                                          |

See [ROADMAP.md](ROADMAP.md) for target versions and dates, and its "Explicitly not doing" section
for things deliberately kept out of scope (a state file, a UI/dashboard, other Git providers, a
central server).

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

| Command     | Flags                             | Behavior                                                                                                       | Exit codes                                                                                                                                                                                    |
| ----------- | --------------------------------- | -------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `--version` | —                                 | Prints `repo_policy.__version__` and exits; no network calls                                                   | 0                                                                                                                                                                                             |
| `validate`  | `--config` (default `policy.yml`) | Parses and schema-validates the policy file only; no network calls                                             | 0 valid, 2 invalid                                                                                                                                                                            |
| `audit`     | `--config`, `--repo`, `--token`   | Read-only compliance check                                                                                     | 0 compliant, 1 drift found, 2 invalid config, 3 API/auth error                                                                                                                                |
| `plan`      | `--config`, `--repo`, `--token`   | Same diff engine as `audit`, renders a human-readable +/-/~/✓ preview                                          | same as `audit`                                                                                                                                                                               |
| `apply`     | `--config`, `--repo`, `--token`   | Executes only the changes `plan` would show, then re-verifies live state from scratch before reporting success | 0 mutations converged (incl. no-op), 1 mutations succeeded but a fresh post-apply check still finds drift, 2 invalid config or setup failure, 3 API/auth error or partial-application failure |

**Token resolution order:** `--token` → `GITHUB_TOKEN` → `GH_TOKEN` → `gh auth token` (a local
`gh` CLI login, tried last and only as a convenience — see [SECURITY.md](SECURITY.md)). **Repo
resolution order:** `--repo owner/name` → `$GITHUB_REPOSITORY` → the local git `origin` remote.

See [ARCHITECTURE.md](ARCHITECTURE.md)'s "Apply Outcomes and Exit Codes" for the full preflight /
mutate / verify breakdown behind `apply`'s row above.

## Schema & Validation

`repo-policy` fails closed on ambiguous or malformed `policy.yml` input rather than guessing what
you meant (`src/repo_policy/models.py`, `src/repo_policy/config.py`):

- **Unknown fields are rejected**, not silently ignored — every schema model uses Pydantic's
  `extra="forbid"`, so a typo like `strcit:` or `secret_scaning:` is a validation error at
  `validate` time, not a quietly-ignored no-op.
- **No implicit type coercion** — every model also sets `strict=True`: a quoted boolean
  (`signed_commits: "true"`), `yes`/`no`/`on`/`off`, or a numeric string in place of a real integer
  is rejected rather than silently coerced.
- **Duplicate YAML keys are rejected** — a custom YAML loader fails a policy file with two `strict:`
  keys, two entries for the same branch name, etc., instead of PyYAML's default silent last-key-wins
  behavior.
- **`pull_requests.approvals` must be `0`–`6`** — anything outside that range is rejected before any
  API call is made.

Supported fields:

| Section         | Field                                                                                                                                                                                        | Type / range                                                                                                                    |
| --------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------- |
| top level       | `version`                                                                                                                                                                                    | must be `1`                                                                                                                     |
| top level       | `strict`                                                                                                                                                                                     | `bool` (default `false`)                                                                                                        |
| top level       | `branches`                                                                                                                                                                                   | `{branch name: branch policy}`                                                                                                  |
| top level       | `repo_settings`                                                                                                                                                                              | optional, see below                                                                                                             |
| per branch      | `enforcement`                                                                                                                                                                                | `branch_protection` (default) \| `ruleset`                                                                                      |
| per branch      | `strict`                                                                                                                                                                                     | `bool`, overrides the top-level default for this branch                                                                         |
| per branch      | `pull_requests.required`                                                                                                                                                                     | `bool` (default `true`)                                                                                                         |
| per branch      | `pull_requests.approvals`                                                                                                                                                                    | `int`, `0`–`6` (default `1`)                                                                                                    |
| per branch      | `pull_requests.code_owner_review`                                                                                                                                                            | `bool`                                                                                                                          |
| per branch      | `pull_requests.dismiss_stale_reviews`                                                                                                                                                        | `bool`                                                                                                                          |
| per branch      | `pull_requests.require_last_push_approval`                                                                                                                                                   | `bool`                                                                                                                          |
| per branch      | `status_checks.required`                                                                                                                                                                     | `list[str]`, no blank or duplicate entries                                                                                      |
| per branch      | `signed_commits`, `linear_history`, `allow_force_push`, `allow_deletion`                                                                                                                     | `bool`                                                                                                                          |
| per branch      | `enforce_admins`, `required_conversation_resolution`, `lock_branch`, `allow_fork_syncing`, `clear_restrictions`                                                                              | `bool` — **no GitHub Rulesets equivalent**; rejected under `enforcement: ruleset` unless left at their permissive (no-op) value |
| per branch      | `pull_requests.dismissal_restrictions.users`, `.teams`                                                                                                                                       | `list[str]` each, **at least one required when declared** — users/teams only (no `apps`); **no GitHub Rulesets equivalent**     |
| per branch      | `pull_requests.bypass_pull_request_allowances.users`, `.teams`, `.apps`                                                                                                                      | `list[str]` each, **at least one required when declared**; **no GitHub Rulesets equivalent**                                    |
| `repo_settings` | `delete_branch_on_merge`, `allow_update_branch`, `vulnerability_alerts`, `automated_security_fixes`, `private_vulnerability_reporting`, `secret_scanning`, `secret_scanning_push_protection` | `bool`                                                                                                                          |

Two cross-field rules are enforced at validation time, before any API call:

- `allow_fork_syncing: true` requires `lock_branch: true` on the same branch — GitHub silently
  resets `allow_fork_syncing` back to `false` otherwise.
- `automated_security_fixes: true` requires `vulnerability_alerts: true`, and
  `secret_scanning_push_protection: true` requires `secret_scanning: true` — GitHub rejects enabling
  either one before its prerequisite.

`pull_requests.dismissal_restrictions`/`pull_requests.bypass_pull_request_allowances`, when
declared, must name at least one user, team, (or app, for the bypass field) — an empty allow-list
is rejected outright rather than sent to GitHub, since it would be ambiguous between "no
restriction" and "restrict to nobody." See
[`docs/adrs/0005-nested-actor-list-fields.md`](docs/adrs/0005-nested-actor-list-fields.md) for why.

`repo-policy validate` checks all of the above against a real `policy.yml`, entirely offline.

## GitHub Action

Pin to an immutable full commit SHA — the same convention this repository's own CI uses for every
third-party Action it consumes (see `.github/workflows/ci.yml`), and the general supply-chain
hardening recommendation for any Action, including this one:

```yaml
- uses: shipsolid/repo-policy@a4d736b7fa8268115f50e61b4398d8d6a7ee7e1a # v0.4.7
  env:
    GITHUB_TOKEN: ${{ secrets.REPO_POLICY_TOKEN }}
  with:
    config: .github/repository-policy.yml
    mode: audit
```

Every release tag here (`v<version>`) is an **annotated tag object**, not a direct pointer to a
commit — so `git rev-parse v<version>` alone returns that tag object's own SHA, not the commit's,
and pinning `uses:` to it would silently defeat the point of pinning to a commit. Peel through the
tag to get the actual commit SHA to pin:

```bash
git rev-parse v<version>^{commit}
```

or find it at [github.com/shipsolid/repo-policy/tags](https://github.com/shipsolid/repo-policy/tags)
→ click the release tag → the commit it points to. (Once the release-bot signing pipeline in
`SECURITY.md`'s "Release Signing" section is live — see its Release Pipeline Setup Checklist for
current status — each of these annotated tags will also carry a verifiable SSH signature; that
doesn't change which SHA to pin here.)

**Convenience alternative — `@v0`:** a floating tag tracking the current major version (the same
convention `actions/checkout` and similar Actions use). Each release force-moves it to nest,
unpeeled, directly on top of that release's own annotated tag object (`v0` → `v<version>` → the
release commit) — this nesting is what will let `git verify-tag v0` keep verifying transitively
against the release-bot's signature after every move, once that signing pipeline is live (see
`docs/ci-cd.md`).

```yaml
- uses: shipsolid/repo-policy@v0
```

**`@v0` is movable, not immutable.** What it resolves to changes on every release without any
corresponding change to your own workflow file to review — convenient for staying current
automatically, but unsuitable anywhere change control requires a pinned, auditable dependency (the
same full-SHA-pinning convention this repository's own workflows follow for every third-party Action
_they_ consume — see [docs/ci-cd.md](docs/ci-cd.md)). Prefer the full-SHA form above unless you have
a specific reason to track the moving tag instead.

**`secrets.GITHUB_TOKEN` will not work here, in any workflow, no matter what `permissions:` you
grant it** — confirmed against a real repo. GitHub Actions' automatically-generated token has no
permission scope covering branch protection or ruleset administration; that's a platform constraint,
not something a workflow can opt into. Create a PAT with `repo` scope (classic) or
`Administration: Read and write` (fine-grained), store it as a repository secret —
`REPO_POLICY_TOKEN` above is just an example name — and reference that secret instead.

## Self-governance

This repository governs itself with its own tool:
[`.github/repository-policy.yml`](.github/repository-policy.yml) declares `main`'s branch protection
and repo security settings, and
[`.github/workflows/policy-audit.yml`](.github/workflows/policy-audit.yml) runs `repo-policy audit`
against it on a daily schedule, on every change to the policy file or that workflow, and on demand.

- The audit workflow is read-only (`mode: audit`) and needs a `POLICY_AUDIT_TOKEN` repository secret
  — a fine-grained PAT scoped to this repository only, with `Administration: Read`. That secret does
  not exist yet; creating it is a live-repo setup step for whoever holds admin access.
- `.github/repository-policy.yml` documents the _intended_ branch protection for this repository —
  it has not yet been applied. Until `repo-policy apply` runs against the live repository (a manual,
  reviewed step — see [SECURITY.md](SECURITY.md)'s Threat Model for why `apply` is never run
  unattended against a real repo from an untrusted trigger), it does not reflect live GitHub state.
- If a change to `main`'s required status check ever leaves it unable to produce a passing
  `required` result — blocking the very fix that would repair it — see SECURITY.md's "Emergency
  Recovery" for the documented, auditable bypass procedure.

## How it works

Every declared branch is diffed against live GitHub state and reconciled through one of two
backends, selected per branch with `enforcement: branch_protection | ruleset` (default
`branch_protection`). See [ARCHITECTURE.md](ARCHITECTURE.md) for the full data flow, safety model,
and known v1 limitations, and [docs/adrs/](docs/adrs/) for why it's built this way.

## Exit codes

This mapping is shared by `audit`, `plan`, and `apply` alike:

| Code | Meaning                                                                                                                                                                                                                                                                                                                                                                          |
| ---- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 0    | Success / compliant / no-op                                                                                                                                                                                                                                                                                                                                                      |
| 1    | Drift detected (`audit`/`plan`), or `apply`'s mutations succeeded but a fresh, independent post-apply check still finds drift or an `unavailable` declared setting                                                                                                                                                                                                               |
| 2    | Invalid `policy.yml`, a setup failure before any API call (missing token, unresolvable `--repo`, GitHub-client construction failure), or a `PolicyResolutionError` _after_ a successful read (GitHub's live state was unparseable, or resolving the declared policy against it produced an invalid combination) — this last case can happen even with a fully valid `policy.yml` |
| 3    | GitHub API/auth/transport error, or a partial-application failure (a mutation failed partway through an apply's mutation phase; zero or more earlier resources in that phase may already have succeeded)                                                                                                                                                                         |

## More docs

- [examples/](examples/) — `default.policy.yml` (a ready-to-use baseline for `main`, pick it up and
  go) and `sample-repository-policy.yml` (every field the schema supports, fully commented)
- [ARCHITECTURE.md](ARCHITECTURE.md) — design, data flow, safety model
- [docs/adrs/](docs/adrs/) — why the key decisions were made
- [docs/ci-cd.md](docs/ci-cd.md) — release pipeline, PyPI publishing, the `v0` tag
- [docs/test-strategy.md](docs/test-strategy.md) — what's tested, and what mocking alone can't catch
- [docs/troubleshooting.md](docs/troubleshooting.md) · [FAQ.md](FAQ.md) · [SUPPORT.md](SUPPORT.md)
- [SECURITY.md](SECURITY.md) — token permissions and threat model
- [CONTRIBUTING.md](CONTRIBUTING.md) · [ROADMAP.md](ROADMAP.md) · [CHANGELOG.md](CHANGELOG.md)

## License

MIT
