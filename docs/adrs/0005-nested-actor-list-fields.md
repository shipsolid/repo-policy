---
adr: 0005
title: Nested actor-list fields — validator placement and the all-empty-declaration rule
status: accepted
date: 2026-09-22
deciders: Amit Singh
domain: platform
---

## Context

`dismissal_restrictions` ("Restrict who can dismiss pull request reviews") and
`bypass_pull_request_allowances` ("Allow specified actors to bypass required pull requests") are
the first fields this codebase models that are both (a) nested inside another optional field
(`pull_requests`, not their own top-level `BranchPolicy` field) and (b) an actor list (users/teams/
apps) rather than a scalar. Both decisions below came up while modeling them and apply to any
future nested or actor-list field, not just these two.

## Decision

**1. Ruleset-unsupported nested fields get their own explicit model validator, not a generalized
`FIELD_SPECS` entry.** Every other "no GitHub Rulesets equivalent" field (`enforce_admins`,
`required_conversation_resolution`, `lock_branch`, `allow_fork_syncing`, `clear_restrictions`) is
tracked in `FIELD_SPECS`/`_RULESET_UNSUPPORTED_FIELDS` and rejected by one generic
`BranchPolicy` validator that does `getattr(self, name)` on each top-level field name. These two
fields live at `pull_requests.dismissal_restrictions`/`pull_requests.bypass_pull_request_allowances`
— `getattr(self, name)` can't reach them. Rather than bend `FIELD_SPECS` into supporting dotted
paths for a single case, `BranchPolicy` gets one more explicit, separately-named validator
(`_reject_ruleset_unsupported_pull_request_fields`) that reaches into `self.pull_requests` directly.

**2. An actor-list field, when declared at all, must name at least one user/team(/app) — an
all-empty declaration (`{users: [], teams: []}`) is rejected at `validate` time, not sent to
GitHub.** This session had no live GitHub access to confirm whether GitHub's API treats a
freshly-authored empty `dismissal_restrictions`/`bypass_pull_request_allowances` as "no
restriction" or "restrict to nobody" — and unlike most of this codebase's other live-behavior
questions (resolved by testing against the real `shipsolid/repo-policy-e2e-fixture` repo, see
`docs/test-strategy.md`), getting this one wrong in production is a lockout footgun: it could mean
nobody can dismiss a stale review, or nobody can bypass a broken required check, on a real team's
repository. Rather than ship a guess, the schema makes the ambiguous state unreachable from
authored `policy.yml`: `DismissalRestrictions`/`BypassPullRequestAllowances` each reject
construction when every field is empty.

This rule applies only to what a human *authors*. Reading a pre-existing all-empty state from live
GitHub (e.g. set by some other tool via the raw API) still works — `from_branch_protection`
constructs the same model, so the same validator fires, but it does so *inside*
`branch_protection.from_api`'s existing `try/except ValidationError` block, which already exists
for exactly this class of problem (see its handling of GitHub returning an
`allow_fork_syncing`/`lock_branch` combination `BranchPolicy`'s own validator rejects). The
`ValidationError` becomes a `PolicyResolutionError` ("GitHub's current branch-protection state for
this branch is internally inconsistent and could not be parsed") — a clear failure on `audit`/
`plan`/`apply` for that specific branch, not a crash and not a silent misread. This is a real,
if narrow, behavior change: a branch that previously worked fine (because these two fields were
unmodeled and merely passed through) can now fail this way if it happens to carry an all-empty
allow-list. Judged an acceptable, honest trade-off over guessing at unverified API semantics.

## Alternatives Considered

- **Allow an empty declaration and document the risk**, matching how `enforce_admins: true`'s
  lockout risk is handled (allowed, documented, not blocked). Rejected: `enforce_admins`'s
  semantics are fully understood, just risky — this is genuine *ambiguity* about what GitHub's API
  even does, a different and more dangerous category. This codebase's own precedent
  (`docs/adrs/0003-*`'s phantom-drift story) is to fail closed on ambiguity, not on risk alone.
- **Promote both fields to top-level `BranchPolicy` fields** instead of nesting them under
  `pull_requests`, so the existing generic `FIELD_SPECS` mechanism could track them with zero new
  validator code. Rejected: both settings are meaningless without `pull_requests.required: true`,
  and nesting gets that for free — `to_branch_protection` already returns `None` entirely when
  `required` is `False`, discarding every `pull_requests` sub-field the same way. A top-level
  placement would need its own new cross-field validator to reject the same contradiction
  (`bypass_pull_request_allowances` declared while `pull_requests.required: false`), for no real
  benefit over the one nested-field validator this decision already needs.
- **Generalize `FieldSpec` to support dotted/nested paths** so both this case and any future one
  reuse the same mechanism. Rejected for now as speculative generalization for a single instance —
  revisit if a second nested field shows up.

## Consequences

- `BranchPolicy` now has two mechanisms for "no Rulesets equivalent": the generic
  `FIELD_SPECS`-driven one (five top-level fields) and this one explicit nested-field validator
  (two fields). A reader must know both exist; each is commented to point at the other.
- `PullRequestPolicy` is no longer reliably hashable once either actor-list field is populated
  (both are list-bearing nested models) — same honest caveat `StatusChecksPolicy` already carries
  for its own `required: list[str]` field. `Change` (`diff.py`) only ever needs this hashability
  with both fields at their default `None`, so nothing currently exercises the broken case.
- A branch whose live GitHub state has an all-empty (but present) `dismissal_restrictions`/
  `bypass_pull_request_allowances` — however it got there — makes `audit`/`plan`/`apply` fail with
  `PolicyResolutionError` for that branch, even if `policy.yml` doesn't declare the field at all.
  Not verified against a real repository in this session (no live GitHub access) — flagged as a
  live-verification gap for the next `pytest -m e2e` run against `shipsolid/repo-policy-e2e-fixture`
  (`docs/test-strategy.md`).

## Links

- `src/repo_policy/models.py` (`DismissalRestrictions`, `BypassPullRequestAllowances`,
  `PullRequestPolicy`, `BranchPolicy._reject_ruleset_unsupported_pull_request_fields`)
- `src/repo_policy/policies/pull_requests.py` (`to_branch_protection`, `from_branch_protection`)
- `src/repo_policy/policies/branch_protection.py` (`from_api`'s `except ValidationError` handling)
- `docs/adrs/0003-managed-scope-default-strict-opt-in.md` (the fail-closed-on-ambiguity precedent)
- `docs/adrs/0002-explicit-enforcement-field-per-branch.md` (the closest prior "valid under one
  backend only" precedent, though for a top-level field)
