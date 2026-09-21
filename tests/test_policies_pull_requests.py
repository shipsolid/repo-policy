from repo_policy.models import BypassPullRequestAllowances, DismissalRestrictions, PullRequestPolicy
from repo_policy.policies import pull_requests


def test_to_branch_protection_none_when_not_required():
    assert pull_requests.to_branch_protection(PullRequestPolicy(required=False)) is None


def test_to_branch_protection_builds_payload():
    policy = PullRequestPolicy(required=True, approvals=2, code_owner_review=True)
    payload = pull_requests.to_branch_protection(policy)
    assert payload["required_approving_review_count"] == 2
    assert payload["require_code_owner_reviews"] is True


def test_to_branch_protection_includes_dismiss_stale_reviews_and_last_push_approval():
    policy = PullRequestPolicy(
        required=True,
        approvals=2,
        code_owner_review=True,
        dismiss_stale_reviews=True,
        require_last_push_approval=True,
    )
    payload = pull_requests.to_branch_protection(policy)
    assert payload["dismiss_stale_reviews"] is True
    assert payload["require_last_push_approval"] is True


def test_to_branch_protection_defaults_new_fields_false():
    policy = PullRequestPolicy(required=True, approvals=2, code_owner_review=True)
    payload = pull_requests.to_branch_protection(policy)
    assert payload["dismiss_stale_reviews"] is False
    assert payload["require_last_push_approval"] is False


def test_to_branch_protection_includes_declared_dismissal_restrictions_and_bypass_allowances():
    """dismissal_restrictions/bypass_pull_request_allowances are fully modeled fields now -- the
    payload reflects whatever's declared on the policy, not a read-through of live GitHub state.
    dismissal_restrictions supports only users/teams (no apps) -- unlike
    bypass_pull_request_allowances and branch-protection restrictions, which both support apps."""
    policy = PullRequestPolicy(
        required=True,
        approvals=2,
        code_owner_review=True,
        dismissal_restrictions=DismissalRestrictions(users=["octocat"], teams=["justice-league"]),
        bypass_pull_request_allowances=BypassPullRequestAllowances(apps=["dependabot"]),
    )
    payload = pull_requests.to_branch_protection(policy)
    assert payload["dismissal_restrictions"] == {"users": ["octocat"], "teams": ["justice-league"]}
    assert "apps" not in payload["dismissal_restrictions"]
    assert payload["bypass_pull_request_allowances"] == {
        "users": [],
        "teams": [],
        "apps": ["dependabot"],
    }


def test_to_branch_protection_omits_dismissal_restrictions_and_bypass_allowances_when_undeclared():
    policy = PullRequestPolicy(required=True, approvals=2, code_owner_review=True)
    payload = pull_requests.to_branch_protection(policy)
    assert "dismissal_restrictions" not in payload
    assert "bypass_pull_request_allowances" not in payload


def test_from_branch_protection_none_means_not_required():
    result = pull_requests.from_branch_protection(None)
    assert result.required is False
    assert result.approvals == 0


def test_from_branch_protection_reads_payload():
    data = {"required_approving_review_count": 3, "require_code_owner_reviews": True}
    result = pull_requests.from_branch_protection(data)
    assert result == PullRequestPolicy(required=True, approvals=3, code_owner_review=True)


def test_from_branch_protection_reads_dismiss_stale_reviews_and_last_push_approval():
    data = {
        "required_approving_review_count": 3,
        "require_code_owner_reviews": True,
        "dismiss_stale_reviews": True,
        "require_last_push_approval": True,
    }
    result = pull_requests.from_branch_protection(data)
    assert result.dismiss_stale_reviews is True
    assert result.require_last_push_approval is True


def test_from_branch_protection_reads_dismissal_restrictions_dropping_apps():
    """GitHub's GET shapes these as full user/team/app objects; dismissal_restrictions supports
    only users/teams on write, so the (always-present) `apps` key from _actor_refs is dropped
    when building the model, not just when building the outbound payload."""
    data = {
        "required_approving_review_count": 2,
        "require_code_owner_reviews": True,
        "dismissal_restrictions": {
            "users": [{"login": "octocat", "id": 1}],
            "teams": [{"slug": "justice-league", "id": 2}],
        },
    }
    result = pull_requests.from_branch_protection(data)
    assert result.dismissal_restrictions == DismissalRestrictions(
        users=["octocat"], teams=["justice-league"]
    )


def test_from_branch_protection_reads_bypass_pull_request_allowances_including_apps():
    data = {
        "required_approving_review_count": 2,
        "require_code_owner_reviews": True,
        "bypass_pull_request_allowances": {
            "users": [],
            "teams": [],
            "apps": [{"slug": "dependabot", "id": 3}],
        },
    }
    result = pull_requests.from_branch_protection(data)
    assert result.bypass_pull_request_allowances == BypassPullRequestAllowances(apps=["dependabot"])


def test_from_branch_protection_leaves_dismissal_restrictions_and_bypass_allowances_none_when_absent():
    data = {"required_approving_review_count": 2, "require_code_owner_reviews": True}
    result = pull_requests.from_branch_protection(data)
    assert result.dismissal_restrictions is None
    assert result.bypass_pull_request_allowances is None


def test_to_ruleset_rule_none_when_not_required():
    assert pull_requests.to_ruleset_rule(PullRequestPolicy(required=False)) is None


def test_to_ruleset_rule_builds_rule():
    policy = PullRequestPolicy(required=True, approvals=1, code_owner_review=False)
    rule = pull_requests.to_ruleset_rule(policy)
    assert rule["type"] == "pull_request"
    assert rule["parameters"]["required_approving_review_count"] == 1


def test_to_ruleset_rule_defaults_review_thread_resolution_false_when_no_current_state():
    policy = PullRequestPolicy(required=True, approvals=1, code_owner_review=False)
    rule = pull_requests.to_ruleset_rule(policy, current=None)
    assert rule["parameters"]["required_review_thread_resolution"] is False


def test_to_ruleset_rule_preserves_review_thread_resolution_from_current_state():
    """required_review_thread_resolution has no modeled field (distinct from
    required_conversation_resolution, which is rejected outright for enforcement: ruleset) -- a
    human-set value on the live pull_request rule must survive a rules-array rebuild triggered by
    an unrelated declared field changing, not be reset to False on every apply."""
    policy = PullRequestPolicy(required=True, approvals=1, code_owner_review=False)
    current_rule = {
        "type": "pull_request",
        "parameters": {
            "required_approving_review_count": 1,
            "required_review_thread_resolution": True,
        },
    }
    rule = pull_requests.to_ruleset_rule(policy, current=current_rule)
    assert rule["parameters"]["required_review_thread_resolution"] is True


def test_from_ruleset_rule_none_means_not_required():
    result = pull_requests.from_ruleset_rule(None)
    assert result.required is False


def test_from_ruleset_rule_reads_rule():
    rule = {
        "type": "pull_request",
        "parameters": {"required_approving_review_count": 2, "require_code_owner_review": True},
    }
    result = pull_requests.from_ruleset_rule(rule)
    assert result == PullRequestPolicy(required=True, approvals=2, code_owner_review=True)


def test_ruleset_rule_round_trips_dismiss_stale_reviews_and_last_push_approval():
    policy = PullRequestPolicy(
        required=True,
        approvals=1,
        code_owner_review=False,
        dismiss_stale_reviews=True,
        require_last_push_approval=True,
    )
    rule = pull_requests.to_ruleset_rule(policy)
    assert rule["parameters"]["dismiss_stale_reviews_on_push"] is True
    assert rule["parameters"]["require_last_push_approval"] is True
    result = pull_requests.from_ruleset_rule(rule)
    assert result.dismiss_stale_reviews is True
    assert result.require_last_push_approval is True
