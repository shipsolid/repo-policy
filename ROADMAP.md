# Roadmap

> Last updated 2026-09-20 · Owner Amit Singh

## Now

| Item | Why it matters | Status | Target |
| ------------------------------------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------- | ------ |
| Real-world hardening from live-repo testing | 3 real bugs found via live verification against a disposable repo (see `docs/test-strategy.md`) were fixed this cycle | shipped | v0.1.4 |
| Document the `GITHUB_TOKEN` platform limitation | Every Action consumer would otherwise hit an unexplained 403 on first use | shipped | v0.1.4 |
| Model the 6 previously-unenforced branch-protection fields | Closed a real coverage gap against a sibling tool's fixed baseline; `enforce_admins` in particular is the highest-impact single field repo-policy didn't enforce | shipped | TBD |
| Model repo-level settings (7 fields: Dependabot, secret scanning, repo settings) | Second half of closing the gap against a sibling tool's fixed baseline — branch-protection fields (above) already shipped | shipped | TBD |
| Model `clear_restrictions`, closing the last repo_security field gap | Every other field from the sibling tool's baseline was already covered by Phase 1/2; this was the one remaining gap | shipped | TBD |
| Fix allow_fork_syncing's wrong permissive default | Found via live-repo verification: GitHub silently discards allow_fork_syncing: true unless lock_branch: true is also set, causing permanent phantom drift on any first-time apply | shipped | TBD |
| Automated end-to-end test against a disposable real repo | Closes the last manual-only gap in `docs/test-strategy.md`; runs nightly/on-demand against `shipsolid/repo-policy-e2e-fixture` via `.github/workflows/e2e.yml` | shipped | TBD |
| Model `dismissal_restrictions`/`bypass_pull_request_allowances` (PR-review actor-list fields) | Closes the last two unmodeled `required_pull_request_reviews` fields; `bypass_pull_request_allowances` is the mechanism for letting bots (release automation, Dependabot) merge without a human review — see `docs/adrs/0005-nested-actor-list-fields.md` | shipped | TBD |

## Next

| Item                                                     | Why                                                                                    | Dependency                                | Target |
| -------------------------------------------------------- | -------------------------------------------------------------------------------------- | ----------------------------------------- | ------ |
| CODEOWNERS / multi-maintainer ownership                  | Currently single-maintainer; not yet warranted                                         | a second regular contributor              | TBD    |
| Live-verify `dismissal_restrictions`/`bypass_pull_request_allowances`/`restrictions` against the e2e fixture | Unit/model coverage is complete but the real API behavior is unconfirmed. **2026-09-22: attempted with a real second collaborator (`shipsolid-release-bot`) added to `shipsolid/repo-policy-e2e-fixture` — GitHub rejected it outright: `422 "Only organization repositories can have users and team restrictions"`.** A second identity alone isn't sufficient; the fixture repo itself is personal-account-owned (`owner.type: "User"`), and GitHub structurally disallows named user/team restrictions on any personal repo, regardless of collaborator count | `shipsolid/repo-policy-e2e-fixture` (or a second, dedicated fixture) living under a GitHub organization, not a personal account | TBD    |

## Later (directional, unscheduled)

- Org-wide policy inheritance (a default policy an org's repos inherit unless overridden)
- Multi-repository orchestration (`repo-policy apply` across a list of repos in one invocation)
- GitHub App authentication, as an alternative to a PAT (would resolve the `GITHUB_TOKEN` limitation
  more elegantly than "store a PAT as a secret," at the cost of an installable App)
- A way to fully "release" a `branch_protection`-backed branch from repo-policy management in strict
  mode (currently a known, documented limitation — see `ARCHITECTURE.md`)

## Explicitly not doing

- **A state file.** The entire safety model (`ARCHITECTURE.md`, `docs/adrs/0003-*`,
  `docs/adrs/0004-*`) is built around not needing one. Any future feature that would require one is
  a sign it belongs in a different tool.
- **A UI or dashboard.** repo-policy is a CLI + Action; a UI is out of scope by design (see README's
  positioning against Terraform / Safe Settings).
- **Other Git providers (GitLab, Bitbucket).** The domain model (branch protection + rulesets) is
  GitHub-specific; supporting another provider would mean a second, differently-shaped backend pair,
  not a small addition.
- **A central server or database.** Zero infrastructure is the differentiator; adding either would
  contradict it.
