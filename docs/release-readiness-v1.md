---
title: "Release Readiness: v1 Audit-Remediation Plan"
---

**Verification date:** 2026-09-21
**Verified by:** Task 14 of the audit-remediation plan (`repo-policy-audit-remediation-plan.md`)
**Decision: GO.** A real release (`v0.4.9`) has already shipped through the fully hardened
pipeline as of this writing — this document records the evidence for that decision, not a
forward-looking recommendation.

## Executive summary

Every gate this plan's Definition of Done requires has durable, independently-verified evidence,
gathered against the real `shipsolid/repo-policy` repository and the real, published `v0.4.9`
artifacts — not against mocks, and not taken on trust from any single tool's exit code. Three real,
previously-unknown bugs were found and fixed during the live verification itself (not in code
review beforehand): a supply-chain version-pinning gap that computed a wrong release version twice,
and a stale-git-state bug that blocked every release from actually completing. All three are
documented in full below, since the fact that live verification caught them — rather than a real
consumer catching them after the fact — is itself part of this plan's evidence that the process
works.

`v0.4.8`'s commit and signed tag exist permanently in this repository's history but were never
published anywhere (no PyPI release, no GitHub Release) — a deliberate, documented choice, not an
oversight. `v0.4.9` is the actual first complete release.

## Evidence index

| Artifact | Reference |
| --- | --- |
| GitHub Release | [v0.4.9](https://github.com/shipsolid/repo-policy/releases/tag/v0.4.9) |
| PyPI package | [repo-policy 0.4.9](https://pypi.org/project/repo-policy/0.4.9/) |
| Release commit | `ba69faa05ab3a2128ec52739b096d83a42bb7504` — `chore(release): v0.4.9 [skip ci]` |
| Signed release tag | `v0.4.9` (tag object `57bbb9c8ca6e771d13e3f42cf5b57cb5085e7238`, SSH-signed by `shipsolid-release-bot`) |
| Floating major tag | `v0` — nests on the same tag object as `v0.4.9`, confirmed live |
| Successful release workflow run | [run 35613700398](https://github.com/shipsolid/repo-policy/actions/runs/35613700398) — `check-release-warranted`, `ci`, `release`, `sbom`, `publish`, `release-assets` all succeeded |
| Docker Action image digest | `sha256:0760b5396d1d3c3ae523d9d7d172ba8ff8fdee0294bef21e2f87e8c281f2254c` |
| Steps 1-5 raw evidence | `.superpowers/sdd/repo-policy-audit-remediation-plan/task-14-steps-1-5-report.md` |

---

## Step 1-5: Pre-release verification

Full command-by-command evidence lives in `task-14-steps-1-5-report.md` (599 lines, every command's
real, unpiped exit code, cross-checked against this project's actual CI-gate invocations wherever a
bare tool invocation looked worse than the enforced gate). Summary:

| Step | Verdict | Headline evidence |
| --- | --- | --- |
| 1. Local quality suite | **PASS** | `ruff format --check` / `ruff check` / `mypy` / `pytest --cov` (384 passed, 96.65% branch coverage ≥ 95% floor) / `python -m build` all exit 0. `pip-audit`: no known vulnerabilities. `zizmor --pedantic --offline --min-severity high`: no findings — the enforced gate is clean; the bare, un-floored `zizmor --pedantic --offline .` and `bandit -q -r src` both exit nonzero for fully triaged, pre-existing, 0-high-severity advisory findings already documented in `SECURITY.md`/`docs/ci-cd.md`, not new regressions. |
| 2. Built distributions | **PASS** | Wheel and sdist each installed into independent, dev-extras-free, non-editable venvs; `--version` and package metadata agree; both validate the known-good fixture policy. |
| 3. Docker Action by immutable reference | **PASS** | `validate`/`audit`/`plan`/`apply` all run from an image built fresh from `HEAD`'s `Dockerfile`, against the live disposable fixture repo; `apply` performed and converged a real mutation. |
| 4. Full live lifecycle | **PASS** | Live `pytest -m e2e -v`, 3/3 passing, every requirement (branch protection, active rulesets, branch applicability, no bypass actors, repo settings, strict pruning, idempotency, cleanup) traced to a specific independent assertion; fixture confirmed back at clean baseline via raw API reads afterward. |
| 5. Failure paths | **PASS** | 6 of 7 required paths covered by 16 named, freshly-run, pristine-passing unit tests; the 7th (release-gating after failed CI) confirmed structurally by citing the exact `needs:`/`if:` lines in `release.yml`, not demonstrated by deliberately breaking real CI. |

No genuine bugs found in Steps 1-5. Two minor documentation-fidelity notes (not defects) are
recorded in the full report and carried into "Documented limitations" below.

---

## Step 6: Repository self-governance

Verified live, directly against the real repository (read-only `gh api` calls):

- `main` is protected and matches `.github/repository-policy.yml` exactly: `enforce_admins: true`,
  `required_conversation_resolution: true`, `required_linear_history: true`,
  `allow_force_pushes: false`, `allow_deletions: false`, PR required (`approvals: 0`, matching the
  policy's documented single-maintainer rationale).
- The `required` status check is correctly mandatory (confirmed both `contexts` and `checks` GET
  fields report `["required"]`), matching the live-verified fix from Task 9's own rollout.
- Direct unverified changes to `main` are structurally blocked (enforce_admins + required PR review
  + required status check together — not destructively tested against the real repo).

**Two real, live gaps were found and fixed during this step, both operational/configuration, not
code:**

1. **`POLICY_AUDIT_TOKEN` had never been created.** The scheduled self-audit workflow
   (`policy-audit.yml`) had been failing on every run since Task 9, because the repository secret
   its `GITHUB_TOKEN` env references was flagged as "needs creating later" and then never actually
   created. Fixed by creating a fine-grained PAT (Administration: Read-only) and adding it as a
   repository secret.
2. **The `release` GitHub Environment had no required-reviewer protection rule at all.** Before this
   was caught, a properly-typed commit landing on `main` would have run the entire release pipeline
   — including the real PyPI publish — with no human approval checkpoint, despite the design
   throughout `SECURITY.md`/`docs/ci-cd.md` assuming one existed. Fixed by adding a required-reviewer
   rule naming the repository owner.

Both were caught specifically *because* this task tested the pipeline for real rather than only
reading the workflow files — the environment's `protection_rules` were empty and would not have
been visible without directly querying it.

**A third, narrower issue found live in this same step:** the self-audit, once `POLICY_AUDIT_TOKEN`
existed, reported a false "2 changes required" on `secret_scanning`/`secret_scanning_push_protection`
— genuinely already-compliant settings, confirmed via a separate, broader-scoped token and a raw API
read. Root cause: `diff_security_and_analysis()` couldn't distinguish "the token can't see this
field" from "the field is genuinely disabled." This became the fix that triggered the first real
release attempt — see Step 9.

---

## Step 7: Release trust

All verified independently against the real, live `v0.4.9` release — not read from the workflow's
own claims:

- **Tag signature:** `git verify-tag v0.4.9`, using the release-bot's real public key fetched live
  from the `RELEASE_BOT_SSH_PUBLIC_KEY` repository variable — `Good "git" signature for
  amitsingh007s+repopolicybot@gmail.com`. `git verify-tag v0` (the floating major tag) also verifies
  transitively; confirmed it shares the identical tag object as `v0.4.9` (the same object, referenced
  by two tag names, not a separate tag pointing at it).
- **PyPI trusted-publishing provenance:** confirmed via PyPI's own native provenance endpoint
  (`pypi.org/integrity/.../provenance`) — a real attestation with predicate type
  `https://docs.pypi.org/attestations/publish/v1`, a valid Sigstore signature, and a real Rekor
  transparency-log inclusion proof.
- **SBOM attachments:** both `sbom-wheel.cdx.json` and `sbom-docker.cdx.json` downloaded directly
  from the real GitHub release and confirmed present.
- **Artifact attestations:** `gh attestation verify` succeeded independently for the real downloaded
  wheel, sdist, and both SBOM files.
- **Container digest:** the real digest (`sha256:0760b539...`) was extracted from the `sbom` job's
  own logs; a real attestation for that exact digest was confirmed live via
  `gh api repos/.../attestations/sha256:...` (this Action is never pushed to a registry, so
  `oci://`-form verification doesn't apply — digest-keyed GitHub attestation does, by design).

**A verification-methodology note worth keeping:** an initial check of `v0`'s signature failed with
a false "no signature found," because `git fetch --tags` (without a forcing `+` refspec) does not
overwrite an already-existing, diverged *local* tag ref. The real remote state (confirmed via
`gh api .../git/refs/tags/v0`) was correct the whole time; the local worktree's cached `v0` ref was
stale from earlier in this same session. Force-refetching (`git fetch origin
'+refs/tags/*:refs/tags/*'`) resolved it. Not a pipeline bug — a reminder for anyone doing manual
tag verification in a long-lived local clone.

---

## Step 8: This document

(This file. See "Documented limitations" below for what remains open.)

---

## Step 9: Creating the release — the real, live release history

This is the step the rest of this plan exists to earn, and it did not go cleanly on the first
attempt — nor the second. All three failures were caught by the live verification this task
performs, fixed, independently re-reviewed, and re-verified before being trusted again. The full
narrative:

### Attempt 1 → wrong version (`1.0.0` instead of `0.4.8`)

The triggering commit was itself a real bug fix, found during Step 6: `fix: don't report secret
scanning as drifted when token can't see security_and_analysis` (commit `21a156e`). Once merged,
`check-release-warranted` correctly fired, and the release job ran — but computed `1.0.0` instead of
the expected `0.4.8` for what its own log labeled a "patch"-type bump.

**Root cause:** the version-bump step ran `python-semantic-release` via a GitHub Action pinned by
full commit SHA (per this project's own Action-pinning rule). That SHA pins the Action's *code*, not
the version of the `python-semantic-release` PyPI package its Dockerfile installs — which resolves
fresh against PyPI on every build, not baked into a fixed layer. The Action's container installed
`10.6.2`, not the `9.21.2` this project is designed and tested against, and `10.6.2`'s algorithm
computed the version incorrectly.

The wrong `1.0.0` landed on `main` as a real commit and a real signed tag before the pipeline failed
at an unrelated later step (`Create the GitHub release`). **Nothing was ever published externally**
— no PyPI release, no GitHub Release — confirmed directly both times this happened.

**Fix:** all three `python-semantic-release` invocations in `release.yml` now pin `==9.21.2`
explicitly via `pip install`, matching the two call sites that were already correct.
Independent review (opus, empirical scratch-clone verification) also caught that dropping the old
Action's `git_committer_name`/`git_committer_email` inputs without a replacement was itself a new
bug — PSR reads committer identity from its own `commit_author` setting, which overrides host git
config regardless — fixed by setting `GIT_COMMIT_AUTHOR` explicitly on the step.

### Attempt 2 → wrong version again (same root cause, new trigger)

The first cleanup attempt reverted the version files in a standalone `chore:` commit, pushed
*separately* from the pipeline fix. That push re-triggered `release.yml` — any push to `main` does
— and `check-release-warranted` correctly fired again: deleting the erroneous tags made `v0.4.7`
"the last release" again from PSR's perspective, and the real `fix:` commit from Attempt 1 was still
sitting, genuinely unreleased, in history since then. PSR has no notion of "already attempted and
failed" separate from git tags. Since the pipeline fix hadn't landed yet at that push, the same
broken Action ran again and produced a second wrong `1.0.0`.

**Lesson, now documented in `docs/ci-cd.md`:** a revert-only push does not stop
`check-release-warranted` from firing again while the underlying commit remains genuinely
unreleased — only landing a correct release, or fixing the pipeline *before* the next push, does.
The actual fix was bundled into the same commit as this second revert for exactly this reason.

### Attempt 3 → correct version, incomplete release (`v0.4.8`)

With the pinning fix live, this attempt correctly computed and published a real, signed
`chore(release): v0.4.8` commit and tag. This is genuine progress — the first time this pipeline
ever computed a correct version. But the release job still failed at the same step as both prior
attempts: `Create the GitHub release`.

**Root cause (a third, separate, previously-unreachable bug):** the release job checks out the repo
once, at the very start, on the pre-merge commit. The version-bump step commits locally on that same
branch, but GitHub's squash-merge creates a **sibling** commit on the real `main`, not a
fast-forward of it — and nothing before this point ever reconciled the two. `semantic-release
changelog --post-to-release-tag` (what actually creates the GitHub Release) needs local `main` to
have reached the real merged commit: it builds its release history by walking `git log` backward
from HEAD, and separately refuses to run at all on a detached HEAD. With local `main` never
advanced, the walk never found the tag. This exact failure occurred identically in **all three**
attempts, including the very first one — it has nothing to do with the version-pinning bug and
predates it; it simply never surfaced before because no earlier attempt had gotten this far.

Confirmed by direct reproduction in a scratch clone: the exact failing command, run with local
`main` left at its stale state, reproduces the error exactly; the same command, after resetting
local `main` to the real merged commit, gets cleanly past that check.

**Fix:** a new step, `git checkout main && git reset --hard "$MERGED_SHA"`, right before the
changelog/release-creation step.

`v0.4.8`'s commit and tag remain, permanently, real parts of this repository's history — signed,
correct, and honest about what happened — but intentionally never completed. Completing it out of
band (bypassing the pipeline's own release-notes generation) was considered and rejected as a
violation of this plan's own "never bypass a required control" constraint.

### Attempt 4 → success

With all three bugs fixed and each fix independently reviewed (opus, two rounds on the version-pinning
fix, one round on the stale-HEAD fix — every claim in every review was empirically re-verified against
scratch clones and the real repository, not taken on the report's word), the fix commit itself
(`fix: advance local main to the merged commit before creating the GitHub release`) triggered its own
release. Every job in the graph succeeded: `check-release-warranted`, `ci`, `release`, `sbom`,
`publish`, `release-assets`. `v0.4.9` is real, live, and published — see the evidence index above.

**Why this narrative matters as evidence, not just history:** every one of these three bugs was
caught by *actually running the pipeline for real*, not by code review beforehand — two of them
(the version-pinning gap, the stale-HEAD bug) were structurally invisible to static review, since
neither depended on anything the diff itself would show; both only manifest against real GitHub
infrastructure, a real PR merge, and a real squash-commit shape. This is direct evidence for why
Task 14 exists as a live-verification step and not a documentation exercise.

---

## Step 10: Re-audit from the published artifacts

Verified independently, against the real, externally-published `v0.4.9` — not this worktree's
source:

- **Installed from PyPI**, clean venv, no local source: `pip install repo-policy==0.4.9`.
  `repo-policy --version` and `importlib.metadata.version("repo-policy")` both agree at `0.4.9`;
  `repo-policy validate` against the known-good fixture succeeds.
- **Ran the Docker Action by immutable commit SHA:** built fresh from the exact `v0.4.9` commit
  (`ba69faa05ab3a2128ec52739b096d83a42bb7504`). Version matches; `validate` works both via direct
  CLI invocation and via the real Action entrypoint contract (`INPUT_MODE`/`INPUT_CONFIG` env
  vars); confirmed running as non-root.
- **No new high-severity finding:** `pip-audit` and `zizmor --pedantic --offline --min-severity
  high` both re-run at the exact `v0.4.9` state — both clean.

Behavior matches the tested release candidate in every respect checked.

---

## Documented limitations

Carried forward, deliberately not fixed as part of this task (out of scope, non-blocking, or
requiring a follow-up decision rather than a mechanical fix):

- **`v0.4.8` exists but was never published.** Its commit and signed tag are real and permanent;
  no PyPI release or GitHub Release was ever created for it, by design (see Step 9). A reader of
  `git tag --list` or `git log` will see it; this document is the explanation.
- **`release.yml:33`'s `uses: $/.github/workflows/ci.yml` syntax is rejected by `actionlint`** (a
  third-party tool) as an unrecognized format. This was researched extensively earlier in this
  plan (zizmor's own compiled binary strings, zizmor's official docs, a cited GitHub Blog post) and
  concluded to be real, correct, newer GitHub syntax that `actionlint` simply hasn't caught up to.
  Now additionally confirmed empirically: this exact line has been exercised by four real,
  live GitHub Actions runs in this task alone (including the fully successful `v0.4.9` run), proving
  the workflow parses and runs correctly regardless of `actionlint`'s objection.
- **A handful of stale `"CI / required"` prose references remain in `release.yml`'s own comments**
  (lines outside the files Task 13's documentation pass touched) — cosmetic, non-functional, no
  machine reads these strings.
- **`python-semantic-release`'s transitive dependencies are now hash-pinned.** Previously only the
  top-level `python-semantic-release==9.21.2` version was pinned at all three call sites; a live
  audit (`AUDIT-GAPS.md`, FINDING-004/TASK-003) found this left ~29 unpinned, unscanned transitive
  dependencies reachable from the `release` job's credentials, including a then-current known
  vulnerability (`PYSEC-2026-2132` in `click==8.1.8`). Closed the same way this project already
  hash-pins its distributed Action's dependencies (`requirements-action.txt` +
  `scripts/verify-action-lock.sh`): `requirements-release.in`/`.txt` +
  `scripts/verify-release-lock.sh`, a `verify-release-lock` CI job (`ci.yml`), and a dedicated
  `pip-audit -r requirements-release.txt --require-hashes` step (`security.yml`). The one remaining
  finding in that lock (`PYSEC-2026-2132`) is upstream-blocked — python-semantic-release 9.21.2's
  own `click~=8.1.0` constraint caps click below the fix — and is explicitly triaged, not silently
  ignored; see `SECURITY.md`'s "Known Limitations".
- **Several Minor findings from Tasks 11-13's reviews remain parked** (test-coverage gaps in the
  E2E suite's ruleset-side PR-parameter assertions, `e2e.yml`'s missing `permissions:` block, a
  couple of stale doc cross-references) — none functional, all previously triaged and explicitly
  deferred to a whole-branch review, tracked in
  `.superpowers/sdd/repo-policy-audit-remediation-plan/progress.md`.

## Definition-of-done cross-check

Every item in the plan's Definition of Done has durable evidence somewhere in this document or the
task reports it references — correctness gates (Tasks 1-5, verified live in Steps 3-5 above),
release/supply-chain gates (Tasks 6-10, verified live in Steps 7 and 9), and test/compatibility/docs
gates (Tasks 11-13, verified in Steps 1 and 4-5). The one item worth calling out explicitly:
**"the release pipeline completes without bypassing a required control"** — literally demonstrated
by Step 9's narrative: three real failures were fixed and re-verified rather than worked around, and
the fourth attempt succeeded through the same, unmodified set of required gates (CI, environment
approval, signature verification, all four release-stage jobs) that were designed from the start.
