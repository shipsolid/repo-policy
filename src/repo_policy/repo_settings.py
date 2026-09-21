from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from repo_policy.apply import ApplyJournalEntry, ApplyStatus, ApplySummary, PartialApplyError
from repo_policy.github_client import GitHubAPIError, GitHubClient
from repo_policy.models import PolicyConfig
from repo_policy.policies.repo_settings import (
    _FLAT_FIELDS,
    _SECURITY_AND_ANALYSIS_FIELDS,
    RepoSettingChange,
    diff_flat_settings,
    diff_security_and_analysis,
    diff_toggle,
    to_flat_settings_payload,
    to_security_and_analysis_payload,
)


@dataclass
class RepoSettingsResult:
    changes: list[RepoSettingChange] = field(default_factory=list)
    unavailable: list[str] = field(default_factory=list)
    applied: bool = False

    @property
    def compliant(self) -> bool:
        """Mirrors audit.AuditResult.compliant's shape: a declared field GitHub reports
        structurally ineligible (`unavailable`) must never read as compliant just because there's
        no pending Change left to apply -- that field can never actually be satisfied, so it's
        drift on its own, the same as a nonempty `changes`."""
        return not self.changes and not self.unavailable


def plan_repo_settings(client: GitHubClient, config: PolicyConfig) -> RepoSettingsResult:
    """Read-only: fetch current state and diff against policy.yml's repo_settings section. Makes
    zero API calls and returns an empty result when the section isn't declared at all."""
    desired = config.repo_settings
    if desired is None:
        return RepoSettingsResult()

    result = RepoSettingsResult()
    current_repo = client.get_repo()
    result.changes.extend(diff_flat_settings(current_repo, desired))
    result.changes.extend(diff_security_and_analysis(current_repo, desired))

    if desired.vulnerability_alerts is not None:
        current = client.get_vulnerability_alerts()
        result.changes.extend(
            diff_toggle("vulnerability_alerts", current, desired.vulnerability_alerts)
        )

    if desired.automated_security_fixes is not None:
        current = client.get_automated_security_fixes()
        result.changes.extend(
            diff_toggle("automated_security_fixes", current, desired.automated_security_fixes)
        )

    if desired.private_vulnerability_reporting is not None:
        current_pvr = client.get_private_vulnerability_reporting()
        if current_pvr is None:
            result.unavailable.append("private_vulnerability_reporting")
        else:
            result.changes.extend(
                diff_toggle(
                    "private_vulnerability_reporting",
                    current_pvr,
                    desired.private_vulnerability_reporting,
                )
            )

    return result


def apply_repo_settings(client: GitHubClient, config: PolicyConfig) -> RepoSettingsResult:
    """Sequential mutation, same fixed call order as before this task (flat settings,
    security-and-analysis, vulnerability alerts, automated security fixes -- which must run after
    vulnerability alerts, see the comment below -- then private vulnerability reporting). Each
    call is now individually journaled: on a GitHubAPIError partway through, the journal entries
    already recorded (the operations that already succeeded) are preserved and raised via
    PartialApplyError instead of being lost to an uncaught exception."""
    desired = config.repo_settings
    if desired is None:
        return RepoSettingsResult()

    result = plan_repo_settings(client, config)
    journal: list[ApplyJournalEntry] = []

    def _mutate_and_journal(
        resource: str, changes: list[RepoSettingChange], mutate: Callable[[], object]
    ) -> None:
        before_unavailable = set(result.unavailable)
        try:
            mutate()
        except GitHubAPIError as exc:
            journal.append(
                ApplyJournalEntry(
                    resource=f"repo settings: {resource}", changes=changes, status="failed"
                )
            )
            raise PartialApplyError(
                ApplySummary(journal=list(journal), unavailable=list(result.unavailable)), exc
            ) from exc
        newly_unavailable = set(result.unavailable) - before_unavailable
        status: ApplyStatus = "unavailable" if newly_unavailable else "applied"
        journal.append(
            ApplyJournalEntry(resource=f"repo settings: {resource}", changes=changes, status=status)
        )

    def _changes_for(field_name: str) -> list[RepoSettingChange]:
        return [c for c in result.changes if c.field == field_name]

    flat_changes = [c for c in result.changes if c.field in _FLAT_FIELDS]
    if flat_changes:
        _mutate_and_journal(
            ", ".join(c.field for c in flat_changes),
            flat_changes,
            lambda: client.update_repo_settings(to_flat_settings_payload(flat_changes)),
        )

    security_changes = [c for c in result.changes if c.field in _SECURITY_AND_ANALYSIS_FIELDS]
    if security_changes:

        def _apply_security_and_analysis() -> None:
            outcome = client.update_security_and_analysis(
                to_security_and_analysis_payload(security_changes)
            )
            if outcome is None:
                result.unavailable.extend(sorted({c.field for c in security_changes}))

        _mutate_and_journal(
            ", ".join(c.field for c in security_changes),
            security_changes,
            _apply_security_and_analysis,
        )

    changed_fields = {c.field for c in result.changes}
    if "vulnerability_alerts" in changed_fields:
        mutate = (
            client.enable_vulnerability_alerts
            if desired.vulnerability_alerts
            else client.disable_vulnerability_alerts
        )
        _mutate_and_journal("vulnerability_alerts", _changes_for("vulnerability_alerts"), mutate)

    # Must run after vulnerability_alerts, above -- GitHub requires Dependabot alerts enabled
    # before Dependabot security updates can be turned on. RepoSettingsPolicy's model validator
    # (models.py) already guarantees automated_security_fixes=True never appears without
    # vulnerability_alerts=True declared, but that only constrains what's *declared* -- this
    # ordering is what makes the two live API calls land in the right sequence.
    if "automated_security_fixes" in changed_fields:
        mutate = (
            client.enable_automated_security_fixes
            if desired.automated_security_fixes
            else client.disable_automated_security_fixes
        )
        _mutate_and_journal(
            "automated_security_fixes", _changes_for("automated_security_fixes"), mutate
        )

    if "private_vulnerability_reporting" in changed_fields:

        def _apply_pvr() -> None:
            applied = (
                client.enable_private_vulnerability_reporting()
                if desired.private_vulnerability_reporting
                else client.disable_private_vulnerability_reporting()
            )
            if not applied:
                result.unavailable.append("private_vulnerability_reporting")

        _mutate_and_journal(
            "private_vulnerability_reporting",
            _changes_for("private_vulnerability_reporting"),
            _apply_pvr,
        )

    result.applied = any(change.field not in result.unavailable for change in result.changes)
    return result
