from __future__ import annotations

from pydantic import ValidationError

from repo_policy.diff import Change, PolicyResolutionError
from repo_policy.models import _RULESET_UNSUPPORTED_FIELDS, BranchPolicy, permissive_branch_policy
from repo_policy.policies import pull_requests, status_checks


def ruleset_name(branch: str) -> str:
    return f"repo-policy:{branch}"


# Every rule type to_api_payload actually builds. GitHub Rulesets support many more (e.g.
# commit_message_pattern, tag_name_pattern, merge_queue, workflows) that repo-policy has no
# schema for -- a human-added rule of one of those types must survive a full-object replace
# triggered by an unrelated, modeled field changing, not be silently dropped.
_MANAGED_RULE_TYPES = {
    "pull_request",
    "required_status_checks",
    "required_signatures",
    "required_linear_history",
    "non_fast_forward",
    "deletion",
}


def from_api(data: dict | None) -> BranchPolicy:
    # enforce_admins/required_conversation_resolution/lock_branch/allow_fork_syncing/
    # clear_restrictions have no GitHub Rulesets equivalent and are rejected for
    # enforcement: ruleset by BranchPolicy's model validator (models.py) -- hardcoded (via
    # models.FIELD_SPECS, the single source of truth for these permissive values) so
    # resolve_desired()/diff() always report zero drift for them on a ruleset-enforced branch, in
    # every mode.
    if data is None:
        return permissive_branch_policy("ruleset")
    rules_by_type = {rule["type"]: rule for rule in data.get("rules", [])}
    try:
        return BranchPolicy(
            enforcement="ruleset",
            pull_requests=pull_requests.from_ruleset_rule(rules_by_type.get("pull_request")),
            status_checks=status_checks.from_ruleset_rule(rules_by_type.get("required_status_checks")),
            signed_commits="required_signatures" in rules_by_type,
            linear_history="required_linear_history" in rules_by_type,
            allow_force_push="non_fast_forward" not in rules_by_type,
            allow_deletion="deletion" not in rules_by_type,
            **_RULESET_UNSUPPORTED_FIELDS,
        )
    except ValidationError as exc:
        # e.g. a live ruleset's pull_request rule with required_approving_review_count outside
        # PullRequestPolicy.approvals' 0..6 range (models.py) -- a direct API write, a future
        # GitHub product change, or a value repo-policy itself wrote before that constraint
        # existed. Nothing above cli.py catches a raw ValidationError; wrapping it as
        # PolicyResolutionError (already handled by cli.py) avoids crashing with Python's default
        # exit code 1, which would collide with EXIT_DRIFT -- the same reasoning as
        # branch_protection.from_api's identical try/except.
        raise PolicyResolutionError(
            f"GitHub's current ruleset state for this branch is internally inconsistent and "
            f"could not be parsed: {exc}"
        ) from exc


def metadata_changes(branch: str, data: dict | None) -> list[Change]:
    """Detects when an existing repo-policy-owned ruleset (`data`, its GET payload) has drifted
    from the canonical, always-active, exactly-scoped, no-bypass shape `to_api_payload()` always
    builds -- the read-side counterpart of that canonicalization, and the mechanism that turns a
    disabled/evaluate ruleset, a wrong target, a missing/excluded branch condition, or a bypass
    actor into reported drift instead of silent false compliance (see ADR 0004: a ruleset named
    `repo-policy:{branch}` is fully owned, so none of these values are ever treated as a human's
    intentional customization to preserve).

    `data is None` means the ruleset doesn't exist yet -- an "add", already handled by the
    existing creation flow in apply.py -- so there's nothing to diff here."""
    if data is None:
        return []
    changes: list[Change] = []

    enforcement = data.get("enforcement")
    if enforcement != "active":
        changes.append(
            Change(
                field="ruleset_enforcement", current_value=enforcement, desired_value="active", action="modify"
            )
        )

    target = data.get("target")
    if target != "branch":
        changes.append(
            Change(field="ruleset_target", current_value=target, desired_value="branch", action="modify")
        )

    own_ref = f"refs/heads/{branch}"
    ref_name = data.get("conditions", {}).get("ref_name", {})
    include = ref_name.get("include", [])
    exclude = ref_name.get("exclude", [])
    if include != [own_ref] or exclude:
        changes.append(
            Change(
                field="ruleset_conditions",
                current_value={"include": include, "exclude": exclude},
                desired_value={"include": [own_ref], "exclude": []},
                action="modify",
            )
        )

    bypass_actors = data.get("bypass_actors", [])
    if bypass_actors:
        changes.append(
            Change(
                field="ruleset_bypass_actors", current_value=bypass_actors, desired_value=[], action="modify"
            )
        )

    return changes


def to_api_payload(branch: str, resolved: BranchPolicy, current_raw: dict | None = None) -> dict:
    """`resolved` must already have every modeled field filled in (see diff.resolve_desired).
    Rulesets are fully owned by repo-policy once named (ADR 0004), so every OWNED metadata field --
    `target`, `enforcement`, `conditions.ref_name` (include/exclude), and `bypass_actors` -- is
    always rebuilt to its canonical, always-active, exactly-scoped, no-bypass shape, regardless of
    whatever enforcement/target/conditions/bypass_actors currently sit on GitHub; preserving any of
    those instead is exactly the false-compliance gap `metadata_changes()` (above) exists to close.
    strict_required_status_checks_policy and any rule of an unmodeled type (see
    _MANAGED_RULE_TYPES) are a different case: repo-policy has no modeled field for them at all
    (see status_checks.to_ruleset_rule for the first), so `current_raw` (the ruleset's current GET
    payload, or None on first creation) is still threaded through to preserve those, the same as
    before."""
    if resolved.pull_requests is None:
        raise ValueError(
            "resolved.pull_requests must not be None; pass a BranchPolicy produced by "
            "diff.resolve_desired(), which always fills every modeled field"
        )
    current_raw = current_raw or {}
    current_rules_by_type = {rule["type"]: rule for rule in current_raw.get("rules", [])}
    # Carry forward any existing rule of a type repo-policy doesn't model at all, verbatim.
    rules: list[dict] = [
        rule for rule in current_raw.get("rules", []) if rule["type"] not in _MANAGED_RULE_TYPES
    ]

    pr_rule = pull_requests.to_ruleset_rule(
        resolved.pull_requests, current=current_rules_by_type.get("pull_request")
    )
    if pr_rule is not None:
        rules.append(pr_rule)

    sc_rule = status_checks.to_ruleset_rule(
        resolved.status_checks, current=current_rules_by_type.get("required_status_checks")
    )
    if sc_rule is not None:
        rules.append(sc_rule)

    if resolved.signed_commits:
        rules.append({"type": "required_signatures"})
    if resolved.linear_history:
        rules.append({"type": "required_linear_history"})
    if resolved.allow_force_push is False:
        rules.append({"type": "non_fast_forward"})
    if resolved.allow_deletion is False:
        rules.append({"type": "deletion"})

    return {
        "name": ruleset_name(branch),
        "target": "branch",
        "enforcement": "active",
        "conditions": {"ref_name": {"include": [f"refs/heads/{branch}"], "exclude": []}},
        "rules": rules,
        "bypass_actors": [],
    }
