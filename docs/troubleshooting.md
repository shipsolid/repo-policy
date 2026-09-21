# Troubleshooting

## `Error: no GitHub token found; pass --token or set GITHUB_TOKEN/GH_TOKEN`

No token was found in `--token`, `GITHUB_TOKEN`, or `GH_TOKEN`. Set one of those. See
[SECURITY.md](../SECURITY.md) for the required token permissions.

## `GitHub API error 403 ... Resource not accessible by integration` (inside a GitHub Action)

You're using the automatically-generated `secrets.GITHUB_TOKEN`. This will never work, regardless
of the `permissions:` block on your workflow — `GITHUB_TOKEN` has no permission scope covering
repository administration. Create a PAT (classic `repo` scope, or fine-grained
`Administration: Read and write`), store it as a repository secret, and reference that secret
instead. See the README's GitHub Action example.

## `GitHub API error 401 ... Bad credentials`

The token is present but invalid or expired. Regenerate it.

## `invalid repository 'xyz'; expected 'owner/name'`

`--repo` (or a resolved `GITHUB_REPOSITORY`) didn't contain a `/`. Pass the full `owner/name` form.

## `could not determine repository; pass --repo owner/name`

None of `--repo`, `$GITHUB_REPOSITORY`, or a `git remote get-url origin` in the current directory
resolved to a repository. This also fires if `git` isn't installed and no `--repo`/
`$GITHUB_REPOSITORY` was supplied.

Recognised `origin` shapes: `git@github.com:owner/name`, `https://github.com/owner/name`,
`ssh://git@github.com/owner/name`, and SSH host aliases like `git@github.com-work:owner/name`,
each with or without `.git`. Anything else (a GitHub Enterprise host, a non-GitHub remote) needs
`--repo`.

## `policy file not found: <path>` / `policy file is empty: <path>`

Check `--config`'s path is correct relative to your current working directory (CLI) or the
Action's `config:` input is relative to the repo root (Action).

## `invalid policy config in <path>: ...`

`validate`'s or `audit`'s Pydantic error output — each line names the exact field and what's wrong
with it (e.g. `branches.main.enforcement: unsupported value`).

## `apply` reports a change every single run, even though nothing should be different

This was the strict-mode phantom-drift bug (fixed — see `docs/test-strategy.md`) on versions
before it shipped. Upgrade. If it recurs on a current version, it's a new bug: file an issue with
the exact `plan` output and your `policy.yml`.

## A ruleset got deleted that I didn't expect

`strict: true` prunes any `repo-policy:*`-named ruleset whose branch is no longer declared in
`policy.yml` (see `../ARCHITECTURE.md`'s Apply Safety Model). Check whether the branch was
recently removed from the config, and whether `strict` is set at the top level (it applies as a
default to every branch unless overridden). This never touches a ruleset that isn't named
`repo-policy:*`.

## `apply completed but policy is not converged` (exit code 1)

Every mutation `apply` attempted succeeded (or none were needed), but the independent, fresh
re-check it runs immediately afterward (`cli.verify_after_apply`) still finds drift, or a declared
`repo_settings` field still comes back `unavailable`. This is not a partial failure — nothing
raised — it means GitHub's own state doesn't match what the mutation calls' 2xx responses implied
it would. Common causes: a declared field GitHub silently ignores or resets under a specific
combination (see `docs/test-strategy.md`'s "What mocking alone could not catch" for real examples
of this), eventual-consistency lag on GitHub's effective-rules view for a just-updated ruleset (a
second `apply` often resolves this on its own), or an org-level policy overriding what repo-policy
just set. Re-run `repo-policy plan` against the same config/repo to see exactly which field(s) are
still reported as drift, and `apply` again — if it doesn't converge after two attempts, treat it as
a real bug and file an issue with both `plan` outputs attached.

## `repo settings: delete_branch_on_merge unavailable on this repository` (exit code 1)

The token can't see the field. `GET /repos/{owner}/{repo}` omits `delete_branch_on_merge` and
`allow_update_branch` entirely for some token scopes (a fine-grained PAT with
`Administration: Read-only` is one confirmed case), and repo-policy refuses to guess. Either drop
the field from `repo_settings`, or use a token that returns it. Confirm what your token sees with:

```bash
curl -s -H "Authorization: Bearer $GITHUB_TOKEN" https://api.github.com/repos/<owner>/<repo> \
  | python3 -c "import sys, json; d = json.load(sys.stdin); print({k: d.get(k, '<ABSENT>') for k in ('delete_branch_on_merge', 'allow_update_branch')})"
```

## `apply` exits 3 partway through, but the output shows some branches already applied

This is `PartialApplyError`: one branch (or repo-setting) mutation failed partway through the same
`apply` run's mutation phase — zero or more earlier resources in that phase may already have been
mutated successfully before it. The printed journal lines (`<resource>: applied N
change(s)` / `<resource>: no changes needed` / `<resource>: failed -- N change(s) not applied`) are
the ground truth for what happened before the failure — nothing before the failed line was rolled
back, since repo-policy has no transaction concept across resources (each branch/repo-setting
mutation is its own independent API call). Read the accompanying error message for the underlying
`GitHubAPIError` (rate limiting, a transient 5xx, a permissions gap partway through a token's scope)
and re-run `apply` once it's addressed — an already-applied resource simply reports zero further
changes needed on the next run (see ARCHITECTURE.md's "Apply Outcomes and Exit Codes").

## `could not initialize GitHub client: ...` (exit code 2)

`repo-policy` sits behind an `httpx.Client`, which honors the standard proxy environment
variables: `HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY`, and `NO_PROXY` (comma-separated hostnames to
bypass the proxy for). `HTTP_PROXY`/`HTTPS_PROXY`/`ALL_PROXY` may use `http://`, `https://`,
`socks5://`, or `socks5h://` schemes — SOCKS support ships by default (via the `httpx[socks]`
extra), so no separate install step is needed.

If GitHub client construction itself fails (not a request — the client never got far enough to
make one), this is always a setup problem, not policy drift: it exits `2`
(`EXIT_CONFIG_ERROR`), the same code as a missing token or malformed `--repo`, never `1`
(`EXIT_DRIFT`) and never an uncaught traceback. Check the accompanying message — it names what's
likely wrong (e.g. an unreachable or malformed proxy URL). If you're running against a mirrored
or vendored install that dropped the `httpx[socks]` extra, reinstall `repo-policy` to restore it.

## Docker image fails to build with `Readme file does not exist: README.md`

If you're building the `Dockerfile` yourself: it must be built with the repo root as build
context, and `README.md` must be copied in alongside `pyproject.toml` — hatchling's build
validates the `readme` field declared in `pyproject.toml` at install time.
