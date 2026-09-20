from __future__ import annotations

import pytest
from click.testing import CliRunner

from repo_policy.cli import main

from .conftest import LIVE_REPO

pytestmark = pytest.mark.e2e

FULL_POLICY = "tests/e2e/fixtures/e2e_full_policy.yml"
PRUNE_POLICY = "tests/e2e/fixtures/e2e_prune_policy.yml"


def _invoke(*args: str):
    return CliRunner().invoke(main, list(args))


def test_validate_accepts_e2e_fixture_policies():
    for config in (FULL_POLICY, PRUNE_POLICY):
        result = _invoke("validate", "--config", config)
        assert result.exit_code == 0, result.output


def test_live_token_and_repo_are_reachable(live_client):
    repo = live_client.get_repo()
    assert repo["full_name"] == LIVE_REPO


def test_plan_reports_full_drift_on_unprotected_branches(clean_fixture_repo, e2e_token):
    result = _invoke("plan", "--config", FULL_POLICY, "--repo", LIVE_REPO, "--token", e2e_token)
    assert result.exit_code == 1, result.output
    assert "Branch: main" in result.output
    assert "Branch: repo-policy-verify" in result.output


def test_apply_full_policy_succeeds(clean_fixture_repo, e2e_token, live_client):
    result = _invoke("apply", "--config", FULL_POLICY, "--repo", LIVE_REPO, "--token", e2e_token)
    assert result.exit_code == 0, result.output

    # Task 4: apply's exit code already proves its own internal post-mutation verify_after_apply
    # pass saw compliance -- independently confirm that against two more sources of truth before
    # any later test (same session-scoped clean_fixture_repo) relies on this state: a direct
    # GitHub read that bypasses repo-policy's read path entirely, and a fresh `audit` invocation
    # (repo-policy's own read path, but a separate process-level command from apply's internal
    # check).
    live_repo = live_client.get_repo()
    assert live_repo["delete_branch_on_merge"] is True
    live_protection = live_client.get_branch_protection("main")
    assert live_protection is not None
    assert live_protection["required_linear_history"]["enabled"] is True
    live_ruleset = live_client.find_ruleset_by_name("repo-policy:repo-policy-verify")
    assert live_ruleset is not None
    assert live_ruleset["enforcement"] == "active"

    audit_result = _invoke("audit", "--config", FULL_POLICY, "--repo", LIVE_REPO, "--token", e2e_token)
    assert audit_result.exit_code == 0, audit_result.output
    assert "is compliant" in audit_result.output


def test_plan_reports_zero_drift_after_apply(clean_fixture_repo, e2e_token):
    """Must run after test_apply_full_policy_succeeds (same session-scoped clean_fixture_repo,
    same fixture repo) -- proves real-world idempotency against a live repo, the exact gap
    docs/test-strategy.md's Known Gaps previously described as manual-only."""
    result = _invoke("plan", "--config", FULL_POLICY, "--repo", LIVE_REPO, "--token", e2e_token)
    assert result.exit_code == 0, result.output
    assert result.output.count("No changes required.") == 2


def test_strict_apply_prunes_orphaned_ruleset(clean_fixture_repo, e2e_token, live_client):
    """Must run after test_apply_full_policy_succeeds -- the repo-policy:repo-policy-verify
    ruleset must already exist (created by that apply) for pruning to have something to prune."""
    before = live_client.find_ruleset_by_name("repo-policy:repo-policy-verify")
    assert before is not None, "precondition failed: expected ruleset from the prior apply"

    result = _invoke("apply", "--config", PRUNE_POLICY, "--repo", LIVE_REPO, "--token", e2e_token)

    assert result.exit_code == 0, result.output
    assert "- removed orphaned ruleset repo-policy:repo-policy-verify" in result.output
    after = live_client.find_ruleset_by_name("repo-policy:repo-policy-verify")
    assert after is None

    # Task 4: confirm this apply's own verify_after_apply pass wasn't the only thing that thought
    # the prune converged -- independently query GitHub (above, `after is None`) and run `audit`
    # against the pruning policy, both showing effective (pruned) state.
    audit_result = _invoke("audit", "--config", PRUNE_POLICY, "--repo", LIVE_REPO, "--token", e2e_token)
    assert audit_result.exit_code == 0, audit_result.output
    assert "is compliant" in audit_result.output
