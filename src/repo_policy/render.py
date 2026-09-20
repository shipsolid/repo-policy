from __future__ import annotations

from repo_policy.diff import _FIELDS, Change
from repo_policy.models import FIELD_SPECS
from repo_policy.repo_settings import RepoSettingsResult

_SYMBOLS = {"add": "+", "modify": "~", "remove": "-"}

# Derived from models.FIELD_SPECS (single source of truth) rather than hand-typed a second time.
_LABELS = {spec.name: spec.label for spec in FIELD_SPECS}

# apply._branch_changes / rulesets.metadata_changes emit Change objects for these fields too --
# they describe the owned ruleset object itself (ADR 0004), not a BranchPolicy field, so they have
# no FIELD_SPECS entry to derive a label from.
_RULESET_METADATA_LABELS = {
    "ruleset_enforcement": "Ruleset enforcement",
    "ruleset_target": "Ruleset target",
    "ruleset_conditions": "Ruleset branch scope",
    "ruleset_bypass_actors": "Ruleset bypass actors",
    "ruleset_effectiveness": "Ruleset effective on branch",
}
_LABELS |= _RULESET_METADATA_LABELS

_REPO_SETTINGS_LABELS = {
    "delete_branch_on_merge": "Delete branch on merge",
    "allow_update_branch": "Allow update branch",
    "vulnerability_alerts": "Dependabot alerts",
    "automated_security_fixes": "Dependabot security updates",
    "private_vulnerability_reporting": "Private vulnerability reporting",
    "secret_scanning": "Secret scanning",
    "secret_scanning_push_protection": "Secret scanning push protection",
}


def render_plan(repo: str, branch: str, changes: list[Change]) -> str:
    changed_fields = {change.field for change in changes}
    lines = [f"Repository: {repo}", f"Branch: {branch}", ""]

    for field in _FIELDS:
        if field not in changed_fields:
            lines.append(f"✓ {_LABELS.get(field, field)}")

    for change in changes:
        symbol = _SYMBOLS[change.action]
        label = _LABELS.get(change.field, change.field)
        lines.append(f"{symbol} {label:<28} {change.current_value} → {change.desired_value}")

    lines.append("")
    if not changes:
        lines.append("No changes required.")
    else:
        noun = "change" if len(changes) == 1 else "changes"
        lines.append(f"{len(changes)} {noun} required.")

    return "\n".join(lines)


def render_repo_settings(repo: str, result: RepoSettingsResult) -> str:
    lines = [f"Repository: {repo}", "Repo-level settings:", ""]

    for change in result.changes:
        symbol = _SYMBOLS[change.action]
        label = _REPO_SETTINGS_LABELS.get(change.field, change.field)
        lines.append(f"{symbol} {label:<28} {change.current_value} → {change.desired_value}")

    for field_name in result.unavailable:
        label = _REPO_SETTINGS_LABELS.get(field_name, field_name)
        lines.append(f"? {label:<28} unavailable on this repository")

    lines.append("")
    # Based purely on result.changes, not "changes or unavailable" -- an unavailable-only result
    # (a declared field that's ineligible on this repo, no actual drift) already got its own "?"
    # warning line above; claiming "0 changes required." directly beneath it reads as
    # self-contradictory even though the CLI still exits with drift status for that case.
    if not result.changes:
        lines.append("No repo-level setting changes required.")
    else:
        noun = "change" if len(result.changes) == 1 else "changes"
        lines.append(f"{len(result.changes)} {noun} required.")

    return "\n".join(lines)
