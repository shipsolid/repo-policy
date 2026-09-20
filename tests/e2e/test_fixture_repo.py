from __future__ import annotations

import httpx
import pytest
from click.testing import CliRunner

from repo_policy.cli import main
from repo_policy.github_client import GitHubClient

from .conftest import LIVE_NAME, LIVE_OWNER, LIVE_REPO, RULESET_BRANCH

pytestmark = pytest.mark.e2e

FULL_POLICY = "tests/e2e/fixtures/e2e_full_policy.yml"
PRUNE_POLICY = "tests/e2e/fixtures/e2e_prune_policy.yml"

RULESET_NAME = "repo-policy:repo-policy-verify"
STATUS_CHECK_CONTEXT = "repo-policy-e2e/verify"


def _invoke(*args: str):
    return CliRunner().invoke(main, list(args))


# --- Independent smoke tests (Task 11 Step 2) --------------------------------------------------
#
# These must never depend on the live fixture repo's current policy state -- only on the token/
# repo being reachable and the fixture config files parsing -- so they stay correct and safe to
# run in any order, subset, or worker relative to test_full_policy_lifecycle below.


def test_validate_accepts_e2e_fixture_policies():
    for config in (FULL_POLICY, PRUNE_POLICY):
        result = _invoke("validate", "--config", config)
        assert result.exit_code == 0, result.output


def test_live_token_and_repo_are_reachable(live_client):
    repo = live_client.get_repo()
    assert repo["full_name"] == LIVE_REPO


# --- Collapsed lifecycle scenario (Task 11 Step 1) ----------------------------------------------
#
# Everything below previously lived in four separate test functions that only passed because
# pytest happened to run them in file order against the same session-scoped clean_fixture_repo
# fixture -- test_apply_full_policy_succeeds's docstring literally said "Must run after
# test_plan_reports_full_drift...". That's a hidden ordering dependency: run a subset, reorder the
# file, or (with a future parallelizing plugin) shard across workers, and either the assertions
# stop making sense or two workers mutate the same live fixture repo at once. Collapsing the whole
# sequence into one test function removes the possibility of cross-test ordering entirely -- there
# is now exactly one path through this scenario, and it is the function body's own statement order.


def test_full_policy_lifecycle(clean_fixture_repo, e2e_token, live_client, raw_http):
    """Clean baseline -> plan -> apply -> independent API verification -> no-drift audit ->
    ineffective-ruleset repair -> strict prune -> (cleanup happens in clean_fixture_repo's session
    finalizer, registered before any mutation -- see conftest.py's Task 11 Step 3 note)."""

    # 1. Clean baseline: clean_fixture_repo's setup already guarantees this (no branch protection,
    #    no rulesets) -- confirmed here via `plan` reporting full drift on both branches.
    baseline_plan = _invoke("plan", "--config", FULL_POLICY, "--repo", LIVE_REPO, "--token", e2e_token)
    assert baseline_plan.exit_code == 1, baseline_plan.output
    assert "Branch: main" in baseline_plan.output
    assert f"Branch: {RULESET_BRANCH}" in baseline_plan.output

    # 2. Apply the full policy.
    apply_result = _invoke("apply", "--config", FULL_POLICY, "--repo", LIVE_REPO, "--token", e2e_token)
    assert apply_result.exit_code == 0, apply_result.output

    # 3. Independent API verification: bypass repo-policy's own read path entirely (Task 11 Step 5
    #    -- status checks, signed commits, ruleset active state/exact conditions/empty bypass
    #    actors, and every repo-level setting the fixture is eligible for).
    _assert_main_branch_protection_matches_policy(live_client)
    _assert_repo_policy_verify_ruleset_matches_policy(live_client)
    _assert_repo_settings_match_policy(live_client)

    # 4. No-drift audit: repo-policy's own read path, but a fresh process-level command from
    #    apply's internal post-mutation check -- proves real-world idempotency against a live repo.
    audit_result = _invoke("audit", "--config", FULL_POLICY, "--repo", LIVE_REPO, "--token", e2e_token)
    assert audit_result.exit_code == 0, audit_result.output
    assert "is compliant" in audit_result.output

    no_drift_plan = _invoke("plan", "--config", FULL_POLICY, "--repo", LIVE_REPO, "--token", e2e_token)
    assert no_drift_plan.exit_code == 0, no_drift_plan.output
    assert no_drift_plan.output.count("No changes required.") == 2

    # 5. Ineffective-ruleset repair scenario (Task 11 Step 6).
    _run_ineffective_ruleset_repair_scenario(live_client, raw_http, e2e_token)

    # 6. Strict prune: switching to a policy that no longer declares repo-policy-verify under
    #    enforcement: ruleset removes its now-orphaned ruleset.
    before = live_client.find_ruleset_by_name(RULESET_NAME)
    assert before is not None, "precondition failed: expected ruleset from the prior apply/repair"

    prune_result = _invoke("apply", "--config", PRUNE_POLICY, "--repo", LIVE_REPO, "--token", e2e_token)
    assert prune_result.exit_code == 0, prune_result.output
    assert f"- removed orphaned ruleset {RULESET_NAME}" in prune_result.output

    after = live_client.find_ruleset_by_name(RULESET_NAME)
    assert after is None

    prune_audit = _invoke("audit", "--config", PRUNE_POLICY, "--repo", LIVE_REPO, "--token", e2e_token)
    assert prune_audit.exit_code == 0, prune_audit.output
    assert "is compliant" in prune_audit.output

    # 7. Final cleanup runs in clean_fixture_repo's session-scoped finalizer once every test using
    #    it has finished -- nothing further to do here.


def _assert_main_branch_protection_matches_policy(live_client: GitHubClient) -> None:
    """Direct GitHub read of `main`'s classic branch protection -- independent of
    branch_protection.from_api, the exact translator apply/audit already trust each other through."""
    protection = live_client.get_branch_protection("main")
    assert protection is not None
    assert protection["required_linear_history"]["enabled"] is True
    assert protection["allow_force_pushes"]["enabled"] is False
    assert protection["allow_deletions"]["enabled"] is False
    assert protection["enforce_admins"]["enabled"] is True
    assert protection["required_conversation_resolution"]["enabled"] is True

    reviews = protection["required_pull_request_reviews"]
    assert reviews["required_approving_review_count"] == 1
    assert reviews["dismiss_stale_reviews"] is True
    assert reviews["require_last_push_approval"] is True

    status_checks = protection.get("required_status_checks") or {}
    contexts = {check["context"] for check in status_checks.get("checks", [])}
    assert STATUS_CHECK_CONTEXT in contexts

    assert live_client.get_required_signatures("main") is True


def _assert_repo_policy_verify_ruleset_matches_policy(live_client: GitHubClient) -> None:
    """Direct GitHub read of the repo-policy-verify ruleset -- active state, exact branch scoping,
    empty bypass actors, and every rule type the full policy declares (Task 11 Step 5), plus the
    live effective-rules cross-check that proves the ruleset actually governs the branch, not just
    that it exists and looks right on paper."""
    ruleset = live_client.find_ruleset_by_name(RULESET_NAME)
    assert ruleset is not None
    assert ruleset["enforcement"] == "active"
    assert ruleset["target"] == "branch"
    assert ruleset["conditions"]["ref_name"]["include"] == [f"refs/heads/{RULESET_BRANCH}"]
    assert ruleset["conditions"]["ref_name"]["exclude"] == []
    assert ruleset["bypass_actors"] == []

    rules_by_type = {rule["type"]: rule for rule in ruleset["rules"]}
    assert rules_by_type["pull_request"]["parameters"]["required_approving_review_count"] == 1
    assert "required_linear_history" in rules_by_type
    assert "non_fast_forward" in rules_by_type
    assert "deletion" in rules_by_type
    assert "required_signatures" in rules_by_type

    status_check_rule = rules_by_type["required_status_checks"]
    contexts = {c["context"] for c in status_check_rule["parameters"]["required_status_checks"]}
    assert STATUS_CHECK_CONTEXT in contexts

    active_ruleset_ids = {r.get("ruleset_id") for r in live_client.get_rules_for_branch(RULESET_BRANCH)}
    assert ruleset["id"] in active_ruleset_ids


def _assert_repo_settings_match_policy(live_client: GitHubClient) -> None:
    """Every repo-level setting the full policy declares and the fixture repo is eligible for
    (Task 11 Step 5) -- each read through its own independent GitHub endpoint, the same ones
    repo_settings.py itself reads from, rather than trusting apply's/audit's own compliance claim."""
    repo = live_client.get_repo()
    assert repo["delete_branch_on_merge"] is True
    assert repo["allow_update_branch"] is True

    security = repo.get("security_and_analysis") or {}
    assert (security.get("secret_scanning") or {}).get("status") == "enabled"
    assert (security.get("secret_scanning_push_protection") or {}).get("status") == "enabled"

    assert live_client.get_vulnerability_alerts() is True
    assert live_client.get_automated_security_fixes() is True
    assert live_client.get_private_vulnerability_reporting() is True


def _run_ineffective_ruleset_repair_scenario(
    live_client: GitHubClient, raw_http: httpx.Client, e2e_token: str
) -> None:
    """Task 11 Step 6: a ruleset can be correctly shaped -- right rules, right metadata -- and
    still not be what GitHub is actually enforcing on the branch, e.g. because a human (or a bug)
    disabled it directly. Simulates exactly that by mutating live state through raw_http (bypassing
    repo-policy's own client entirely, the same way a human using the GitHub UI/API directly
    would), confirms `audit`/`plan` surface it as drift, repairs it via a normal `apply`, and
    independently re-confirms both the ruleset's own metadata and the branch's live effective rules
    (not just the ruleset object in isolation) show it active again."""
    ruleset = live_client.find_ruleset_by_name(RULESET_NAME)
    assert ruleset is not None, "precondition failed: expected ruleset from the prior apply"
    ruleset_id = ruleset["id"]

    disable_response = raw_http.put(
        f"/repos/{LIVE_OWNER}/{LIVE_NAME}/rulesets/{ruleset_id}", json={"enforcement": "disabled"}
    )
    assert disable_response.status_code == 200, disable_response.text
    assert live_client.get_ruleset(ruleset_id)["enforcement"] == "disabled"  # perturbation landed

    drift_audit = _invoke("audit", "--config", FULL_POLICY, "--repo", LIVE_REPO, "--token", e2e_token)
    assert drift_audit.exit_code == 1, drift_audit.output
    assert f"{RULESET_BRANCH}: 1 change(s) required" in drift_audit.output

    drift_plan = _invoke("plan", "--config", FULL_POLICY, "--repo", LIVE_REPO, "--token", e2e_token)
    assert "Ruleset enforcement" in drift_plan.output

    repair_result = _invoke("apply", "--config", FULL_POLICY, "--repo", LIVE_REPO, "--token", e2e_token)
    assert repair_result.exit_code == 0, repair_result.output

    assert live_client.get_ruleset(ruleset_id)["enforcement"] == "active"
    active_ruleset_ids = {
        rule.get("ruleset_id") for rule in live_client.get_rules_for_branch(RULESET_BRANCH)
    }
    assert ruleset_id in active_ruleset_ids
