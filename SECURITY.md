# Security Policy

## Reporting a Vulnerability

Please report security issues privately via GitHub's
["Report a vulnerability"](https://github.com/shipsolid/repo-policy/security/advisories/new)
form rather than opening a public issue. We'll acknowledge within 5 business days.

## Scope

`repo-policy` requires a GitHub token with `repo` (or fine-grained `administration:write`)
permissions to manage branch protection and rulesets. Treat that token with the same care as any
credential capable of changing repository security settings. The tool never stores the token —
it is read once per invocation from `--token`, `GITHUB_TOKEN`, or `GH_TOKEN`.

**In GitHub Actions, this must be a real PAT stored as a repository secret — never the
automatically-generated `secrets.GITHUB_TOKEN`.** Confirmed against a real workflow run:
`GITHUB_TOKEN`'s permission scopes don't include repository administration under any
`permissions:` configuration, so it always fails with `403 Resource not accessible by
integration` on branch protection/ruleset endpoints, regardless of what the workflow grants it.

## Threat Model

| Actor | Attack Vector | Impact | Mitigation |
|---|---|---|---|
| Attacker with repo write access but not admin | Modify `policy.yml` to weaken branch protection (e.g. drop required approvals), merge it, wait for `apply` to run in CI | Branch protection silently weakened | Require review on changes to `policy.yml` itself via branch protection on the repo that runs repo-policy — repo-policy cannot protect its own config file from a compromised reviewer |
| Attacker who obtains the CI-stored PAT | Full read/write on whatever the PAT's scope covers — not limited to what `policy.yml` declares | Arbitrary branch protection/ruleset changes, or broader if the PAT has full `repo` scope | Scope the PAT as narrowly as GitHub allows (fine-grained PAT, `Administration` permission only, single-repository access); rotate it; never log it (repo-policy never prints the token) |
| Malicious `policy.yml` in a fork's PR, run via `pull_request_target` | Attacker-controlled config gets a privileged token via workflow misconfiguration | Same blast radius as PAT compromise above | Never run repo-policy's `apply` mode on `pull_request_target` against untrusted input; `audit`/`plan` read-only are lower risk but still exercise real API calls with the token |
| Naming collision: something else creates a ruleset named `repo-policy:<branch>` | `strict` mode's prune logic would treat it as repo-policy-owned and could delete it | Loss of an unrelated ruleset | The naming convention is a documented hard constraint (see `docs/adrs/0004-*`) — don't create rulesets with that prefix outside repo-policy |
| Attacker who obtains `RELEASE_BOT_TOKEN` or `RELEASE_BOT_SIGNING_KEY` (Task 10) | Open/merge an arbitrary release PR as the release-bot, or forge a signature that verifies as the bot's identity | A malicious, signature-"verified" release published under the bot's name | Both live only as secrets scoped to the protected `release` GitHub Environment (required-reviewer approval, not a plain repository secret) — see "Release Signing" below; `RELEASE_BOT_TOKEN` is a **classic** PAT (not fine-grained — see "Secrets Management" below for why) scoped to `public_repo` only, the narrowest classic scope GitHub offers, issued from the `shipsolid-release-bot` account rather than the human owner's; `public_repo` is coarser than a fine-grained PAT's separately-toggled permissions would have been, but still never full `repo` scope, and the bot's own collaborator access is Write, not Admin, so it can't touch branch protection/ruleset settings even with the token in hand |
| A consumer pins their workflow to the floating `@v0` Action tag | A compromised or buggy release becomes live in every consuming workflow the moment it's published, with no corresponding diff in the consumer's own repository to review or hold back | Unreviewed supply-chain exposure on every release, for any consumer who chose the moving tag | Documented as a deliberate trade-off, not hidden: README's GitHub Action section leads with the immutable full-commit-SHA form and calls out `@v0` explicitly as movable and unsuitable wherever change control requires a pinned dependency — the same full-SHA-pinning convention this repository's own workflows follow for every third-party Action *they* consume (every `uses:` in `.github/workflows/*.yml` is pinned to a full commit SHA, not a tag; see `docs/ci-cd.md`). Consumers who need the convenience of automatic updates accept this exposure knowingly, as a choice, not a documentation gap |

## Authentication

repo-policy authenticates to the GitHub REST API with a single bearer token, resolved in order
from `--token`, `GITHUB_TOKEN`, then `GH_TOKEN` (`cli._resolve_token`). There is no OAuth flow, no
session, and no credential caching — the token lives only in the process's memory for the
duration of one invocation.

## Network Path: Proxy Support

`repo-policy`'s `httpx.Client` (`github_client.py`) is constructed with `httpx`'s own defaults —
`trust_env` is not overridden — so it honors the standard `HTTP_PROXY`/`HTTPS_PROXY`/`ALL_PROXY`/
`NO_PROXY` environment variables exactly like any other well-behaved `httpx`/`requests`-based tool;
`socks5://`/`socks5h://` proxy URLs also work, since `httpx[socks]` ships as a hard dependency (see
`docs/troubleshooting.md` for the corresponding config-error behavior when SOCKS support is
missing from a mirrored install). This means every request — including the `Authorization: Bearer
<token>` header — is routed through whatever proxy your environment configures, the same as any
other HTTPS client running in that environment: an ordinary forward proxy using `CONNECT` tunneling
never sees inside the TLS session (the token stays opaque to it), but an organization that
terminates/inspects TLS at its proxy (a corporate MITM proxy with an injected root CA) can observe
everything a normal HTTPS request carries, including this token. This is standard behavior for any
HTTPS client, not something specific to how `repo-policy` handles the token — but it's worth stating
explicitly here since it changes where the token is actually exposed in a given network topology,
which is relevant to the token-scoping guidance above.

## Authorization

repo-policy performs no authorization of its own; it relies entirely on GitHub's own permission
model for the token it's given. Whatever the token can do, repo-policy can do — it does not
restrict itself to a subset. This is why token scoping (above) is the primary control.

## Secrets Management

- The token is never written to disk, logged, or included in any error message.
- `policy.yml` is not an appropriate place to store secrets and repo-policy never expects one
  there — it contains only declarative policy, no credentials.
- In CI, store the PAT as an encrypted repository (or organization) secret; never as a plaintext
  workflow env default or a committed file.
- A second, narrower-scoped PAT (`REPO_POLICY_E2E_TOKEN`) exists for the automated E2E suite
  (`tests/e2e/`, `.github/workflows/e2e.yml`): fine-grained, `Administration: Read and write`,
  restricted to the single disposable fixture repo (`shipsolid/repo-policy-e2e-fixture`). Its
  blast radius is bounded to that one repo — a concrete instance of the "scope the PAT as
  narrowly as GitHub allows" mitigation already listed in the Threat Model below, not a new
  category of risk.
- A third PAT, `POLICY_AUDIT_TOKEN`, is expected by `.github/workflows/policy-audit.yml`: this
  repository's own self-audit (see README's "Self-governance"). Narrower still — fine-grained,
  `Administration: Read` only (not `Read and write`), restricted to this single repository
  (`shipsolid/repo-policy`) — because that workflow only ever runs `audit`, never `apply`, so it
  has no legitimate need for write access at all. This secret does not exist yet; creating it is a
  live-repo setup step for whoever holds admin access on `shipsolid/repo-policy`.
- A fourth PAT, `RELEASE_BOT_TOKEN` (Task 10), belongs to the dedicated `shipsolid-release-bot`
  identity and is what `.github/workflows/release.yml`'s `release` job uses to push its version-bump
  branch, open and squash-merge the release pull request, push the signed release tag, and create
  the GitHub release — see "Release Signing" below for the full design. **Classic** PAT, not
  fine-grained: fine-grained PATs can only be issued by an account that is the repository's owner or
  an org member with access, and `shipsolid-release-bot` is a plain outside collaborator on
  `shipsolid/repo-policy` — a personal-account-owned repository, with no org membership concept to
  grant it through — so it structurally cannot create a fine-grained PAT scoped to this repo at all,
  confirmed against GitHub's own fine-grained-PAT documentation. Scoped to `public_repo` only (this
  repository is currently public) — the narrowest classic scope GitHub offers; classic scopes are
  coarser than fine-grained's separately-toggled repository permissions, so this is broader than the
  originally-specified `Contents: Read and write` + `Pull requests: Read and write` would have been,
  but still far short of full `repo` scope, and the bot's own collaborator access remains Write, not
  Admin, so branch protection/ruleset settings stay out of reach regardless of what the token itself
  can technically call. `release.yml` retries a plain `gh pr merge` on an interval rather than
  polling check-run status directly — this is a deliberate design choice (a classic PAT like this one
  CAN call the Checks API, so it isn't a workaround for a permission gap), which happens to also
  sidestep a real, separate constraint should a future migration back to fine-grained ever become
  possible: fine-grained PATs currently cannot call the Checks API at all (confirmed against GitHub's
  own fine-grained-PAT permissions reference — there is no selectable "Checks" repository
  permission). Relying instead on GitHub's own server-side mergeability evaluation (which checks
  `required` using the repository's branch-protection state, not the caller's token scope) is
  simply a simpler dependency — see `docs/ci-cd.md` for the full design reasoning. Stored as a secret
  on the protected `release` GitHub Environment, not as a repository secret, so it's only
  materialized on the runner after a human approves that environment's required-reviewer gate.
  Provisioned (issued from the `shipsolid-release-bot` account, stored as a `release`-environment
  secret); see the setup checklist below for what else still needs confirming before the first live
  release.
- `RELEASE_BOT_SIGNING_KEY` (Task 10) — the release-bot's SSH private signing key, also stored as a
  `release`-environment secret (same approval gate as `RELEASE_BOT_TOKEN` above), used by
  `release.yml` to produce a signed commit and a signed, annotated release tag. The corresponding
  public key is `RELEASE_BOT_SSH_PUBLIC_KEY`, a plain (non-secret) repository **variable** — public
  keys don't need encryption, and keeping it as a variable rather than a secret makes it visible in
  the Actions UI for anyone auditing what key the pipeline currently trusts. Both are now
  provisioned, and the public key is registered on the bot's GitHub account as a Signing Key; see the
  setup checklist below for what else still needs confirming.

## Vulnerability Management

### Scan cadence

| Check | Tool | Runs | Scope |
|---|---|---|---|
| Dependency vulnerabilities | `pip-audit` | Every PR, every push to `main`, weekly (`.github/workflows/security.yml`) | This project's own dependencies (`pyproject.toml`) and the Docker Action's locked, hash-pinned dependency set (`requirements-action.txt`) |
| Static code analysis | CodeQL (`python`, `actions`) | Every PR, every push to `main`, weekly | `src/`, `tests/`, and `.github/workflows/*.yml` |
| Workflow YAML security | `zizmor --pedantic` | Every PR, every push to `main` (`ci.yml`'s `security` job, PR-blocking), weekly again (`security.yml`, advisory) | `.github/workflows/*.yml` |
| Container image vulnerabilities | Trivy (Task 7) | Every PR, every push to `main` (`ci.yml`'s `docker` job) | The Docker Action image, `CRITICAL` blocking / `CRITICAL,HIGH` reported |
| Dependency update proposals | Dependabot (`.github/dependabot.yml`) | Weekly, grouped per ecosystem (`pip`, `github-actions`, `docker`), with a 7-day cooldown before a newly-published version is proposed | Every dependency this project or its Docker image declares |
| Repository-policy compliance | `repo-policy audit` | Daily, plus push to `main` touching the policy file or itself, plus manual dispatch (`.github/workflows/policy-audit.yml`, read-only) | This repository's own live branch protection / security settings vs. `.github/repository-policy.yml` (Task 9 dogfooding, see README's "Self-governance") |

`security.yml`'s pip-audit/zizmor/CodeQL jobs are advisory, not PR-blocking -- they are not part of
`ci.yml`'s `required` check. `ci.yml`'s own narrower `security` job (zizmor over workflow YAML
only, `--min-severity high`) remains the one PR-blocking security gate, unchanged by this. See
`docs/ci-cd.md` for the full pipeline architecture and the reasoning behind that split.

### Severity response times

These are target response times for a finding surfaced by any of the scans above, not a
contractual SLA:

| Severity | Response |
|---|---|
| Critical / High | Triaged within 5 business days; fixed or explicitly risk-accepted (with reasoning recorded here or in the relevant PR) before the next release |
| Medium | Triaged within 2 weeks; fixed opportunistically, typically bundled with the next `fix:`/`chore:` commit touching the same area |
| Low / Informational | Reviewed on a best-effort basis; commonly tolerated as documented, known findings (see `docs/ci-cd.md`'s zizmor discussion) rather than fixed individually |

### Dependency-update policy

- Dependency upper bounds are pinned in `pyproject.toml` to bound the blast radius of an
  unreviewed transitive upgrade; Dependabot's weekly, grouped PRs are the mechanism that actually
  proposes moving those pins forward, rather than upper bounds alone going stale indefinitely.
- The 7-day cooldown (`dependabot.yml`'s `cooldown.default-days`) means a newly-published package
  version isn't proposed the same day it lands on PyPI/the Actions marketplace/Docker Hub -- a
  deliberate window against a compromised-release supply-chain attack landing in a merged PR
  before it's been caught and yanked upstream.
- A Dependabot PR against `requirements-action.txt` specifically may fail CI's
  `verify-action-lock` check even when the underlying version bump is legitimate, because that
  file is a `uv --generate-hashes` lockfile, not one Dependabot's pip-compile-aware update path
  recognizes (see `dependabot.yml`'s own comment). Regenerate it by hand with the command
  `verify-action-lock.sh`'s failure output prints; don't merge Dependabot's version of that file
  directly.

### Verifying release artifacts

Starting with the first release cut after this SBOM/attestation tooling merged (check
`CHANGELOG.md` for the earliest entry mentioning SBOMs, or look for a release whose assets include
`sbom-wheel.cdx.json`/`sbom-docker.cdx.json` — as of this writing that tooling has not yet shipped
in a release; the current in-development version is `0.4.7`), every release publishes, alongside
the PyPI package: a CycloneDX SBOM for the wheel's install environment and one for the Docker
Action image (both attached to the GitHub release), plus GitHub artifact attestations
(Sigstore-backed build provenance) for the wheel, sdist, both SBOMs, and the Docker Action image
(by digest). To verify a downloaded artifact's provenance:

```bash
# Verify the wheel/sdist/SBOM attestations (requires the GitHub CLI, `gh`, and repo read access)
gh attestation verify dist/repo_policy-<version>-py3-none-any.whl --owner shipsolid
gh attestation verify dist/repo_policy-<version>.tar.gz --owner shipsolid
gh attestation verify sbom-wheel.cdx.json --owner shipsolid
gh attestation verify sbom-docker.cdx.json --owner shipsolid
```

The SBOMs themselves (`sbom-wheel.cdx.json`, `sbom-docker.cdx.json`) are downloadable from each
GitHub release's assets and are valid CycloneDX 1.6 JSON -- validate structurally with any
CycloneDX-compliant tool, e.g. `cyclonedx-py`'s own `--validate` (on by default) or
[cyclonedx-cli](https://github.com/CycloneDX/cyclonedx-cli) `validate --input-file
sbom-wheel.cdx.json`.

**Known limitation -- the Docker Action image's attestation cannot currently be verified with a
single command, and its digest is not reproducible across rebuilds.** `gh attestation verify`
requires either a local file path or a registry-resolvable `oci://` reference to recompute the
subject's digest and compare it against the signed record (confirmed against `gh`'s own
documentation). This repository never pushes the Docker Action image to a registry -- `action.yml`
builds it fresh from the pinned `Dockerfile` at consumption time (see Task 7) -- so there is no
`oci://` reference to verify against.

It's also not enough to just rebuild locally and compare digests: **the image's digest is not
reproducible across independent builds**, confirmed by building this exact commit twice
(`docker build --no-cache`) and comparing `docker inspect --format='{{.Id}}'` output -- the two
builds produced different image IDs, with different `RootFS.Layers` digests on every layer that
touches `src/`, `requirements-action.txt`, or either `pip install` step. Only the base-image layers
(digest-pinned in the `Dockerfile`) matched. This is consistent with what Task 7's own CI check
(`ci.yml`'s `docker` job) actually verifies: it diffs **package and OS inventories** (`pip list
--format=freeze`, `dpkg -l`) between two clean builds, not image digests -- Task 7 established
"the same packages, at the same versions, every time," not "byte-identical image layers." Treating
the two as equivalent (an earlier draft of this document did) is wrong and would send anyone who
tried to verify a release's image digest straight into a false "tampering" conclusion.

What you *can* verify for a specific release's Docker image, without trusting anything blindly:

1. **The attestation is a real, auditable record.** It's visible under the repository's
   Attestations tab on GitHub, and fetchable directly by digest via `GET
   /repos/shipsolid/repo-policy/attestations/<digest>` -- it proves *some* GitHub Actions run in
   this repository, at this commit, produced an image with that exact digest, signed via Sigstore.
   Treat it as an audit trail, not as something you locally re-derive.
2. **The SBOM's component list is reproducible even though the image digest isn't.** Rebuild the
   image from the same release tag's `Dockerfile`, extract its package list (`docker run --rm
   --entrypoint pip <image> list --format=freeze`), and diff it against the release's
   `sbom-docker.cdx.json` components -- the *packages and versions* installed are pinned by
   `requirements-action.txt`'s hashes and are what should match, not the image digest.
3. Turning digest-level verification into a real one-command check would require either making the
   build byte-for-byte reproducible (e.g. `SOURCE_DATE_EPOCH` pinned to the commit timestamp plus
   auditing every remaining source of build-time nondeterminism, then proving it with repeated
   `--no-cache` builds) or publishing the image to a registry (`ghcr.io`/Docker Hub) so `gh
   attestation verify oci://...` has something to resolve against. Both are real scope beyond this
   SBOM/attestation-plumbing task and are open follow-up items, not done here.

## Release Signing

Task 10 added a dedicated release identity (`shipsolid-release-bot`) and SSH-based commit/tag
signing to `.github/workflows/release.yml`. See `docs/ci-cd.md`'s "Release-bot identity,
commit/tag signing, and the PR-merge redesign" for the full research trail and design reasoning
(why the release **tag**, not the commit that lands on `main`, is the signed and verified artifact —
a hard GitHub platform constraint, not a design shortcut). This section covers what to do when
something about that signing setup needs to change.

### What's signed, and how to check it yourself

Every release tag (`v{version}`) is an annotated tag, SSH-signed by the release-bot's key, pushed
directly (tags are outside branch protection's scope). `release.yml` verifies it itself with
`git verify-tag` immediately before the floating major tag moves, the GitHub release is created, or
anything is published to PyPI — a failure there stops the whole pipeline before any of that happens.
To verify it yourself, from a checkout with the bot's public key registered as a trusted signer:

```bash
# One-time, per machine: register the bot's public key as a trusted signer for its committer email
# (the same public key that should be registered on the bot's GitHub account as a Signing Key —
# see RELEASE_BOT_SSH_PUBLIC_KEY in "Secrets Management" above).
echo "amitsingh007s+repopolicybot@gmail.com <the bot's SSH public key>" >> ~/.ssh/allowed_signers
git config gpg.ssh.allowedSignersFile ~/.ssh/allowed_signers

git fetch --tags origin
git verify-tag v<version>
```

GitHub also shows its own "Verified" badge on the tag/release page once the bot's public key is
registered there as a Signing Key — that's the independent, third-party confirmation that the
signature really does belong to the `shipsolid-release-bot` account, not just that some SSH
signature validates locally. The commit the tag points at on `main` **will** also show as Verified —
GitHub signs commits made through its web interface/API (which is what a squash-merge is) with its
own key and shows them Verified (see `https://github.com/web-flow.gpg` and GitHub's own
commit-signature-verification docs); that's a *different* Verified signature from the tag's, though:
GitHub's own, attesting "GitHub performed this merge," not the release-bot's, attesting "the
release-bot produced this release." The "no verification" behavior documented in `docs/ci-cd.md` is
specific to *rebase*-merge, not squash — GitHub's docs state rebase-merge replays commits without
commit signature verification because GitHub never actually authors the resulting commit and so
can't sign it; squash-merge is not that case. Both signatures are real and independently meaningful,
which is a stronger story than "the commit shows nothing": the tag remains this pipeline's
release-bot-attributed verified artifact for the reason above (there is still no way to get the
release-bot's own signature onto the merge-synthesized commit object), not because the commit itself
goes unverified.

### Key rotation

Rotate the release-bot's SSH signing key on a routine schedule (annually, at minimum) or immediately
after any suspected exposure:

1. Generate a new SSH key pair (`ssh-keygen -t ed25519 -C "shipsolid-release-bot release signing"`,
   generated locally by whoever holds `release`-environment admin access — never inside a workflow
   run, and never sent anywhere the private half could be logged).
2. Add the new public key to the bot's GitHub account (Settings → SSH and GPG keys → New SSH key →
   key type **Signing Key**) **alongside** the old one, not replacing it yet — GitHub will verify a
   tag against whichever registered key actually signed it, so both can be valid simultaneously
   during the rotation window.
3. Update the `release`-environment secret `RELEASE_BOT_SIGNING_KEY` with the new private key, and
   the repository variable `RELEASE_BOT_SSH_PUBLIC_KEY` with the new public key.
4. Confirm the next release verifies correctly (`git verify-tag`, and the GitHub "Verified" badge)
   against the new key.
5. Remove the old public key from the bot's GitHub account. Past releases signed with it remain
   verifiable against GitHub's historical record of what was registered when they were signed —
   removing it going forward does not retroactively invalidate already-published releases.

### Key revocation (suspected compromise)

If the private signing key or `RELEASE_BOT_TOKEN` is suspected compromised, treat it as an incident,
not a routine rotation:

1. **Immediately** remove the signing key from the bot's GitHub account (Settings → SSH and GPG
   keys → delete) and revoke/regenerate `RELEASE_BOT_TOKEN` (GitHub → Developer settings → Personal
   access tokens → Tokens (classic) — this is a classic PAT, not a fine-grained one, see "Repository
   access and credentials" above — → regenerate or delete) from the bot's account. This stops the
   key from producing any further "Verified" releases and stops the PAT from opening/merging any
   further PRs, immediately.
2. Delete both from the `release` GitHub Environment's secrets so a queued or in-flight workflow run
   can't pick up the now-revoked credential.
3. Audit recent releases (`git log --show-signature` on release tags, or the GitHub UI's Verified
   badges) for anything signed with the compromised key that wasn't a legitimate release from this
   pipeline. Treat any unexplained signed tag as a confirmed compromise, not a false positive.
4. Follow the "Key rotation" steps above to provision a replacement identity before the next release
   is attempted — until then, `release.yml` will fail at the `git verify-tag` step (no valid key
   configured), which is the correct fail-closed behavior, not a bug to work around.
5. Record what happened, when the credential was live, and what (if anything) it was used for
   illegitimately — same audit-trail expectation as "Emergency Recovery" below.

### Release-environment recovery

If the `release` GitHub Environment itself is misconfigured, deleted, or needs to be rebuilt (e.g.
the required-reviewer list needs to change, or the environment was accidentally removed):

1. Re-create the environment (Settings → Environments → `release`) with a required-reviewer
   protection rule — see the setup checklist below for the exact configuration.
2. Re-add `RELEASE_BOT_TOKEN` and `RELEASE_BOT_SIGNING_KEY` as **environment** secrets (not
   repository secrets — a repository secret bypasses the approval gate entirely, which defeats the
   point).
3. Until the environment exists again, `release.yml`'s `release` job simply cannot start — GitHub
   blocks a job from running against a named `environment:` that doesn't exist on the repository.
   This is a safe failure mode (no release runs at all) rather than an unsafe one (a release running
   without the approval gate).

### Removing an unverified release before PyPI publication

`release.yml` is already structured so this should never be reachable in normal operation —
`git verify-tag` runs before the floating tag moves, before the GitHub release is created, and
before `publish`/`sbom`/`release-assets` run at all, so an unverified tag stops the pipeline before
anything ships. If it's ever necessary to undo a release that slipped through anyway (e.g. a manual
`git push` of an unsigned tag outside this pipeline, or a workflow bug found after the fact):

1. **Do not publish to PyPI** if that step hasn't run yet — PyPI never allows removing or reusing a
   version number once it's live, so preventing the publish is far cheaper than remediating after.
2. Delete the GitHub release (Releases page → the release → Delete) and the tag itself
   (`git push origin :refs/tags/v<version>` — deletes the remote tag; also delete it locally with
   `git tag -d v<version>`). Deleting a tag is not blocked by branch protection (tags are outside its
   scope, as established above) but is a destructive, unrecoverable action against the published
   ref — confirm with whoever else relies on this repository's tags before doing it against anything
   that might already be in use.
3. If the floating major tag (`v0`) was already moved to point at the bad release, move it back:
   `git tag -f v0 <last-good-tag> && git push origin v0 --force`.
4. If PyPI publication already happened before the problem was caught, this is no longer a "remove
   an unverified release" scenario — follow `docs/ci-cd.md`'s "Rollback" section instead (yank on
   PyPI, fix forward).

## Security Baseline

- Dependencies pinned with upper bounds (`pyproject.toml`).
- PyPI publishing uses trusted publishing (OIDC) — no long-lived PyPI API token stored anywhere.
- No secrets, tokens, or credentials are ever persisted by repo-policy itself.
- Automated dependency-vulnerability scanning (`pip-audit`), static analysis (CodeQL), and workflow
  security linting (`zizmor`) run on every PR, every push to `main`, and weekly
  (`.github/workflows/security.yml`); Dependabot proposes grouped, weekly dependency updates with a
  7-day cooldown (`.github/dependabot.yml`). See "Vulnerability Management" above.
- Every release publishes CycloneDX SBOMs (wheel + Docker Action image) and GitHub artifact
  attestations (Sigstore-backed build provenance) for the wheel, sdist, both SBOMs, and the Docker
  image. See "Verifying release artifacts" above.
- Every release tag is SSH-signed by a dedicated release-bot identity and verified
  (`git verify-tag`) before the floating major tag moves, the GitHub release is created, or
  anything is published to PyPI — publication fails closed if that verification doesn't pass. See
  "Release Signing" above. **Not yet live** — see "Release Pipeline Setup Checklist" below for what
  still needs to exist on GitHub before this protection is active.

## Repository Settings Declared, Pending First `apply`

The following GitHub repository-settings toggles (Settings → Code security) are now declared in
[`.github/repository-policy.yml`](.github/repository-policy.yml)'s `repo_settings` block
(`secret_scanning: true`, `secret_scanning_push_protection: true`) but not yet live on
`shipsolid/repo-policy` — they take effect the first time `repo-policy apply` runs against this
repository, which has not happened yet (see README's "Self-governance"). Recorded here as an open
action item, not silently skipped, until that first apply completes:

- **Secret scanning** — flags secrets matching known provider patterns that get committed to the
  repository.
- **Push protection** — enabled after secret scanning is on. Blocks a `git push` containing a
  detected secret before it ever lands in the repository's history, rather than only flagging it
  after the fact.

Both are available on public repositories at no cost, and on private repositories with GitHub
Advanced Security. `repo-policy` manages both through the REST API's `PATCH /repos/{owner}/{repo}`
`security_and_analysis` field (see `github_client.py`'s `update_security_and_analysis`) — the same
mechanism it uses for every other declared `repo_settings` toggle. Turning them on for
`shipsolid/repo-policy` itself now requires running `repo-policy apply` with sufficient credentials
(repository-admin / fine-grained `Administration: Read and write`), not a separate manual UI step.

## Release Pipeline Setup Checklist (Task 10)

`.github/workflows/release.yml` and `pyproject.toml` are already written for the design described in
"Release Signing" above and in `docs/ci-cd.md`. Items 2–4 below are now **provisioned** (the token,
signing key, and public key exist and are stored correctly); the remaining items are either
unconfirmed or are new pre-flight checks a later review added. This is the exhaustive list of what
needs to be true before the first live release under this design; everything below is a live-repo
state check or admin action outside what a code change can do on its own (same pattern as
`POLICY_AUDIT_TOKEN` above).

1. **The `shipsolid-release-bot` GitHub account** (already created per the dispatch that produced
   this design):
   - Add `amitsingh007s+repopolicybot@gmail.com` as a **verified** email on the account — SSH
     signature verification checks the signing commit/tag's committer email against a verified
     email on the account that owns the registered signing key, so an unverified email means every
     release shows as unverified on GitHub even with a technically-valid signature. *(Not confirmed
     — verify before the first live release.)*
   - Add the bot's SSH **public** key under Settings → SSH and GPG keys → New SSH key, with key type
     set to **Signing Key** (not "Authentication Key" — the two are registered separately on GitHub
     and only a Signing Key is checked against commit/tag signatures). **Done** — the bot's public
     key is registered as a Signing Key on its account.
   - Confirm the bot's collaborator permission level on `shipsolid/repo-policy` is **Write**, not
     Admin. Write is sufficient — `RELEASE_BOT_TOKEN` only needs to push branches, open PRs, and
     merge PRs that already satisfy branch protection's requirements (approvals: 0, `required`
     green); it never touches branch protection/ruleset settings itself, so Admin would be
     unnecessary standing privilege on a repository that specifically avoids granting exactly that
     kind of unnecessary standing privilege (see this file's Threat Model). *(Not confirmed — verify
     before the first live release.)*
2. **`RELEASE_BOT_TOKEN`** — a **classic** personal access token, not fine-grained, issued from the
   bot's own account (not the human owner's), scoped to `public_repo` only (this repository is
   currently public). Classic, not fine-grained, because fine-grained PATs can only be issued by an
   account that owns the target repository or belongs to the org that does; `shipsolid-release-bot`
   is a plain outside collaborator on `shipsolid/repo-policy`, a personal-account-owned repository
   with no org membership path around that restriction, so a fine-grained PAT scoped to this repo is
   not something the bot's account can create at all — confirmed against GitHub's own fine-grained-
   PAT documentation. `public_repo` is the narrowest classic scope available; it's coarser than the
   originally-specified fine-grained `Contents: Read and write` + `Pull requests: Read and write`
   would have been, but the bot's Write-only collaborator access (item 1 above) still keeps branch
   protection/ruleset settings out of reach regardless. **Done** — stored as a **secret on the
   `release` GitHub Environment**, not a repository or organization secret.
3. **`RELEASE_BOT_SIGNING_KEY`** — the bot's SSH *private* signing key (the one whose public half
   was added to the bot's account in step 1), generated locally by whoever administers this — never
   pasted into a workflow run, an issue, or a chat transcript. **Done** — stored as a **secret on the
   `release` GitHub Environment**.
4. **`RELEASE_BOT_SSH_PUBLIC_KEY`** — the corresponding SSH *public* key, same value as registered
   on the bot's GitHub account in step 1. **Done** — stored as a plain **repository variable**
   (Settings → Secrets and variables → Actions → Variables), not a secret — it's not sensitive, and
   keeping it as a variable makes it visible in the Actions UI for anyone auditing which key the
   pipeline currently trusts.
5. **The `release` GitHub Environment** (Settings → Environments, named exactly `release` to match
   `environment: release` in `release.yml`) — must already exist, since items 2–3 above are stored
   as secrets scoped to it:
   - Add a **required reviewers** protection rule naming the repository owner (or whoever should
     approve releases) — this is the human-in-the-loop gate from brief Step 2; every real release
     pauses here for a manual approval click before the `release` job's first step runs. *(Not
     confirmed — verify this rule is actually configured, not just that the environment exists,
     before the first live release: without it, the two secrets above are exposed to the `release`
     job with no approval gate at all.)*
   - Recommended, not required: restrict the environment's allowed deployment branches to `main` —
     `release.yml`'s only trigger is already `push: branches: [main]`, so this is defense in depth,
     not a functional requirement.
6. **"Allow squash merging" must be enabled** in `shipsolid/repo-policy`'s repository settings
   (Settings → General → Pull Requests). `gh pr merge --squash` hard-fails if it isn't, and nothing
   in `.github/repository-policy.yml` declares or detects this setting (it's a merge-method toggle,
   not something `repo-policy` models), so its absence wouldn't surface as a clear error — it would
   show up only as the merge-retry loop's 30-minute timeout, with a "not mergeable" message that
   looks identical to "required hasn't finished yet."
7. **Confirm no out-of-band tag-protection rule exists** for `refs/tags/*` on the live repository
   (Settings → Tags, Settings → Rules) that could block the release-bot's direct tag push. This
   repo's own self-policy declares neither a classic tag-protection rule nor a tag-scoped Ruleset
   (see docs/ci-cd.md's "Tag protection vs. branch protection" research), but that only covers what
   `repo-policy` itself manages — it can't rule out something added by hand outside `repo-policy`.
8. **Confirm `required` actually reports as that exact status-check context** on a real
   bot-authored PR before relying on it for the first live release. This design's merge-retry loop
   (see `release.yml`'s "Wait for the PR's required check and squash-merge it" step) entirely depends
   on GitHub evaluating mergeability against that exact context name; if the release-bot's PR ever
   produces a differently-named or missing check for any reason, every release attempt will time out
   at 30 minutes with a misleading "not mergeable yet" message rather than a clear "wrong check name"
   error. This is a live-repo verification step for whoever runs the first real release, not
   something re-checked here.

Whatever in items 1, 5, 6, 7, and 8 above isn't yet true, `release.yml`'s `release` job will either
fail to start, fail on first use of a missing/misconfigured piece, or — the more insidious case for
items 6–8 — run for the full 30-minute merge-retry window before failing with a timeout message that
doesn't point at the real cause. All of these are safe failure modes (no release ships), just not
always a *fast* one; confirming items 6–8 before the first live release attempt avoids burning that
timeout on a problem the retry loop was never going to be able to solve.

## Emergency Recovery

`.github/repository-policy.yml` declares `main` with `enforce_admins: true` — nobody, including
the repository owner, is exempted from requiring a passing `required` status check to merge —
and `clear_restrictions: true`, which resets any push-restriction allowlist GitHub might already
hold for the branch. That second field is a narrower guarantee than "no bypass actors": it does
not touch `bypass_pull_request_allowances`, a separate GitHub setting that lets specific actors
skip required PR-approval counts, which `repo-policy` reads through from whatever is already live
on GitHub rather than clearing (`src/repo_policy/policies/pull_requests.py`) — a human-set
allowance there would silently survive every `apply`. This combination is deliberate (see Threat
Model above), but it creates one failure mode this policy cannot resolve on its own: if
`required` itself becomes permanently unable to pass — a broken step in `ci.yml`, an
expired/revoked pinned Action, a GitHub Actions outage — no PR can merge, including the PR that
would fix the breakage.

There is no policy field for "allow a bypass under condition X"; recovering from this is a manual,
audited, time-boxed repository-settings change, not something `repo-policy` itself performs:

1. **Confirm the required check is actually broken**, not just failing correctly on real
   problems — re-run the `required` job and read its logs before touching branch protection.
2. **Temporarily relax the specific setting blocking the fix**, via the GitHub UI (Settings →
   Branches → the `main` protection rule) or the REST API's branch-protection endpoint — e.g.
   unchecking "Require status checks to pass" or "Include administrators" just long enough to
   merge the one PR that repairs `required`. Change the minimum needed, not the whole rule.
3. **Merge the fix**, confirm `required` passes again on `main` from a fresh run (not the
   bypassed one).
4. **Restore full protection immediately** — re-enable whatever was relaxed in step 2. Don't wait
   for `policy-audit.yml`'s next scheduled run to notice; confirm it yourself with
   `repo-policy audit --config .github/repository-policy.yml --repo shipsolid/repo-policy`
   (0 = compliant again).
5. **Open a follow-up issue the same day**, recording: what broke and why, exactly what was
   temporarily relaxed and for how long, who performed the bypass, which PR/commit merged under
   it, and confirmation from step 4 that protection was restored. This is the audit trail for an
   event that, by definition, happened outside the normal PR-reviewed path.

## Known Limitations

- No mTLS or certificate-based auth path — token-based auth only.
- No built-in secret scanning of `policy.yml` — a user could technically put a secret in a custom
  field extension in the future; the current schema has no such field, so this is currently moot.
- See the Threat Model above for the PAT-scope and `policy.yml`-review gaps, which are
  organizational controls repo-policy cannot enforce on your behalf.
- `bandit -q -r src` reports one Medium-severity finding, `B506` (`yaml_load`) at
  `config.py:103`, on `yaml.load(raw_text, Loader=_StrictLoader)`. This is a documented false
  positive: `_StrictLoader` (`config.py:17`) is a subclass of `yaml.SafeLoader`, not `yaml.Loader`
  — it only narrows two of SafeLoader's own implicit-resolver surprises (the yes/no/on/off bool
  words, and octal/sexagesimal ints) and rejects duplicate mapping keys; it never adds a
  constructor capable of instantiating arbitrary Python objects, so it carries exactly the same
  safety guarantee as `yaml.safe_load()` itself. Bandit's `B506` check flags any `yaml.load(...,
  Loader=...)` call pattern regardless of which Loader class is actually passed, so it can't
  distinguish this from a genuinely unsafe `Loader=yaml.Loader`. Not suppressed with `# nosec`
  in-source because `bandit` isn't wired into either CI workflow (see `docs/ci-cd.md`'s "Security
  workflow" section for why CodeQL's Python analysis is the automated static-analysis coverage
  instead) — there is no gate for a source-level suppression to silence, only this note for the
  next person who runs `bandit` locally and sees the same finding.
