import pytest

from repo_policy.diff import PolicyResolutionError
from repo_policy.models import BranchPolicy, PullRequestPolicy, StatusChecksPolicy
from repo_policy.policies import branch_protection


def test_from_api_none_means_fully_permissive():
    result = branch_protection.from_api(None, signed_commits=False)
    assert result.pull_requests == PullRequestPolicy(required=False, approvals=0, code_owner_review=False)
    assert result.status_checks is None
    assert result.signed_commits is False
    assert result.linear_history is False
    assert result.allow_force_push is True
    assert result.allow_deletion is True
    assert result.enforce_admins is False


def test_from_api_reads_wrapped_booleans():
    data = {
        "required_pull_request_reviews": {"required_approving_review_count": 2, "require_code_owner_reviews": True},
        "required_status_checks": {"contexts": ["build"], "checks": []},
        "required_linear_history": {"enabled": True},
        "allow_force_pushes": {"enabled": False},
        "allow_deletions": {"enabled": False},
        "enforce_admins": {"enabled": True},
    }
    result = branch_protection.from_api(data, signed_commits=True)
    assert result.pull_requests == PullRequestPolicy(required=True, approvals=2, code_owner_review=True)
    assert result.status_checks == StatusChecksPolicy(required=["build"])
    assert result.linear_history is True
    assert result.allow_force_push is False
    assert result.allow_deletion is False
    assert result.signed_commits is True
    assert result.enforce_admins is True


def test_from_api_wraps_validation_error_as_policy_resolution_error():
    """allow_fork_syncing=true with lock_branch false/absent is rejected by BranchPolicy's own
    model_validator (models.py's _allow_fork_syncing_requires_lock_branch) -- if GitHub's GET
    response ever returned that combination, from_api's direct BranchPolicy(...) construction
    would previously raise a raw pydantic ValidationError that nothing above cli.py catches,
    crashing with Python's default exit code 1 (colliding with EXIT_DRIFT) instead of a clean,
    already-handled PolicyResolutionError."""
    data = {"allow_fork_syncing": {"enabled": True}, "lock_branch": {"enabled": False}}
    with pytest.raises(PolicyResolutionError):
        branch_protection.from_api(data, signed_commits=False)


def test_to_api_payload_builds_full_replace_body():
    resolved = BranchPolicy(
        pull_requests=PullRequestPolicy(required=True, approvals=2, code_owner_review=True),
        status_checks=StatusChecksPolicy(required=["build"]),
        linear_history=True,
        allow_force_push=False,
        allow_deletion=False,
    )
    payload = branch_protection.to_api_payload(resolved, current_raw=None)
    assert payload["enforce_admins"] is False
    assert payload["restrictions"] is None
    assert payload["required_pull_request_reviews"]["required_approving_review_count"] == 2
    assert "contexts" not in payload["required_status_checks"]
    assert payload["required_linear_history"] is True
    assert payload["allow_force_pushes"] is False
    assert payload["allow_deletions"] is False


def test_to_api_payload_preserves_current_block_creations():
    """block_creations has no modeled field -- a human-enabled "restrict who can create matching
    branches" must survive a full-object PUT triggered by an unrelated, modeled field changing."""
    resolved = BranchPolicy(
        pull_requests=PullRequestPolicy(required=False, approvals=0, code_owner_review=False),
        linear_history=True,
    )
    current_raw = {"block_creations": {"enabled": True}}
    payload = branch_protection.to_api_payload(resolved, current_raw=current_raw)
    assert payload["block_creations"] is True


def test_to_api_payload_defaults_block_creations_false_on_first_creation():
    resolved = BranchPolicy(
        pull_requests=PullRequestPolicy(required=False, approvals=0, code_owner_review=False)
    )
    payload = branch_protection.to_api_payload(resolved, current_raw=None)
    assert payload["block_creations"] is False


def test_to_api_payload_preserves_restrictions_from_current_state():
    """restrictions is the one PUT-required field the v1 schema still doesn't model. GitHub's GET
    response shapes restrictions.users/teams/apps as arrays of full objects (login/slug plus
    other metadata); the PUT request body expects arrays of bare login/slug strings -- sending
    the GET shape back verbatim 422s."""
    current_raw = {
        "restrictions": {
            "users": [{"login": "octocat", "id": 1, "type": "User"}],
            "teams": [{"slug": "justice-league", "id": 2, "name": "Justice League"}],
            "apps": [],
        }
    }
    resolved = BranchPolicy(
        pull_requests=PullRequestPolicy(required=False, approvals=0, code_owner_review=False),
        linear_history=False,
        allow_force_push=True,
        allow_deletion=True,
        enforce_admins=False,
    )
    payload = branch_protection.to_api_payload(resolved, current_raw=current_raw)
    assert payload["restrictions"] == {"users": ["octocat"], "teams": ["justice-league"], "apps": []}


def test_from_api_none_means_clear_restrictions_true():
    result = branch_protection.from_api(None, signed_commits=False)
    assert result.clear_restrictions is True


def test_from_api_reads_clear_restrictions_true_when_no_live_restriction():
    data = {"restrictions": None}
    result = branch_protection.from_api(data, signed_commits=False)
    assert result.clear_restrictions is True


def test_from_api_reads_clear_restrictions_false_when_live_restriction_exists():
    data = {"restrictions": {"users": ["octocat"], "teams": [], "apps": []}}
    result = branch_protection.from_api(data, signed_commits=False)
    assert result.clear_restrictions is False


def test_to_api_payload_forces_restrictions_null_when_clear_restrictions_true():
    resolved = BranchPolicy(
        pull_requests=PullRequestPolicy(required=False, approvals=0, code_owner_review=False),
        linear_history=False,
        allow_force_push=True,
        allow_deletion=True,
        clear_restrictions=True,
    )
    current_raw = {"restrictions": {"users": ["octocat"], "teams": [], "apps": []}}
    payload = branch_protection.to_api_payload(resolved, current_raw=current_raw)
    assert payload["restrictions"] is None


def test_to_api_payload_preserves_restrictions_when_clear_restrictions_false():
    resolved = BranchPolicy(
        pull_requests=PullRequestPolicy(required=False, approvals=0, code_owner_review=False),
        linear_history=False,
        allow_force_push=True,
        allow_deletion=True,
        clear_restrictions=False,
    )
    current_raw = {
        "restrictions": {"users": [{"login": "octocat", "id": 1}], "teams": [], "apps": []}
    }
    payload = branch_protection.to_api_payload(resolved, current_raw=current_raw)
    assert payload["restrictions"] == {"users": ["octocat"], "teams": [], "apps": []}


def test_to_api_payload_writes_enforce_admins_from_resolved_policy():
    resolved = BranchPolicy(
        pull_requests=PullRequestPolicy(required=False, approvals=0, code_owner_review=False),
        linear_history=False,
        allow_force_push=True,
        allow_deletion=True,
        enforce_admins=True,
    )
    payload = branch_protection.to_api_payload(resolved, current_raw={"enforce_admins": {"enabled": False}})
    assert payload["enforce_admins"] is True  # resolved wins, current_raw is ignored for this field now


def test_to_api_payload_writes_required_conversation_resolution():
    resolved = BranchPolicy(
        pull_requests=PullRequestPolicy(required=False, approvals=0, code_owner_review=False),
        linear_history=False,
        allow_force_push=True,
        allow_deletion=True,
        enforce_admins=False,
        required_conversation_resolution=True,
    )
    payload = branch_protection.to_api_payload(resolved, current_raw=None)
    assert payload["required_conversation_resolution"] is True


def test_to_api_payload_writes_lock_branch():
    resolved = BranchPolicy(
        pull_requests=PullRequestPolicy(required=False, approvals=0, code_owner_review=False),
        linear_history=False,
        allow_force_push=True,
        allow_deletion=True,
        enforce_admins=False,
        required_conversation_resolution=False,
        lock_branch=True,
    )
    payload = branch_protection.to_api_payload(resolved, current_raw=None)
    assert payload["lock_branch"] is True


def test_to_api_payload_writes_allow_fork_syncing():
    resolved = BranchPolicy(
        pull_requests=PullRequestPolicy(required=False, approvals=0, code_owner_review=False),
        linear_history=False,
        allow_force_push=True,
        allow_deletion=True,
        allow_fork_syncing=False,
    )
    payload = branch_protection.to_api_payload(resolved, current_raw=None)
    assert payload["allow_fork_syncing"] is False


def test_to_api_payload_writes_allow_fork_syncing_true_when_paired_with_lock_branch():
    resolved = BranchPolicy(
        pull_requests=PullRequestPolicy(required=False, approvals=0, code_owner_review=False),
        linear_history=False,
        allow_force_push=True,
        allow_deletion=True,
        lock_branch=True,
        allow_fork_syncing=True,
    )
    payload = branch_protection.to_api_payload(resolved, current_raw=None)
    assert payload["allow_fork_syncing"] is True
    assert payload["lock_branch"] is True


def test_to_api_payload_preserves_unmodeled_status_check_strict_field():
    """Regression test: a targeted change to one declared field (allow_force_push) must not
    silently reset the status-check 'strict' (require branches up to date) setting — the one
    remaining nested field repo-policy doesn't model but a human may have set manually on GitHub.
    (dismiss_stale_reviews/require_last_push_approval used to be covered by this same test, but
    became modeled fields — see tests/test_policies_pull_requests.py instead.)"""
    current_raw = {
        "required_pull_request_reviews": {
            "required_approving_review_count": 2,
            "require_code_owner_reviews": True,
        },
        "required_status_checks": {"contexts": ["build"], "checks": [], "strict": True},
    }
    resolved = BranchPolicy(
        pull_requests=PullRequestPolicy(required=True, approvals=2, code_owner_review=True),
        status_checks=StatusChecksPolicy(required=["build"]),
        linear_history=False,
        allow_force_push=False,  # the only field actually changing
        allow_deletion=True,
    )
    payload = branch_protection.to_api_payload(resolved, current_raw=current_raw)
    assert payload["required_status_checks"]["strict"] is True


def test_to_api_payload_raises_explicit_error_when_pull_requests_unresolved():
    unresolved = BranchPolicy(pull_requests=None)
    with pytest.raises(ValueError, match="pull_requests"):
        branch_protection.to_api_payload(unresolved, current_raw=None)
