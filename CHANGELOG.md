# CHANGELOG


## v0.5.0 (2026-09-21)

### Bug Fixes

- Release job detaches HEAD before computing the version bump
  ([#25](https://github.com/shipsolid/repo-policy/pull/25),
  [`426acce`](https://github.com/shipsolid/repo-policy/commit/426accecc2e066afeae444106a4e40cfdd11e7f1))

The "Fast-forward to main's current tip" step ran `git checkout origin/main` -- checking out the
  remote-tracking ref directly leaves the working tree in detached HEAD state, distinct from `git
  checkout main`. semantic-release's `branch = "main"` config (pyproject.toml) refuses to compute a
  version there: "Detached HEAD state cannot match any release groups" (confirmed live -- run
  35652737004, triggered by PR #24 landing on main).

The job's own initial `actions/checkout` (no `ref:` override) already leaves HEAD attached to local
  `main` for a push-to-main trigger; this step only needs to fast-forward that local branch to
  origin/main's current tip (in case other PRs merged while the job sat at the environment-approval
  gate), not re-checkout anything. `git merge --ff-only origin/main` does that while staying on the
  branch, and still fails loudly under this step's existing `set -euo pipefail` if origin/main
  somehow isn't a fast-forward -- the same safety property the checkout-based version accidentally
  had.

Confirmed via grep this was the only `checkout origin/<branch>` occurrence across every workflow
  file.

Co-authored-by: Claude Sonnet 5 <noreply@anthropic.com>

### Chores

- Update ([#22](https://github.com/shipsolid/repo-policy/pull/22),
  [`e9d199f`](https://github.com/shipsolid/repo-policy/commit/e9d199f8eee74c45bf48d905a2cc7dec73144bdc))

- Update ([#23](https://github.com/shipsolid/repo-policy/pull/23),
  [`eb7e70e`](https://github.com/shipsolid/repo-policy/commit/eb7e70ed9c27511a42fe4691b919a9367e1c74d9))

### Continuous Integration

- Fast-forward the release job to main before computing the version bump
  ([`d8b9862`](https://github.com/shipsolid/repo-policy/commit/d8b98620a5e88dafe6e4a4a47c045e5b2ba32444))

The release job's checkout defaults to github.sha, frozen at trigger time, but the job can sit for
  up to 45 minutes at the release environment's human-approval gate. Any commit that merges to main
  in that window -- including this session's own Task 4 -- leaves the version-bump commit built on
  stale state, which GitHub's PAT-workflow-scope protection correctly rejected on 2026-09-21 (run
  35633848337) once a workflow file had changed in the interim. Fast-forward to origin/main's
  current tip right before computing the bump, and rebind the 'only touched version/changelog files'
  validation to the same fresh baseline, so the release commit's tree can never disagree with main
  on any path.

Co-authored-by: Claude Sonnet 5 <noreply@anthropic.com>

### Features

- Pr-review actor-list fields and gh CLI token fallback
  ([#24](https://github.com/shipsolid/repo-policy/pull/24),
  [`e9893c0`](https://github.com/shipsolid/repo-policy/commit/e9893c08f30ab118a396954e5e4b5d71a84c22e3))

* feat: model dismissal_restrictions and bypass_pull_request_allowances

GitHub's "Restrict who can dismiss pull request reviews" and "Allow specified actors to bypass
  required pull requests" were previously read-through-preserved only (policies/pull_requests.py
  could never declare either) -- both are now fully modeled PullRequestPolicy sub-fields, following
  the exact migration pattern commit 41e59cb used for
  dismiss_stale_reviews/require_last_push_approval.

Both are nested inside pull_requests, not their own top-level BranchPolicy field -- they only mean
  anything when pull_requests.required: true, so nesting gets that "meaningless when not required"
  case handled for free via to_branch_protection's existing early return. Neither has a GitHub
  Rulesets equivalent; a new, separately-named BranchPolicy validator
  (_reject_ruleset_unsupported_pull_request_fields) rejects declaring either under enforcement:
  ruleset, since the existing FIELD_SPECS/ _RULESET_UNSUPPORTED_FIELDS mechanism only tracks
  top-level field names.

An explicitly-declared actor list must name at least one user/team(/app) -- an all-empty declaration
  is rejected at validate time rather than sent to GitHub, since this session had no live GitHub
  access to confirm whether GitHub's API treats a freshly-authored empty allow-list as "no
  restriction" or "restrict to nobody." See docs/adrs/0005-nested-actor-list-fields.md for the full
  reasoning, including the resulting known gap: audit/plan/apply now fail with PolicyResolutionError
  for a branch whose *live* GitHub state happens to carry an all-empty-but-present value for either
  field. Flagged in ROADMAP.md as needing live e2e verification once a real identity for
  shipsolid/repo-policy-e2e-fixture is available.

dismissal_restrictions supports only users/teams (no apps field at all, matching GitHub's real API);
  bypass_pull_request_allowances supports users/teams/apps -- the mechanism for letting a release
  bot or Dependabot merge without a human review.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>

* feat: fall back to gh auth token for local CLI use

_resolve_token previously only checked --token, GITHUB_TOKEN, and GH_TOKEN -- a user running
  repo-policy locally had to manually export a token even when already logged in via `gh auth
  login`. Adds a fourth, silent, last-resort fallback: _gh_cli_token() shells out to `gh auth
  token`, mirroring _resolve_repo's existing `git remote get-url origin` fallback exactly
  (subprocess.run with check=False, FileNotFoundError for a missing binary, returncode checked
  before trusting stdout).

Only reached when all three explicit sources are absent -- an intentional flag or env var always
  wins. Verified end-to-end against the real GitHub API (audit against shipsolid/repo-policy with
  GITHUB_TOKEN/GH_TOKEN unset, using this machine's real `gh` login). Pure local-CLI convenience:
  GitHub Actions usage is unaffected, since an Actions runner never has an interactive `gh auth
  login` session to reuse and already requires an explicit token regardless.

Caught and fixed a real test fragility while implementing:
  test_audit_reports_usage_error_when_no_token_configured only deleted GITHUB_TOKEN/GH_TOKEN, so on
  any machine with `gh` actually logged in (this dev machine included) it would have started passing
  for the wrong reason -- now also mocks `gh auth token` to fail, restoring a deterministic "no
  token available at all" scenario.

SECURITY.md documents the new trust boundary this introduces (shelling out to whatever `gh` binary
  is first on PATH) explicitly, rather than leaving it implicit.

---------

Co-authored-by: Claude Sonnet 5 <noreply@anthropic.com>


## v0.4.10 (2026-09-21)

### Bug Fixes

- Report absent delete_branch_on_merge/allow_update_branch as unavailable, not drift
  ([`907b950`](https://github.com/shipsolid/repo-policy/commit/907b950a3d480bccef55daf583369374b4d846d5))

GET /repos/{owner}/{repo} omits both keys when the token cannot see them (a fine-grained
  Administration: Read-only PAT, live-confirmed on shipsolid/repo-policy). diff_flat_settings read
  the absent key as False and reported a permanent false 'add', which is why every policy-audit.yml
  run failed since the audit token was created. Route absent keys to 'unavailable', the same
  contract diff_security_and_analysis adopted in v0.4.8.

Co-authored-by: Claude Sonnet 5 <noreply@anthropic.com>

- Resolve the repository from SSH host-alias origin URLs
  ([`6b08b1a`](https://github.com/shipsolid/repo-policy/commit/6b08b1a6493d01eeef15a8202cb48d40cb2b18b2))

* fix: resolve the repository from SSH host-alias origin URLs

git@github.com-work:owner/name (an ~/.ssh/config alias) previously fell through to 'could not
  determine repository' and forced --repo.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>

* fix: tighten github.com host match to reject lookalike domains

The prior _GITHUB_REMOTE regex had no left boundary before `github\.com` and allowed dots in the
  alias-suffix segment, so a lookalike host containing that substring (mygithub.com,
  github.company.com, github.comcast.net) was silently mis-resolved to a valid-looking owner/repo
  instead of raising 'could not determine repository' as docs/troubleshooting.md promises.

Anchor on an actual host-start position ((?:^|[@/])) and restrict the optional ~/.ssh/config alias
  suffix to a `-`-prefixed segment, matching the original task-8 intent without regressing any of
  the four supported origin shapes.

---------

Co-authored-by: Claude Sonnet 5 <noreply@anthropic.com>

### Build System

- Pin dev toolchain versions so CI gates cannot drift on upstream releases
  ([`952199e`](https://github.com/shipsolid/repo-policy/commit/952199ec5ed20c310728e1f084c2c030d3f197a6))

Co-authored-by: Claude Sonnet 5 <noreply@anthropic.com>

### Chores

- Update ([#14](https://github.com/shipsolid/repo-policy/pull/14),
  [`71c0268`](https://github.com/shipsolid/repo-policy/commit/71c0268b9cfa12e58940ea01bd1431529c55f8df))

- **release**: V0.4.10 [skip ci]
  ([`82516ca`](https://github.com/shipsolid/repo-policy/commit/82516ca849d9daf0bc1c2fcd803da74047e55e15))

### Continuous Integration

- Harden e2e.yml -- explicit permissions, no persisted checkout credentials, named job
  ([`3fa89a3`](https://github.com/shipsolid/repo-policy/commit/3fa89a37361ecc86bc38a39b7b0ba39afdaed1f9))

Brings the last unhardened workflow to the baseline the other four already meet, and closes CodeQL
  alert #2 (actions/missing-workflow-permissions).

Co-authored-by: Claude Sonnet 5 <noreply@anthropic.com>

- Ignore Python major/minor base-image bumps in Dependabot
  ([`a80fae7`](https://github.com/shipsolid/repo-policy/commit/a80fae7f8944b4ee87950d5f1fea7af6416dde22))

The Action's dependency lock is compiled for Python 3.12; a 3.13/3.14 base-image PR can never pass
  verify-action-lock without a matching lock regeneration, so it must be a deliberate change, not a
  weekly proposal.

Co-authored-by: Claude Sonnet 5 <noreply@anthropic.com>

### Documentation

- Record Task 14's production-readiness evidence
  ([`778bfa6`](https://github.com/shipsolid/repo-policy/commit/778bfa6eea31d386ed4ae4c4517bd7feabdb3992))

Adds docs/release-readiness-v1.md consolidating the full evidence trail for Task 14: pre-release
  checks, live self-governance verification (2 real gaps found and fixed), release trust artifacts
  independently re-verified against v0.4.9, the full incident narrative (3 real bugs found and fixed
  across 4 live release attempts), and the re-audit from published artifacts. Decision recorded: GO.


## v0.4.9 (2026-09-21)

### Bug Fixes

- Advance local main to the merged commit before creating the GitHub release
  ([`d55a55d`](https://github.com/shipsolid/repo-policy/commit/d55a55d2d9920ba1bdb3b2250120dc3dd2bfbe8b))

The release job's checkout happens once, at the start, before the version-bump commit and
  squash-merge. Nothing reconciled local main with the real merged commit before the
  changelog/release-creation step, which needs local HEAD to have reached it. This fix resets local
  main to the merged commit right before that step runs. v0.4.8's commit and tag are already live
  and correct; that release never completed (no GitHub Release, no PyPI publish). This fix will
  trigger its own release (v0.4.9) that supersedes the incomplete v0.4.8.

### Chores

- **release**: V0.4.9 [skip ci]
  ([`ba69faa`](https://github.com/shipsolid/repo-policy/commit/ba69faa05ab3a2128ec52739b096d83a42bb7504))


## v0.4.8 (2026-09-21)

### Bug Fixes

- Don't report secret scanning as drifted when token can't see security_and_analysis
  ([`21a156e`](https://github.com/shipsolid/repo-policy/commit/21a156e5d65b769020a569a74bac1094aaccb59a))

diff_security_and_analysis() collapsed two different GET /repos/{owner}/{repo} response shapes into
  the same "disabled" reading: (1) the security_and_analysis block present but a sub-key
  legitimately absent/off, and (2) the whole block missing because the authenticated token lacks
  permission to see it. Case 2 was treated as "disabled" for diff purposes, producing a false
  positive on the live self-audit.

diff_security_and_analysis now returns (changes, unavailable_field_names) instead of just a list of
  changes -- when the whole block is absent, every declared field routes to `unavailable` rather
  than being diffed as False, mirroring the existing diff_toggle/private_vulnerability_reporting
  pattern. Behavior is unchanged when the block is present but a sub-key is absent -- that still
  means "disabled" and still produces a real Change.

### Chores

- Revert erroneous v1.0.0 version bump back to 0.4.7
  ([`240da00`](https://github.com/shipsolid/repo-policy/commit/240da00920083105d79c0927c2d0d29f9ec8c509))

The release pipeline's version-computation step computed 1.0.0 instead of the correct 0.4.8 for a
  patch-level release, and that wrong version landed on main before the pipeline failed at a later
  step. Root cause (a python-semantic-release version mismatch between two steps of release.yml) is
  being fixed separately. Nothing was ever published externally under 1.0.0 -- no PyPI release, no
  GitHub Release entry. The erroneous v1.0.0/v1 tags have been deleted.

- Revert second erroneous v1.0.0 bump and finish the PSR pinning fix
  ([`86420bc`](https://github.com/shipsolid/repo-policy/commit/86420bcd54f92dbf8d99a76fa73fea22ce51a05e))

Fixes the root cause of a live incident (twice): release.yml's version-bump step ran
  python-semantic-release via a SHA-pinned GitHub Action, but the SHA pins the Action's own code,
  not the PSR package version its Dockerfile installs. It installed 10.6.2 instead of 9.21.2,
  computing 1.0.0 instead of 0.4.8. All three PSR invocations in release.yml now pin 9.21.2
  explicitly via pip, and the version-bump step now sets GIT_COMMIT_AUTHOR explicitly (PSR overrides
  host git config for committer identity). Both errant v1.0.0/v1 tag pairs have been deleted from
  origin.

- **deps**: Bump the github-actions-dependencies group across 1 directory with 5 updates
  ([#3](https://github.com/shipsolid/repo-policy/pull/3),
  [`d99b109`](https://github.com/shipsolid/repo-policy/commit/d99b109b73b13cd500c575d904529d9fffeaa1b2))

Bumps the github-actions-dependencies group with 5 updates in the / directory:

| Package | From | To | | --- | --- | --- | |
  [actions/checkout](https://github.com/actions/checkout) | `4.4.0` | `7.0.1` | |
  [actions/setup-python](https://github.com/actions/setup-python) | `5.6.0` | `7.0.0` | |
  [actions/upload-artifact](https://github.com/actions/upload-artifact) | `4.6.2` | `7.0.1` | |
  [actions/download-artifact](https://github.com/actions/download-artifact) | `4.3.0` | `8.0.1` | |
  [python-semantic-release/python-semantic-release](https://github.com/python-semantic-release/python-semantic-release)
  | `9.21.2` | `10.6.2` |

Updates `actions/checkout` from 4.4.0 to 7.0.1 - [Release
  notes](https://github.com/actions/checkout/releases) -
  [Changelog](https://github.com/actions/checkout/blob/main/CHANGELOG.md) -
  [Commits](https://github.com/actions/checkout/compare/11d5960a326750d5838078e36cf38b85af677262...3d3c42e5aac5ba805825da76410c181273ba90b1)

Updates `actions/setup-python` from 5.6.0 to 7.0.0 - [Release
  notes](https://github.com/actions/setup-python/releases) -
  [Commits](https://github.com/actions/setup-python/compare/a26af69be951a213d495a4c3e4e4022e16d87065...5fda3b95a4ea91299a34e894583c3862153e4b97)

Updates `actions/upload-artifact` from 4.6.2 to 7.0.1 - [Release
  notes](https://github.com/actions/upload-artifact/releases) -
  [Commits](https://github.com/actions/upload-artifact/compare/ea165f8d65b6e75b540449e92b4886f43607fa02...043fb46d1a93c77aae656e7c1c64a875d1fc6a0a)

Updates `actions/download-artifact` from 4.3.0 to 8.0.1 - [Release
  notes](https://github.com/actions/download-artifact/releases) -
  [Commits](https://github.com/actions/download-artifact/compare/d3f86a106a0bac45b974a628896c90dbdf5c8093...3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c)

Updates `python-semantic-release/python-semantic-release` from 9.21.2 to 10.6.2 - [Release
  notes](https://github.com/python-semantic-release/python-semantic-release/releases) -
  [Changelog](https://github.com/python-semantic-release/python-semantic-release/blob/master/CHANGELOG.rst)
  -
  [Commits](https://github.com/python-semantic-release/python-semantic-release/compare/21ed7fa03e4a17ac49406eff4b60d5ad050fbdc2...9a026e9303981c866c3425723009becb2437c757)

--- updated-dependencies: - dependency-name: actions/checkout dependency-version: 7.0.1

dependency-type: direct:production

update-type: version-update:semver-major

dependency-group: github-actions-dependencies

- dependency-name: actions/download-artifact dependency-version: 8.0.1

- dependency-name: actions/setup-python dependency-version: 7.0.0

- dependency-name: actions/upload-artifact dependency-version: 7.0.1

- dependency-name: python-semantic-release/python-semantic-release dependency-version: 10.6.2

dependency-group: github-actions-dependencies ...

Signed-off-by: dependabot[bot] <support@github.com>

Co-authored-by: dependabot[bot] <49699333+dependabot[bot]@users.noreply.github.com>

- **release**: V0.4.8 [skip ci]
  ([`b759aa4`](https://github.com/shipsolid/repo-policy/commit/b759aa479e6dbf0b45f0819301e7ad7117374a8b))

- **release**: V1.0.0 [skip ci]
  ([`de59e98`](https://github.com/shipsolid/repo-policy/commit/de59e98ee5e45113f5f9ab3e94c4509201a01c3f))

- **release**: V1.0.0 [skip ci]
  ([`2171dd0`](https://github.com/shipsolid/repo-policy/commit/2171dd0db9dbdf558172d209c3fc86dbaf69b95a))


## v0.4.7 (2026-09-20)

### Bug Fixes

- Audit/plan warn about orphaned rulesets before strict apply prunes them
  ([`add393a`](https://github.com/shipsolid/repo-policy/commit/add393a7272a90f3f1013c25e9d257d65fbb7520))

audit_all() only checked declared branches and stale classic branch protection -- it never scanned
  for orphaned repo-policy:* rulesets, so a clean audit/plan could report full compliance right up
  until the next strict apply's prune_rulesets silently deleted one. Extracted the orphan-finding
  logic prune_rulesets already had into a shared apply.find_orphaned_ruleset_names(), added
  audit.detect_orphaned_rulesets() (gated on config.strict the same way prune_rulesets itself is
  invoked), and folded it into audit_all()'s result. audit_all() now returns (results,
  orphaned_rulesets) instead of just results -- all five existing call sites/tests updated
  accordingly. cli.py's audit/plan now warn about each orphan and count it toward the drift exit
  code.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>

- Dedupe duplicate check contexts when reading required_status_checks
  ([`a41fded`](https://github.com/shipsolid/repo-policy/commit/a41fdeddb2191732a63250d62306b2089fa8a67a))

from_branch_protection's checks-array fallback never deduplicated context names, so two entries
  sharing a context but different app_id (e.g. mid-migration between CI apps) collapsed into a
  duplicated required list -- re-serialized, that produced two identical {"context": X, "app_id":
  None} entries and lost the distinct app-scoping entirely. dict.fromkeys() dedupes while preserving
  first-occurrence order.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>

- Handle explicit null users/teams/apps key in _actor_refs
  ([`2a7de34`](https://github.com/shipsolid/repo-policy/commit/2a7de342e6cad23a268f03bfdcafc2ca1de68e89))

raw.get(key, []) only substitutes the default when the key is absent, not when it's present with an
  explicit null value -- if GitHub's GET response for
  restrictions/dismissal_restrictions/bypass_pull_request_allowances ever sent "apps": null instead
  of omitting the key, this crashed with TypeError: 'NoneType' object is not iterable. Switched to
  raw.get(key) or [], which treats both the same.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>

- Make BranchPolicy, RepoSettingsPolicy, PolicyConfig frozen
  ([`20496ec`](https://github.com/shipsolid/repo-policy/commit/20496ec39f9a58acc122ae40b13a203f1d54af67))

PullRequestPolicy/StatusChecksPolicy already documented and enforced the "point-in-time snapshot,
  never mutated in place" invariant via frozen=True; BranchPolicy, RepoSettingsPolicy, and
  PolicyConfig documented no such thing and had no enforcement, despite the same invariant actually
  holding today (verified: no in-place attribute assignment exists anywhere in src/repo_policy/). An
  accidental `resolved.enforce_admins = True` instead of model_copy(update=...) would have silently
  mutated a shared instance in place, invisibly propagating through any aliased reference -- exactly
  the class of bug frozen=True exists to catch on the sibling models.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>

- Make permissive_branch_policy robust to a future colliding FieldSpec name
  ([`182a0ed`](https://github.com/shipsolid/repo-policy/commit/182a0edfa9ddcde472a8f1bcf6ea1d27075d2b7f))

It excluded "signed_commits" from its FIELD_SPECS-derived kwargs via a bare string-literal
  comparison, then splatted the rest into BranchPolicy(...) alongside the explicit
  enforcement=/signed_commits= keywords. A future FieldSpec named "enforcement" or "strict" would
  collide with the explicit enforcement= keyword and crash with TypeError: got multiple values for
  keyword argument (reproduced directly against the old implementation). Switched to
  model_validate() over a plain dict built via key-overwrite (the same pattern
  diff.resolve_desired() already uses) -- the explicit value always wins regardless of what
  FIELD_SPECS contains.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>

- Make status_checks diff order-insensitive
  ([`e852290`](https://github.com/shipsolid/repo-policy/commit/e852290c9fcfa9b7f64b86bd4f34e062dff93e9c))

StatusChecksPolicy.required is a semantically unordered set of contexts, but diff() compared it via
  plain pydantic equality, which is positional. If GitHub's GET ever returned the same contexts in a
  different order than policy.yml declared them, diff() would report a permanent phantom modify that
  re-sends an identical-content-but-reordered payload on every apply, never converging. Added
  _values_equal(), which compares status_checks via sorted(required) and falls back to plain
  equality for every other field.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>

- Preserve per-check app_id/integration_id on required status checks
  ([`1fdfb24`](https://github.com/shipsolid/repo-policy/commit/1fdfb24b89be4772e37c934940362e31797e26b3))

to_branch_protection hardcoded every check's app_id to null and to_ruleset_rule omitted
  integration_id entirely, both regardless of current state -- a human-pinned "only this GitHub App
  may satisfy this check" was silently reset/dropped by any unrelated declared field change
  triggering a required_status_checks rebuild. Both are now read through per-context from current
  state, the same pattern already used for strict/do_not_enforce_on_create.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>

- Preserve required_review_thread_resolution on ruleset pull_request rule
  ([`c2f2d78`](https://github.com/shipsolid/repo-policy/commit/c2f2d7805d8173c6b2c069355493168d18823578))

pull_requests.to_ruleset_rule had no current-state parameter at all (unlike every sibling to_*_rule
  function) and unconditionally hardcoded required_review_thread_resolution to False -- a real,
  independently-settable ruleset parameter distinct from required_conversation_resolution (which IS
  rejected outright under enforcement: ruleset). Any unrelated pull_requests field change rebuilding
  the rules array silently clobbered a human-set True back to False. Now read through from current
  state, same pattern as status_checks.to_ruleset_rule.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>

- Reject --repo with extra slashes or an empty owner/name segment
  ([`3e3c28a`](https://github.com/shipsolid/repo-policy/commit/3e3c28aaaa70d9a46965ca2580cef304496c98e9))

_split_repo() only rejected a repo string with zero slashes; a string with more than one slash
  (typo, or a pasted URL fragment like 'owner/name/tree/main') silently passed through via
  split('/', 1), discarding everything after the second segment and building a malformed API path.
  Read calls using allow_404=True would then silently return None -- indistinguishable from "no
  protection configured" -- instead of failing fast. Now validates exactly two non-empty segments.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>

- Stop render_repo_settings claiming '0 changes required' next to a warning
  ([`2c26db5`](https://github.com/shipsolid/repo-policy/commit/2c26db51da173747b35f24f4faba54fef50216cf))

The summary line was gated on "changes or unavailable", so an unavailable-only result (a declared
  field ineligible on this repo, no actual drift) printed "0 changes required." directly beneath its
  own "? ... unavailable" warning -- self-contradictory output. Now gated purely on result.changes;
  an unavailable-only result prints "No repo-level setting changes required." instead.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>

- Wrap branch_protection.from_api's ValidationError as PolicyResolutionError
  ([`6731185`](https://github.com/shipsolid/repo-policy/commit/67311857837f540557eb6430a13f56ccbc6adf20))

from_api() built a BranchPolicy directly from live GET data with no try/except, so a model_validator
  rejection (e.g. GitHub ever returning allow_fork_syncing=true with lock_branch false/absent)
  propagated as a raw pydantic ValidationError -- uncaught by cli.py's except GitHubAPIError/
  PolicyResolutionError, crashing with Python's default exit code 1 and colliding with EXIT_DRIFT,
  the exact ambiguity class the httpx-retry wrapping was built to prevent for network errors. Now
  wrapped as PolicyResolutionError, which cli.py already handles cleanly.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>

- Wrap response.json() parsing to raise clean GitHubAPIError on invalid JSON
  ([`9bbecf7`](https://github.com/shipsolid/repo-policy/commit/9bbecf7541893e1fc527f47c0050e92a7a09ca2b))

Every response.json() call happened outside _request()'s try/except, so a 2xx response with a
  truncated or non-JSON body (proxy/CDN interstitial, network hiccup after headers sent) raised an
  uncaught json.JSONDecodeError -- the same ambiguous-exit-code problem the transport-error retry
  wrapping in _request() already solves for connection failures. Added _parse_json() and routed all
  twelve .json() call sites through it.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>

### Documentation

- Fix ARCHITECTURE.md's incorrect claim about unavailable drift exit code
  ([`e1fe631`](https://github.com/shipsolid/repo-policy/commit/e1fe6318f9534efaf2eb9a4c1b5547c50b861aca))

Stated that an 'unavailable' repo-settings outcome "never sets audit/plan's drift exit code,"
  directly contradicted by cli.py's _run_check (which sets any_drift=True on unavailable) and by
  test_audit_reports_drift_for_declared_but_unavailable_setting_with_no_other_changes. An engineer
  reading the old text could reasonably conclude an unavailable setting can never fail a CI gate,
  then hit an unexpected exit-1 failure the docs said couldn't happen.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>

### Refactoring

- Dedup permissive BranchPolicy/PullRequestPolicy literals via FieldSpec registry
  ([`7862088`](https://github.com/shipsolid/repo-policy/commit/7862088421386090d8b89cf431f70c7fe150a72e))

branch_protection.from_api(None) and rulesets.from_api(None) each hand-typed the same "nothing
  configured" BranchPolicy literal; pull_requests.py's two from_*(None) fallbacks hand-typed the
  same PullRequestPolicy literal a second time. Both now build from models.FIELD_SPECS via the new
  permissive_branch_policy()/PERMISSIVE_PULL_REQUESTS, so the four call sites can't drift from each
  other or from diff._SCHEMA_DEFAULTS.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>

- Derive diff._FIELDS/_SCHEMA_DEFAULTS/_INVERTED_FIELDS from FieldSpec registry
  ([`507417b`](https://github.com/shipsolid/repo-policy/commit/507417b8281f91b0ea5aa8d0ca6a28927cd1b1e0))

Same names, same values -- now sourced from models.FIELD_SPECS instead of three independently
  hand-typed tables that had to be kept in sync by hand.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>

- Derive render._LABELS from FieldSpec registry
  ([`92ecf02`](https://github.com/shipsolid/repo-policy/commit/92ecf020e29cb27fcee59f9c23ba1ba73b7cf730))

Same name, same values -- sourced from models.FIELD_SPECS instead of a fifth hand-typed copy of the
  same field-name-to-label facts.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>

- Introduce FieldSpec registry as single source of BranchPolicy field metadata
  ([`994f216`](https://github.com/shipsolid/repo-policy/commit/994f21629264261b2424d136161b53c9300abf2f))

Adds models.FieldSpec (permissive default, inverted polarity, ruleset support, display label) and
  models.FIELD_SPECS, deriving _RULESET_UNSUPPORTED_FIELDS from it instead of hand-typing the same
  facts a second time. Closes out a recommendation raised independently across five review passes
  this session: no single registry backed BranchPolicy's field metadata, so it was hand-duplicated
  across diff.py/models.py/render.py/the policy translators.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>

### Testing

- Cross-check FIELD_SPECS against BranchPolicy.model_fields itself
  ([`68bf16b`](https://github.com/shipsolid/repo-policy/commit/68bf16b969220aaf79f7fdde965454a154b5f297))

test_field_specs_cover_every_diffable_field compared FIELD_SPECS names against a second hand-typed
  literal list, so it could never catch a future BranchPolicy field added without a matching
  FieldSpec entry -- neither side of the old assertion referenced BranchPolicy at all. Now
  cross-checked against BranchPolicy.model_fields directly (minus enforcement/strict, the only two
  non-diffable mode-selector fields), verified to still pass today and to actually fail if the two
  ever diverge.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>

- Derive ruleset-unsupported field set from FieldSpec registry
  ([`ed4763d`](https://github.com/shipsolid/repo-policy/commit/ed4763d17a0b55bbe3c8b0c8a4c4e4660b06d07a))

This test file hand-copied the same 5-field ruleset-unsupported set a third time
  (models._RULESET_UNSUPPORTED_FIELDS and diff._SCHEMA_DEFAULTS were the other two, now both derived
  from models.FIELD_SPECS too). Sourcing it from the registry closes the last hand-maintained copy
  of this fact.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>


## v0.4.6 (2026-09-20)

### Bug Fixes

- Catch httpx transport errors, retry and wrap instead of crashing raw
  ([`190b897`](https://github.com/shipsolid/repo-policy/commit/190b8970a570c88d04d674e4af6a3dd63365658c))

_request()'s retry loop only ever branched on response.status_code from a response object it assumed
  it would always get back -- a transient network failure (DNS hiccup, TLS reset, connection
  refused, read/connect timeout) propagated as a raw, unwrapped httpx.RequestError. cli.py only
  catches GitHubAPIError/PolicyResolutionError, so this crashed with a Python traceback and the
  interpreter's default exit code 1 -- colliding with EXIT_DRIFT, the exact ambiguity
  _ConfigClickException already exists to prevent for setup errors, just via an uncaught path
  instead of a click one.

Now retries a transport error exactly like a 5xx (same idempotency rule -- create_ruleset still
  won't retry), then raises a clean GitHubAPIError if every attempt fails, so a flaky connection
  mid-run gets EXIT_API_ERROR instead of a raw crash a CI pipeline can't distinguish from real
  drift.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>

- Dismissal_restrictions payload never includes an apps key
  ([`36b5d9c`](https://github.com/shipsolid/repo-policy/commit/36b5d9cd1ef24fd533228bd27b40f49f350b12b9))

Caught before it could ship as a live bug: the shared _actor_refs() helper (introduced in e36927a)
  always emits users/teams/apps, but GitHub's required_pull_request_reviews.dismissal_restrictions
  schema supports only users/teams -- unlike bypass_pull_request_allowances and branch-protection
  restrictions, which both accept apps. Sending an unrecognized "apps" key in dismissal_restrictions
  risked 422ing the entire branch-protection PUT, failing an unrelated legitimate change bundled in
  the same apply call, not just the dismissal-restriction preservation this was meant to fix.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>

- Preserve block_creations on branch-protection updates
  ([`6ae3b92`](https://github.com/shipsolid/repo-policy/commit/6ae3b92f314618b5fda53d1be0b1a445a43f27c4))

Same bug class as the ruleset enforcement/bypass_actors/unmanaged-rule-type fixes (d7a82af,
  85dd8b6): block_creations ("restrict who can create matching branches") has no modeled
  BranchPolicy field and was never read from current_raw either, so a human-enabled block_creations
  setting was silently reset to false the next time repo-policy PUT branch protection for any
  unrelated reason. Now read through via the existing _unwrap helper, same pattern as every other
  {"enabled": bool}-wrapped field on this resource.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>

- Preserve do_not_enforce_on_create on ruleset required_status_checks rule
  ([`535d8b3`](https://github.com/shipsolid/repo-policy/commit/535d8b36a036f58a1ea1dda8de20139600602e06))

Same class as strict_required_status_checks_policy right next to it: GitHub's Rulesets
  required_status_checks rule has a third parameter, do_not_enforce_on_create ("allow repositories
  and branches to be created if this check would otherwise prevent it"), which to_ruleset_rule
  already had the current-state read-through machinery for (via `current_params`) but never actually
  read this one field through -- it was simply absent from the returned parameters, defaulting to
  false on GitHub's side the next time repo-policy rebuilt the rule for an unrelated, modeled-field
  change.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>

- Preserve PR-review dismissal_restrictions/bypass_pull_request_allowances
  ([`150ac5a`](https://github.com/shipsolid/repo-policy/commit/150ac5a0362bd6fc13e6fd0a56ff7582e355fe79))

Same bug class as block_creations (9b6e9e6) and the earlier ruleset fields:
  required_pull_request_reviews.dismissal_restrictions (who can dismiss PR reviews) and
  .bypass_pull_request_allowances (who can bypass the PR requirement) have no modeled field and
  pull_requests.to_branch_protection didn't accept a `current` parameter at all -- structurally
  unable to preserve either, unlike its sibling status_checks.to_branch_protection, which already
  threads `current` through for exactly this "unmodeled sub-field" reason. A human-configured
  dismissal team or bypass allowance was silently wiped the next time repo-policy PUT branch
  protection for any unrelated reason.

Also generalizes branch_protection.py's restrictions GET-to-PUT shape transform (7fc2473) into a
  shared _actor_refs helper in github_client.py, since
  dismissal_restrictions/bypass_pull_request_allowances have the exact same users/teams/apps
  full-object-vs-login-string asymmetry.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>

- Preserve ruleset conditions.exclude and extra include entries
  ([`daadce9`](https://github.com/shipsolid/repo-policy/commit/daadce9ab025288331cc37c2fe759e0dfc119428))

Same bug class again: conditions.ref_name was fully rebuilt from scratch on every apply as
  {"include": [own_ref], "exclude": []}, discarding any human-added exclude pattern (e.g. carving
  out an automation ref from a broad include glob) or additional include entry the next time
  repo-policy touched that ruleset for an unrelated, modeled-field change -- silently narrowing or
  widening enforcement scope with no diff/plan output ever mentioning conditions, since it isn't a
  modeled field.

Now reads both through from current_raw, always keeping repo-policy's own branch ref present in
  include.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>

- Stop YAML 1.1 octal/sexagesimal int coercion from corrupting numeric fields
  ([`48e4d87`](https://github.com/shipsolid/repo-policy/commit/48e4d87941ea917b7b4821e4b109ba50ad020843))

Same class of gotcha as the earlier yes/no/on/off fix (8c9d322), for the int resolver instead of the
  bool one: PyYAML's SafeLoader treats a leading-zero scalar as legacy octal (confirmed:
  yaml.safe_load("010") returns 8, not 10) and a colon-separated scalar as base-60 sexagesimal. A
  leading-zero approvals count -- plausible from copy/paste alignment or a %02d-formatted generator
  -- silently weakened the declared policy instead of matching what was typed, with no error
  anywhere.

_StrictLoader (renamed from _StrictBoolLoader, since it now narrows two resolvers) drops the int
  resolver's octal and sexagesimal branches, keeping binary/decimal/hex. A leading-zero scalar like
  "010" now stays a plain string; pydantic's own int coercion then parses it via Python's int("010")
  == 10, producing the value a human actually expects rather than erroring or silently
  reinterpreting it. Verified the loader remains exactly as safe as SafeLoader (no constructors
  added or changed).

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>


## v0.4.5 (2026-09-19)

### Bug Fixes

- Clear_restrictions: false no longer reports permanent phantom drift
  ([`73bf4af`](https://github.com/shipsolid/repo-policy/commit/73bf4afe95a4fecd420a4aaf31b30c392604d5e7))

clear_restrictions=False means "preserve whatever restriction is currently there" -- not "the branch
  must have a restriction". When current.clear_restrictions is True (no live restriction exists at
  all), there is nothing to preserve: branch_protection.to_api_payload's restrictions field
  collapses to None whether resolved.clear_restrictions is True or False, since
  _restrictions_payload(None) is None either way. diff() still reported this as a real Change every
  run, so a branch declaring clear_restrictions: false with no existing restriction could never
  reach a compliant state -- apply "succeeded" but sent the identical payload clear_restrictions:
  true would have, and the next audit reported the same drift again.

This is a narrower, asymmetric case than the "both empty" fix in d76c30c: the reverse direction (an
  existing restriction actually being cleared) is still correctly reported as real, applicable
  drift.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>

- Diff() treats both-empty compound fields as equal, not just raw-equal
  ([`eb88a7b`](https://github.com/shipsolid/repo-policy/commit/eb88a7b21d91e78e45b67badb21dd7d3768ff7ca))

status_checks: {required: []} (declared empty) and status_checks undeclared both produce the exact
  same API payload -- to_branch_protection/ to_ruleset_rule return None for an empty required list
  either way -- but resolve_desired() sets resolved.status_checks to a real StatusChecksPolicy
  instance in the first case, while branch_protection.from_api/rulesets. from_api represent "nothing
  configured" as None. Raw `==` treats these as different, so diff() reported permanent phantom
  drift and apply_branch re-issued a no-op API call on every single run for a branch declaring an
  empty status_checks block.

Same failure mode for pull_requests: `{required: false, approvals: 5}` never raw-equals the
  canonical "nothing configured" PullRequestPolicy (approvals/code_owner_review differ), even though
  both produce an identical None payload once required is False --
  to_branch_protection/to_ruleset_rule never look at the other sub-fields in that case.

diff()'s per-field comparison now also treats two values as equal when both are "empty" per the
  field's own existing _is_empty() semantics (already used for add/remove/modify classification) --
  a no-op for every plain boolean field, since raw equality already catches those; it only changes
  behavior for the two compound types where "empty" isn't a single canonical value.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>

- Preserve unmanaged rule types on ruleset updates
  ([`31daf75`](https://github.com/shipsolid/repo-policy/commit/31daf75472016e0f2a694418deb926235ceda5ef))

Broader version of d7a82af's enforcement/bypass_actors gap: to_api_payload() rebuilt the entire
  `rules` array from scratch, containing only the 6 rule types repo-policy models (pull_request,
  required_status_checks, required_signatures, required_linear_history, non_fast_forward, deletion).
  GitHub Rulesets support many more (commit_message_pattern, tag_name_pattern, merge_queue,
  workflows, code_scanning, file_path_restriction, ...) -- any human-added rule of one of those
  types was silently dropped the next time repo-policy touched that ruleset for an unrelated,
  modeled-field change, with no warning from diff/plan/audit since none of them are modeled fields.

Now carries forward any rule whose type isn't in the newly-introduced _MANAGED_RULE_TYPES set,
  verbatim, alongside rebuilding the ones repo-policy actually manages.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>

- Repo-settings CLI output no longer overstates or hides drift
  ([`cd07043`](https://github.com/shipsolid/repo-policy/commit/cd07043e710b0f82d7d4d6308465e88930dd52e8))

Two related bugs in how cli.py surfaces RepoSettingsResult:

1. apply's "repo settings: applied N change(s)" counted every entry in result.changes, including
  ones that ended up in result.unavailable (a 422 during apply -- distinct from result.applied
  itself, which was already fixed in be11aa8 to exclude them from the pass/fail boolean, but the
  printed count still didn't). A partial success (1 of 2 changes landed) printed "applied 2
  change(s)" right next to the "unavailable" line contradicting it.

2. _run_check (audit/plan) only reported repo_settings_result.unavailable when result.changes was
  also non-empty. A repo-setting that's declared but structurally ineligible (e.g.
  private_vulnerability_reporting on a repo that doesn't support it) with zero other drift was
  silently dropped entirely -- audit printed "compliant" and exited 0 even though that part of the
  policy can never actually be satisfied.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>

- Restore retry for idempotent POST calls, keep it off only for create_ruleset
  ([`14fff26`](https://github.com/shipsolid/repo-policy/commit/14fff2672b2788a673406967942b73f61d8b84fb))

Regression in f2c3dea's non-idempotent-POST guard: it blocked retries for EVERY POST, but
  set_required_signatures's enable path is also a POST and IS idempotent (repeating it is a no-op)
  -- a transient 5xx on that call now aborted the whole apply run instead of retrying, while the
  functionally identical PUT-based enable_vulnerability_alerts() etc. would have retried and likely
  succeeded.

_request() now takes an explicit idempotent=True default; only create_ruleset (the one call that
  actually creates a new resource each time) opts out with idempotent=False. Every other call, POST
  or not, retries on 5xx as before this was ever touched.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>

- Stop YAML 1.1's yes/no/on/off from silently coercing to booleans
  ([`8c9d322`](https://github.com/shipsolid/repo-policy/commit/8c9d3223bc1c1637511254690d2c7a32916a62dd))

yaml.safe_load's default resolver treats bare yes/no/on/off (any casing) as booleans, not just
  true/false -- confirmed: yaml.safe_load("no") returns False. A branch name or status-check context
  that happens to be one of those words (e.g. `branches: {no: {...}}`) was silently turned into a
  Python bool before pydantic ever validated it, surfacing as a confusing "Input should be a valid
  string" error on a dict key that never looks like what the user typed.

_StrictBoolLoader subclasses SafeLoader and removes only the y/Y/n/N/o/O implicit-resolver entries
  for the bool tag -- true/false (any casing) still resolve as booleans, nothing else changes. This
  is NOT yaml.load()'s usual security footgun: no constructors were added or changed, so it remains
  exactly as safe as SafeLoader against arbitrary object construction (verified:
  !!python/object/apply tags still raise ConstructorError).

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>

- Transform restrictions from GET shape to PUT shape before re-sending
  ([`78eba5f`](https://github.com/shipsolid/repo-policy/commit/78eba5fdf06c7c2c4adf71ee420f4ad3fbd545a3))

to_api_payload() re-sent current_raw["restrictions"] verbatim when clear_restrictions is False.
  GitHub's GET response shapes restrictions.users/teams/apps as arrays of full objects (login/slug
  plus other metadata); the PUT request body expects arrays of bare login/slug strings. Any branch
  with an existing push restriction and clear_restrictions left at its inherited managed-scope
  default (False) would 422 the entire PUT the next time ANY other declared field changed --
  aborting the whole apply run with GitHubAPIError, unrelated to what was actually being changed.

The existing tests never caught this because their current_raw fixtures already used the flat string
  shape instead of GitHub's real GET shape -- fixed those too, so they now exercise the actual
  transform.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>

### Refactoring

- Dedupe repo-settings field groupings, fix resolve_desired's docstring
  ([`48418a4`](https://github.com/shipsolid/repo-policy/commit/48418a455b3b34b4d53493069c1f75dfb81be630))

apply_repo_settings() re-derived which Change.field values belong to the "flat settings" and
  "security_and_analysis" PATCH groups via inline tuple literals, byte-identical to but independent
  of policies/repo_settings.py's _FLAT_FIELDS/_SECURITY_AND_ANALYSIS_FIELDS (the ones
  diff_flat_settings/ diff_security_and_analysis already use to detect drift in the first place).
  Currently in sync, but a future field added to one and not the other would mean plan/audit
  correctly show a field needing a change while apply silently excludes it from the PATCH payload --
  reads as success, never sent to GitHub. Now imports and reuses the same tuples.

Also corrects resolve_desired()'s docstring, which claimed the result "always has every field
  concretely set" -- status_checks' own permissive value is deliberately None (matching how from_api
  represents "nothing configured"), so that one field is the documented exception, not an oversight
  diff() has to work around.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>


## v0.4.4 (2026-09-19)

### Bug Fixes

- _is_empty dispatches on isinstance, not a shared .required attribute name
  ([`2eacaa5`](https://github.com/shipsolid/repo-policy/commit/2eacaa5b0092719655753b47d69216995ff52460))

hasattr(value, "required") was standing in for "is this a PullRequestPolicy or a StatusChecksPolicy"
  -- a naming coincidence, not a type check. A future value type exposing an unrelated .required
  attribute wouldn't just be misclassified, it would crash: the isinstance(required, bool) branch
  calls len() on the fallback path, which raises TypeError for anything that's neither bool nor
  sized. Explicit isinstance checks preserve identical behavior for both current cases.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>

- Honor GitHub's Retry-After header instead of blind exponential backoff
  ([`f2c3dea`](https://github.com/shipsolid/repo-policy/commit/f2c3deafb6604c260bcc3bd7662c177eee96504d))

The fixed exponential backoff (1s/2s/4s by default) ignored Retry-After on secondary-rate-limit
  429/403 responses, so a sustained rate-limit window could exhaust all retry attempts well before
  GitHub's own requested wait cleared, aborting the run when a header-aware wait would have
  succeeded.

Deliberately does not honor X-RateLimit-Reset (the primary rate limit) -- that reset can be up to an
  hour away, and silently blocking a CLI invocation for that long is a product decision (fail fast
  with a clear error vs. block), not a pure reliability fix. Falls back to the existing exponential
  backoff when Retry-After is absent or unparseable.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>

- Preserve enforcement mode and bypass_actors on ruleset updates
  ([`d7a82af`](https://github.com/shipsolid/repo-policy/commit/d7a82af5102f38869144f636e40a57a850c07483))

Same bug class as the earlier strict_required_status_checks_policy fix (f291a05), and it wasn't
  fully closed: to_api_payload() also hardcoded "enforcement": "active" unconditionally and never
  carried bypass_actors through at all. Since ruleset PUT/POST is a full-object replace, either gap
  meant an unrelated declared change (e.g. bumping approvals) would silently re-activate a ruleset a
  human had flipped to "evaluate" (dry-run) mode, or wipe out bypass permissions a security team had
  configured -- neither ever surfaced by diff/plan/audit, since neither field is modeled.

Both are now read through from current_raw the same way, defaulting to "active"/[] only when there's
  no current state to read (first creation).

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>

- Pullrequestpolicy/statuscheckspolicy are frozen, fixing Change's hashability for pull_requests
  ([`72fcafb`](https://github.com/shipsolid/repo-policy/commit/72fcafbb2ab563244816c714ad3f8ea5d0f548ff))

Change is @dataclass(frozen=True), whose auto-derived __hash__ requires every field to be hashable
  -- but current_value/desired_value can hold PullRequestPolicy/StatusChecksPolicy instances, and
  plain (non-frozen) pydantic BaseModel instances aren't hashable. Nothing currently hashes a
  Change, so this was latent, but a real footgun for future code (e.g. dedup via a set).

Made both models frozen: pydantic's frozen models are both immutable and (when every field is
  hashable) hashable, matching Change's own frozen, snapshot-value-object nature. Confirmed no code
  path mutates either model in place anywhere in this codebase, so this is behavior-preserving.

Fully fixes it for pull_requests (all-scalar fields). status_checks stays technically unhashable
  regardless -- StatusChecksPolicy.required is a list, and a list field makes a frozen model's
  derived __hash__ raise TypeError just the same. Freezing it still adds the immutability guarantee;
  changing `required` to a tuple to close the remaining gap would ripple into a public field's type
  and at least one test's equality assertion for a still-latent, never-triggered edge case -- left
  alone as not worth that footprint.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>

### Documentation

- Replace dangling plan-doc references with commit hashes
  ([`3cd2728`](https://github.com/shipsolid/repo-policy/commit/3cd2728799abf6978463d51f4fdb5b2dfbf6849e))

Both comments cited docs/superpowers/plans/2026-09-19-*.md files that no longer exist -- this
  project deletes plan docs once the work they guided ships, which leaves any comment pointing at
  one a dead end. Commit hashes are durable; point to those instead.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>

### Refactoring

- Dedupe the enforce_admins/lock_branch/etc. permissive-value table
  ([`e83920b`](https://github.com/shipsolid/repo-policy/commit/e83920b9b7baa1d43baddaca7b6e31e25172d706))

The permissive (no-op) value for the 5 ruleset-unsupported fields (enforce_admins,
  required_conversation_resolution, lock_branch, allow_fork_syncing, clear_restrictions) was
  hand-copied in 3 places: models._RULESET_UNSUPPORTED_FIELDS (the validator's source of truth),
  diff._SCHEMA_DEFAULTS, and twice more inline in policies/rulesets.py's from_api(). All three now
  read from models._RULESET_UNSUPPORTED_FIELDS -- the only module with no dependency on the other
  two, so no import cycle. Left the broader "fully permissive BranchPolicy/PullRequestPolicy
  literal" consolidation alone (policies/branch_protection.py and rulesets.py's from_api(None), and
  pull_requests.py's from_*(None)) -- unlike this 5-key subset, those also share a nested
  PullRequestPolicy instance, and having multiple BranchPolicy objects reference the exact same
  PullRequestPolicy by identity would reintroduce the shared-mutable-state class of bug the
  _merge_pull_requests fix (see the resolve_desired commit) specifically eliminated. Not worth that
  risk for a cosmetic DRY win on rarely-changed, self-documenting literals.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>

- Derive BranchResult.applied instead of setting it at each call site
  ([`5a09d1c`](https://github.com/shipsolid/repo-policy/commit/5a09d1c55351702390ea9c3c226080304d63cc0e))

applied was always exactly bool(changes) at both of BranchResult's construction sites -- now a
  @property, matching the pattern already used for AuditResult.compliant. Behavior-preserving: no
  test or caller ever passed applied= explicitly.

Skipped a related dedup: having apply_all batch detect_stale_branch_protection once (mirroring
  audit_all) and pass membership into apply_branch, instead of apply_branch re-deriving the
  single-branch predicate inline. Reverted after confirming it breaks apply_branch's
  standalone-callable contract --
  test_apply_branch_flags_stale_branch_protection_for_ruleset_enforced_branch calls apply_branch
  directly (not through apply_all) and relies on it self-detecting staleness. The two predicates
  read identically but intentionally live at different scopes; not a safe dedup.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>

- Extract _build_client, shared by _run_check and apply
  ([`30905b1`](https://github.com/shipsolid/repo-policy/commit/30905b11698e82c170189190cc474180220b301b))

apply() re-implemented _run_check's exact load_policy -> _resolve_repo -> _split_repo ->
  GitHubClient(...) setup sequence inline instead of sharing it, a real drift risk (e.g. a future
  GitHubClient constructor change or _resolve_token behavior change would need updating in two
  places). Extracted _build_client(config_path, repo, token) -> (PolicyConfig, str, GitHubClient),
  covering exactly the setup with no command-specific behavior; ConfigError and click.ClickException
  both still propagate uncaught so each caller keeps its own exit-code idiom (_run_check returns
  EXIT_CONFIG_ERROR, apply calls sys.exit(EXIT_CONFIG_ERROR)).

Caught during this refactor: `with client:` (no `as`) doesn't rebind to whatever __enter__() returns
  -- harmless in production since GitHubClient.__enter__ returns self, but it silently broke every
  mocked CLI test, which configure behavior on mock_client_cls.return_value.__enter__ .return_value
  and expect the code under test to operate on that object. Fixed with `with client as client:`,
  caught by running the full suite before committing.

Skipped per independent review: sharing the --config/--repo/--token option decorator across
  audit/plan/apply (inert boilerplate at 3 call sites, not worth a shared decorator) and merging
  render_plan/render_repo_settings (the two functions differ in load-bearing ways -- compliant-field
  listing, unavailable-section, strict-vs-tolerant label lookup -- that a shared abstraction would
  paper over).

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>

- Render.py drives field iteration from diff._FIELDS, labels fail safe
  ([`2e4f49a`](https://github.com/shipsolid/repo-policy/commit/2e4f49a1c0a135d3e13a864a3fa5781aa541e669))

_LABELS was a hand-maintained duplicate of diff._FIELDS' key set with no safety net -- render_plan
  indexed it with a bare _LABELS[change.field], so a future BranchPolicy field added to _FIELDS
  without a matching _LABELS entry would raise KeyError the first time it produced a Change. Now
  iterates _FIELDS directly (matching render_repo_settings' already-tolerant
  _REPO_SETTINGS_LABELS.get(field, field) pattern) and falls back to the raw field name instead of
  crashing.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>

- Reuse _unwrap for GitHub's {"enabled": bool} response shape
  ([`37e4829`](https://github.com/shipsolid/repo-policy/commit/37e4829c290a06867dc5ee261572decfc04db14c))

get_required_signatures, get_automated_security_fixes, and get_private_vulnerability_reporting each
  hand-rolled their own inline .get("enabled", default) unwrap instead of reusing _unwrap, which
  already existed for exactly this GitHub response convention but lived in
  policies/branch_protection.py, several layers away from github_client.py's response parsing.
  Relocated _unwrap into github_client.py (where 3 of its 4 use sites already lived, and where it
  belongs conceptually -- parsing GitHub's raw JSON shapes); policies/branch_protection.py now
  imports it.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>


## v0.4.3 (2026-09-19)

### Bug Fixes

- Detect and clean up stale rules when a branch's enforcement mode switches
  ([`f9e3217`](https://github.com/shipsolid/repo-policy/commit/f9e32178ec1d1ae776546a356d6a5e3e9b24c6df))

Switching enforcement: ruleset -> branch_protection left the orphaned repo-policy:<branch> ruleset
  permanently un-prunable (prune_rulesets keyed on branch-name presence, not current enforcement) --
  now fixed, since ruleset ownership is unambiguous via the naming convention. Switching
  branch_protection -> ruleset left the old classic branch protection fully active and invisible to
  audit, which reported the branch compliant -- classic branch protection has no ownership marker
  (same reason ARCHITECTURE.md already documents for branch removal), so this direction is detected
  and reported rather than auto-deleted.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>

- Get_private_vulnerability_reporting fails closed, and GitHubClient stops closing injected clients
  ([`9ac408c`](https://github.com/shipsolid/repo-policy/commit/9ac408c2cf5460ecb6024922c15315adb58f71de))

get_private_vulnerability_reporting() defaulted a missing 'enabled' key to True; the structurally
  identical get_automated_security_fixes() defaults to False. Matches that pattern now -- failing
  open in the riskier direction was wrong for a security setting.

Also fixes close()/__exit__ unconditionally closing self._client even when it was injected via the
  constructor's client= parameter, which would break a caller sharing one httpx.Client across
  multiple GitHubClient wrappers. Landed together since both are small fixes to the same file.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>

- Give an actionable message when policy.yml's top level isn't a mapping
  ([`fa1ccd8`](https://github.com/shipsolid/repo-policy/commit/fa1ccd881b36193e975379c2e776aff1b615a80a))

pydantic reports a root-level type error with loc == (), which rendered as a bare ' - : <message>'
  bullet with no location hint.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>

- Preserve strict_required_status_checks_policy on ruleset-enforced branches
  ([`f291a05`](https://github.com/shipsolid/repo-policy/commit/f291a055927c8301540a7810026567a6dc92230a))

The ruleset backend hardcoded strict_required_status_checks_policy: False on every apply, silently
  disabling a human-configured 'require branches up to date' setting whenever any other declared
  field changed. The branch_protection backend already reads this through from current state for
  exactly this reason (see docs/test-strategy.md bug #1) -- this mirrors that fix to the ruleset
  backend.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>

- Re-validate resolve_desired()'s merged policy and merge pull_requests per field
  ([`47456f3`](https://github.com/shipsolid/repo-policy/commit/47456f36fc399e201d386d079276c712f34b81f7))

model_copy(update=...) never re-runs BranchPolicy's model_validators, so managed-scope mode could
  reconstruct the exact allow_fork_syncing/lock_branch combination the allow_fork_syncing validator
  exists to reject (a recurrence of a bug already fixed once via live-repo testing, see
  docs/test-strategy.md bug #4) -- now caught and raised as PolicyResolutionError instead of
  shipping to the GitHub API. Also fixes pull_requests being merged as one atomic block: a partial
  declaration (e.g. approvals only) silently reset
  code_owner_review/dismiss_stale_reviews/require_last_push_approval to pydantic's class defaults
  instead of preserving current state -- now merged field-by-field via model_fields_set, matching
  every other BranchPolicy field's managed-scope behavior.

Also fixes clear_restrictions being classified with the wrong add/remove polarity in plan/audit
  output -- it shares allow_force_push/allow_deletion's true-means-permissive polarity but was
  missing from _INVERTED_FIELDS. Landed together since both are in diff.py.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>

- Reject secret_scanning_push_protection without secret_scanning at config time
  ([`0558656`](https://github.com/shipsolid/repo-policy/commit/0558656c4e1bc83ec89c274f473975536bec8547))

Mirrors the existing automated_security_fixes/vulnerability_alerts validator. Without it, declaring
  only secret_scanning_push_protection: true produced a real GitHub 422 at apply time, misreported
  via the same 'unavailable on this repository' message used for genuinely-unlicensed GHAS -- hiding
  an actionable config fix as if nothing could be done.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>

- Stop CLI setup errors from colliding with the policy-drift exit code
  ([`762299d`](https://github.com/shipsolid/repo-policy/commit/762299dbc6e535d89797a86abdfc0ea4e4c016f0))

click.ClickException defaults to exit_code=1, identical to this CLI's own EXIT_DRIFT -- a missing
  GITHUB_TOKEN, an unresolvable --repo, or a malformed --repo flag were all indistinguishable from
  real policy drift for any CI pipeline branching on exit code.
  _resolve_token/_resolve_repo/_split_repo now raise a _ConfigClickException subclass that forces
  exit_code=2. Also fixes _resolve_repo() silently ignoring a failing git invocation's returncode
  and parsing whatever stdout happened to contain regardless.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>

- Stop retrying non-idempotent POST requests on 5xx
  ([`1b5719e`](https://github.com/shipsolid/repo-policy/commit/1b5719e7b77a0d24b02477cc4a2a25d110d7b367))

create_ruleset is a POST -- retrying it on a 5xx whose response was lost after GitHub already
  processed the request could create a duplicate repo-policy:<branch> ruleset.
  429/secondary-rate-limit responses still retry for every method, since GitHub rejects those before
  doing any work.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>

- Support disabling repo-settings toggles and stop misreporting applied
  ([`be11aa8`](https://github.com/shipsolid/repo-policy/commit/be11aa8d06263c7615837a36302d9c16904eec5e))

vulnerability_alerts/automated_security_fixes/private_vulnerability_reporting could only ever be
  enabled -- github_client.py had no DELETE-endpoint method for any of them, so apply_repo_settings
  always called the enable path regardless of the declared direction. Declaring false on an
  already-enabled repo was a silent no-op reported as success, with audit re-flagging the same drift
  forever. GitHub supports DELETE on all three endpoints (confirmed by the existing enable/disable
  pair already implemented for required_signatures); this adds the missing disable path.

Also fixes RepoSettingsResult.applied, which was bool(result.changes) and stayed True even when the
  only detected change immediately moved to result.unavailable (e.g. a 422 from unlicensed GHAS) --
  apply printed 'applied 1 change(s)' and 'unavailable' for the same field in the same run. Landed
  together since both touch apply_repo_settings's final lines.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>

### Chores

- Mark fix-audit-findings plan tasks complete
  ([`20078d5`](https://github.com/shipsolid/repo-policy/commit/20078d587acd3f9d6351fc263978e4a2dc945f07))

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>

- Remove internal planning doc for the audit-findings fix round
  ([`ef75e8f`](https://github.com/shipsolid/repo-policy/commit/ef75e8f10c1719fc6f2fd623cd12608753778e14))

Matches this project's established convention of removing docs/superpowers/plans/*.md once the work
  they guided has shipped (see commit cc06ebf).

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>


## v0.4.2 (2026-09-19)

### Bug Fixes

- Don't attempt ruleset-branch creation with a token that can't do it
  ([`46308ce`](https://github.com/shipsolid/repo-policy/commit/46308ce14e0e233ef7696e08f315b1e41a32bb05))

REPO_POLICY_E2E_TOKEN is scoped to Administration: Read and write only, which doesn't cover git
  ref/branch creation (that needs the separate Contents: Read and write permission). The first real
  CI run of the E2E workflow failed with 403 "Resource not accessible by personal access token" on
  the self-heal POST to create repo-policy-verify.

Bootstrapped the branch once, out of band, with a higher-privilege session (gh api ... git/refs).
  _ensure_ruleset_branch_exists now only verifies presence and fails with an actionable message if
  it's ever missing again, instead of attempting a create call the token's documented
  least-privilege scope can't perform.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>

### Continuous Integration

- Add nightly/manual E2E workflow against the live fixture repo
  ([`9a7e203`](https://github.com/shipsolid/repo-policy/commit/9a7e203f7abc58b29438a8587de447ee69b32775))

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>

### Documentation

- Record the automated E2E suite against the live fixture repo
  ([`463c3c5`](https://github.com/shipsolid/repo-policy/commit/463c3c5c4ff3a31f43475ea869dd2bb6cf63b852))

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>

### Testing

- Add e2e fixture policy files modeled on this session's manual verification
  ([`9b8bf88`](https://github.com/shipsolid/repo-policy/commit/9b8bf886aaea0642750126831c845723be1a2083))

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>

- Add e2e marker infrastructure and live fixture-repo connectivity check
  ([`30aca6e`](https://github.com/shipsolid/repo-policy/commit/30aca6e218c169f9e217259fa06cf82f1fc342cb))

Adds docs/superpowers/plans/2026-09-20-e2e-fixture-repo-testing.md, the pytest e2e marker (excluded
  by default via addopts), and tests/e2e/conftest.py's session-scoped live-repo fixtures against
  shipsolid/repo-policy-e2e-fixture.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>

- Add full E2E lifecycle coverage against the live fixture repo
  ([`663d0b5`](https://github.com/shipsolid/repo-policy/commit/663d0b5d09389cd2356934665d0171ebe9c0c509))

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>


## v0.4.1 (2026-09-19)

### Bug Fixes

- Correct allow_fork_syncing's permissive default from true to false
  ([`99fb988`](https://github.com/shipsolid/repo-policy/commit/99fb988ccd7c611c2afe2006911c5a7bfd0a9054))

Found via live-repo verification against a real GitHub repo (shipsolid/playground): GitHub silently
  discards allow_fork_syncing: true on any branch protection PUT where lock_branch is false --
  confirmed by pairing them (works) and unpairing them (silently resets to false) against the real
  branch-protection API. The old default of true meant ANY first-time apply against a
  previously-unprotected branch inherited lock_branch: false + allow_fork_syncing: true via
  resolve_desired()'s managed-scope current-state inheritance, and strict mode's schema-default
  injection had the identical problem for any branch that left the field undeclared -- neither path
  goes through model validation (Pydantic's model_copy() skips validators), so this couldn't have
  been caught by a validator alone. False is now the default everywhere: diff._SCHEMA_DEFAULTS, both
  backends' from_api(), and the ruleset-unsupported-fields validator's permissive-value entry. No
  longer inverted polarity -- removed from diff._INVERTED_FIELDS.

Known, expected gap closed by the very next commit: tests/test_models.py's
  test_branch_policy_rejects_allow_fork_syncing_under_ruleset still asserts the OLD non-permissive
  value (False) triggers ruleset rejection; it now needs True instead. Deliberately left red here
  since that test lives in this plan's Task 2 (the new lock_branch-pairing validator), not this
  task's file list -- fixed as Task 2 Step 1 before any new validator code is added.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>

- Reject allow_fork_syncing: true without lock_branch: true
  ([`89e04f5`](https://github.com/shipsolid/repo-policy/commit/89e04f549a353243f197a4efba0d316d53da3d41))

Defense-in-depth alongside Task 1's default-value fix: Pydantic's model_copy() (used throughout
  diff.resolve_desired()) skips validators, so this new validator only catches EXPLICIT policy.yml
  declarations of the broken combination -- it cannot see values injected by schema defaults or
  current-state inheritance, which is why Task 1's fix to the underlying representation was the
  primary correction and this is the secondary one. Scoped to enforcement: branch_protection only --
  under enforcement: ruleset, allow_fork_syncing: true is the allowed permissive no-op value
  (existing _reject_ruleset_unsupported_fields validator), and lock_branch: true is itself rejected
  there, so requiring the pairing under ruleset enforcement would be unsatisfiable.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>

### Documentation

- Record the allow_fork_syncing bug as the 4th live-testing find
  ([`2bbe0c5`](https://github.com/shipsolid/repo-policy/commit/2bbe0c59213cd225e4df213265988cdc76075586))

docs/test-strategy.md's "three real bugs" section becomes four, matching its existing tone and
  structure. ROADMAP.md's Now table gets a row matching the established Phase 1/2/3 convention.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>


## v0.4.0 (2026-09-19)

### Documentation

- Document clear_restrictions and the closed repo_security gap
  ([`376090b`](https://github.com/shipsolid/repo-policy/commit/376090bb654ed4e5ac6921a941591f0cb277a663))

ARCHITECTURE.md's Domain Model and Apply Safety Model sections corrected -- restrictions is no
  longer in the unmodeled-fields list. ROADMAP.md's Now table gets a row matching the Phase 1/2
  convention.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>

### Features

- Model clear_restrictions, closing the last repo_security field gap
  ([`9abd10a`](https://github.com/shipsolid/repo-policy/commit/9abd10a663aec1d9e3fa13d709af34dcfa13bcf4))

Closes the final field-level gap against the sibling repo_security tool's baseline: GitHub
  branch-protection restrictions (push allowlist), which repo_security unconditionally clears to
  null on every apply. repo-policy only supports declaring the clear -- not setting an arbitrary
  allowlist, since neither repo-policy's schema nor repo_security itself ever manages one.
  Branch_protection-only, guarded by the same ruleset-unsupported-fields validator as
  enforce_admins/ required_conversation_resolution/lock_branch/allow_fork_syncing.

Also excludes clear_restrictions from test_policies_parity.py's generic BRANCH_PROTECTION_FIELDS
  parametrize (mirroring the existing signed_commits exclusion): that test calls to_api_payload with
  current_raw=None for both sides of the comparison, so there's no existing restrictions value to
  preserve either way -- clear_restrictions True and False both collapse to restrictions: None
  there, a real blind spot in that specific test setup, not a code bug. The actual round-trip is
  proven by two dedicated tests using realistic current_raw data. Plan doc updated to record this,
  found only by running the tests.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>


## v0.3.0 (2026-09-19)

### Documentation

- Document the repo_settings section and its unavailable outcome
  ([`d7095a4`](https://github.com/shipsolid/repo-policy/commit/d7095a4358c2e6542fc7a90a84c78b39be46364b))

ARCHITECTURE.md's Domain Model, Container/Component View, and a new Repo-Level Settings subsection;
  ROADMAP.md's Later section updated to mark this phase shipped.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>

### Features

- Add repo_settings section — delete_branch_on_merge, allow_update_branch
  ([`e3857e7`](https://github.com/shipsolid/repo-policy/commit/e3857e72ca8a80be032f07221d7506f0722552bb))

First slice of repo-wide (non-branch) settings management, closing part of the gap against a sibling
  tool's fixed baseline. Establishes the full new architecture end-to-end: RepoSettingsPolicy
  schema, pure diff translation (policies/repo_settings.py), orchestration (repo_settings.py),
  rendering, and CLI wiring in audit/plan/apply. diff_security_and_analysis/diff_toggle ship as
  pure, fully-tested functions here but aren't wired into orchestration yet -- that happens in the
  task that also adds the matching live GitHubClient method, so no commit in this series ever
  reports a policy.yml field as drifted that apply can't actually fix. Zero API calls when
  policy.yml has no repo_settings section, matching every existing adopter's current behavior
  exactly.

Includes the Phase 2 implementation plan (written earlier in the conversation, corrected here after
  a real mypy failure surfaced the premature security_and_analysis wiring this commit's message
  describes avoiding).

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>

- Enforce automated_security_fixes (Dependabot security updates)
  ([`649f107`](https://github.com/shipsolid/repo-policy/commit/649f107540c2426bed7a9d2294356584c1996660))

Must apply after vulnerability_alerts in the same run -- GitHub rejects enabling Dependabot security
  updates before Dependabot alerts. Guarded two ways: RepoSettingsPolicy's model validator (Task 1)
  rejects a policy.yml that declares automated_security_fixes: true without also declaring
  vulnerability_alerts: true, at parse time; apply_repo_settings additionally sequences the two live
  API calls correctly for the case where both are changing in the same run.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>

- Enforce private_vulnerability_reporting
  ([`e55403b`](https://github.com/shipsolid/repo-policy/commit/e55403bcc6bfe3117290d6659936bf2bd8c00337))

Free on every repo, but not every repo is eligible (e.g. dependency graph disabled) -- introduces
  the unavailable outcome, informational and distinct from drift or an error, first used here.
  GitHubClient._request gains allow_422 (mirroring the existing allow_404) to make this
  distinguishable from a genuine API error.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>

- Enforce secret_scanning and secret_scanning_push_protection
  ([`d979394`](https://github.com/shipsolid/repo-policy/commit/d97939476b0f091bc232e25ae5f485c0a24cd8b2))

Highest security value in this series, GHAS-gated -- 422 means no GitHub Advanced Security license,
  surfaced as unavailable (Task 4's pattern), not an error. Wires diff_security_and_analysis/
  to_security_and_analysis_payload (pure functions shipped in Task 1) into
  plan_repo_settings/apply_repo_settings for the first time, now that
  GitHubClient.update_security_and_analysis exists to make them usable end-to-end. Completes the
  7-field repo_settings series.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>

- Enforce vulnerability_alerts (Dependabot alerts)
  ([`d46c538`](https://github.com/shipsolid/repo-policy/commit/d46c538fddf180744ed74e2484518b5c465bb261))

Free on every repo. Foundational for the next field in this series (automated_security_fixes), which
  GitHub requires this to already be enabled before it can be turned on.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>


## v0.2.0 (2026-09-19)

### Bug Fixes

- Drop redundant forward-reference quotes on BranchPolicy validator
  ([`6f64b39`](https://github.com/shipsolid/repo-policy/commit/6f64b39cf69b7b96b87ecc9df80f731ddd905b6f))

ruff (UP037) flagged the return type annotation on _reject_ruleset_unsupported_fields -- unnecessary
  since models.py already has `from __future__ import annotations`. Caught running this project's
  actual CI checks (ruff + mypy), not just pytest.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>

### Chores

- Gitignore .claude/ (worktree tool state)
  ([`1291c30`](https://github.com/shipsolid/repo-policy/commit/1291c30f17acb376f1874f16fef690f6269c3326))

EnterWorktree creates worktrees under .claude/worktrees/ but nothing ignored the directory -- a
  future 'git add -A' could have swept an entire linked worktree (including its own .venv and nested
  .git) into a commit. No tracked content exists under .claude/ today.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>

### Documentation

- Add branch-protection field-parity plan; reformat ROADMAP tables
  ([`53085b6`](https://github.com/shipsolid/repo-policy/commit/53085b6e66c7c186e2afd188a04c488d6edac97f))

Plan closes the two "Later" gap-analysis bullets added earlier against the sibling repo_security
  baseline tool. ROADMAP.md's table reformat is an editor auto-format pass with no content change.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>

- Correct stale PyPI trusted-publishing failure narrative
  ([`537485f`](https://github.com/shipsolid/repo-policy/commit/537485fdf81b1773c9a7cc45a73d01c953a330e9))

Both the release.yml comment and docs/ci-cd.md read as if PyPI publish was still failing. It isn't:
  v0.1.0-v0.1.2 failed with invalid-publisher because the trusted publisher wasn't yet registered on
  pypi.org, but v0.1.3 and v0.1.4 published successfully once it was. Confirmed against the actual
  Actions run logs and live PyPI project state.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>

- Reflect the 6 newly-modeled branch-protection fields
  ([`ac6d515`](https://github.com/shipsolid/repo-policy/commit/ac6d515569d164c84ef9a0503d57e5cc037469cb))

ARCHITECTURE.md's Domain Model and Apply Safety Model sections, and ROADMAP.md's Later section,
  described the pre-this-plan state (fields read-through/unmodeled). Updates both to match what
  Tasks 1-5 shipped.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>

### Features

- Model allow_fork_syncing, branch_protection-only
  ([`4cc46b4`](https://github.com/shipsolid/repo-policy/commit/4cc46b4449b150ecee1be718542db4644256a789))

Completes the four branch_protection-only fields (with enforce_admins,
  required_conversation_resolution, lock_branch). Inverted polarity like
  allow_force_push/allow_deletion: GitHub's own default is true (syncing allowed), so true/unset is
  the permissive value here, not false.

Also fixes tests/test_policies_parity.py's own PERMISSIVE fixture, which never got the three earlier
  fields either -- masked until now because model_copy(update=...) always sets a real value on the
  'resolved' side, so an unset PERMISSIVE's implicit bool(None)==False happened to match each
  earlier field's real permissive default. allow_fork_syncing's inverted polarity broke that
  coincidence and the parity guard caught it, exactly as designed. Plan doc updated to match.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>

- Model dismiss_stale_reviews and require_last_push_approval
  ([`41e59cb`](https://github.com/shipsolid/repo-policy/commit/41e59cb84d08c9e6ebb7a858bb82049ccda5a254))

Previously read through from live GitHub state and never enforced. Both GitHub backends (classic
  branch protection's required_pull_request_reviews, and the ruleset pull_request rule's parameters)
  already had a slot for these -- converts them from unmodeled/preserved to declared/enforced,
  following the same pattern approvals and code_owner_review already use.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>

- Model enforce_admins, branch_protection-only
  ([`97a531a`](https://github.com/shipsolid/repo-policy/commit/97a531a80869d3d686b64464ba6e0c350e2999bc))

Highest-impact of the branch-protection fields the v1 schema doesn't model: without it, a repo admin
  can bypass every other declared rule at will. No GitHub Rulesets equivalent exists (would require
  bypass_actors role-ID configuration, out of scope here), so this is branch_protection enforcement
  only -- a new BranchPolicy model validator rejects declaring a non-permissive value under
  enforcement: ruleset at policy.yml parse time (the permissive value itself is still allowed
  through, since rulesets.from_api's internal "current state" representation must be able to
  construct it too), and rulesets.from_api hardcodes the field to its permissive constant so no
  ruleset-enforced branch ever shows phantom drift for a field it structurally cannot represent.

Also fixes tests/test_diff.py's PERMISSIVE fixture, which didn't set enforce_admins explicitly and
  defaulted to None -- diverging from every real from_api() call, which now always returns a
  concrete False. Left unfixed this reproduces the exact strict-mode phantom-drift bug class
  docs/test-strategy.md documents from earlier live-repo testing. Plan doc updated in the same
  commit to match: the validator's actual shape (a permissive-value-aware dict, not a plain non-None
  check) and the added test_diff.py step were both discovered only while running the real test
  suite, not anticipated when the plan was written.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>

- Model lock_branch, branch_protection-only
  ([`4ff8c9e`](https://github.com/shipsolid/repo-policy/commit/4ff8c9eaa81d5007bf66d548c7233f8631f50086))

Makes the branch fully read-only when true. No ruleset rule type exists for this at all --
  branch_protection-only, guarded by the same ruleset-unsupported-fields validator as enforce_admins
  and required_conversation_resolution.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>

- Model required_conversation_resolution, branch_protection-only
  ([`5b34a5c`](https://github.com/shipsolid/repo-policy/commit/5b34a5c00cca09e5aae3e33159e393fbe52faa4c))

Same shape as enforce_admins: GitHub's nearest ruleset equivalent
  (required_review_thread_resolution) only exists as a pull_request rule parameter, which doesn't
  always exist (pull_requests.required can be false) -- rather than build a mapping that's sometimes
  silently unenforceable, this stays branch_protection-only, guarded by the same model validator
  enforce_admins added.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>


## v0.1.5 (2026-09-19)

### Chores

- Remove internal planning docs (spec + implementation plan)
  ([`cc06ebf`](https://github.com/shipsolid/repo-policy/commit/cc06ebfa8cbf79003ca70a9c0a853d2be56de130))

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>

### Documentation

- Document that GITHUB_TOKEN cannot manage branch protection/rulesets
  ([`12a725c`](https://github.com/shipsolid/repo-policy/commit/12a725c08bf51141151cd1229126b9cc6edbfaa6))

Confirmed against a real GitHub Actions run: the auto-generated secrets.GITHUB_TOKEN has no
  permission scope covering repository administration, under any permissions: configuration -- it
  always fails with 403 Resource not accessible by integration on branch protection/ruleset
  endpoints. This is a GitHub platform constraint, not something repo-policy or a workflow can work
  around. Consumers must supply a real PAT via a custom repository secret; the README's GitHub
  Action example and SECURITY.md now say so explicitly.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>

- Write full documentation set for repo-policy
  ([`a81512f`](https://github.com/shipsolid/repo-policy/commit/a81512fee5ab5c44ccb9bfba003dbef885b1726a))

Adds ARCHITECTURE.md, 4 ADRs, docs/ci-cd.md, docs/test-strategy.md, docs/troubleshooting.md,
  ROADMAP.md, FAQ.md, SUPPORT.md, and CODE_OF_CONDUCT.md; updates README/CONTRIBUTING/SECURITY in
  place (fixes a dead link to the deleted planning spec, adds a CLI reference table, adds a threat
  model). Content is grounded in this project's actual build history -- the three bugs live testing
  caught, the GITHUB_TOKEN platform limitation, and the release-pipeline step-ordering fix -- rather
  than generic template filler. Documentation domains that don't apply to a CLI tool (SLO, runbook,
  PRD, k8s ops, etc.) were deliberately skipped, not padded in.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>


## v0.1.4 (2026-09-19)

### Bug Fixes

- Keep repo_policy.__version__ in sync with the published version
  ([`afe820a`](https://github.com/shipsolid/repo-policy/commit/afe820a045d323a28b2df0b76a2e131768818b3d))

Confirmed via a real 'pip install repo-policy' from PyPI: __version__ still reported 0.1.0 while the
  actual published package was 0.1.3. semantic-release's version_toml only updates pyproject.toml,
  never the hardcoded string in __init__.py -- added version_variables so both stay in sync on every
  future release, corrected the current value, and changed the test to check the value is a valid
  semver string instead of hardcoding a literal that will always drift again otherwise.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>


## v0.1.3 (2026-09-19)

### Bug Fixes

- Pin dependency upper bounds to prevent future breaking installs
  ([`c259eba`](https://github.com/shipsolid/repo-policy/commit/c259eba3180c45e10b6b6dca44b2ab56b7aaaf09))

Runtime dependencies had no upper bound (httpx>=0.27, pyyaml>=6.0, pydantic>=2.0, click>=8.1), so a
  future major-version release of any of them could silently break installs with no warning. Caps
  each at its current major version; dev-only deps are left open since they don't affect what ships
  to consumers.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>

### Continuous Integration

- Run the floating major tag step before the PyPI publish step
  ([`9e11f36`](https://github.com/shipsolid/repo-policy/commit/9e11f3688a35d9ef1177cab92b9227d465f284bd))

Sequenced after PyPI meant a PyPI failure (which has occurred on every release so far, since trusted
  publishing was never configured) silently skipped the tag move every time -- confirmed via the
  last 3 release runs, where 'Move floating major tag' shows as skipped and no v1 tag was ever
  created. These two steps are independent; the Action's own consumers (uses:
  shipsolid/repo-policy@v1) must not be blocked by an unrelated PyPI outage.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>

### Documentation

- Correct the Action's floating tag from @v1 to @v0
  ([`a58436c`](https://github.com/shipsolid/repo-policy/commit/a58436ca0f976b380c42a979378fa7c064a12e50))

The release workflow's tag-move step correctly extracts the CURRENT major version from each semver
  release tag (v0.1.3 -> major 0) -- that step was working correctly the whole time. The bug was
  mine: I manually created a v1 tag earlier, based on the README's example usage rather than actual
  semver, and it silently went stale since the workflow only ever updates v0 (we haven't shipped
  1.0.0). Deleted the stale v1 tag and corrected the README to the tag that's actually kept up to
  date.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>


## v0.1.2 (2026-09-19)

### Bug Fixes

- Strict mode reported phantom drift for unconfigured status checks
  ([`90b2576`](https://github.com/shipsolid/repo-policy/commit/90b25761d978aeab0bc71a5e81361f7e84399f02))

Found via live verification against a real repo (shipsolid/playground), not the earlier mocked
  audit. diff.py's strict-mode schema default for status_checks was StatusChecksPolicy(required=[]),
  but branch_protection .from_api() and rulesets.from_api() both represent 'no status checks
  configured' as None. The mismatch meant any fully-compliant branch under strict: true showed
  permanent 1-change drift and triggered an unnecessary

PUT on every single apply -- confirmed live: 'repo-policy plan' kept reporting 'Required status
  checks None -> required=[]' against a repo that had no status checks configured at all, before and
  after apply.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>


## v0.1.1 (2026-09-19)


## v0.1.0 (2026-09-19)

### Bug Fixes

- Catch missing git binary in _resolve_repo
  ([`3337879`](https://github.com/shipsolid/repo-policy/commit/33378798cde8acd8de36573b406fcd8ba09a2346))

subprocess.run raised an uncaught FileNotFoundError when git isn't on PATH (plausible for the
  pip-installed CLI run outside the Docker Action) and no --repo/GITHUB_REPOSITORY was supplied,
  instead of the intended click.ClickException.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>

- Fail fast with a clear error when no GitHub token is configured
  ([`ec113c5`](https://github.com/shipsolid/repo-policy/commit/ec113c552fcf599551ed6289a6c39423a02866e9))

_resolve_token silently fell back to an empty string, so a missing token sent 'Authorization: Bearer
  ' on every API call and surfaced as a generic 401 deep inside _request instead of an actionable
  message before any request was made.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>

- Paginate list_rulesets() using GitHub's Link header
  ([`2c309da`](https://github.com/shipsolid/repo-policy/commit/2c309daa412a950275e4faa476c0b8141790da54))

Rulesets past page 1 were invisible to find_ruleset_by_name and prune_rulesets, since
  list_rulesets() issued a single unpaginated GET. Now requests per_page=100 and follows rel="next"
  links until exhausted.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>

- Preserve unmodeled review/status-check fields on apply
  ([`775888f`](https://github.com/shipsolid/repo-policy/commit/775888f0b6e2503ec2fa0e8667bee5988c5671d7))

pull_requests.to_branch_protection() and status_checks.to_branch_protection() hardcoded
  dismiss_stale_reviews, require_last_push_approval, and required_status_checks.strict to False on
  every call. Since apply_branch rebuilds these nested objects in full whenever ANY declared field
  changes, a targeted edit to e.g. allow_force_push silently reset any of these three settings a
  human had configured manually on GitHub, contradicting this module's own docstring and the
  README's managed-scope promise.

Both functions now take the current GET payload and read these unmodeled fields through from it
  (falling back to False only on first-ever creation), matching the pattern already used for
  enforce_admins/restrictions.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>

- Replace bare assert with explicit errors in prod code paths
  ([`46f08a0`](https://github.com/shipsolid/repo-policy/commit/46f08a0a0c192b43a02db214d22844d2daca710a))

Bare asserts are stripped entirely under python -O, silently removing the invariant checks they were
  meant to enforce -- and this project's own AGENTS.md convention calls for explicit error handling.
  Replaces: - 5 sites in GitHubClient (put/list/get/create/update) with a shared _expect_response()
  helper that raises GitHubAPIError. - 2 sites in branch_protection.py/rulesets.py's
  to_api_payload() with a ValueError naming the violated contract (resolved.pull_requests must come
  from diff.resolve_desired()).

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>

- Retry HTTP 429 responses in GitHubClient
  ([`4c538e1`](https://github.com/shipsolid/repo-policy/commit/4c538e1c25f20d265c364baa936b87ad2a53ee89))

GitHub's secondary rate limiting and abuse-detection responses return 429, which the retry predicate
  never matched (it only checked 403-with- 'rate limit'-text and >=500). A 429 raised GitHubAPIError
  immediately instead of backing off -- exactly the case the retry loop exists for.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>

- Share one ruleset-list fetch across apply/audit/prune
  ([`ae23abb`](https://github.com/shipsolid/repo-policy/commit/ae23abb87acda3664dea04f4f3e502b6fbe6c4e6))

find_ruleset_by_name() previously called list_rulesets() fresh on every invocation, so a single
  'repo-policy apply' run against N ruleset-enforced branches issued N+1 identical GET /rulesets
  calls, and a strict-mode apply issued yet another for prune_rulesets right after. Added
  prefetch_rulesets() (computes the list once, only when actually needed) and threaded an optional
  rulesets_cache through fetch_current/plan_branch/apply_branch/ apply_all/prune_rulesets/audit_all;
  the apply CLI command now fetches once and shares it with both apply_all and prune_rulesets. Also
  closes a real gap: strict-mode apply had zero test coverage before this commit.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>

- Treat empty INPUT_CONFIG/INPUT_MODE as unset in the Action entrypoint
  ([`fa4f81d`](https://github.com/shipsolid/repo-policy/commit/fa4f81d515b27a0268a3e5a8be2cd204d5535c54))

os.environ.get(key, default) only falls back when the env var is entirely absent; GitHub Actions
  sets INPUT_* vars to whatever the caller's 'with:' value resolves to, including an explicit empty
  string, which passed straight through as a confusing failure instead of honoring action.yml's
  documented defaults. Also adds the first test coverage for entrypoint.py, which had none.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>

- Validate --repo format before splitting owner/name
  ([`058a8b1`](https://github.com/shipsolid/repo-policy/commit/058a8b1c41e0324bf27deb06047fb7e87dfaa59c))

A repo string with no '/' (a malformed --repo flag or GITHUB_REPOSITORY) crashed audit/plan/apply
  with an unhandled ValueError from the tuple unpack, duplicated across two call sites. Extracted a
  _split_repo() helper that raises a clean click.ClickException instead.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>

### Chores

- Scaffold repo-policy package
  ([`add2293`](https://github.com/shipsolid/repo-policy/commit/add2293fa49dee656b1e1bffbb6465a3991a7364))

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>

### Continuous Integration

- Add semantic-release automation with floating major tag
  ([`6c549c5`](https://github.com/shipsolid/repo-policy/commit/6c549c5cc14fa93dac013d3cbc04d3c6979269a4))

Routes the release tag through an env var instead of interpolating ${{ }} directly into the shell
  script, per the repo's workflow-injection guidance.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>

- Add test/lint/typecheck workflow
  ([`8b22219`](https://github.com/shipsolid/repo-policy/commit/8b22219717de31b5a2b46bdc9d56afdb0cb1014a))

Also fixes the ruff/mypy findings this surfaced: Optional[X] -> X | None style throughout, import
  ordering, an unused unpacked variable in a test, and a PYI034 __enter__ return-type nit (kept as a
  string annotation since typing.Self needs Python 3.11+ and we target 3.10+).

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>

### Documentation

- Add README, CONTRIBUTING, and SECURITY
  ([`b270ac8`](https://github.com/shipsolid/repo-policy/commit/b270ac89a6892a7058efb79faa2b46ec65ad5453))

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>

### Features

- Add apply engine with managed-scope and prune support
  ([`c982117`](https://github.com/shipsolid/repo-policy/commit/c982117b9f6b16e62172cbca0cf81ba51fac1c15))

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>

- Add audit orchestration
  ([`e25c131`](https://github.com/shipsolid/repo-policy/commit/e25c1318e50eeaa781ffc3a041ae95115cb3537e))

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>

- Add branch protection and required-signatures client methods
  ([`5914757`](https://github.com/shipsolid/repo-policy/commit/5914757f9b7c9515c47f248b9ef53cc134b73dc8))

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>

- Add branch_protection API translator
  ([`75b2ed8`](https://github.com/shipsolid/repo-policy/commit/75b2ed88f1fe565ad019d1f9f305dd84d902ac9f))

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>

- Add diff engine with managed-scope and strict resolution
  ([`2f68e61`](https://github.com/shipsolid/repo-policy/commit/2f68e61c60eefa770e5d01b1aa482126869e2712))

Fixes an add/remove misclassification for allow_force_push/allow_deletion, whose polarity is
  inverted relative to every other field (False means a restriction is present, not absent).

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>

- Add GitHub Action (Docker container action)
  ([`9dc68f1`](https://github.com/shipsolid/repo-policy/commit/9dc68f103b11935730cf45bc634ff22a849688a1))

Copies README.md into the build context too — hatchling's readme field validation fails the build
  otherwise.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>

- Add GitHubClient with retrying request core
  ([`d58ca80`](https://github.com/shipsolid/repo-policy/commit/d58ca8046a3406309e2bb1efea7a1e633645026d))

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>

- Add human-readable plan rendering
  ([`9f7c44f`](https://github.com/shipsolid/repo-policy/commit/9f7c44f176fbbeb92c090f7498fb5719dd4c1824))

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>

- Add policy.yml loader with validation errors
  ([`aaee7b5`](https://github.com/shipsolid/repo-policy/commit/aaee7b5cfb0ff0ccf484bba8a094d89c7638c637))

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>

- Add PolicyConfig models
  ([`2320da0`](https://github.com/shipsolid/repo-policy/commit/2320da0c801206b7b9acd95dc1f2afd78d3d7a0a))

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>

- Add repo-policy CLI with validate/audit/plan/apply
  ([`6ea9217`](https://github.com/shipsolid/repo-policy/commit/6ea9217e4381d9ff24e208f5f8664d4f40ee9d87))

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>

- Add ruleset API translator
  ([`2e7e411`](https://github.com/shipsolid/repo-policy/commit/2e7e411b90dd6f81c74e6e0688a6a102dc4ae0e6))

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>

- Add ruleset CRUD to GitHubClient
  ([`4dc5765`](https://github.com/shipsolid/repo-policy/commit/4dc576532cb6badc93d1ad92915948562526ea19))

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>

- Add shared pull_requests and status_checks translators
  ([`50c7417`](https://github.com/shipsolid/repo-policy/commit/50c7417b071f49cc2929572790e97309480ce5e9))

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>

### Testing

- Add cross-backend field-parity guard
  ([`d63a39c`](https://github.com/shipsolid/repo-policy/commit/d63a39c68b064ed32dac606c0e9bef46a35cf5ca))

branch_protection.py and rulesets.py each hand-write their own payload translation with no shared
  source of truth between them -- the structural reason the previous commit's clobber bug existed in
  only one backend with zero test coverage. A full unification refactor is more risk than this
  warrants right now; this parametrized test is the cheaper guardrail: it fails loudly if a future
  field is wired into only one backend.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>

- Add idempotency guarantee for apply
  ([`102505c`](https://github.com/shipsolid/repo-policy/commit/102505c1656308bb18784beb0327eed7f6f4b961))

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
