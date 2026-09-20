"""Cross-backend parity guard.

branch_protection.py and rulesets.py each hand-write their own full-payload translation with no
shared source of truth (see the design spec's discussion of why they're separate modules). That
duplication is exactly how the dismiss_stale_reviews/strict clobber bug slipped in undetected:
a field handled by one backend had no representation in the other's tests. This file iterates
every field in diff._FIELDS and asserts both backends actually respond to it, so a future field
addition that's wired into only one translator fails loudly here instead of shipping silently.
"""

import pytest

from repo_policy.diff import _FIELDS
from repo_policy.models import FIELD_SPECS, BranchPolicy, PullRequestPolicy, StatusChecksPolicy
from repo_policy.policies import branch_protection, rulesets

PERMISSIVE = BranchPolicy(
    pull_requests=PullRequestPolicy(required=False, approvals=0, code_owner_review=False),
    status_checks=StatusChecksPolicy(required=[]),
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

RESTRICTIVE_VALUES = {
    "pull_requests": PullRequestPolicy(
        required=True,
        approvals=2,
        code_owner_review=True,
        dismiss_stale_reviews=True,
        require_last_push_approval=True,
    ),
    "status_checks": StatusChecksPolicy(required=["build"]),
    "signed_commits": True,
    "linear_history": True,
    "allow_force_push": False,
    "allow_deletion": False,
    "enforce_admins": True,
    "required_conversation_resolution": True,
    "lock_branch": True,
    "allow_fork_syncing": True,
    "clear_restrictions": False,
}

# signed_commits is deliberately excluded here: for the branch_protection backend it's handled
# by a separate GitHub endpoint (GitHubClient.set_required_signatures), never by to_api_payload —
# that path is covered by test_apply_branch_sets_signed_commits_separately instead.
#
# clear_restrictions is deliberately excluded too: this test calls to_api_payload with
# current_raw=None for both the baseline and the field-under-test, which means there is no existing
# `restrictions` value to preserve in either case -- clear_restrictions=True and =False both
# collapse to `restrictions: None` in the payload, so this generic comparison can't tell them apart
# (confirmed by actually running it: the assertion fails on real output, not a hypothetical). The
# real round-trip -- clearing an existing restriction vs. preserving one -- is covered directly in
# tests/test_policies_branch_protection.py's test_to_api_payload_forces_restrictions_null_when_clear_restrictions_true
# and test_to_api_payload_preserves_restrictions_when_clear_restrictions_false, both of which pass a
# non-empty current_raw so the distinction is actually observable.
BRANCH_PROTECTION_FIELDS = [f for f in _FIELDS if f not in ("signed_commits", "clear_restrictions")]

# enforce_admins/required_conversation_resolution/lock_branch/allow_fork_syncing/
# clear_restrictions have no GitHub Rulesets equivalent -- BranchPolicy's model validator
# (models.py) rejects setting them under enforcement: ruleset, so the ruleset backend never needs
# to represent them (see rulesets.from_api, which hardcodes each to its permissive constant
# instead of reading it). Sourced from models.FIELD_SPECS rather than hand-copied a third time, so
# this can't silently drift from models._RULESET_UNSUPPORTED_FIELDS.
RULESET_UNSUPPORTED_FIELDS = {spec.name for spec in FIELD_SPECS if not spec.ruleset_supported}
RULESET_FIELDS = [f for f in _FIELDS if f not in RULESET_UNSUPPORTED_FIELDS]


@pytest.mark.parametrize("field", BRANCH_PROTECTION_FIELDS)
def test_field_is_represented_by_branch_protection_backend(field):
    resolved = PERMISSIVE.model_copy(update={field: RESTRICTIVE_VALUES[field]})
    baseline = branch_protection.to_api_payload(PERMISSIVE, current_raw=None)
    payload = branch_protection.to_api_payload(resolved, current_raw=None)
    assert payload != baseline, f"branch_protection backend ignores field {field!r}"


@pytest.mark.parametrize("field", RULESET_FIELDS)
def test_field_is_represented_by_ruleset_backend(field):
    resolved = PERMISSIVE.model_copy(update={field: RESTRICTIVE_VALUES[field]})
    baseline_rules = rulesets.to_api_payload("main", PERMISSIVE)["rules"]
    payload_rules = rulesets.to_api_payload("main", resolved)["rules"]
    assert payload_rules != baseline_rules, f"ruleset backend ignores field {field!r}"
