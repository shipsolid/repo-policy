from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class _PolicyModel(BaseModel):
    """Base for every policy-schema model (declared policy.yml content and the internal
    snapshots derived from it -- not the raw dicts this codebase reads from the GitHub API,
    which stay plain dicts until translated). This is governance policy: a wrong guess about
    what an operator meant becomes wrong branch protection, so every model built on this fails
    closed instead of being permissive --

    - `extra="forbid"`: an unknown/misspelled field (`strcit`, `secret_scaning`) is rejected
      instead of silently ignored, so a typo doesn't quietly disable the rule the operator thought
      they were declaring.
    - `strict=True`: no implicit scalar coercion (`"no"` -> `False`, a numeric string -> `int`,
      `1` -> `True`) -- every translator that builds these models from parsed GitHub API JSON
      must therefore pass real `bool`/`int` values, which they already do (JSON parsing itself
      never produces stringy booleans/ints).
    - `frozen=True`: the same "never mutated in place" guarantee every model here already
      documented individually before this base class existed.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


class DismissalRestrictions(_PolicyModel):
    """Who's allowed to dismiss a pull request review -- GitHub's "Restrict who can dismiss pull
    request reviews". No `apps` field: unlike BypassPullRequestAllowances and branch-protection
    `restrictions`, GitHub's API only accepts users/teams here -- declaring `apps:` is rejected by
    `_PolicyModel`'s `extra="forbid"`, the same fail-closed treatment as any other unknown field,
    not a silent no-op. Must name at least one user or team when declared at all -- see
    docs/adrs/0005-nested-actor-list-fields.md for why an all-empty declaration is rejected
    outright instead of being sent to GitHub as-is."""

    users: list[str] = Field(default_factory=list)
    teams: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _reject_all_empty(self) -> DismissalRestrictions:
        if not (self.users or self.teams):
            raise ValueError(
                "dismissal_restrictions must name at least one user or team -- omit the field "
                "entirely instead of declaring an empty allow-list (which would be ambiguous "
                "between 'no restriction' and 'restrict to nobody')"
            )
        return self


class BypassPullRequestAllowances(_PolicyModel):
    """Who's allowed to bypass the pull-request requirement entirely -- GitHub's "Allow specified
    actors to bypass required pull requests". Unlike DismissalRestrictions, GitHub's API accepts
    apps here too -- the common case this exists for: letting a release bot or a tool like
    Dependabot merge without a human review. Same all-empty rejection as DismissalRestrictions,
    for the same reason."""

    users: list[str] = Field(default_factory=list)
    teams: list[str] = Field(default_factory=list)
    apps: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _reject_all_empty(self) -> BypassPullRequestAllowances:
        if not (self.users or self.teams or self.apps):
            raise ValueError(
                "bypass_pull_request_allowances must name at least one user, team, or app -- omit "
                "the field entirely instead of declaring an empty allow-list (which would be "
                "ambiguous between 'no bypass' and 'bypass restricted to nobody')"
            )
        return self


class PullRequestPolicy(_PolicyModel):
    """Point-in-time policy snapshot (declared, current, or resolved), never mutated in place
    anywhere in this codebase -- see _PolicyModel. Frozen makes every scalar field here hashable --
    but `dismissal_restrictions`/`bypass_pull_request_allowances` break that guarantee when
    populated (list-bearing nested models, the same way StatusChecksPolicy's own `required`
    already does; see that class's docstring for the same honest caveat). Change (diff.py) only
    ever needs this hashability with these two fields at their default `None`, so nothing
    currently breaks in practice -- full hashability here is best-effort, not a strict guarantee."""

    required: bool = True
    approvals: int = Field(default=1, ge=0, le=6)
    code_owner_review: bool = False
    dismiss_stale_reviews: bool = False
    require_last_push_approval: bool = False
    dismissal_restrictions: DismissalRestrictions | None = None
    bypass_pull_request_allowances: BypassPullRequestAllowances | None = None


class StatusChecksPolicy(_PolicyModel):
    """Frozen for the same immutability guarantee as PullRequestPolicy -- but `required` is a
    list, which stays unhashable regardless (pydantic's frozen-model __hash__ hashes each field
    value, and a list is never hashable), so instances of this model still can't be hashed. Only
    PullRequestPolicy's hashability was actually needed to fix Change's; this is immutability for
    its own sake, not a claim of full hashability."""

    required: list[str] = Field(default_factory=list)

    @field_validator("required")
    @classmethod
    def _reject_blank_and_duplicate_checks(cls, value: list[str]) -> list[str]:
        if any(not name.strip() for name in value):
            raise ValueError("status check names must not be blank or whitespace-only")
        counts: dict[str, int] = {}
        for name in value:
            counts[name] = counts.get(name, 0) + 1
        duplicates = {name for name, count in counts.items() if count > 1}
        if duplicates:
            raise ValueError(f"duplicate status check name(s): {', '.join(sorted(duplicates))}")
        return value


PERMISSIVE_PULL_REQUESTS = PullRequestPolicy(required=False, approvals=0, code_owner_review=False)


@dataclass(frozen=True)
class FieldSpec:
    """Describes one diffable BranchPolicy field: its permissive (no-op) default, whether its
    boolean polarity is inverted (False/None means "a restriction exists" rather than "no rule" --
    see diff._is_empty's docstring for why allow_force_push/allow_deletion/clear_restrictions are
    inverted and allow_fork_syncing is not, despite superficially resembling them), whether GitHub
    Rulesets can represent it at all, and its human-readable display label. Single source of truth
    for what used to be four independently hand-maintained tables (diff._FIELDS/_SCHEMA_DEFAULTS/
    _INVERTED_FIELDS, this module's own _RULESET_UNSUPPORTED_FIELDS) plus render._LABELS and a
    third hand-copy of the ruleset-unsupported set in tests/test_policies_parity.py -- exactly the
    kind of divergence between hand-copies that let the dismiss_stale_reviews/strict clobber bug
    ship undetected."""

    name: str
    default: Any
    inverted: bool = False
    ruleset_supported: bool = True
    label: str = ""


FIELD_SPECS: tuple[FieldSpec, ...] = (
    FieldSpec("pull_requests", PERMISSIVE_PULL_REQUESTS, label="Pull request requirements"),
    FieldSpec("status_checks", None, label="Required status checks"),
    FieldSpec("signed_commits", False, label="Signed commits"),
    FieldSpec("linear_history", False, label="Linear history"),
    FieldSpec("allow_force_push", True, inverted=True, label="Force pushes"),
    FieldSpec("allow_deletion", True, inverted=True, label="Branch deletion"),
    FieldSpec("enforce_admins", False, ruleset_supported=False, label="Admin enforcement"),
    FieldSpec(
        "required_conversation_resolution",
        False,
        ruleset_supported=False,
        label="Conversation resolution",
    ),
    FieldSpec("lock_branch", False, ruleset_supported=False, label="Branch lock"),
    FieldSpec("allow_fork_syncing", False, ruleset_supported=False, label="Fork syncing"),
    FieldSpec(
        "clear_restrictions",
        True,
        inverted=True,
        ruleset_supported=False,
        label="Push restrictions",
    ),
)

# field name -> its permissive (no-op) value under enforcement: ruleset. Derived from FIELD_SPECS
# (single source of truth) rather than hand-typed -- rulesets.from_api() constructs internal
# "current state" BranchPolicy objects with these exact values for each field below (never None)
# — the validator below must let that through unrejected, so it only rejects a *non-permissive*
# (actually-restrictive) value, not merely a non-None one. A human writing
# `enforce_admins: false` under `enforcement: ruleset` is a harmless no-op declaration and is
# allowed; `enforce_admins: true` is a real restriction with no ruleset equivalent and is rejected.
_RULESET_UNSUPPORTED_FIELDS: dict[str, bool] = {
    spec.name: spec.default for spec in FIELD_SPECS if not spec.ruleset_supported
}


class BranchPolicy(_PolicyModel):
    """Frozen for the same reason as PullRequestPolicy/StatusChecksPolicy: a point-in-time policy
    snapshot (declared, current, or resolved) that's never mutated in place anywhere in this
    codebase -- frozen makes that a guarantee instead of an unenforced convention."""

    enforcement: Literal["branch_protection", "ruleset"] = "branch_protection"
    strict: bool | None = None
    pull_requests: PullRequestPolicy | None = None
    status_checks: StatusChecksPolicy | None = None
    signed_commits: bool | None = None
    linear_history: bool | None = None
    allow_force_push: bool | None = None
    allow_deletion: bool | None = None
    enforce_admins: bool | None = None
    required_conversation_resolution: bool | None = None
    lock_branch: bool | None = None
    allow_fork_syncing: bool | None = None
    clear_restrictions: bool | None = None

    @model_validator(mode="after")
    def _reject_ruleset_unsupported_fields(self) -> BranchPolicy:
        if self.enforcement != "ruleset":
            return self
        set_fields = [
            name
            for name, permissive in _RULESET_UNSUPPORTED_FIELDS.items()
            if getattr(self, name) not in (None, permissive)
        ]
        if set_fields:
            raise ValueError(
                f"{', '.join(set_fields)} not supported under enforcement: ruleset "
                "(no GitHub Rulesets equivalent) -- use enforcement: branch_protection, "
                "or remove these fields"
            )
        return self

    @model_validator(mode="after")
    def _reject_ruleset_unsupported_pull_request_fields(self) -> BranchPolicy:
        """dismissal_restrictions/bypass_pull_request_allowances live nested inside
        `pull_requests`, not as their own top-level BranchPolicy field, so they can't be tracked
        by FIELD_SPECS/_RULESET_UNSUPPORTED_FIELDS above (those only ever do
        `getattr(self, name)` on a top-level field name). This is a deliberate, separate special
        case for the one nested field this codebase models -- not an oversight that
        generalizing FIELD_SPECS missed."""
        if self.enforcement != "ruleset" or self.pull_requests is None:
            return self
        set_fields = [
            name
            for name in ("dismissal_restrictions", "bypass_pull_request_allowances")
            if getattr(self.pull_requests, name) is not None
        ]
        if set_fields:
            raise ValueError(
                f"pull_requests.{', pull_requests.'.join(set_fields)} not supported under "
                "enforcement: ruleset (no GitHub Rulesets equivalent) -- use enforcement: "
                "branch_protection, or remove these fields"
            )
        return self

    @model_validator(mode="after")
    def _allow_fork_syncing_requires_lock_branch(self) -> BranchPolicy:
        if self.enforcement != "branch_protection":
            return self
        if self.allow_fork_syncing is True and self.lock_branch is not True:
            raise ValueError(
                "allow_fork_syncing: true requires lock_branch: true to also be declared -- "
                "GitHub silently resets allow_fork_syncing back to false whenever lock_branch is "
                "false (confirmed via live-repo verification, see docs/test-strategy.md)"
            )
        return self


def permissive_branch_policy(
    enforcement: Literal["branch_protection", "ruleset"], *, signed_commits: bool = False
) -> BranchPolicy:
    """The fully-permissive ("nothing configured") BranchPolicy for a branch with no existing
    protection/ruleset -- used by branch_protection.from_api(None) and rulesets.from_api(None) as
    their "no current state" baseline. `signed_commits` is a real, separately-fetched fact (via
    GitHubClient.get_required_signatures for branch_protection; hardcoded False for rulesets,
    since "no ruleset exists" already means no required_signatures rule), not a schema default, so
    it's a parameter rather than sourced from FIELD_SPECS. Built from FIELD_SPECS so it can't
    drift from diff._SCHEMA_DEFAULTS / resolve_desired()'s own strict-mode defaults. Uses
    model_validate() over a plain dict (the same pattern diff.resolve_desired() uses to build a
    BranchPolicy from a dynamically-assembled dict) rather than a filtered **splat alongside
    explicit keywords, so a future FieldSpec named "enforcement" or "signed_commits" can't
    collide with the explicit values below and raise TypeError: got multiple values for keyword
    argument -- dict-key-overwrite means the explicit value always wins instead."""
    fields: dict[str, Any] = {spec.name: spec.default for spec in FIELD_SPECS}
    fields["enforcement"] = enforcement
    fields["signed_commits"] = signed_commits
    return BranchPolicy.model_validate(fields)


class RepoSettingsPolicy(_PolicyModel):
    """Frozen for the same "never mutated in place" reason as BranchPolicy."""

    delete_branch_on_merge: bool | None = None
    allow_update_branch: bool | None = None
    vulnerability_alerts: bool | None = None
    automated_security_fixes: bool | None = None
    private_vulnerability_reporting: bool | None = None
    secret_scanning: bool | None = None
    secret_scanning_push_protection: bool | None = None

    @model_validator(mode="after")
    def _automated_security_fixes_requires_vulnerability_alerts(self) -> RepoSettingsPolicy:
        if self.automated_security_fixes is True and self.vulnerability_alerts is not True:
            raise ValueError(
                "automated_security_fixes: true requires vulnerability_alerts: true to also be "
                "declared -- GitHub rejects enabling Dependabot security updates before Dependabot "
                "alerts are enabled"
            )
        return self

    @model_validator(mode="after")
    def _secret_scanning_push_protection_requires_secret_scanning(self) -> RepoSettingsPolicy:
        if self.secret_scanning_push_protection is True and self.secret_scanning is not True:
            raise ValueError(
                "secret_scanning_push_protection: true requires secret_scanning: true to also be "
                "declared -- GitHub rejects enabling push protection before secret scanning is "
                "enabled"
            )
        return self


class PolicyConfig(_PolicyModel):
    """Frozen for the same "never mutated in place" reason as BranchPolicy -- unhashable
    regardless (its `branches` dict field is never hashable), the same documented limitation as
    StatusChecksPolicy."""

    version: int
    strict: bool = False
    branches: dict[str, BranchPolicy]
    repo_settings: RepoSettingsPolicy | None = None

    @field_validator("version")
    @classmethod
    def _version_must_be_supported(cls, value: int) -> int:
        if value != 1:
            raise ValueError(f"unsupported policy version: {value} (only version 1 is supported)")
        return value


def effective_strict(config: PolicyConfig, branch: str) -> bool:
    """A branch's own `strict` always wins; otherwise inherit the top-level default."""
    branch_policy = config.branches[branch]
    return branch_policy.strict if branch_policy.strict is not None else config.strict
