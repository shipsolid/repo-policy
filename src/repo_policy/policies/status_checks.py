from __future__ import annotations

from repo_policy.models import StatusChecksPolicy


def to_branch_protection(
    policy: StatusChecksPolicy | None, current: dict | None = None
) -> dict | None:
    """`current` is the branch's existing required_status_checks GET payload (or None on first
    creation). repo-policy doesn't model the 'require branches up to date' (strict) setting, so
    it's read through from current state rather than reset to False on every apply. Likewise for
    each check's `app_id` (a human-pinned "only this GitHub App may satisfy this check", keyed by
    context) -- read through per-context rather than reset to null (any app) on every apply; a
    newly-declared context with no matching current check defaults to null."""
    if policy is None or not policy.required:
        return None
    current = current or {}
    current_app_ids = {check["context"]: check.get("app_id") for check in current.get("checks", [])}
    return {
        "strict": current.get("strict", False),
        "checks": [
            {"context": name, "app_id": current_app_ids.get(name)} for name in policy.required
        ],
    }


def from_branch_protection(data: dict | None) -> StatusChecksPolicy | None:
    if data is None:
        return None
    contexts = data.get("contexts") or [check["context"] for check in data.get("checks", [])]
    if not contexts:
        return None
    # dict.fromkeys dedupes while preserving first-occurrence order -- the checks array can
    # contain two entries sharing a context but different app_id (e.g. mid-migration between CI
    # apps); without this, required ends up with that context listed twice.
    return StatusChecksPolicy(required=list(dict.fromkeys(contexts)))


def to_ruleset_rule(policy: StatusChecksPolicy | None, current: dict | None = None) -> dict | None:
    """`current` is the existing rules-array entry of type "required_status_checks" (or None on
    first creation). repo-policy doesn't model strict_required_status_checks_policy or
    do_not_enforce_on_create, so both are read through from current state rather than reset on
    every apply -- the ruleset-backend counterpart of to_branch_protection()'s `current`
    read-through, for the exact same reason. Likewise for each check's `integration_id` (the
    ruleset-schema equivalent of app_id, keyed by context) -- read through per-context and
    omitted entirely (not sent as null) for a context with no current pin, matching how this
    function already omits the key entirely when there's no current state at all."""
    if policy is None or not policy.required:
        return None
    current_params = current.get("parameters", {}) if current is not None else {}
    current_integration_ids = {
        check["context"]: check.get("integration_id")
        for check in current_params.get("required_status_checks", [])
    }
    required_status_checks = []
    for name in policy.required:
        entry: dict[str, object] = {"context": name}
        integration_id = current_integration_ids.get(name)
        if integration_id is not None:
            entry["integration_id"] = integration_id
        required_status_checks.append(entry)
    return {
        "type": "required_status_checks",
        "parameters": {
            "required_status_checks": required_status_checks,
            "strict_required_status_checks_policy": current_params.get(
                "strict_required_status_checks_policy", False
            ),
            "do_not_enforce_on_create": current_params.get("do_not_enforce_on_create", False),
        },
    }


def from_ruleset_rule(rule: dict | None) -> StatusChecksPolicy | None:
    if rule is None:
        return None
    checks = rule["parameters"].get("required_status_checks", [])
    if not checks:
        return None
    # dict.fromkeys dedupes while preserving first-occurrence order -- the ruleset-schema
    # counterpart of from_branch_protection's identical dedup above: this array can likewise
    # contain two entries sharing a context but different integration_id (e.g. mid-migration
    # between CI apps), and StatusChecksPolicy.required now rejects duplicate entries outright
    # (models.py), so reading back a GitHub ruleset actually in that transient state would
    # otherwise raise instead of just reporting the (deduped) current state.
    return StatusChecksPolicy(required=list(dict.fromkeys(check["context"] for check in checks)))
