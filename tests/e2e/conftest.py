from __future__ import annotations

import os
from collections.abc import Iterator

import httpx
import pytest

from repo_policy.github_client import GitHubClient

LIVE_OWNER = "shipsolid"
LIVE_NAME = "repo-policy-e2e-fixture"
LIVE_REPO = f"{LIVE_OWNER}/{LIVE_NAME}"
RULESET_BRANCH = "repo-policy-verify"


@pytest.fixture(scope="session")
def e2e_token() -> str:
    token = os.environ.get("REPO_POLICY_E2E_TOKEN")
    if not token:
        pytest.skip(
            "REPO_POLICY_E2E_TOKEN not set -- skipping E2E tests against the live fixture "
            f"repo ({LIVE_REPO}); export it to run this suite locally"
        )
    return token


@pytest.fixture(scope="session")
def live_client(e2e_token: str) -> Iterator[GitHubClient]:
    with GitHubClient(token=e2e_token, owner=LIVE_OWNER, repo=LIVE_NAME) as client:
        yield client


@pytest.fixture(scope="session")
def raw_http(e2e_token: str) -> Iterator[httpx.Client]:
    """Raw client for test-only repo-reset calls (unconditional protection removal, ref lookups)
    that repo-policy's own GitHubClient has no production reason to expose."""
    with httpx.Client(
        base_url="https://api.github.com",
        headers={
            "Authorization": f"Bearer {e2e_token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        timeout=30.0,
    ) as client:
        yield client


def _strip_protection_and_rulesets(live_client: GitHubClient, raw_http: httpx.Client) -> None:
    for branch in ("main", RULESET_BRANCH):
        response = raw_http.delete(f"/repos/{LIVE_OWNER}/{LIVE_NAME}/branches/{branch}/protection")
        assert response.status_code in (204, 404), response.text

    for summary in live_client.list_rulesets():
        live_client.delete_ruleset(summary["id"])


def _ensure_ruleset_branch_exists(raw_http: httpx.Client) -> None:
    """Verifies (does not create) RULESET_BRANCH. REPO_POLICY_E2E_TOKEN is scoped to
    `Administration: Read and write` only (see SECURITY.md) -- branch/ref creation needs the
    separate `Contents: Read and write` fine-grained PAT permission, which this token
    deliberately does not have (least-privilege: it only needs to manage protection/rulesets/
    settings, never git data). RULESET_BRANCH is therefore a one-time manual bootstrap on the
    persistent fixture repo, not something this suite (re)creates -- e.g.:
    `gh api repos/{owner}/{repo}/git/refs -f ref=refs/heads/{RULESET_BRANCH} -f sha=<main's sha>`.
    """
    ref_response = raw_http.get(f"/repos/{LIVE_OWNER}/{LIVE_NAME}/git/ref/heads/{RULESET_BRANCH}")
    assert ref_response.status_code == 200, (
        f"{RULESET_BRANCH} branch is missing on {LIVE_REPO} and REPO_POLICY_E2E_TOKEN cannot "
        f"create it -- see this function's docstring to recreate it manually. Response: "
        f"{ref_response.text}"
    )


@pytest.fixture(scope="session")
def clean_fixture_repo(
    request: pytest.FixtureRequest, live_client: GitHubClient, raw_http: httpx.Client
) -> None:
    """Resets shipsolid/repo-policy-e2e-fixture to a known baseline once per test session: no
    branch protection, no rulesets, and a second branch (repo-policy-verify) for
    ruleset-enforcement coverage. This is a persistent, shared, real repo reused across every
    local and CI run -- not created fresh per run -- so tests cannot assume a blank slate without
    this fixture. See docs/test-strategy.md's E2E section.

    Task 11 Step 3: the final cleanup is registered via `request.addfinalizer` *before* the first
    mutation below, rather than as the tail half of a `yield`-based generator fixture. That
    distinction matters: pytest only registers a generator fixture's post-yield code as a
    finalizer *after* the code before `yield` returns successfully -- if `_ensure_ruleset_branch_
    exists`'s assertion (a read, but still a failure point) had raised, the old yield-based version
    would never have reached `yield`, so its post-yield cleanup would never have been scheduled at
    all, silently leaving a stripped-but-not-fully-verified repo with no guaranteed reset at session
    end. `request.addfinalizer` has no such ordering hazard -- once called, the finalizer is queued
    unconditionally and runs at session teardown regardless of what happens afterward in this
    fixture's own setup or in any test that depends on it (an assertion failure, an API error, or
    the ineffective-ruleset repair scenario's own direct-API perturbation of live state)."""
    request.addfinalizer(lambda: _strip_protection_and_rulesets(live_client, raw_http))
    _strip_protection_and_rulesets(live_client, raw_http)
    _ensure_ruleset_branch_exists(raw_http)
