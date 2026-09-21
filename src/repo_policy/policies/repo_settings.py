from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from repo_policy.models import RepoSettingsPolicy

RepoSettingAction = Literal["add", "modify", "remove"]

_FLAT_FIELDS = ("delete_branch_on_merge", "allow_update_branch")
_SECURITY_AND_ANALYSIS_FIELDS = ("secret_scanning", "secret_scanning_push_protection")


@dataclass(frozen=True)
class RepoSettingChange:
    field: str
    current_value: Any
    desired_value: Any
    action: RepoSettingAction


def _classify(current_value: bool, desired_value: bool) -> RepoSettingAction:
    if current_value is False and desired_value is True:
        return "add"
    if current_value is True and desired_value is False:
        return "remove"
    return "modify"


def diff_flat_settings(current_repo: dict, desired: RepoSettingsPolicy) -> list[RepoSettingChange]:
    """delete_branch_on_merge / allow_update_branch -- both bare top-level booleans on the
    GET /repos/{owner}/{repo} response, matched 1:1 by PATCH /repos/{owner}/{repo}."""
    changes: list[RepoSettingChange] = []
    for field_name in _FLAT_FIELDS:
        desired_value = getattr(desired, field_name)
        if desired_value is None:
            continue  # not declared -- managed-scope: don't touch
        current_value = bool(current_repo.get(field_name, False))
        if current_value != desired_value:
            changes.append(
                RepoSettingChange(
                    field_name,
                    current_value,
                    desired_value,
                    _classify(current_value, desired_value),
                )
            )
    return changes


def to_flat_settings_payload(changes: list[RepoSettingChange]) -> dict:
    return {change.field: change.desired_value for change in changes}


def diff_security_and_analysis(
    current_repo: dict, desired: RepoSettingsPolicy
) -> tuple[list[RepoSettingChange], list[str]]:
    """secret_scanning / secret_scanning_push_protection -- nested under
    security_and_analysis.<field>.status ("enabled"/"disabled") on the repo GET response.

    The whole security_and_analysis block is absent from the response (current_repo.get(...) is
    None, not merely an empty dict) when the authenticated token lacks permission to see this
    field -- e.g. a fine-grained PAT scoped to Administration: Read-only gets no
    security_and_analysis key at all, while a more broadly-scoped token sees the real block. That
    case is structurally undeterminable and must never be conflated with the block being present
    but a sub-key legitimately absent/off, which GitHub documents as meaning 'disabled' and is
    still treated as a normal, diffable state. Returns (changes, unavailable_field_names): each
    declared field routes to `unavailable` when the whole block is missing, or to `changes`
    otherwise -- mirroring how diff_toggle's current_value=None and plan_repo_settings'
    private_vulnerability_reporting handling already keep 'cannot determine' separate from a real
    diff (see repo_settings.py's plan_repo_settings and RepoSettingsResult.compliant)."""
    security_block = current_repo.get("security_and_analysis")
    block_absent = security_block is None
    security = security_block or {}
    changes: list[RepoSettingChange] = []
    unavailable: list[str] = []
    for field_name in _SECURITY_AND_ANALYSIS_FIELDS:
        desired_value = getattr(desired, field_name)
        if desired_value is None:
            continue
        if block_absent:
            unavailable.append(field_name)
            continue
        current_status = (security.get(field_name) or {}).get("status")
        current_value = current_status == "enabled"
        if current_value != desired_value:
            changes.append(
                RepoSettingChange(
                    field_name,
                    current_value,
                    desired_value,
                    _classify(current_value, desired_value),
                )
            )
    return changes, unavailable


def to_security_and_analysis_payload(changes: list[RepoSettingChange]) -> dict:
    return {
        change.field: {"status": "enabled" if change.desired_value else "disabled"}
        for change in changes
    }


def diff_toggle(
    field_name: str, current_value: bool | None, desired_value: bool | None
) -> list[RepoSettingChange]:
    """vulnerability_alerts / automated_security_fixes / private_vulnerability_reporting -- each
    is a single independent boolean fetched from its own GET endpoint, not a shared payload shape
    like the two functions above. current_value=None means 'unavailable' (see repo_settings.py) and
    never produces a Change -- there's nothing to diff against."""
    if desired_value is None or current_value is None:
        return []
    if current_value == desired_value:
        return []
    return [
        RepoSettingChange(
            field_name, current_value, desired_value, _classify(current_value, desired_value)
        )
    ]
