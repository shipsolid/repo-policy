---
title: Test Strategy
---

## Test Pyramid

A broad mocked unit-level suite (`pytest`, every GitHub API call mocked via `respx`), organized one
file per source module (`tests/test_<module>.py`), plus two cross-cutting files:
`tests/test_idempotency.py` (one integration-shaped test proving a second `apply` against an
already-compliant repo makes zero mutating calls) and `tests/test_policies_parity.py` (a
parametrized regression guard: for every field in `diff._FIELDS`, both the `branch_protection` and
`ruleset` backends must respond to it). Run `pytest --collect-only -q | tail -1` for the exact,
current count — it changes with every test added, so it's deliberately not hardcoded here (see
"`__version__`/PyPI version divergence" below for what hardcoding a value that's supposed to track
something else costs this project).

Alongside the mocked suite, 3 end-to-end test functions in `tests/e2e/` exercise the real GitHub
API against a live, persistent fixture repo (`shipsolid/repo-policy-e2e-fixture`): two independent
smoke tests (config validation, token/repo reachability) and one collapsed lifecycle scenario
covering everything else (see "E2E Test Isolation and Concurrency Safety" below for why it's one
test, not several). The E2E suite is excluded from the default `pytest` run (pytest marker `e2e`);
run it explicitly with `pytest -m e2e` (requires `REPO_POLICY_E2E_TOKEN`), or via the
nightly/manual `.github/workflows/e2e.yml`.

## Approach: TDD throughout

Every module in `src/repo_policy/` was built test-first: write the failing test, watch it fail for
the expected reason, implement the minimal code to pass, run the full suite, commit. The TDD
discipline itself is enforced by convention, not tooling; the resulting coverage level is enforced
by CI (`ci.yml`'s `test` job fails under 95% branch coverage — see Known Gaps for what CI does and
does not gate).

## What the mocked suite is good at

- Every GitHub API interaction is mocked via `respx` against real-shaped fixture payloads (built
  from GitHub's actual documented request/response schemas, not guesses) — see
  `tests/test_github_client.py` for retry/pagination/auth coverage.
- The diff/resolve engine (`tests/test_diff.py`) and both backend translators
  (`tests/test_policies_branch_protection.py`, `tests/test_policies_rulesets.py`) are exercised
  field-by-field, including the polarity inversion of `allow_force_push`/`allow_deletion` relative
  to every other field.
- `tests/test_idempotency.py` proves, at the unit level, that applying an already-compliant policy
  twice makes zero mutating calls the second time — the tool's core correctness promise.
- `tests/test_policies_parity.py` is a parametrized guard: for every field in `diff._FIELDS`, both
  the `branch_protection` and `ruleset` backends must respond to it. This exists specifically
  because a bug once shipped with zero test coverage in exactly the gap this test now closes.

## What mocking alone could not catch — four real bugs, found only by testing against a live repo

Mocked tests describe the API the way the author believes it behaves. All four of these bugs
passed a 100%-green mocked suite before being found:

1. **Silent field clobbering.** `apply` rebuilt the entire `required_pull_request_reviews` /
   `required_status_checks` payload on any change, hardcoding three unmodeled GitHub fields
   (`dismiss_stale_reviews`, `require_last_push_approval`, status-check `strict`) to `False` —
   silently resetting them if a human had set them manually. Found during code review, confirmed
   with a live-repo reproduction before fixing.
2. **Strict-mode phantom drift.** `diff._SCHEMA_DEFAULTS["status_checks"]` was
   `StatusChecksPolicy(required=[])`, but the real API translators represent "no status checks
   configured" as `None` — semantically identical, not `==`-equal. Every `strict: true` apply
   against an already-compliant, unconfigured branch reported permanent 1-field drift and issued
   an unnecessary API call, forever. **Only surfaced by running `repo-policy apply` with
   `strict: true` against a real repository** and noticing the tool claimed 1 change when nothing
   should have changed. No mocked test combined "strict mode" with "current state has
   `status_checks=None`" — the exact combination that broke.
3. **`__version__`/PyPI version divergence.** `python-semantic-release`'s `version_toml` config
   only updates `pyproject.toml`; the hardcoded string in `src/repo_policy/__init__.py` silently
   drifted across 4 releases. Found by literally running `pip install repo-policy` in a clean venv
   and checking `repo_policy.__version__` against `pip show`'s reported version.
4. **`allow_fork_syncing`'s wrong permissive default.** `diff._SCHEMA_DEFAULTS["allow_fork_syncing"]`
   was `True`, chosen to match the sibling `repo_security` tool's own recommended baseline value —
   never independently verified against live GitHub. **Only surfaced by running `repo-policy apply`
   against a real repository** and independently checking the resulting branch protection via
   `gh api`: GitHub silently discards `allow_fork_syncing: true` on any branch where `lock_branch`
   is `false`, resetting it to `false` regardless of what's sent. Because `True` was also the value
   `from_api(None, ...)` used to represent "nothing configured," `diff.resolve_desired()`'s
   managed-scope current-state inheritance carried the broken pairing into *any* first-time `apply`
   against a previously-unprotected branch — even for a `policy.yml` that never mentions
   `allow_fork_syncing` at all. No mocked test could catch this: it requires a real GitHub API
   response to observe that a value sent in a `PUT` doesn't persist. Fixed by flipping the default
   to `False` (the value GitHub always honors regardless of `lock_branch`) and adding a model
   validator that rejects an explicit `allow_fork_syncing: true` declaration unless `lock_branch:
   true` is also declared — see `CHANGELOG.md`'s "Correct `allow_fork_syncing`'s permissive default
   from true to false" and "Reject `allow_fork_syncing: true` without `lock_branch: true`" entries
   for the full fix history.

The pattern across all four: the bug was invisible to any test that only asserted repo-policy's
own internal consistency. Each one required checking repo-policy's output against an independent,
real source of truth — a live repo's actual API state, or a real PyPI install.

## Manual Verification Checklist (run before any release you don't fully trust)

Steps 1–7 below are now automated in `tests/e2e/test_fixture_repo.py` (run via `pytest -m e2e`,
or the nightly `.github/workflows/e2e.yml`) — see `CHANGELOG.md`'s "Add full E2E lifecycle
coverage against the live fixture repo" and related entries for the build-out history. This
checklist remains as the human-readable reference and manual fallback for anyone without CI access
to the fixture repo.

Against a disposable repository:

1. `validate` a config, confirm exit 0 on valid / exit 2 on invalid.
2. `plan` against an unprotected branch — confirm it reports every field as a change.
3. `apply`, then independently confirm via
   `gh api repos/<owner>/<repo>/branches/<branch>/protection` that the real state matches.
4. `plan` again — confirm zero drift (real-world idempotency, not just the mocked test).
5. Repeat 2-4 with `enforcement: ruleset` on a second branch, cross-checking
   `gh api repos/<owner>/<repo>/rulesets`.
6. Remove a declared branch from the config with `strict: true`, `apply`, confirm the orphaned
   ruleset (and only that one) is deleted.
7. Restore the repository to its original state.

The automated suite now also covers a scenario beyond these 7 steps: after `apply`, directly
disable the managed ruleset through the raw GitHub API (bypassing repo-policy), confirm `audit`/
`plan` report it as drift, repair it with another `apply`, and independently re-confirm the
branch's live effective rules — see "Ineffective-ruleset repair" below.

## E2E Test Isolation and Concurrency Safety

`tests/e2e/` mutates one shared, persistent, real repository (`shipsolid/repo-policy-e2e-fixture`)
across every local run and every CI run. Two hazards follow directly from that: test-order
dependence within a single run, and two runs (a manual `workflow_dispatch` and the nightly
schedule, or two manual dispatches) racing each other's mutations against the same repo. Both are
addressed structurally, not by convention:

- **One collapsed lifecycle test, not several dependent ones.** Earlier revisions of this suite
  split "plan shows drift", "apply succeeds", "plan shows zero drift", and "strict apply prunes"
  into separate test functions that only produced a coherent story because pytest happened to run
  them in file order against the same session-scoped `clean_fixture_repo` fixture — one docstring
  literally said "Must run after test_plan_reports_full_drift...". `test_full_policy_lifecycle` in
  `tests/e2e/test_fixture_repo.py` replaces all of them with one function whose only ordering is
  its own statement order: clean baseline → `plan` (full drift) → `apply` → independent API
  verification → no-drift `audit`/`plan` → **ineffective-ruleset repair** (disable the managed
  ruleset directly via the raw API, confirm `audit`/`plan` surface it as drift, repair with
  another `apply`, re-confirm the branch's live effective rules) → strict prune (switch to a
  policy that drops the ruleset-enforced branch, confirm the orphan is deleted) → final cleanup.
  There is no longer a subset or reordering of this suite that can produce a different outcome.
- **Independent smoke tests stay independent.** `test_validate_accepts_e2e_fixture_policies` (pure
  config parsing, no API calls) and `test_live_token_and_repo_are_reachable` (token/repo
  reachability only) never take `clean_fixture_repo` and never assert anything about live policy
  state, so they remain correct regardless of what state the fixture repo happens to be in.
- **Cleanup is guaranteed even when setup itself fails partway.** `clean_fixture_repo`
  (`tests/e2e/conftest.py`) registers its teardown via `request.addfinalizer` *before* performing
  its first mutation, rather than relying on the tail half of a `yield`-based generator fixture.
  pytest only turns a generator fixture's post-`yield` code into a registered finalizer once the
  code *before* `yield` has already returned successfully — so if a setup step after the first
  mutation had raised (e.g. the ruleset-branch existence check), the old design would never have
  reached `yield` and would silently skip scheduling any cleanup at all. `request.addfinalizer`
  has no such gap: once registered, it always runs at session teardown, regardless of what happens
  afterward in this fixture's own remaining setup or in any test built on top of it.
- **Concurrent workflow dispatch cannot overlap.** `.github/workflows/e2e.yml` declares
  `concurrency: {group: repo-policy-e2e-fixture, cancel-in-progress: false}`. A fixed group name
  (not templated on a ref or run id) means every trigger of this workflow — scheduled or manual —
  shares one queue; `cancel-in-progress: false` queues a second run behind the first instead of
  cancelling it, since a cancelled mid-mutation run would abandon the fixture repo in whatever
  partial state the cancellation caught it in, defeating the cleanup guarantee above. The net
  effect: GitHub Actions structurally never lets two runs of this workflow touch the fixture repo
  at the same time, regardless of how they were triggered.

## Known Gaps

- ~~No CI-enforced coverage threshold~~ — closed: `ci.yml`'s `test` job runs
  `pytest --cov-fail-under=95` on every Python version in the support matrix (3.10–3.14), and
  `ruff format --check` blocks merging on formatting drift.
- ~~No automated end-to-end test against a real GitHub repository~~ — closed: see `tests/e2e/`
  and `.github/workflows/e2e.yml`.
- No test exercises GitHub's classic-branch-protection-specific edge cases beyond what's modeled
  (e.g. `restrictions` with actual user/team push restrictions configured, or
  `dismissal_restrictions`/`bypass_pull_request_allowances`). **2026-09-22: attempted with a real
  second collaborator (`shipsolid-release-bot`) added to `shipsolid/repo-policy-e2e-fixture`** —
  blocked by a GitHub API constraint, not a missing identity: `422 "Only organization repositories
  can have users and team restrictions"`. The fixture repo is personal-account-owned
  (`owner.type: "User"`); GitHub rejects named user/team restrictions on any personal repo
  regardless of collaborator count. Live-verifying these three fields needs the fixture repo (or a
  second, dedicated one) to live under a GitHub organization — see `ROADMAP.md`'s "Next" table.
