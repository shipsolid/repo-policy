import pytest

from repo_policy.diff import Change, PolicyResolutionError, diff, resolve_desired
from repo_policy.models import BranchPolicy, PullRequestPolicy, StatusChecksPolicy

PERMISSIVE = BranchPolicy(
    pull_requests=PullRequestPolicy(required=False, approvals=0, code_owner_review=False),
    status_checks=None,
    signed_commits=False,
    linear_history=False,
    allow_force_push=True,
    allow_deletion=True,
    enforce_admins=False,
    required_conversation_resolution=False,
    lock_branch=False,
    allow_fork_syncing=False,
    clear_restrictions=True,
)


def test_diff_empty_when_equal():
    assert diff(PERMISSIVE, PERMISSIVE) == []


def test_diff_detects_modify():
    desired = PERMISSIVE.model_copy(update={"linear_history": True})
    changes = diff(desired, PERMISSIVE)
    assert len(changes) == 1
    assert changes[0].field == "linear_history"
    assert changes[0].action == "add"


def test_diff_detects_approvals_increase_as_modify():
    current = PERMISSIVE.model_copy(
        update={
            "pull_requests": PullRequestPolicy(required=True, approvals=1, code_owner_review=False)
        }
    )
    desired = PERMISSIVE.model_copy(
        update={
            "pull_requests": PullRequestPolicy(required=True, approvals=2, code_owner_review=False)
        }
    )
    changes = diff(desired, current)
    assert len(changes) == 1
    assert changes[0].field == "pull_requests"
    assert changes[0].action == "modify"


def test_diff_detects_remove():
    current = PERMISSIVE.model_copy(update={"allow_force_push": False})
    desired = PERMISSIVE.model_copy(update={"allow_force_push": True})
    changes = diff(desired, current)
    assert len(changes) == 1
    assert changes[0].action == "remove"


def test_allow_fork_syncing_normal_polarity_add_when_paired_with_lock_branch():
    """allow_fork_syncing is normal polarity, NOT inverted like allow_force_push/allow_deletion --
    False/unset is the stable, always-safe default (see models.py's
    _allow_fork_syncing_requires_lock_branch validator for why True alone is rejected at parse
    time: GitHub silently discards allow_fork_syncing=true unless lock_branch=true is set in the
    same state, confirmed via live-repo verification -- see docs/test-strategy.md). This test
    exercises diff()'s classification for the one combination that's actually valid."""
    current = PERMISSIVE.model_copy(update={"lock_branch": True})
    desired = PERMISSIVE.model_copy(update={"lock_branch": True, "allow_fork_syncing": True})
    changes = diff(desired, current)
    assert len(changes) == 1
    assert changes[0].field == "allow_fork_syncing"
    assert changes[0].action == "add"


def test_resolve_desired_managed_scope_inherits_current_for_unset_fields():
    desired = BranchPolicy(linear_history=True)  # everything else left unset
    current = PERMISSIVE.model_copy(
        update={
            "pull_requests": PullRequestPolicy(required=True, approvals=3, code_owner_review=True)
        }
    )
    resolved = resolve_desired(desired, current, strict=False)
    assert resolved.linear_history is True
    assert resolved.pull_requests == PullRequestPolicy(
        required=True, approvals=3, code_owner_review=True
    )
    changes = diff(resolved, current)
    assert len(changes) == 1
    assert changes[0].field == "linear_history"


def test_resolve_desired_strict_uses_schema_defaults_for_unset_fields():
    desired = BranchPolicy(linear_history=True)
    current = BranchPolicy(
        pull_requests=PullRequestPolicy(required=True, approvals=3, code_owner_review=True),
        status_checks=StatusChecksPolicy(required=["build"]),
        signed_commits=True,
        linear_history=False,
        allow_force_push=False,
        allow_deletion=False,
    )
    resolved = resolve_desired(desired, current, strict=True)
    assert resolved.pull_requests == PullRequestPolicy(
        required=False, approvals=0, code_owner_review=False
    )
    assert resolved.status_checks is None
    assert resolved.signed_commits is False
    assert resolved.allow_force_push is True
    assert resolved.allow_deletion is True
    # strict still respects fields the branch DID declare
    assert resolved.linear_history is True


def test_resolve_desired_raises_when_managed_scope_merge_produces_invalid_combination():
    """model_copy() alone wouldn't re-validate; this proves resolve_desired() catches the same
    invalid allow_fork_syncing/lock_branch combination models.py's validator rejects at parse
    time, even when it only arises from merging declared + current state."""
    desired = BranchPolicy(lock_branch=False)  # allow_fork_syncing left unset
    current = PERMISSIVE.model_copy(update={"lock_branch": True, "allow_fork_syncing": True})
    with pytest.raises(PolicyResolutionError):
        resolve_desired(desired, current, strict=False)


def test_resolve_desired_managed_scope_merges_pull_requests_field_by_field():
    """Declaring only `approvals` must not silently reset code_owner_review/dismiss_stale_reviews/
    require_last_push_approval to pydantic's class defaults -- managed-scope mode preserves
    whatever current already has for anything not explicitly declared, at the pull_requests
    sub-field level too, not just at the top level."""
    desired = BranchPolicy(pull_requests=PullRequestPolicy(approvals=2))
    current = PERMISSIVE.model_copy(
        update={
            "pull_requests": PullRequestPolicy(
                required=True,
                approvals=1,
                code_owner_review=True,
                dismiss_stale_reviews=True,
                require_last_push_approval=True,
            )
        }
    )
    resolved = resolve_desired(desired, current, strict=False)
    assert resolved.pull_requests == PullRequestPolicy(
        required=True,
        approvals=2,
        code_owner_review=True,
        dismiss_stale_reviews=True,
        require_last_push_approval=True,
    )


def test_resolve_desired_strict_merges_pull_requests_field_by_field_with_schema_defaults():
    desired = BranchPolicy(pull_requests=PullRequestPolicy(approvals=2))
    current = PERMISSIVE.model_copy(
        update={
            "pull_requests": PullRequestPolicy(required=True, approvals=1, code_owner_review=True)
        }
    )
    resolved = resolve_desired(desired, current, strict=True)
    assert resolved.pull_requests == PullRequestPolicy(
        required=False,
        approvals=2,
        code_owner_review=False,
        dismiss_stale_reviews=False,
        require_last_push_approval=False,
    )


def test_change_is_hashable_even_with_compound_field_values():
    """Change is @dataclass(frozen=True), which auto-derives __hash__ from all fields -- but that
    only actually works if every field value is itself hashable. pull_requests/status_checks
    changes carry PullRequestPolicy/StatusChecksPolicy instances as current/desired_value."""
    change = Change(
        field="pull_requests",
        current_value=PullRequestPolicy(),
        desired_value=PullRequestPolicy(approvals=2),
        action="modify",
    )
    hash(change)  # must not raise TypeError: unhashable type


def test_is_empty_does_not_misfire_on_unrelated_object_with_a_required_attribute():
    """_is_empty used to dispatch via hasattr(value, "required") -- any future value type that
    happens to expose a .required attribute with different emptiness semantics would have been
    silently misclassified. Regression-proofing via a minimal stand-in object with a `.required`
    that's neither a bool nor a list."""

    class NotAPolicyValue:
        required = 42

    from repo_policy.diff import _is_empty

    assert _is_empty("some_field", NotAPolicyValue()) is False


def test_diff_treats_declared_empty_status_checks_as_equivalent_to_undeclared():
    """status_checks: {required: []} and status_checks entirely undeclared both produce the same
    API payload (status_checks.to_branch_protection/to_ruleset_rule both return None for an empty
    required list) -- diff() must not report drift just because one side is StatusChecksPolicy
    (required=[]) and the other is the None that branch_protection.from_api/rulesets.from_api use
    for "nothing configured". Without this, a declared-but-empty status_checks block reports
    permanent phantom drift and re-issues a no-op API call on every apply, forever."""
    current = PERMISSIVE.model_copy(update={"status_checks": None})
    desired = PERMISSIVE.model_copy(update={"status_checks": StatusChecksPolicy(required=[])})
    assert diff(desired, current) == []


def test_diff_treats_declared_not_required_pull_requests_as_equivalent_regardless_of_other_fields():
    """pull_requests: {required: false, approvals: 5} and the canonical 'nothing configured'
    PullRequestPolicy both produce the same API payload (to_branch_protection/to_ruleset_rule
    return None whenever required is False, regardless of approvals/code_owner_review) -- diff()
    must not report drift over sub-fields that are irrelevant once required is False."""
    current = PERMISSIVE.model_copy(
        update={
            "pull_requests": PullRequestPolicy(required=False, approvals=0, code_owner_review=False)
        }
    )
    desired = PERMISSIVE.model_copy(
        update={
            "pull_requests": PullRequestPolicy(required=False, approvals=5, code_owner_review=True)
        }
    )
    assert diff(desired, current) == []


def test_diff_reports_no_drift_for_clear_restrictions_false_when_nothing_to_preserve():
    """clear_restrictions=False means "preserve whatever restriction is currently there" -- but
    when current.clear_restrictions is True (no live restriction exists at all), there is nothing
    to preserve, so branch_protection.to_api_payload sends restrictions: None either way
    (confirmed: _restrictions_payload(None) is None regardless of resolved.clear_restrictions).
    Declaring clear_restrictions: false on such a branch must not report permanent phantom drift
    for a declared value that can never actually change GitHub's state."""
    current = PERMISSIVE.model_copy(update={"clear_restrictions": True})
    desired = PERMISSIVE.model_copy(update={"clear_restrictions": False})
    assert diff(desired, current) == []


def test_diff_still_reports_drift_clearing_a_restriction_that_actually_exists():
    """The other direction must still work: an existing restriction (current.clear_restrictions
    is False) really is removed when clear_restrictions: true is declared -- that's a genuine,
    reportable change, not the same "nothing to preserve" no-op as the test above."""
    current = PERMISSIVE.model_copy(update={"clear_restrictions": False})
    desired = PERMISSIVE.model_copy(update={"clear_restrictions": True})
    changes = diff(desired, current)
    assert len(changes) == 1
    assert changes[0].field == "clear_restrictions"
    assert changes[0].action == "remove"


def test_clear_restrictions_inverted_polarity_remove_when_clearing_an_existing_restriction():
    """clear_restrictions=False means an actual restriction exists (non-permissive); True means
    it's cleared (permissive) -- the same true-means-permissive polarity as allow_force_push/
    allow_deletion. Going from an existing restriction (current=False) to cleared (desired=True)
    is removing a restriction, not adding one."""
    current = PERMISSIVE.model_copy(update={"clear_restrictions": False})
    desired = PERMISSIVE.model_copy(update={"clear_restrictions": True})
    changes = diff(desired, current)
    assert len(changes) == 1
    assert changes[0].field == "clear_restrictions"
    assert changes[0].action == "remove"


def test_diff_treats_status_checks_as_equal_regardless_of_context_order():
    """StatusChecksPolicy.required is a semantically unordered set of contexts, but pydantic's
    default equality compares the list positionally. If GitHub's GET ever returns the same
    contexts in a different order than policy.yml declared them, diff() must not report a
    permanent phantom 'modify' that re-issues an identical-content-but-reordered API call on
    every single apply without ever converging."""
    current = PERMISSIVE.model_copy(
        update={"status_checks": StatusChecksPolicy(required=["build", "test"])}
    )
    desired = PERMISSIVE.model_copy(
        update={"status_checks": StatusChecksPolicy(required=["test", "build"])}
    )
    assert diff(desired, current) == []


def test_diff_still_detects_a_real_status_checks_content_change():
    current = PERMISSIVE.model_copy(
        update={"status_checks": StatusChecksPolicy(required=["build"])}
    )
    desired = PERMISSIVE.model_copy(
        update={"status_checks": StatusChecksPolicy(required=["build", "test"])}
    )
    changes = diff(desired, current)
    assert len(changes) == 1
    assert changes[0].field == "status_checks"
    assert changes[0].action == "modify"


def test_strict_mode_reports_no_drift_for_an_already_compliant_permissive_branch():
    """Regression test: found via live testing against a real repo. A branch with no status
    checks configured normalizes to status_checks=None (see branch_protection.from_api /
    rulesets.from_api), not StatusChecksPolicy(required=[]) -- the strict schema default must
    match that exact representation, or strict mode reports permanent phantom drift and issues
    an unnecessary API call on every single apply."""
    desired = PERMISSIVE.model_copy(update={"linear_history": True})
    current = PERMISSIVE.model_copy(update={"linear_history": True})
    resolved = resolve_desired(desired, current, strict=True)
    assert diff(resolved, current) == []
