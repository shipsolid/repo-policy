# Usage Guide

A step-by-step walkthrough of `repo-policy`, from "I've never used this" to "this runs in my CI
pipeline every day." Written so a fresher can follow it top to bottom with no prior context, and
structured so a senior engineer can jump straight to the section they need from the table of
contents below.

This guide teaches you **how to use** `repo-policy`. It intentionally does not repeat the full CLI
flag/exit-code table or the full `policy.yml` schema table — those already live in
[README.md](README.md) as the canonical, always-current reference. Where this guide and the README
would say the same thing, this guide links to the README instead of copying it, so the two can never
drift out of sync.

## Table of contents

- [Usage Guide](#usage-guide)
  - [Table of contents](#table-of-contents)
  - [1. What repo-policy does](#1-what-repo-policy-does)
  - [2. Core concepts, in plain language](#2-core-concepts-in-plain-language)
  - [3. Prerequisites](#3-prerequisites)
  - [Step 1 — Install](#step-1--install)
  - [Step 2 — Write your first policy.yml](#step-2--write-your-first-policyyml)
  - [Step 3 — Validate it offline](#step-3--validate-it-offline)
  - [Step 4 — Get a GitHub token](#step-4--get-a-github-token)
  - [Step 5 — Audit a real repository (read-only)](#step-5--audit-a-real-repository-read-only)
  - [Step 6 — Preview changes with plan](#step-6--preview-changes-with-plan)
  - [Step 7 — Apply the policy](#step-7--apply-the-policy)
  - [11. What apply actually does, in order](#11-what-apply-actually-does-in-order)
  - [12. Choosing an enforcement mode: branch\_protection vs. ruleset](#12-choosing-an-enforcement-mode-branch_protection-vs-ruleset)
  - [13. Managed-scope vs. strict: how much control do you want](#13-managed-scope-vs-strict-how-much-control-do-you-want)
  - [14. Repo-level settings](#14-repo-level-settings)
  - [15. Running repo-policy inside GitHub Actions](#15-running-repo-policy-inside-github-actions)
  - [16. Cookbook: common tasks](#16-cookbook-common-tasks)
  - [17. Exit codes at a glance](#17-exit-codes-at-a-glance)
  - [18. When something goes wrong](#18-when-something-goes-wrong)
  - [19. Where to go next](#19-where-to-go-next)

---

## 1. What repo-policy does

`repo-policy` reads a YAML file that declares how you want one or more GitHub branches protected
(required reviews, required status checks, signed commits, force-push/deletion rules, and so on),
compares that declaration against what GitHub actually has configured right now, and tells you — or
makes it — match.

It is **not** a Terraform replacement. There is no state file and no backend. That's a deliberate
design choice (see
[`docs/adrs/0003-managed-scope-default-strict-opt-in.md`](docs/adrs/0003-managed-scope-default-strict-opt-in.md)):
you can point it at a repository that's already been configured by hand for years, and by default it
will only ever touch the branches and fields you explicitly declare — never reset something you
didn't mention.

> **New to "branch protection"?** It's a GitHub feature that stops people (including admins, if you
> want) from pushing directly to an important branch like `main` — instead, changes have to go
> through a reviewed pull request, pass your CI checks, and so on. `repo-policy` is a way to write
> those rules down in a file, instead of clicking through GitHub's settings UI by hand for every
> repository your team owns.

## 2. Core concepts, in plain language

You need these five ideas before anything else in this guide will make sense. Read this section
once; everything below builds on it.

| Concept          | What it means here                                                                                                                                                                 |
| ---------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **`policy.yml`** | The YAML file you write, declaring the rules you want. Yours can be named anything — `policy.yml` is just the default filename `repo-policy` looks for.                            |
| **Drift**        | Any difference between what your `policy.yml` declares and what GitHub actually has configured right now.                                                                          |
| **audit**        | Read-only. Checks for drift and reports it. Never changes anything on GitHub. Safe to run any time, by anyone, as often as you like.                                               |
| **plan**         | Same check as `audit`, but prints a human-readable preview of exactly what would change, field by field. Still read-only.                                                          |
| **apply**        | Actually makes the changes `plan` would show, then re-checks GitHub from scratch afterward to confirm the change really took effect (not just that the API call returned success). |

The tool is intentionally a **progression**: `validate` → `audit` → `plan` → `apply`. Each step is
strictly safer than the next one, and this guide walks them in that exact order — by the time you
run `apply`, you will already have seen, twice, exactly what it is about to do.

## 3. Prerequisites

Before you start, you need:

- **Python 3.10 or newer.** Check with `python3 --version`.
- **A terminal.** Every command in this guide is a shell command; there is no GUI.
- **A GitHub repository you're allowed to administer** — or read-only access, if you're only going
  to run `audit`. You don't need admin rights for `validate`, `audit`, or `plan` on a repo you can
  merely read; you need admin-level token permissions only for `apply` (Step 7 explains why).
- **Fifteen minutes**, for your first full pass through Steps 1–7.

> **If you're new to the command line:** every code block below is meant to be copy-pasted directly
> into your terminal, one block at a time, in order. Where a command produces output, this guide
> shows you exactly what to expect, so you can tell whether it worked.

## Step 1 — Install

```bash
pip install repo-policy
```

Confirm it installed correctly:

```bash
repo-policy --version
```

```text
repo-policy, version 0.4.10
```

(Your version number may be newer — that's fine. This command makes no network calls; if it prints a
version, the install worked.)

> **Tip for senior engineers setting this up for a team:** you almost never install `repo-policy`
> this way in CI — see [Step 15](#15-running-repo-policy-inside-github-actions) for the GitHub
> Action, which needs no `pip install` step at all. Local install is for authoring and testing
> `policy.yml` on your own machine first.

## Step 2 — Write your first policy.yml

Rather than write one from scratch, start from the ready-to-use baseline this repository ships at
[`examples/default.policy.yml`](examples/default.policy.yml) — industry-standard defaults for
`main`, safe to apply to most repositories as-is. The rest of this guide uses it as the running
example, so your output will match what's shown below.

If you're setting this up in your own project, fetch it directly:

```bash
curl -o policy.yml https://raw.githubusercontent.com/shipsolid/repo-policy/main/examples/default.policy.yml
```

If you already have a local checkout of `repo-policy` itself (e.g. you're following along inside
this repository):

```bash
cp examples/default.policy.yml policy.yml
```

Open `policy.yml` — stripped of its comments, this is what you now have (the real file has one
above every field explaining what it does and why that's the default; worth reading once):

```yaml
version: 1

branches:
  main:
    enforcement: branch_protection

    pull_requests:
      required: true
      approvals: 1
      code_owner_review: false
      dismiss_stale_reviews: true
      require_last_push_approval: true

    status_checks:
      required: [] # <- you need to fill this in; see below

    signed_commits: false
    linear_history: false
    allow_force_push: false
    allow_deletion: false
    required_conversation_resolution: true
    lock_branch: false
    allow_fork_syncing: false
    clear_restrictions: true
    enforce_admins: false

repo_settings:
  delete_branch_on_merge: true
  allow_update_branch: true
  vulnerability_alerts: true
  automated_security_fixes: true
  private_vulnerability_reporting: true
  secret_scanning: true
  secret_scanning_push_protection: true
```

Two things need your attention before this is ready for a real repository:

1. **The branch name.** Rename the `main:` key if your repository's default branch is called
   something else (`master`, `trunk`, …). This guide assumes `main`.
2. **`status_checks.required`.** It ships empty on purpose — a placeholder check name that doesn't
   match your real CI would block every PR forever, waiting on a status that will never be
   reported. Open a recent pull request on your target repo, look at the checks listed at the
   bottom, and copy the name shown there verbatim — not your workflow's `name:` field, and often
   not what you'd guess from the workflow YAML. For this walkthrough, assume your CI reports two
   checks named `build` and `test`:

   ```yaml
   status_checks:
     required: [build, test]
   ```

   Make that one edit to your `policy.yml` now — every command's output for the rest of this guide
   assumes it.

Walking through the fields that most shape what you'll see over the next few steps (the full
field-by-field reference is
[README.md's "Schema & Validation" section](README.md#schema--validation);
`repo_settings` is covered separately in [§14](#14-repo-level-settings)):

| Field | In plain language |
| --- | --- |
| `pull_requests.required: true` + `approvals: 1` | Changes to `main` must go through a pull request with at least 1 approving review. |
| `status_checks.required: [build, test]` | The `build` and `test` CI checks must pass before merging — the exact check-run names, not workflow names. |
| `allow_force_push: false` / `allow_deletion: false` | Nobody can force-push over history on `main`, or delete it. |
| `required_conversation_resolution: true` | Every PR review conversation must be marked resolved before merging. |
| `enforce_admins: false` | Repo admins can still bypass these rules if they need to — a deliberately cautious starting point; see the comment in the file for when to flip this on. |

This is a genuinely opinionated **baseline**, not the full schema — several fields (`signed_commits`,
`linear_history`, `lock_branch`, `allow_fork_syncing`) are left at their off/permissive default here.
Want to see every field the schema supports, each one explained in depth?
[`examples/sample-repository-policy.yml`](examples/sample-repository-policy.yml) is the exhaustive
reference version of this same idea.

## Step 3 — Validate it offline

Before talking to GitHub at all, check that the file itself is well-formed:

```bash
repo-policy validate
```

```text
policy.yml is valid.
```

This makes **zero network calls** — it only parses and schema-checks the YAML. Try breaking it on
purpose, to see what a validation error looks like. Change `approvals: 1` to `approvals: 12`:

```bash
repo-policy validate
```

```text
invalid policy config in policy.yml:
  - branches.main.pull_requests.approvals: Input should be less than or equal to 6
```

Put it back to `1` before continuing. `validate` exits `0` when the file is valid and `2` when it
isn't — see [Exit codes](#17-exit-codes-at-a-glance) for why `2`, not `1`.

`repo-policy` is intentionally strict here rather than forgiving:

- An **unknown field** (a typo like `aprovals:` or `strcit:`) is rejected, not silently ignored — so
  a typo can never quietly turn into a rule you thought you'd declared but didn't.
- A **quoted boolean** (`"true"` instead of `true`) or a numeric string in place of a real integer
  is rejected, not silently coerced.
- **Duplicate YAML keys** (two `strict:` entries, two `main:` branch blocks) are rejected outright,
  instead of YAML's usual silent "last one wins" behavior.

If `validate` ever surprises you, the error message names the exact field and what's wrong with it —
that's intentional, so you never have to guess.

## Step 4 — Get a GitHub token

Every command from here on talks to GitHub's API, which means it needs a token. `repo-policy` looks
for one in this order (first one found wins):

1. `--token` flag, passed directly on the command line
2. `GITHUB_TOKEN` environment variable
3. `GH_TOKEN` environment variable
4. `gh auth token` — a **silent, last-resort fallback**: if you're already logged in locally via
   the [GitHub CLI](https://cli.github.com/) (`gh auth login`), `repo-policy` reuses that session
   automatically. It's only ever tried when none of the first three are set, so it never overrides
   an explicit `--token`/env var, and it changes nothing about how the GitHub Action authenticates
   (see [Step 15](#15-running-repo-policy-inside-github-actions) — Actions always need an explicit
   token regardless).

For **this guide's Steps 5 and 6** (`audit` and `plan` — both read-only), a token with read access
to the repository is enough. For **Step 7** (`apply`), the token needs write access to
administration settings:

- **Classic PAT:** the `repo` scope.
- **Fine-grained PAT:** `Administration: Read and write` on the target repository (or
  `Administration: Read` if you only ever intend to run `audit`/`plan` with it, never `apply`).

Create one at <https://github.com/settings/tokens>, then export it in your shell:

```bash
export GITHUB_TOKEN=ghp_your_token_here
```

> **Important — this is the single most common setup mistake, and it's a GitHub platform limitation,
> not something `repo-policy` can work around:** if you're running inside a GitHub Actions workflow,
> `secrets.GITHUB_TOKEN` (the token GitHub automatically generates for every workflow run) **will
> never work for this tool**, no matter what `permissions:` block you add to your workflow. That
> auto-generated token simply has no permission scope that covers branch protection or ruleset
> administration — confirmed against a real repository, not a guess. You must create your own PAT as
> described above and store it as a **repository secret** with a different name (this guide and the
> README both use `REPO_POLICY_TOKEN` as the example name). See
> [Step 15](#15-running-repo-policy-inside-github-actions).

## Step 5 — Audit a real repository (read-only)

Pick a repository you have read access to — ideally a test/sandbox repo the first time through, not
something production-critical. Run:

```bash
repo-policy audit --repo your-org/your-repo
```

Two things can happen.

**If the repo already matches your policy:**

```text
your-org/your-repo is compliant.
```

**If it doesn't** (far more likely the first time — most repositories weren't already configured to
match a brand-new `policy.yml`):

```text
main: 5 change(s) required
repo settings: 7 change(s) required
```

(Shown here for a repository with no prior branch protection and none of the 7 `repo_settings`
toggles touched yet — a common starting point for a first-time `repo-policy` adoption. Your own
counts will differ based on what's already configured on your repository and organization; if some
of these were already on, they'll show up as `✓` instead once you get to `plan` in the next step.)

Either way, **nothing on GitHub changed.** `audit` only ever reads. This is deliberately the safest
possible way to try `repo-policy` for the first time against a real repository — run it as many
times as you like.

Notice you didn't pass `--config` or `--token` explicitly above:

- `--config` defaults to `policy.yml` in your current directory.
- `--token` was picked up from the `GITHUB_TOKEN` environment variable you exported in Step 4.
- `--repo` can _also_ be omitted if you run this from inside a git checkout of the target repo with
  a GitHub `origin` remote — `repo-policy` will resolve it from `git remote get-url origin`
  automatically. This guide passes `--repo` explicitly throughout so every command is self-contained
  and copy-pasteable regardless of your working directory.

## Step 6 — Preview changes with plan

`audit` told you _that_ 5 branch changes (and 7 repo-setting changes) are needed. `plan` shows you
exactly _what_ they are, before anything happens:

```bash
repo-policy plan --repo your-org/your-repo
```

```text
Repository: your-org/your-repo
Branch: main

✓ Signed commits
✓ Linear history
✓ Admin enforcement
✓ Branch lock
✓ Fork syncing
✓ Push restrictions
+ Pull request requirements    required=False approvals=0 code_owner_review=False dismiss_stale_reviews=False require_last_push_approval=False → required=True approvals=1 code_owner_review=False dismiss_stale_reviews=True require_last_push_approval=True
+ Required status checks       None → required=['build', 'test']
+ Force pushes                 True → False
+ Branch deletion              True → False
+ Conversation resolution      False → True

5 changes required.
```

(This is real output, captured from running `repo-policy plan` against `examples/default.policy.yml`
with `status_checks.required` filled in as in Step 2, diffed against a repository with no existing
branch protection at all.)

Reading this output:

| Symbol | Meaning                                                        |
| ------ | -------------------------------------------------------------- |
| `✓`    | Already matches your policy — this field will not change.      |
| `+`    | Currently unset/empty on GitHub; your policy adds a rule here. |
| `~`    | Currently set to something else; your policy changes it.       |
| `-`    | Currently set; your policy removes this restriction.           |

Every line shows `current_value → desired_value`, so you can see precisely what GitHub has today and
precisely what it will have after `apply`. This is still entirely read-only — `plan` uses the exact
same read-and-diff engine `audit` does, it just renders the full field-by-field picture instead of a
one-line count.

Notice `Pull request requirements` shows five `field=value` pairs on each side of the arrow, not a
single value — `pull_requests` is one compound field in the schema (`required`, `approvals`,
`code_owner_review`, `dismiss_stale_reviews`, `require_last_push_approval` all live under it), so a
change to any of its sub-fields bundles into one line here, never five separate ones. The same real
`plan` output also includes a second, `repo settings:`-labeled section for the `repo_settings` block
— covered in [§14](#14-repo-level-settings).

**Stop and actually read this output before moving to Step 7.** This is the whole point of the
`validate` → `audit` → `plan` → `apply` progression: by now you have seen, in detail, exactly what
`apply` is about to do. If anything here surprises you — a field you didn't expect to see, a value
that doesn't look right — go back and fix `policy.yml`, then re-run `plan`, before applying
anything.

## Step 7 — Apply the policy

Once `plan`'s output looks correct:

```bash
repo-policy apply --repo your-org/your-repo
```

```text
main: applied 5 changes
repo settings: applied 7 change(s)
```

That's it — GitHub now matches your `policy.yml`. Run `repo-policy audit --repo your-org/your-repo`
again to confirm:

```text
your-org/your-repo is compliant.
```

If you run `apply` a second time with nothing changed, it reports no-op, not an error:

```text
main: no changes needed
```

`apply` is **safe to re-run**. It never re-applies a change that's already in effect, and it always
re-verifies live GitHub state from scratch after mutating anything — see the next section for
exactly what that means and why it matters.

## 11. What apply actually does, in order

This section matters if you want to trust `apply` in an automated pipeline, not just run it by hand.
`apply` is not "send the API calls and hope." It runs three distinct phases every time:

1. **Preflight** — fetches whatever current state it needs (existing rulesets, whether stale classic
   branch protection exists on a ruleset-enforced branch) before changing anything.
2. **Mutate** — sends only the changes `plan` would have shown, one resource (branch, or repo
   settings) at a time, recording each outcome as it happens.
3. **Verify** — after every mutation call has returned successfully, `apply` throws away that
   optimism and **re-reads live GitHub state from scratch**, using the exact same read-and-diff
   engine `audit`/`plan` use, before declaring success.

That third phase exists because a `2xx` response from GitHub's API is not, by itself, proof that a
setting actually took effect — GitHub can silently ignore or reset certain field combinations, or
lag briefly before a just-updated ruleset becomes fully effective. If that fresh re-check still
finds drift, `apply` tells you so explicitly and exits with the drift exit code, instead of quietly
reporting success on a mutation that didn't really stick:

```text
apply completed but policy is not converged
```

If a mutation fails partway through (a rate limit, a transient GitHub 5xx, a permissions gap), you
get a line-by-line journal of exactly what completed before the failure — `applied N changes`,
`no changes needed`, or `failed -- N change(s) not applied`, one line per resource — so you know
precisely what state you're in and don't have to guess what already happened before re-running.

For the full breakdown of every apply outcome and which exit code each one maps to, see
[ARCHITECTURE.md's "Apply Outcomes and Exit Codes"](ARCHITECTURE.md).

## 12. Choosing an enforcement mode: branch_protection vs. ruleset

Every branch you declare picks one of two GitHub mechanisms, via `enforcement:` (defaults to
`branch_protection` if you don't set it):

```yaml
branches:
  main:
    enforcement: branch_protection   # or: ruleset
```

|                                              | `branch_protection` (default)                                                  | `ruleset`                                                                          |
| -------------------------------------------- | ------------------------------------------------------------------------------ | ---------------------------------------------------------------------------------- |
| GitHub feature                               | Classic branch protection                                                      | GitHub Rulesets                                                                    |
| Supports every field in the schema           | Yes                                                                            | No — see below                                                                     |
| Ownership tracking                           | None needed (one object per branch, unambiguous)                               | By name: repo-policy creates/manages rulesets named exactly `repo-policy:<branch>` |
| `strict: true` can delete an orphaned object | No — branch protection has no ownership marker to prove repo-policy created it | Yes — only ever deletes a ruleset matching the `repo-policy:` prefix               |

**Fields with no GitHub Rulesets equivalent at all** — declaring any of these under
`enforcement: ruleset` is a validation error, not a silent no-op: `enforce_admins`,
`required_conversation_resolution`, `lock_branch`, `allow_fork_syncing`, `clear_restrictions`.

**Rule of thumb:**

- Need `enforce_admins`, `required_conversation_resolution`, or the other ruleset-unsupported fields
  above? You must use `branch_protection`.
- Otherwise, `ruleset` is generally the more actively-hardened choice: it's what `strict` mode can
  safely clean up after itself (see
  [§13](#13-managed-scope-vs-strict-how-much-control-do-you-want)), and its ownership is provable by
  name instead of implicit.

See
[`docs/adrs/0004-ruleset-ownership-via-naming-convention.md`](docs/adrs/0004-ruleset-ownership-via-naming-convention.md)
for the full reasoning, and this repository's own
[`.github/repository-policy.yml`](.github/repository-policy.yml) for a real, in-production example
of a policy that's forced to `branch_protection` specifically because it needs
`enforce_admins: true` and `required_conversation_resolution: true`.

## 13. Managed-scope vs. strict: how much control do you want

This is the single most important safety concept in the whole tool, and it's the reason
`repo-policy` is safe to point at a repository someone already configured by hand.

**Default behavior (`strict: false`, the default) — "managed-scope":**

- A branch **absent** from `policy.yml` is never touched, at all.
- Within a branch you **did** declare, only the fields you actually wrote are enforced. Everything
  else on that branch is read from GitHub's current state and left exactly as-is.

**Opt-in behavior (`strict: true`) — full enforcement:**

- Every field on a declared branch that you _didn't_ set is treated as "must be off/empty," the same
  way Terraform-style full desired-state tools behave.
- On `ruleset`-enforced branches only, any `repo-policy:*`-named ruleset whose branch is no longer
  declared in `policy.yml` gets deleted on the next `apply`.

Set it at the top level (applies to every branch) or per-branch (a branch's own `strict` always wins
over the top-level default):

```yaml
version: 1
strict: false          # top-level default

branches:
  main:
    strict: true        # overrides the default just for this branch
    pull_requests:
      required: true
      approvals: 2
```

**Why the default is managed-scope, not strict:** a policy tool that reconciles live infrastructure
has to answer "what happens to a setting nobody declared?" The first `apply` against an already-live
repository should never be a surprise that resets or deletes something a teammate configured by hand
years ago. Full desired-state enforcement is available — it's just something you turn on
deliberately, once you actually want full drift correction, not something that can catch you off
guard on your very first run. The full design reasoning (including the alternatives that were
rejected and why) is in
[`docs/adrs/0003-managed-scope-default-strict-opt-in.md`](docs/adrs/0003-managed-scope-default-strict-opt-in.md).

> **For teams rolling this out gradually:** start every new repository with `strict: false` (or just
> omit it), declare a handful of fields you actually care about, run `audit`/`plan`/`apply` to
> confirm it behaves the way you expect, and only flip to `strict: true` once you're confident your
> `policy.yml` is a complete, intentional description of that branch — not a partial one.

## 14. Repo-level settings

Separate from per-branch rules, `repo_settings:` declares repository-wide toggles — these are
checked and applied the same way branches are (`audit`/`plan`/`apply` all cover them), just at the
repo level instead of per-branch. This is the block your `policy.yml` already has, copied from
[`examples/default.policy.yml`](examples/default.policy.yml) back in [Step 2](#step-2--write-your-first-policyyml):

```yaml
repo_settings:
  delete_branch_on_merge: true
  allow_update_branch: true
  vulnerability_alerts: true
  automated_security_fixes: true
  private_vulnerability_reporting: true
  secret_scanning: true
  secret_scanning_push_protection: true
```

Two fields depend on another being enabled first — `repo-policy` catches this at `validate` time,
before any API call, rather than letting GitHub reject the request partway through an `apply`:

- `automated_security_fixes: true` requires `vulnerability_alerts: true` also set.
- `secret_scanning_push_protection: true` requires `secret_scanning: true` also set.

Some fields can come back as **unavailable** rather than compliant/drifted — this happens when your
token's permission scope genuinely can't see that field on the target repository (a fine-grained PAT
with `Administration: Read-only` is one confirmed case for
`delete_branch_on_merge`/`allow_update_branch`). `repo-policy` reports this explicitly rather than
guessing:

```text
repo settings: delete_branch_on_merge unavailable on this repository
```

If you see this, either drop the field from `repo_settings` or use a token whose scope returns it —
see [`docs/troubleshooting.md`](docs/troubleshooting.md) for a one-line command to check exactly
what your current token can see.

## 15. Running repo-policy inside GitHub Actions

This is how most teams actually run `repo-policy` day to day — not from a laptop, but as a scheduled
or on-PR check. Three steps.

**Step A — Create the token.** As covered in [Step 4](#step-4--get-a-github-token),
`secrets.GITHUB_TOKEN` cannot do this. Create a PAT (classic `repo` scope, or fine-grained
`Administration: Read and write` for `apply` / `Administration: Read` if the workflow only ever runs
`audit` or `plan`), then add it as a **repository secret**: Settings → Secrets and variables →
Actions → New repository secret. This guide uses `REPO_POLICY_TOKEN` as the example secret name —
pick whatever name you like, it just has to match what your workflow references.

**Step B — Find the exact commit SHA to pin.** Never reference a release tag directly in `uses:` —
pin to the immutable commit it points to instead (the same supply-chain-hardening convention this
repository's own CI uses for every third-party Action it consumes):

```bash
git rev-parse v0.5.0^{commit}
```

(Replace `v0.5.0` with the release you want. The `^{commit}` suffix matters — release tags here are
annotated tag objects, so `git rev-parse v0.5.0` alone would return the tag object's own SHA, not
the commit's, silently defeating the point of pinning.)

**Step C — Add the workflow step:**

```yaml
- uses: shipsolid/repo-policy@5dcf57b3b2eb12e5b878f70b7119c26d86788c23 # v0.5.0
  env:
    GITHUB_TOKEN: ${{ secrets.REPO_POLICY_TOKEN }}
  with:
    config: .github/repository-policy.yml
    mode: audit
```

The Action takes two inputs, both optional:

| Input    | Default                         | Notes                                                |
| -------- | ------------------------------- | ---------------------------------------------------- |
| `config` | `.github/repository-policy.yml` | Path to your policy file, relative to the repo root. |
| `mode`   | `audit`                         | One of `validate`, `audit`, `plan`, `apply`.         |

**Start with `mode: audit`, on a schedule, before you ever wire up `mode: apply`.** A read-only
audit workflow is the safest way to introduce `repo-policy` to a team — it reports drift without
ever being able to change anything, so there's no blast radius to reason about while people get
comfortable with what it reports. This repository does exactly that for its own branch protection —
see [`.github/workflows/policy-audit.yml`](.github/workflows/policy-audit.yml) and the
"Self-governance" section of [README.md](README.md#self-governance) for a real, working example: a
daily-scheduled, read-only `audit` run, using a token scoped to `Administration: Read` only —
deliberately not write access, since this particular workflow never needs to mutate anything.

Only move a repository to `mode: apply` once you've watched its `audit` (or `plan`) output for a
while and are confident the policy is exactly what you want enforced automatically. Because `apply`
changes live repository configuration, treat turning it on the same way you'd treat any other change
to CI/CD pipeline permissions — reviewed, not casual.

For the convenience `@v0` floating-tag alternative (and why it's unsuitable anywhere change control
requires a pinned dependency), see the [README's GitHub Action section](README.md#github-action).

## 16. Cookbook: common tasks

Quick-reference recipes for things you'll want to do once you're past the first walkthrough.

**"I just want to know if we're compliant — I don't want anything to change."**

```bash
repo-policy audit --repo your-org/your-repo
```

Safe to run as often as you like, by anyone with read access. Exit code `0` means compliant, `1`
means drift was found (see [§17](#17-exit-codes-at-a-glance)) — useful for a CI job that should fail
loudly on drift without ever being allowed to fix it automatically.

**"I want to see exactly what would change, field by field, before deciding."**

```bash
repo-policy plan --repo your-org/your-repo
```

**"I'm ready to enforce this policy for real."**

```bash
repo-policy apply --repo your-org/your-repo
```

**"I want full drift correction — reset anything not explicitly declared."**

Add `strict: true` at the top level of `policy.yml` (see
[§13](#13-managed-scope-vs-strict-how-much-control-do-you-want) first — this is a deliberate,
not-casual choice).

**"I manage dozens of repositories and want one policy file reused across all of them."**

Point `--config` at the same file and loop `--repo` over each target — `repo-policy` has no concept
of "multiple repos in one file" by design; one invocation always targets exactly one repository:

```bash
for repo in org/repo-a org/repo-b org/repo-c; do
  repo-policy apply --config shared-policy.yml --repo "$repo"
done
```

**"I want to check my policy file is valid without touching any real repository, e.g. in a
pre-commit hook."**

```bash
repo-policy validate --config .github/repository-policy.yml
```

Zero network calls — safe and fast enough to run on every commit.

**"My branch is protected with a GitHub Ruleset, not classic branch protection."**

Set `enforcement: ruleset` on that branch — see
[§12](#12-choosing-an-enforcement-mode-branch_protection-vs-ruleset).

**"CI is red because a required status check name doesn't match what my workflow actually
reports."**

This is the single most common first-time mistake. `status_checks.required` needs the check-run name
**exactly** as GitHub displays it on a pull request — not your workflow's `name:` field, and often
not what you'd guess from the workflow's YAML. Open a recent PR on the target repo, look at the
checks listed at the bottom, and copy the name shown there verbatim. This repository's own policy
file has a documented real-world example of getting this wrong the first time — see the
`status_checks` comment in [`.github/repository-policy.yml`](.github/repository-policy.yml).

## 17. Exit codes at a glance

Every command uses the same four exit codes, so a CI pipeline can branch on them reliably
(`validate` only ever uses `0`/`2` — it never talks to GitHub, so drift and API-error codes don't
apply to it):

| Code | Meaning                                                                                                                            |
| ---- | ---------------------------------------------------------------------------------------------------------------------------------- |
| `0`  | Success — valid config, compliant repo, or a no-op apply.                                                                          |
| `1`  | Drift found (`audit`/`plan`), or `apply`'s mutations succeeded but the fresh post-apply re-check still finds drift.                |
| `2`  | Invalid `policy.yml`, or a setup failure before any GitHub API call (missing token, unresolvable `--repo`, a broken proxy config). |
| `3`  | A GitHub API/auth/transport error, or a mutation failed partway through `apply`.                                                   |

This is deliberately a shared, stable contract across `validate`/`audit`/`plan`/`apply` — a CI step
like `repo-policy audit --repo org/repo || exit 1` (fail the build on drift, but never on a token
problem being mistaken for drift) relies on `2` and `1` never colliding. The full per-command
breakdown, plus exactly which condition produces which code during `apply`'s three-phase run, is in
[README.md's CLI Reference table](README.md#cli-reference) and
[ARCHITECTURE.md's "Apply Outcomes and Exit Codes"](ARCHITECTURE.md).

## 18. When something goes wrong

Don't re-derive a fix from first principles — check
[`docs/troubleshooting.md`](docs/troubleshooting.md) first. It's a maintained list of the exact
error messages this tool produces (token errors, repo-resolution failures, schema validation errors,
partial-apply failures, proxy/SOCKS setup issues, non-convergence after `apply`, and more), each
with the specific cause and fix — not generic advice.

If you hit something not covered there, or a bug you can reproduce, open an issue — see
[SUPPORT.md](SUPPORT.md) for where.

## 19. Where to go next

This guide got you from zero to a working `apply` in CI. For everything past that:

| You want to...                                                                             | Read                                                 |
| ------------------------------------------------------------------------------------------ | ---------------------------------------------------- |
| See the full CLI flag and schema field reference                                           | [README.md](README.md)                               |
| Understand the internal data flow, safety model, and known v1 limitations                  | [ARCHITECTURE.md](ARCHITECTURE.md)                   |
| Understand _why_ a specific design decision was made                                       | [`docs/adrs/`](docs/adrs/)                           |
| Understand the threat model, token permissions in depth, and emergency-recovery procedures | [SECURITY.md](SECURITY.md)                           |
| Diagnose a specific error message                                                          | [`docs/troubleshooting.md`](docs/troubleshooting.md) |
| See how this tool's own CI/CD pipeline and release process work                            | [`docs/ci-cd.md`](docs/ci-cd.md)                     |
| See planned future work                                                                    | [ROADMAP.md](ROADMAP.md)                             |
| Contribute code                                                                            | [CONTRIBUTING.md](CONTRIBUTING.md)                   |
| Get help or report a bug                                                                   | [SUPPORT.md](SUPPORT.md)                             |
| Check a quick answered question                                                            | [FAQ.md](FAQ.md)                                     |
