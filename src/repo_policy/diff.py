from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import ValidationError

from repo_policy.models import (
    FIELD_SPECS,
    BranchPolicy,
    PullRequestPolicy,
    StatusChecksPolicy,
)

ChangeAction = Literal["add", "modify", "remove"]


class PolicyResolutionError(Exception):
    """Raised when resolve_desired() merges a declared policy with current GitHub state into a
    combination BranchPolicy's own validators reject (e.g. inheriting allow_fork_syncing=True
    from current while declaring lock_branch=False) -- model_copy() doesn't re-validate, so this
    is what actually catches it before the invalid combination reaches the GitHub API."""


# _FIELDS/_SCHEMA_DEFAULTS/_INVERTED_FIELDS are all derived from models.FIELD_SPECS (the single
# source of truth for these per-field facts) rather than hand-typed -- see FieldSpec's docstring
# for why that consolidation matters. status_checks' permissive default is None, not
# StatusChecksPolicy(required=[]): branch_protection.from_api/rulesets.from_api both represent "no
# status checks configured" as None, and the strict default must match that exact representation
# or a fully-compliant permissive branch shows permanent phantom drift. allow_force_push/
# allow_deletion/clear_restrictions have inverted polarity vs. every other field: False means a
# restriction IS present (force push blocked / a push-restriction allowlist exists), True means no
# restriction — the opposite of fields like linear_history, where False/empty means no rule
# exists. allow_fork_syncing is NOT inverted, despite superficially resembling these -- GitHub
# only honors allow_fork_syncing=true when lock_branch=true is also set (see models.py's
# _allow_fork_syncing_requires_lock_branch validator), so False/unset is the safe, always-stable
# default here, not True. Confirmed via live-repo verification -- see docs/test-strategy.md.
_FIELDS = tuple(spec.name for spec in FIELD_SPECS)
_SCHEMA_DEFAULTS: dict[str, Any] = {spec.name: spec.default for spec in FIELD_SPECS}
_INVERTED_FIELDS = {spec.name for spec in FIELD_SPECS if spec.inverted}


@dataclass(frozen=True)
class Change:
    field: str
    current_value: Any
    desired_value: Any
    action: ChangeAction


def resolve_desired(desired: BranchPolicy, current: BranchPolicy, *, strict: bool) -> BranchPolicy:
    """Fill in every undeclared (None) field: from `current` in managed-scope mode, or from the
    permissive schema default in strict mode. Every field ends up set to its intended value --
    except `status_checks`, whose own permissive value (`_SCHEMA_DEFAULTS["status_checks"]`) is
    deliberately `None`, matching how branch_protection.from_api/rulesets.from_api represent "no
    status checks configured"; `diff()`'s `_is_empty` already treats that `None` as empty, the
    same as every other field's permissive value. Re-validates the merged result (see
    PolicyResolutionError) since BranchPolicy.model_validate() re-runs every model_validator,
    unlike model_copy()."""
    resolved: dict[str, Any] = {}
    for field in _FIELDS:
        if field == "pull_requests":
            resolved[field] = _merge_pull_requests(
                desired.pull_requests, current.pull_requests, strict=strict
            )
            continue
        value = getattr(desired, field)
        if value is not None:
            resolved[field] = value
        elif strict:
            resolved[field] = _SCHEMA_DEFAULTS[field]
        else:
            resolved[field] = getattr(current, field)
    try:
        return BranchPolicy.model_validate(
            {"enforcement": desired.enforcement, "strict": desired.strict, **resolved}
        )
    except ValidationError as exc:
        raise PolicyResolutionError(
            "resolving declared policy against current GitHub state produced an invalid "
            f"combination: {exc}"
        ) from exc


def _merge_pull_requests(
    desired_pr: PullRequestPolicy | None, current_pr: PullRequestPolicy | None, *, strict: bool
) -> PullRequestPolicy:
    """Mirrors resolve_desired()'s top-level field-by-field merge one level deeper: only the
    sub-fields the user actually wrote in policy.yml (tracked via pydantic's model_fields_set)
    come from `desired_pr`; every other sub-field is preserved from `current_pr` in managed-scope
    mode, or reset to the permissive schema default in strict mode -- never silently substituted
    with PullRequestPolicy's own class defaults. `current_pr` is typed Optional to match
    BranchPolicy.pull_requests, though in practice branch_protection.from_api/rulesets.from_api
    always populate it concretely; None falls back to the schema default just like `strict` does."""
    schema_default = _SCHEMA_DEFAULTS["pull_requests"]
    effective_current = current_pr if current_pr is not None else schema_default
    if desired_pr is None:
        return schema_default if strict else effective_current
    declared_fields = desired_pr.model_fields_set
    merged: dict[str, Any] = {}
    for field_name in PullRequestPolicy.model_fields:
        if field_name in declared_fields:
            merged[field_name] = getattr(desired_pr, field_name)
        elif strict:
            merged[field_name] = getattr(schema_default, field_name)
        else:
            merged[field_name] = getattr(effective_current, field_name)
    return PullRequestPolicy(**merged)


def diff(desired: BranchPolicy, current: BranchPolicy) -> list[Change]:
    changes: list[Change] = []
    for field in _FIELDS:
        desired_value = getattr(desired, field)
        current_value = getattr(current, field)
        if _values_equal(field, desired_value, current_value):
            continue
        # Not raw-equal, but both may still be "empty" in a way that produces the identical API
        # payload -- e.g. status_checks=StatusChecksPolicy(required=[]) vs the None that
        # branch_protection.from_api/rulesets.from_api use for "nothing configured", or
        # pull_requests differing only in sub-fields that to_branch_protection/to_ruleset_rule
        # ignore once required=False. Comparing raw equality alone reports permanent phantom
        # drift and re-issues a no-op API call on every apply for either case.
        if _is_empty(field, desired_value) and _is_empty(field, current_value):
            continue
        # clear_restrictions is a "preserve whatever's there" declaration when False, not a
        # target state -- when current is already empty (clear_restrictions=True, no live
        # restriction to preserve), declaring False can never actually change the API payload
        # (branch_protection.to_api_payload's restrictions field collapses to None either way,
        # since there's nothing to carry forward), unlike every other inverted field, where an
        # empty current and a non-empty desired is a real, applicable change.
        if field == "clear_restrictions" and current_value is True:
            continue
        changes.append(
            Change(
                field=field,
                current_value=current_value,
                desired_value=desired_value,
                action=_classify_action(field, current_value, desired_value),
            )
        )
    return changes


def _values_equal(field: str, desired_value: Any, current_value: Any) -> bool:
    """Plain `==` for every field except status_checks, whose `required` is a semantically
    unordered set of contexts -- pydantic's default equality compares it positionally, so if
    GitHub's GET ever returns the same contexts in a different order than policy.yml declared
    them, raw equality reports a permanent phantom 'modify' that can never converge (every apply
    re-sends an identical-content-but-reordered payload, which GitHub may again return in yet
    another order)."""
    if (
        field == "status_checks"
        and isinstance(desired_value, StatusChecksPolicy)
        and isinstance(current_value, StatusChecksPolicy)
    ):
        return sorted(desired_value.required) == sorted(current_value.required)
    return bool(desired_value == current_value)


def _classify_action(field: str, current_value: Any, desired_value: Any) -> ChangeAction:
    if _is_empty(field, current_value):
        return "add"
    if _is_empty(field, desired_value):
        return "remove"
    return "modify"


def _is_empty(field: str, value: Any) -> bool:
    if field in _INVERTED_FIELDS:
        return value is True or value is None
    if value is None or value is False:
        return True
    if isinstance(value, (list, dict)) and not value:
        return True
    if isinstance(value, PullRequestPolicy):
        return value.required is False
    if isinstance(value, StatusChecksPolicy):
        return len(value.required) == 0
    return False
