# CI/CD Pipeline

## Pipeline Architecture

| Workflow | File | Trigger | Purpose |
|---|---|---|---|
| CI | `.github/workflows/ci.yml` | every PR, push to `main`, and as a reusable workflow called from Release | lint, typecheck, unit tests, package build, workflow security scan |
| Release | `.github/workflows/release.yml` | push to `main` | run CI, then (if CI passes, and a human approves the `release` environment) compute a version bump, land it on `main` via a signed, release-bot-authored pull request, sign and push the release tag, PyPI publish, SBOM generation, artifact attestation |
| Security | `.github/workflows/security.yml` | every PR, push to `main`, weekly schedule | dependency-vulnerability scanning (pip-audit), static code analysis (CodeQL: Python + Actions), a second, weekly zizmor pass -- advisory/reporting, not PR-blocking (see below) |
| Policy Audit | `.github/workflows/policy-audit.yml` | daily schedule, push to `main` touching the policy file or itself, manual dispatch | read-only `repo-policy audit` against this repo's own `.github/repository-policy.yml` (Task 9 dogfooding) -- reports drift, never mutates; not PR-blocking |

The pre-gating version of both workflows ran green on real GitHub Actions runners, not just
locally (see `docs/test-strategy.md` for the project's general stance on real-vs-mocked
verification). The job split, `workflow_call` gating, and permission scoping described below are
new in this revision and are pending their first live run — see this repository's audit-remediation
plan (Task 6) for the local `zizmor` verification performed instead.

### Pipeline flow: gating and permission separation

```
push to main
     │
     ├─────────────────────────────────┐
     ▼                                  ▼
 ci.yml (direct trigger)          release.yml
 contents: read                        │
 -> "required" status check            ├─ ci  (workflow_call -> re-runs ci.yml's job graph)
    (what branch protection            │      contents: read
    requires to merge any PR)          │
                                        ├─ check-release-warranted  (no environment: gate)
                                        │      contents: read -- dry-run
                                        │      `semantic-release --strict version --print-tag`,
                                        │      zero commit/tag/push side effects
                                        │
                                        ▼  (only if ci passed AND a release is warranted)
                              ┌─────────────────────────────┐
                              │ release                     │  <- environment: release
                              │ contents: read (default     │     requires a human's approval
                              │ token); real writes via     │     click before this job's first
                              │ secrets.RELEASE_BOT_TOKEN   │     step runs -- RELEASE_BOT_TOKEN /
                              │ (environment secret)        │     RELEASE_BOT_SIGNING_KEY only
                              └──────────────┬──────────────┘     materialize on the runner then
                                             │  version bump -> signed local commit+tag ->
                                             │  release-bot PR -> squash-merge -> push signed
                                             │  tag -> `git verify-tag` (fails closed if the
                                             │  tag isn't verifiably signed)
                              ┌──────────────┼──────────────┐
                              ▼                              ▼
                        publish                          sbom
                        id-token: write only        contents: read, id-token: write,
                        (OIDC -> PyPI trusted        attestations: write
                        publishing; never                  │  CycloneDX SBOMs (wheel install +
                        checks out the repo)               │  Docker image), Sigstore-backed
                                                            │  attestations (wheel, sdist, both
                                                            │  SBOMs, Docker image by digest)
                                                            ▼
                                                     release-assets
                                                     contents: write only (no checkout, no
                                                     id-token) -- attaches the two SBOM files to
                                                     the GitHub release `release` already created
```

No job ever holds both `contents: write` and `id-token: write` at once — see "Permission scoping
and concurrency" below for why that split specifically matters for PyPI trusted publishing's
security model, and "SBOM and provenance" for why `sbom`/`release-assets` are two jobs, not one.

### CI job graph

`ci.yml` splits what used to be one `test` job into nine independent jobs: `lint` (`ruff check` +
`ruff format --check`), `typecheck` (`mypy`), `test` (`pytest --cov-fail-under=95`, matrixed over
every Python version `repo-policy` claims to support, 3.10–3.14), `build` (`python -m build`,
package-validation, and the source of the `dist/` artifact every downstream job reuses),
`wheel-smoke` (Task 12: installs the actual built wheel — no dev extras, no editable install — into
clean 3.10 and 3.14 interpreters and smoke-tests the installed CLI), `security` (`zizmor --pedantic
--offline` against this repo's own workflow YAML), `validate-self-policy` (Task 9: catches a
malformed `.github/repository-policy.yml` at PR time, before `policy-audit.yml`'s next scheduled
run would), `verify-action-lock` (Task 7: regenerates `requirements-action.txt`'s hash lock and
byte-compares it against the checked-in file), and `docker` (Task 7: two clean `--no-cache` builds
of the Action image, an inventory diff between them, a smoke-test battery against the built image,
and a Trivy vulnerability scan) — plus a final `required` job that `needs:` all nine and fails if
any of them failed, were cancelled, or were skipped. `required` exists purely to give branch
protection and Release a single stable status name, `required`, that doesn't change as jobs
are added or removed. Each of the other nine jobs declares its own minimal `permissions:`
(`contents: read`); `required` itself needs no permissions (it only evaluates its dependencies'
outcomes) and declares `permissions: {}`. The workflow-level default is also `permissions: {}`.

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
graph twice — once producing the standalone `required` check, once nested under `Release / ci`
gating the release. This is deliberate: reliability of "is `main` green" outweighs the modest extra
compute for a repository this size. A real release now runs it a third time too — see below.

### Release-bot identity, commit/tag signing, and the PR-merge redesign (Task 10)

Task 9 applied real branch protection to `main` on `shipsolid/repo-policy`:
`pull_requests.required: true` plus `enforce_admins: true`, and `repo-policy` deliberately never
declares a PR-bypass allowlist for this repo (`bypass_pull_request_allowances` is a fully modeled
`policy.yml` field — see `docs/adrs/0005-nested-actor-list-fields.md` — that `.github/repository-
policy.yml` simply never sets, so managed-scope leaves whatever's already on GitHub, nothing,
untouched). The practical effect:
nothing can push directly to `main` anymore, including this workflow's own `python-semantic-release`
step, which previously did exactly that with `secrets.GITHUB_TOKEN`. This section documents the
research behind the redesign that followed and the reasoning for each departure from the pre-Task-10
design; see `SECURITY.md`'s "Release Signing" section for what's signed, how to verify it, and the
key-rotation/recovery procedures, and its "Release Pipeline Setup Checklist" for exactly what still
needs to exist on GitHub before any of this can run for real.

**What `python-semantic-release` v9 actually supports (checked against the installed `9.21.2`, not
assumed):** its `version` command exposes `--push`/`--no-push` and `--vcs-release`/
`--no-vcs-release` as independent flags (`semantic-release version --help`), and its own
`gitproject.py` shells out to plain `git commit`/`git tag -a`/`git push` with no signing override,
so local `git config` (`commit.gpgsign`, `tag.gpgSign`, `gpg.format`) applies to whatever
PSR does exactly as it would to a manual `git commit`. Committer *identity* is the one exception:
PSR reads it from its own `commit_author` setting (`GIT_COMMIT_AUTHOR` env var, or
`[tool.semantic_release] commit_author` in `pyproject.toml`) and force-exports
`GIT_AUTHOR_NAME`/`EMAIL`/`GIT_COMMITTER_NAME`/`EMAIL` from it — confirmed by reading `gitproject.py`
and `config.py` — which overrides whatever `git config user.name`/`user.email` the host has set, so
the version-bump step in `release.yml` sets `GIT_COMMIT_AUTHOR` explicitly rather than relying on
the host git identity the signing config above configures for a different purpose. That's what
makes the split below possible:
`version --no-push --no-vcs-release` computes the bump, writes it to `pyproject.toml`/`__init__.py`,
builds `dist/`, and creates a **local, signed** commit and tag — without ever touching the network.
Separately, PSR's `publish` command turns out to be for uploading already-built distributions to an
*existing* VCS release (`hvcs_client.upload_dists`), not for creating one — the command that actually
does what this pipeline needs post-merge is `changelog --post-to-release-tag <tag>`, which builds
release notes from git history for an already-tagged version and creates (or updates) the remote
release for it, with no commit/tag/push side effects of its own. Re-running `version` a second time
after the tag already exists was considered and rejected: PSR treats an already-released version as
"nothing to do" (checked via `previously_released_versions` in `version.py`) and silently no-ops,
so it can't be used to "finish" a release after an out-of-band tag push.

**Signing config — one `$HOME`, one place it's set:** `release.yml` has a single, explicit
"Configure host-side git signing" step, right after checkout and before the version-bump step,
that writes the signing key and an `allowed_signers` file and sets `gpg.format ssh` /
`user.signingKey` / `commit.gpgsign` / `tag.gpgsign` globally on the runner **host's** `$HOME`.
Every later step in this job — the version-bump computation itself, the signing-smoke-test right
after it, both diff-checks, and the tag-creation/floating-tag steps further down — is a plain
`run:` step on that same host, so all of them see the same config with no duplication needed.

This wasn't always true: an earlier version of this step ran `python-semantic-release` via a
pinned Docker container Action instead of a plain host-side `pip install`, which meant the
Action's own container-internal signing config (written to `/github/home`, a separate
runner-temp-dir bind mount — confirmed by tracing the Action's own `action.sh` and the GitHub
Actions runner's `ContainerActionHandler.cs` source) never reached any of the host-side steps
around it, requiring a *second*, duplicated `git config --global` setup just for the host. That
Action is gone now — see "Why the version-bump step doesn't use a pinned Action" below — so
there's no longer a second `$HOME` to account for at all.

One second-order consequence of setting `tag.gpgsign true` globally on the host: the pre-existing
"move the floating major tag" step (`git tag -f "$MAJOR_TAG" "$RELEASE_TAG"`, no `-a`/`-m` of its
own) stops working once that global config is in place — git refuses to create a tag with no message
once tag signing is on ("fatal: no tag message?", exit 128), confirmed by local reproduction. The
fix is a one-command override, `git -c tag.gpgsign=false tag -f "$MAJOR_TAG" "$RELEASE_TAG"`, safe
specifically because `$MAJOR_TAG` (e.g. `v0`) is a floating convenience alias, not itself the
verified release artifact. The `^{}`-peeling this step doesn't do is deliberate, not an oversight:
without it, `git tag -f "$MAJOR_TAG" "$RELEASE_TAG"` creates a *nested* lightweight tag pointing
directly at the signed annotated `$RELEASE_TAG` object, and `git verify-tag "$MAJOR_TAG"` verifies
transitively through that nesting — confirmed by local scratch testing (a real ed25519 key, a real
`allowed_signers` file: the nested tag verifies cleanly against the same signature the underlying
annotated tag carries). Peeling to the commit would lose that property for no benefit.

**Why the version-bump step doesn't use a pinned Action:** it used to. The step ran
`python-semantic-release/python-semantic-release`, pinned by full commit SHA per this project's
own Action-pinning rule. That turned out not to be enough: the SHA pins the Action's own code, not
the version of the `python-semantic-release` PyPI package its Dockerfile installs — which is
resolved fresh against PyPI on every build, not baked into a fixed image layer at the SHA's commit
time. This caused a real incident during this project's first live release attempt: the Action's
container installed `python-semantic-release==10.6.2`, not the `9.21.2` this project is designed,
tested, and documented against (a fact confirmed directly in that run's own job logs), and 10.6.2
computed a "patch"-type version bump from `0.4.7` as `1.0.0` instead of the correct `0.4.8` —
confirmed independently by installing the pinned `9.21.2` directly and running
`semantic-release version --print` against the same commit history, which correctly prints
`0.4.8`. The wrong `1.0.0` landed on `main` and as a signed tag before the pipeline failed at a
later step; nothing was published externally (no PyPI release, no GitHub Release).

The first cleanup attempt — a standalone `chore:` commit reverting the version files, pushed
separately from the pipeline fix — turned out to be wrong, and produced a second occurrence of the
exact same incident: any push to `main` re-triggers `release.yml`, and `check-release-warranted`
re-evaluates the whole range since the last release TAG, not just the triggering commit. Deleting
the erroneous `v1.0.0`/`v1` tags made `v0.4.7` "the last release" again, and the real `fix:` commit
this release exists to ship was still sitting in history since then — PSR has no notion of
"already attempted and failed" separate from git tags, so `check-release-warranted` correctly said
"warranted" again on the revert-only push, and since the pipeline fix hadn't landed yet, the same
broken Action ran again and produced a second wrong `1.0.0` commit and tags. Contained again (no
external publish either time), but avoidable: **a revert-only push does not stop this from firing
again while the underlying commit remains genuinely unreleased — only landing a correct release,
or fixing the pipeline *before* the next push, does.** The actual fix below was bundled into the
same commit as the second revert for exactly this reason.

The fix: stop depending on the Action's own floating dependency resolution. The version-bump step
now installs `python-semantic-release==9.21.2` directly via `pip`, exactly like
`check-release-warranted` and the "Create the GitHub release" step already (and correctly) do —
all three PSR invocations in this workflow now pin the same explicit package version, not just an
Action wrapping it. Running on the host directly removes the Action's `ssh_*_signing_key` inputs:
this step now just inherits the host-side signing config from the step before it, the same as
every other step in this job. Committer *identity* is a different matter, though, and is **not**
inherited the same way — dropping the Action's `git_committer_name`/`git_committer_email` inputs
without a replacement was an early draft of this fix, and it was wrong: see the `GIT_COMMIT_AUTHOR`
note above for why PSR needs that identity told to it explicitly regardless of host git config.

**Why local `main` gets reset before creating the GitHub release:** this job's checkout happens
once, at the very start, and the version-bump step's local commit (step 4) lands on that same
local `main` — but GitHub's squash-merge (step 6) creates a brand new commit on the real `main`,
a *sibling* of that local commit, not a descendant of it. Nothing before "Create the GitHub
release" ever reconciles the two: the tag-creation step (step 9) creates the release tag directly
against the captured merge SHA, without touching local `main` at all. `semantic-release changelog
--post-to-release-tag` (what actually creates the GitHub release) needs local `main` to have
reached that commit, though — it builds its release history by walking `git log` backwards from
HEAD and matching commits against tag targets, and it separately refuses to run at all on a
detached HEAD. Left unreconciled, this pipeline's first three live release attempts all failed at
this exact step with "tag v0.4.8 not in release history" — a bug unrelated to, and older than, the
`python-semantic-release` version-pinning incident documented above; it simply never surfaced
before because no earlier attempt had gotten this far. The fix is a dedicated step,
`git checkout main && git reset --hard "$MERGED_SHA"`, right before the changelog command runs —
a hard reset, not a fast-forward, since the two commits are siblings; safe because nothing after
this point in the job reads local HEAD for anything other than this one command, and `MERGED_SHA`
is already this pipeline's own captured source of truth for exactly which commit is real.

**Tag protection vs. branch protection (verified, not assumed):** the task that produced this
redesign started from a documentation-based hypothesis that classic branch protection — the
`branch_protection` enforcement `repository-policy.yml` declares for `main`, which calls
`PUT /repos/{owner}/{repo}/branches/{branch}/protection` (`src/repo_policy/github_client.py`) — only
ever governs `refs/heads/<branch>`, and that tag pushes (`refs/tags/*`) are a structurally separate,
ungoverned namespace unless a *different* mechanism (a classic tag-protection rule, or a Ruleset with
a tag target) is also configured. That was checked against GitHub's own documentation rather than
taken on faith: the classic-protected-branches docs never mention tags at all and describe every
setting (including "Require linear history") purely in terms of branches; the Rulesets docs describe
tag-scoped rules as a *separate*, opt-in capability ("you specify which branches or tags... the
ruleset applies to"), not something a branch-scoped rule inherits. `repository-policy.yml` declares
neither a Ruleset nor a classic tag-protection rule for this repository, and the branch-protection
REST endpoint is structurally incapable of addressing a tag (its URL is parameterized by branch
name). Conclusion: the hypothesis holds, and the release tag can still be pushed directly, exactly
as before Task 9 — this residual uncertainty is real (this dispatch had no live-repo API access to
positively confirm no *other*, out-of-band tag protection exists on `shipsolid/repo-policy` today)
but every piece of evidence available says tags remain ungoverned.

**Why the commit landing on `main` can't itself be the signed artifact:** the obvious design —
sign the commit locally, push a branch, merge via PR, done — runs into a hard GitHub platform
constraint. GitHub's merge API creates a **new commit** for every merge method: squash always
synthesizes one from the PR's diff; even rebase-merge, which looks like it should fast-forward
when the base hasn't moved, "always updates the committer information and creates new commit SHAs"
per GitHub's own docs, and "the commits ... are added to the base branch without commit signature
verification" because "GitHub didn't truly create this commit, and can't therefore sign it" with the
original author's key. **That "no verification" behavior is specific to rebase-merge, not squash** —
squash-merge is exactly the case GitHub *did* truly create: GitHub signs commits made through its web
interface/API with its own key (`web-flow.gpg`) and shows them Verified, and a squash-merge performed
via `gh pr merge` goes through that same API path. So the squash-merge commit that lands on `main`
does get a real, independently-Verified signature — just not the release-bot's. That distinction is
the actual reason the tag, not the commit, remains this pipeline's release-bot-attributed artifact:
there is no merge method that lands the release-bot's own exact, pre-signed commit object on `main`
through a required PR merge — full stop, not a gap in this design — not because the resulting commit
is unverified in any sense. The annotated release **tag** doesn't have this problem: it's a separate
git object from the commit it points at, created and pushed directly (tags being outside branch
protection's scope, per the research above) against whatever commit actually landed on `main`,
carrying its own valid release-bot signature regardless of whether — or by whom — the underlying
commit is itself signed. From Task 10 on, both signatures are real and independently checkable
(GitHub's on the commit, attesting the merge itself; the release-bot's on the tag, attesting this
specific release), but the tag is the one this pipeline treats as its cryptographically-verified
release artifact, since it's the only one that identifies the release-bot specifically. `release.yml`'s
`git verify-commit HEAD` right after PSR runs is a signing *smoke test* on the pre-merge commit
(confirms the signing config works before spending a PR/CI/approval cycle on it), not the release's
real verification gate. That's `git verify-tag`, run once against the pushed tag, immediately before
anything downstream (floating-tag move, GitHub release creation, PyPI publish, SBOM/attestation)
is allowed to proceed.

**Why squash, specifically, not rebase:** `linear_history: true` already rules out a merge commit.
Between squash and rebase, squash was chosen because `gh pr merge --squash --subject "..."` lets this
pipeline set the final commit message on `main` independently of the PR's own head commit's message
— needed for the `[skip ci]` timing below. Rebase-merge replays the original commit(s) verbatim; there
would be no way to inject `[skip ci]` into the landing commit without it also being present on the
PR's head commit, which breaks the next point entirely.

**Why `[skip ci]` moved from the commit message to the merge subject:** the pre-Task-10
`commit_message` (`"chore(release): {version} [skip ci]"`, in `pyproject.toml`) existed to stop the
version-bump commit's own push from re-triggering `ci.yml`/`release.yml`. GitHub's skip-ci keywords
are evaluated against "the commit that contains the skip instructions" for **both** `push` and
`pull_request` events — including "the HEAD commit of a pull request." If the release-bot's local
commit still carried `[skip ci]` when pushed to its branch and opened as a PR, `ci.yml`'s
`pull_request` trigger would be skipped too, `required` would never appear on the PR, and the
merge — which this pipeline waits on that exact check for — would hang forever. So `commit_message`
in `pyproject.toml` dropped `[skip ci]` entirely, and it's added back only in the squash-merge's own
subject (`gh pr merge --subject "chore(release): ${TAG} [skip ci]"`), so only the commit that
actually lands on `main` carries it. That matters beyond tidiness: `RELEASE_BOT_TOKEN` is a real PAT,
and unlike the default `GITHUB_TOKEN` (whose pushes never trigger new workflow runs — GitHub's own
recursion guard), a PAT's pushes trigger normally. Without `[skip ci]` on the landing commit, the
squash-merge would fire `release.yml` again on itself — harmless in the end (PSR would find nothing
new to release and no-op) but wasteful, and it would demand a second, redundant owner-approval click
on the `release` environment for no real release.

**Waiting for the merge to become possible, without reading check status directly:** the alternative
design — poll `gh pr checks --required --watch` until `required` reports success, then merge —
would work fine with the token this pipeline actually uses: `RELEASE_BOT_TOKEN` is a **classic** PAT
(see SECURITY.md's "Secrets Management" and setup checklist for why classic, not fine-grained —
fine-grained PATs can't be issued by an account that's only an outside collaborator on a
personal-account-owned repo), and classic PATs can call the Checks API without restriction. So the
retry-loop design below is a deliberate choice on its own merits, not a workaround forced by a
permission gap: `release.yml` retries a **plain `gh pr merge`** on a 15-second interval, relying on
GitHub evaluating mergeability — including whether `required` has actually passed — server-side,
from the repository's own branch-protection state, independent of whatever the calling token can
itself read. That's a simpler dependency to reason about than a separate check-status poll, and it
happens to also sidestep a real, separate constraint should this project ever migrate `RELEASE_BOT_TOKEN`
back to a fine-grained PAT in the future (e.g. if `shipsolid/repo-policy` moves to an org): fine-grained
PATs currently cannot call the Checks API at all (confirmed against GitHub's own fine-grained-PAT
permissions reference — there is no selectable "Checks" repository permission for that token type;
the closest options, "Actions" and the legacy "Commit statuses," don't reliably cover the check-run
rollup a merge decision actually depends on, per multiple GitHub community reports). A "not mergeable
yet" failure is the expected, retriable state while CI is still running on the PR; the loop is
bounded (`deadline`/30 minutes) so a genuinely broken `required` (or any other permanent merge
blocker) fails the release job loudly instead of blocking the `release-main` concurrency queue
indefinitely, at the cost of a slower failure than a check-status-aware wait would give for that
specific case. The loop also classifies a handful of `gh pr merge` failure messages (grounded in
`gh`'s own CLI source, not guesswork) as non-retriable — a real merge conflict or a stale base branch
can never resolve just by retrying, so those fail fast instead of burning the full 30 minutes — see
`release.yml`'s own comment on that step for the exact classification and reasoning.
`security.yml`'s pip-audit/CodeQL/zizmor jobs also run on this PR (nothing special-cases a
release-bot PR out of them) but are advisory, not PR-blocking (see "Security workflow" below), so
their runtime doesn't factor into how long the retry loop needs to wait.

**The owner-approval gate:** the `release` job now declares `environment: release`. Once that
environment exists with a required-reviewer protection rule (see `SECURITY.md`'s setup checklist),
every real release pauses there for a human approval click before the job's first step runs — and,
because `RELEASE_BOT_TOKEN` and the SSH signing key are environment secrets rather than repository
secrets, they aren't even materialized on the runner until that approval is granted. This is a
deliberate behavior change from the pre-Task-10 pipeline, which released fully automatically on every
qualifying push to `main`: from Task 10 on, a push to `main` still runs CI and computes the version
bump automatically, but landing it requires one manual approval per release.

## Branch Strategy

Single `main` branch, no long-lived release branches. Every release is still computed from `main`'s
own history by `python-semantic-release` reading Conventional Commits since the last release tag;
the only branch involved is the release-bot's own short-lived `release-bot/v{version}` branch (Task
10), which exists solely to carry the version-bump commit through the required pull request and is
deleted on merge by the repository's own `delete_branch_on_merge: true` self-policy setting
(`.github/repository-policy.yml`) — not by a `--delete-branch` flag on the merge call itself; see
"Waiting for the merge to become possible..." above for why the two were deliberately decoupled.

## Release Process

1. A commit lands on `main` (via a merged PR — nothing can push directly to `main` since Task 9).
2. `release.yml`'s `ci` job runs the full CI job graph (lint, typecheck, test, build, security)
   against that commit, and its `check-release-warranted` job (no `environment:` gate, so no
   approval wait) runs in parallel, installing the pinned `python-semantic-release` CLI directly and
   running `semantic-release --strict version --print-tag` to determine, with zero side effects,
   whether this commit's history actually warrants a release. If CI fails, nothing below happens —
   `release`/`publish`/etc. are skipped. If `check-release-warranted` says no release is warranted
   (e.g. a `chore:`/`docs:`-only push), `release` is skipped too, **without ever reaching the
   approval gate below** — see "The owner-approval gate" above for why this matters given
   `concurrency: release-main`.
3. If a release is warranted, `release` waits for a human to approve the `release` GitHub
   Environment (see "The owner-approval gate" above and `SECURITY.md`'s setup checklist). Once
   approved, `python-semantic-release` inspects commits since the last release tag:
   - `feat: ...` → minor bump
   - `fix: ...` → patch bump
   - `feat!: ...` or a `BREAKING CHANGE:` footer → major bump
   - `chore:` / `docs:` / `test:` / `ci:` → no release
4. If a release is warranted, it bumps `pyproject.toml`'s `project.version` **and**
   `src/repo_policy/__init__.py`'s `__version__` (via `version_variables` in
   `[tool.semantic_release]` — both must be listed, or they silently diverge; this happened once
   in production, see `docs/test-strategy.md`), regenerates `CHANGELOG.md`, and creates a **local**,
   release-bot-signed commit (`chore(release): {version}`) and tag (`v{version}`) — neither is
   pushed yet. (Host-side git signing config for this and every later signing/verification step in
   the job is set up once, right after checkout, before this step — see "Signing config — one
   `$HOME`, one place it's set" below.)
5. The workflow diffs that local commit against the pre-release commit and fails the job if it
   touched anything other than `pyproject.toml`, `src/repo_policy/__init__.py`, and
   `CHANGELOG.md` — an early, fail-fast copy of the check in step 8, against a compromised or
   misbehaving semantic-release run silently slipping in an unrelated code change.
6. The release-bot pushes a `release-bot/v{version}` branch and opens a pull request into `main`.
7. The workflow retries `gh pr merge --squash` against that PR (skipping straight through if a
   previous, timed-out run already merged it) until GitHub's own server-side mergeability check
   passes — which depends on the PR's `required` — or a genuinely non-retriable failure (a real
   merge conflict, a stale base branch) is detected and fails fast instead of waiting out the full
   30-minute budget. `[skip ci]` is added to the squash commit's own message (not present earlier —
   see "Why `[skip ci]` moved..." above) so the merge doesn't re-trigger this same workflow. It then
   reads back the exact commit the merge produced (`gh pr view --json mergeCommit`) for the next two
   steps to use.
8. The workflow diffs that exact merged commit against its own immediate parent — the re-scoped,
   real copy of step 5's check, since GitHub's squash-merge always creates a new commit distinct
   from the one diffed in step 5. This uses the commit captured in step 7, not a fresh
   `git rev-parse origin/main`, so it can't be thrown off by an unrelated commit landing on `main`
   in the meantime.
9. The release-bot creates, signs, and pushes the `v{version}` tag directly against that same
   captured commit (tags are outside branch protection's scope — see "Tag protection vs. branch
   protection" above), then verifies its own signature with `git verify-tag` before anything below
   runs. The floating major tag (`v0` until a `1.0.0` ships — see README) is force-moved to point at
   it (with tag signing disabled for just that one command — see "Signing config — one `$HOME`,
   one place it's set" below for why that's necessary and safe). Local `main` — still sitting at
   the release-bot's own local bump commit from step 4, a sibling of the real squash-merge commit,
   not an ancestor of it — is then reset to that captured commit directly (see "Why local `main`
   gets reset before creating the GitHub release" below for why this step exists and what breaks
   without it), and only then does `semantic-release changelog --post-to-release-tag` create the
   GitHub release for it.
10. The built sdist/wheel (built locally in step 4, before any of the push/PR/merge machinery
    above — their file contents don't change when the surrounding commit gets squashed) are handed
    off (via `actions/upload-artifact` / `download-artifact`) to a separate `publish` job, and
    published to PyPI via trusted publishing (OIDC) — no long-lived API token stored in the repo.
11. In parallel with `publish`, two more jobs (Task 8) handle SBOM and provenance: `sbom`
    re-checks out the exact released commit (by tag, since the release-bot's own PR merge moved
    `main` past `github.sha`), generates a CycloneDX SBOM for the wheel's install environment and
    one for the Docker Action image (built fresh from the release commit's `Dockerfile`), and signs
    GitHub artifact attestations (Sigstore-backed build provenance) for the wheel, sdist, both
    SBOMs, and the Docker image (by digest); `release-assets` then attaches the two SBOM files to
    the GitHub release step 9 already created. Split into two jobs rather than one so no single job
    ever holds both `contents: write` (needed to attach release assets) and `id-token: write`
    (needed to mint attestations) at once — see "SBOM and provenance" below and `SECURITY.md`'s
    "Verifying release artifacts" for how to check these.

### Permission scoping and concurrency

Each job in `release.yml` carries only the permission its own steps need:

| Job | Permissions | Why |
|---|---|---|
| `ci` | `contents: read` | Only checks out code to lint/type-check/test/build. |
| `check-release-warranted` | `contents: read` | Read-only: checks out `main`'s history and runs `semantic-release --strict version --print-tag`, which has no commit/tag/push side effects. No `environment:` gate — see "The owner-approval gate" below for why that's the point. |
| `release` | `contents: read` (default token); real write access comes from `secrets.RELEASE_BOT_TOKEN` instead | Every push, PR open/merge, tag push, and GitHub-release creation in this job authenticates as the release-bot via its own PAT, not the workflow's ambient `GITHUB_TOKEN` — so the default token's own `permissions:` grant stays at read-only. Gated behind the `environment: release` approval (only reached when `check-release-warranted` says a release is warranted); `RELEASE_BOT_TOKEN` and the SSH signing key are environment secrets, not repository secrets, so they don't exist on the runner until a human approves. |
| `publish` | `id-token: write` | OIDC trusted publishing only — never checks out the repo, never sees `contents: write`. |
| `sbom` | `contents: read`, `id-token: write`, `attestations: write` | Generates SBOMs and signs Sigstore-backed attestations. Its own steps (installing a PyPI package, `docker build`, `pip install` a built wheel) aren't narrow enough to also trust with `contents: write` in the same job — see "SBOM and provenance" below for why that matters concretely (PyPI trusted publishing matches on repository + workflow filename). |
| `release-assets` | `contents: write` only | Attaches the SBOMs `sbom` produced to the GitHub release. Never checks out the repo and never holds `id-token: write` — mirrors the `release`/`publish` split for the same reason. |

The `release` job's own default-token `permissions: contents: read` is what it needs anyway: the
version-bump step's `pip install "python-semantic-release==9.21.2"` plus a plain
`semantic-release version --no-push --no-vcs-release` call has no `github_token`-shaped input of
its own to hand a token to at all — it never authenticates to GitHub, since `--no-push`/
`--no-vcs-release` mean it only reads local git history and writes local, unpushed commit/tag
objects.

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

## SBOM and provenance (`sbom` and `release-assets` jobs)

Added in Task 8, alongside `pip-audit`/CodeQL/`security.yml` — see "Security workflow" below for
those; this section is specifically about what happens per-release.

Two jobs run after `release` succeeds, in parallel with `publish` — split the same way
`release`/`publish` are split (see the permission-scoping table above), and for the same reason:
neither job may hold `contents: write` and `id-token: write` at the same time. `sbom`'s own steps
(installing `cyclonedx-bom` from PyPI, `docker build` from the release commit, `docker run`, `pip
install` the built wheel) aren't narrow enough to trust with repo-write *and* the ability to mint
an OIDC token in the same job — PyPI trusted publishing matches on repository + workflow filename,
and both live in `release.yml` alongside `publish`, so a compromised step here holding both
capabilities could mint a `pypi`-audience token and publish an arbitrary release. So:

1. **`sbom`** (`contents: read`, `id-token: write`, `attestations: write`) — checks out the exact
   released commit by tag (`needs.release.outputs.tag`), not `github.sha` (which by this point
   points at that commit's *parent* — `release`'s own version-bump commit already moved `main`
   forward); downloads the `dist/` artifact `release` uploaded; installs the wheel into a
   throwaway venv and runs `cyclonedx-py environment` against it (`sbom-wheel.cdx.json`); builds
   the Docker Action image fresh from the release commit's `Dockerfile` (a single build — see the
   digest-reproducibility caveat below for why this isn't the same guarantee as `ci.yml`'s
   two-build check), extracts its installed-package list (`pip list --format=freeze`) and runs
   `cyclonedx-py requirements` against it (`sbom-docker.cdx.json`); runs
   `actions/attest-build-provenance` twice — once (`subject-path`) covering the wheel, sdist, and
   both SBOM files together, once more (`subject-name` + `subject-digest`) for the Docker image,
   since a file-based and a digest-based subject can't share one call; uploads both SBOM files as
   a workflow artifact (the same `upload-artifact`/`download-artifact` handoff `release` uses to
   pass `dist/` to `publish`).
2. **`release-assets`** (`contents: write` only, no checkout) — downloads the SBOM artifact and
   runs `gh release upload` to attach both files to the GitHub release that `release`'s own
   python-semantic-release step already created (that step creates the VCS release but does not
   upload `dist/` to it; PyPI, via the separate `publish` job, remains the actual package-install
   source).

**Why the Docker image is attested by digest, not by registry reference — and what that digest
does and doesn't prove:** this repository never pushes the Docker Action image to a registry —
`action.yml` builds it fresh from the pinned `Dockerfile` at consumption time (see Task 7). The
attestation therefore records the digest of the image `sbom` itself built from the release commit,
not a `ghcr.io`/Docker Hub tag.

**This digest is not reproducible across independent builds** — confirmed by building the same
commit twice with `docker build --no-cache` and comparing `docker inspect --format='{{.Id}}'`
output: the two builds produced different image IDs, with different layer digests on every layer
that touches `src/`, `requirements-action.txt`, or either `pip install` step (only the digest-pinned
base-image layers matched). `ci.yml`'s `docker` job never claimed otherwise: it diffs **package and
OS inventories** (`pip list --format=freeze`, `dpkg -l`) between its own two clean builds, not image
digests — that check establishes "the same packages, at the same versions, every time," not
"byte-identical image layers." See `SECURITY.md`'s "Verifying release artifacts" for what *is*
verifiable today (the attestation as an audit record, plus SBOM-component comparison) and the exact
commands.

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

If the release tag itself fails `git verify-tag` (Task 10's fail-closed gate, right before the
floating-tag move and PyPI publish), the pipeline already stops on its own before anything ships —
see `SECURITY.md`'s "Release Signing" section for how to remove that unverified release/tag before
it reaches PyPI, and for signing-key rotation and revocation procedures.

## Known Platform Constraint: `secrets.GITHUB_TOKEN` Cannot Run This Action

Confirmed against a real workflow run in a real repository: the GitHub Actions auto-generated
`secrets.GITHUB_TOKEN` has no permission scope covering repository administration (branch
protection or rulesets), under any `permissions:` block configuration. Every consumer of
`shipsolid/repo-policy@v0` must supply a real PAT via a custom repository secret and pass it as
`env: GITHUB_TOKEN: ${{ secrets.<YOUR_SECRET_NAME> }}` on the step — see the README's GitHub
Action example. This is a GitHub platform limitation, not something this pipeline or the Action's
`action.yml` can work around.
