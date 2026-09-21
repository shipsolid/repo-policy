from __future__ import annotations

from repo_policy.github_client import _actor_refs
from repo_policy.models import (
    PERMISSIVE_PULL_REQUESTS,
    BypassPullRequestAllowances,
    DismissalRestrictions,
    PullRequestPolicy,
)


def to_branch_protection(policy: PullRequestPolicy) -> dict | None:
    """dismiss_stale_reviews/require_last_push_approval/dismissal_restrictions/
    bypass_pull_request_allowances are all modeled directly on PullRequestPolicy now (the first
    two since commit 41e59cb, "model dismiss_stale_reviews and require_last_push_approval"; the
    last two following that exact same pattern). No `current` parameter anymore -- managed-scope
    passthrough for an undeclared field now happens one layer up, in diff.resolve_desired's
    per-sub-field merge, the same way it already does for every other PullRequestPolicy field."""
    if not policy.required:
        return None
    payload: dict[str, object] = {
        "required_approving_review_count": policy.approvals,
        "require_code_owner_reviews": policy.code_owner_review,
        "dismiss_stale_reviews": policy.dismiss_stale_reviews,
        "require_last_push_approval": policy.require_last_push_approval,
    }
    if policy.dismissal_restrictions is not None:
        payload["dismissal_restrictions"] = {
            "users": policy.dismissal_restrictions.users,
            "teams": policy.dismissal_restrictions.teams,
        }
    if policy.bypass_pull_request_allowances is not None:
        payload["bypass_pull_request_allowances"] = {
            "users": policy.bypass_pull_request_allowances.users,
            "teams": policy.bypass_pull_request_allowances.teams,
            "apps": policy.bypass_pull_request_allowances.apps,
        }
    return payload


def from_branch_protection(data: dict | None) -> PullRequestPolicy:
    if data is None:
        return PERMISSIVE_PULL_REQUESTS
    dismissal_refs = _actor_refs(data.get("dismissal_restrictions"))
    bypass_refs = _actor_refs(data.get("bypass_pull_request_allowances"))
    return PullRequestPolicy(
        required=True,
        approvals=data.get("required_approving_review_count", 0),
        code_owner_review=data.get("require_code_owner_reviews", False),
        dismiss_stale_reviews=data.get("dismiss_stale_reviews", False),
        require_last_push_approval=data.get("require_last_push_approval", False),
        dismissal_restrictions=(
            # dismissal_restrictions supports only users/teams, unlike
            # bypass_pull_request_allowances and branch-protection restrictions (both
            # apps-capable) -- _actor_refs always includes an "apps" key, so drop it here rather
            # than risk GitHub rejecting an unrecognized field on the next apply.
            DismissalRestrictions(users=dismissal_refs["users"], teams=dismissal_refs["teams"])
            if dismissal_refs is not None
            else None
        ),
        bypass_pull_request_allowances=(
            BypassPullRequestAllowances(**bypass_refs) if bypass_refs is not None else None
        ),
    )


def to_ruleset_rule(policy: PullRequestPolicy, current: dict | None = None) -> dict | None:
    """`current` is the existing rules-array entry of type "pull_request" (or None on first
    creation). required_review_thread_resolution has no modeled field -- distinct from
    required_conversation_resolution, which IS rejected outright for enforcement: ruleset by
    BranchPolicy's model validator (models.py) -- so it's read through from current state rather
    than reset to False on every apply, the same pattern as status_checks.to_ruleset_rule's
    strict_required_status_checks_policy. dismissal_restrictions/bypass_pull_request_allowances
    need no handling here at all -- GitHub Rulesets' pull_request rule type has no parameter for
    either, and BranchPolicy's own
    _reject_ruleset_unsupported_pull_request_fields validator (models.py) already guarantees
    `policy` can never carry a non-None value for them by the time this function runs on a
    ruleset-enforced branch."""
    if not policy.required:
        return None
    current_params = current.get("parameters", {}) if current is not None else {}
    return {
        "type": "pull_request",
        "parameters": {
            "required_approving_review_count": policy.approvals,
            "require_code_owner_review": policy.code_owner_review,
            "require_last_push_approval": policy.require_last_push_approval,
            "dismiss_stale_reviews_on_push": policy.dismiss_stale_reviews,
            "required_review_thread_resolution": current_params.get(
                "required_review_thread_resolution", False
            ),
        },
    }


def from_ruleset_rule(rule: dict | None) -> PullRequestPolicy:
    if rule is None:
        return PERMISSIVE_PULL_REQUESTS
    params = rule["parameters"]
    return PullRequestPolicy(
        required=True,
        approvals=params.get("required_approving_review_count", 0),
        code_owner_review=params.get("require_code_owner_review", False),
        dismiss_stale_reviews=params.get("dismiss_stale_reviews_on_push", False),
        require_last_push_approval=params.get("require_last_push_approval", False),
    )
