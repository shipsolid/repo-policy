import pytest

from repo_policy.diff import PolicyResolutionError
from repo_policy.models import BranchPolicy, PullRequestPolicy, StatusChecksPolicy
from repo_policy.policies import rulesets


def compliant_ruleset(branch: str, *, rules: list[dict] | None = None) -> dict:
    """A ruleset GET payload already in the canonical, always-active, exactly-scoped, no-bypass
    shape `to_api_payload()` builds -- the baseline `test_metadata_changes_rejects_ineffective_
    ruleset` patches away from, one owned-metadata field at a time."""
    return {
        "id": 7,
        "name": rulesets.ruleset_name(branch),
        "target": "branch",
        "enforcement": "active",
        "conditions": {"ref_name": {"include": [f"refs/heads/{branch}"], "exclude": []}},
        "rules": rules if rules is not None else [],
        "bypass_actors": [],
    }


def test_ruleset_name_is_deterministic():
    assert rulesets.ruleset_name("main") == "repo-policy:main"


def test_metadata_changes_none_when_ruleset_does_not_exist_yet():
    """data is None -- the ruleset hasn't been created yet, an "add" already handled by the
    existing creation flow in apply.py, so there's nothing to diff here."""
    assert rulesets.metadata_changes("main", None) == []


def test_metadata_changes_empty_for_an_already_canonical_ruleset():
    assert rulesets.metadata_changes("main", compliant_ruleset("main")) == []


@pytest.mark.parametrize(
    ("patch", "field"),
    [
        ({"enforcement": "disabled"}, "ruleset_enforcement"),
        ({"enforcement": "evaluate"}, "ruleset_enforcement"),
        ({"target": "tag"}, "ruleset_target"),
        ({"conditions": {"ref_name": {"include": [], "exclude": []}}}, "ruleset_conditions"),
        (
            {"conditions": {"ref_name": {"include": ["refs/heads/main"], "exclude": ["refs/heads/main"]}}},
            "ruleset_conditions",
        ),
        (
            {"bypass_actors": [{"actor_id": 5, "actor_type": "RepositoryRole", "bypass_mode": "always"}]},
            "ruleset_bypass_actors",
        ),
    ],
)
def test_metadata_changes_rejects_ineffective_ruleset(patch, field):
    raw = compliant_ruleset("main") | patch
    assert field in {change.field for change in rulesets.metadata_changes("main", raw)}


def test_from_api_hardcodes_enforce_admins_false():
    assert rulesets.from_api(None).enforce_admins is False
    assert rulesets.from_api({"rules": []}).enforce_admins is False


def test_from_api_hardcodes_required_conversation_resolution_false():
    assert rulesets.from_api(None).required_conversation_resolution is False
    assert rulesets.from_api({"rules": []}).required_conversation_resolution is False


def test_from_api_hardcodes_lock_branch_false():
    assert rulesets.from_api(None).lock_branch is False
    assert rulesets.from_api({"rules": []}).lock_branch is False


def test_from_api_hardcodes_allow_fork_syncing_false():
    assert rulesets.from_api(None).allow_fork_syncing is False
    assert rulesets.from_api({"rules": []}).allow_fork_syncing is False


def test_from_api_hardcodes_clear_restrictions_true():
    assert rulesets.from_api(None).clear_restrictions is True
    assert rulesets.from_api({"rules": []}).clear_restrictions is True


def test_from_api_none_means_fully_permissive():
    result = rulesets.from_api(None)
    assert result.pull_requests == PullRequestPolicy(required=False, approvals=0, code_owner_review=False)
    assert result.status_checks is None
    assert result.signed_commits is False
    assert result.linear_history is False
    assert result.allow_force_push is True
    assert result.allow_deletion is True


def test_from_api_reads_rules_array():
    data = {
        "rules": [
            {"type": "pull_request", "parameters": {"required_approving_review_count": 1, "require_code_owner_review": False}},
            {"type": "required_signatures"},
            {"type": "non_fast_forward"},
        ]
    }
    result = rulesets.from_api(data)
    assert result.pull_requests == PullRequestPolicy(required=True, approvals=1, code_owner_review=False)
    assert result.signed_commits is True
    assert result.linear_history is False
    assert result.allow_force_push is False
    assert result.allow_deletion is True


def test_from_api_wraps_validation_error_as_policy_resolution_error():
    """PullRequestPolicy.approvals is now constrained to GitHub's actual 0..6 range (models.py) --
    if a live ruleset's pull_request rule ever has required_approving_review_count outside that
    range (a direct API write, a future GitHub product change, or a value repo-policy itself wrote
    before this constraint existed), from_api's direct BranchPolicy(...) construction would
    otherwise raise a raw pydantic ValidationError that nothing above cli.py catches, crashing
    with Python's default exit code 1 (colliding with EXIT_DRIFT) instead of a clean,
    already-handled PolicyResolutionError -- the same failure mode branch_protection.from_api's
    own test_from_api_wraps_validation_error_as_policy_resolution_error guards against."""
    data = {
        "rules": [
            {
                "type": "pull_request",
                "parameters": {"required_approving_review_count": 7, "require_code_owner_review": False},
            }
        ]
    }
    with pytest.raises(PolicyResolutionError):
        rulesets.from_api(data)


def test_to_api_payload_builds_ruleset_targeting_branch():
    resolved = BranchPolicy(
        pull_requests=PullRequestPolicy(required=True, approvals=2, code_owner_review=True),
        status_checks=StatusChecksPolicy(required=["build"]),
        signed_commits=True,
        linear_history=True,
        allow_force_push=False,
        allow_deletion=False,
    )
    payload = rulesets.to_api_payload("main", resolved)
    assert payload["name"] == "repo-policy:main"
    assert payload["target"] == "branch"
    assert payload["enforcement"] == "active"
    assert payload["conditions"]["ref_name"]["include"] == ["refs/heads/main"]
    rule_types = {rule["type"] for rule in payload["rules"]}
    assert rule_types == {
        "pull_request",
        "required_status_checks",
        "required_signatures",
        "required_linear_history",
        "non_fast_forward",
        "deletion",
    }


def test_to_api_payload_omits_rules_for_permissive_fields():
    resolved = BranchPolicy(
        pull_requests=PullRequestPolicy(required=False, approvals=0, code_owner_review=False),
        status_checks=None,
        signed_commits=False,
        linear_history=False,
        allow_force_push=True,
        allow_deletion=True,
    )
    payload = rulesets.to_api_payload("main", resolved)
    assert payload["rules"] == []


def test_to_api_payload_raises_explicit_error_when_pull_requests_unresolved():
    unresolved = BranchPolicy(pull_requests=None)
    with pytest.raises(ValueError, match="pull_requests"):
        rulesets.to_api_payload("main", unresolved)


def test_to_api_payload_forces_active_enforcement_even_when_current_is_evaluate():
    """enforcement (active/evaluate/disabled) is owned metadata (ADR 0004) -- a ruleset dry-run-ed
    to "evaluate" (or turned off entirely via "disabled") is exactly the false-compliance case
    Task 1 closes, so an unrelated apply must always flip it back to "active", never preserve it."""
    resolved = BranchPolicy(
        pull_requests=PullRequestPolicy(required=False, approvals=0, code_owner_review=False),
        linear_history=True,
    )
    current_raw = {"id": 7, "enforcement": "evaluate", "rules": []}
    payload = rulesets.to_api_payload("main", resolved, current_raw=current_raw)
    assert payload["enforcement"] == "active"


def test_to_api_payload_defaults_enforcement_active_on_first_creation():
    resolved = BranchPolicy(
        pull_requests=PullRequestPolicy(required=False, approvals=0, code_owner_review=False)
    )
    payload = rulesets.to_api_payload("main", resolved, current_raw=None)
    assert payload["enforcement"] == "active"


def test_to_api_payload_strips_bypass_actors_even_when_current_has_some():
    """bypass_actors is owned metadata (ADR 0004) -- a bypass actor is exactly the false-
    compliance case Task 1 closes (it lets someone route around every rule below), so an unrelated
    apply must always clear it, never preserve it."""
    resolved = BranchPolicy(
        pull_requests=PullRequestPolicy(required=False, approvals=0, code_owner_review=False),
        linear_history=True,
    )
    current_raw = {
        "id": 7,
        "bypass_actors": [{"actor_id": 1, "actor_type": "Team", "bypass_mode": "always"}],
        "rules": [],
    }
    payload = rulesets.to_api_payload("main", resolved, current_raw=current_raw)
    assert payload["bypass_actors"] == []


def test_to_api_payload_clears_conditions_exclude_even_when_current_has_some():
    """conditions.ref_name.exclude is owned metadata (ADR 0004) -- an exclude pattern that covers
    repo-policy's own branch is exactly the false-compliance case Task 1 closes, so an unrelated
    apply must always clear it, never preserve it."""
    resolved = BranchPolicy(
        pull_requests=PullRequestPolicy(required=False, approvals=0, code_owner_review=False),
        linear_history=True,
    )
    current_raw = {
        "id": 7,
        "conditions": {"ref_name": {"include": ["refs/heads/main"], "exclude": ["refs/heads/main-bot"]}},
        "rules": [],
    }
    payload = rulesets.to_api_payload("main", resolved, current_raw=current_raw)
    assert payload["conditions"]["ref_name"]["exclude"] == []
    assert payload["conditions"]["ref_name"]["include"] == ["refs/heads/main"]


def test_to_api_payload_drops_extra_current_includes_even_when_present():
    """conditions.ref_name.include is owned metadata (ADR 0004) -- an extra include glob widens
    the ruleset beyond repo-policy's own branch, so an unrelated apply must always collapse it
    back down to exactly `refs/heads/{branch}`, never preserve the extra entry."""
    resolved = BranchPolicy(
        pull_requests=PullRequestPolicy(required=False, approvals=0, code_owner_review=False),
        linear_history=True,
    )
    current_raw = {
        "id": 7,
        "conditions": {"ref_name": {"include": ["refs/heads/main", "refs/heads/release/*"], "exclude": []}},
        "rules": [],
    }
    payload = rulesets.to_api_payload("main", resolved, current_raw=current_raw)
    assert payload["conditions"]["ref_name"]["include"] == ["refs/heads/main"]


def test_to_api_payload_defaults_conditions_on_first_creation():
    resolved = BranchPolicy(
        pull_requests=PullRequestPolicy(required=False, approvals=0, code_owner_review=False)
    )
    payload = rulesets.to_api_payload("main", resolved, current_raw=None)
    assert payload["conditions"] == {"ref_name": {"include": ["refs/heads/main"], "exclude": []}}


def test_to_api_payload_preserves_unmanaged_rule_types():
    """repo-policy has no schema for rule types like commit_message_pattern or merge_queue --
    a human-added rule of an unrecognized type must survive a full-object replace triggered by an
    unrelated, modeled field changing, not be silently dropped because `rules` is rebuilt from
    scratch each time."""
    resolved = BranchPolicy(
        pull_requests=PullRequestPolicy(required=False, approvals=0, code_owner_review=False),
        linear_history=True,
    )
    current_raw = {
        "id": 7,
        "rules": [
            {"type": "commit_message_pattern", "parameters": {"pattern": "^JIRA-"}},
            {"type": "merge_queue", "parameters": {}},
        ],
    }
    payload = rulesets.to_api_payload("main", resolved, current_raw=current_raw)
    rule_types = {rule["type"] for rule in payload["rules"]}
    assert "commit_message_pattern" in rule_types
    assert "merge_queue" in rule_types
    assert "required_linear_history" in rule_types  # the modeled change is still applied


def test_to_api_payload_drops_stale_unmanaged_rule_when_no_longer_present_in_current():
    """Sanity check for the other direction: to_api_payload only carries forward whatever is
    ACTUALLY in current_raw right now -- it's not accumulating rules across calls."""
    resolved = BranchPolicy(
        pull_requests=PullRequestPolicy(required=False, approvals=0, code_owner_review=False)
    )
    payload = rulesets.to_api_payload("main", resolved, current_raw={"id": 7, "rules": []})
    assert payload["rules"] == []


def test_to_api_payload_defaults_bypass_actors_empty_on_first_creation():
    resolved = BranchPolicy(
        pull_requests=PullRequestPolicy(required=False, approvals=0, code_owner_review=False)
    )
    payload = rulesets.to_api_payload("main", resolved, current_raw=None)
    assert payload["bypass_actors"] == []


def test_to_api_payload_preserves_current_strict_required_status_checks_policy():
    resolved = BranchPolicy(
        pull_requests=PullRequestPolicy(required=False, approvals=0, code_owner_review=False),
        status_checks=StatusChecksPolicy(required=["build"]),
    )
    current_raw = {
        "id": 7,
        "rules": [
            {
                "type": "required_status_checks",
                "parameters": {
                    "required_status_checks": [{"context": "build"}],
                    "strict_required_status_checks_policy": True,
                },
            }
        ],
    }
    payload = rulesets.to_api_payload("main", resolved, current_raw=current_raw)
    sc_rule = next(r for r in payload["rules"] if r["type"] == "required_status_checks")
    assert sc_rule["parameters"]["strict_required_status_checks_policy"] is True
