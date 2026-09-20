from unittest.mock import MagicMock

import pytest

from repo_policy.apply import PartialApplyError
from repo_policy.github_client import GitHubAPIError
from repo_policy.models import PolicyConfig, RepoSettingsPolicy
from repo_policy.repo_settings import apply_repo_settings, plan_repo_settings


def test_plan_repo_settings_returns_empty_result_when_section_absent():
    client = MagicMock()
    config = PolicyConfig(version=1, branches={})  # no repo_settings declared
    result = plan_repo_settings(client, config)
    assert result.changes == []
    assert result.unavailable == []
    client.get_repo.assert_not_called()


def test_plan_repo_settings_detects_flat_setting_drift():
    client = MagicMock()
    client.get_repo.return_value = {"delete_branch_on_merge": False}
    config = PolicyConfig(
        version=1, branches={}, repo_settings=RepoSettingsPolicy(delete_branch_on_merge=True)
    )
    result = plan_repo_settings(client, config)
    assert len(result.changes) == 1
    assert result.changes[0].field == "delete_branch_on_merge"


def test_apply_repo_settings_calls_update_when_drift_exists():
    client = MagicMock()
    client.get_repo.return_value = {"delete_branch_on_merge": False}
    config = PolicyConfig(
        version=1, branches={}, repo_settings=RepoSettingsPolicy(delete_branch_on_merge=True)
    )
    result = apply_repo_settings(client, config)
    assert result.applied is True
    client.update_repo_settings.assert_called_once_with({"delete_branch_on_merge": True})


def test_apply_repo_settings_is_idempotent_when_already_compliant():
    client = MagicMock()
    client.get_repo.return_value = {"delete_branch_on_merge": True}
    config = PolicyConfig(
        version=1, branches={}, repo_settings=RepoSettingsPolicy(delete_branch_on_merge=True)
    )
    result = apply_repo_settings(client, config)
    assert result.applied is False
    client.update_repo_settings.assert_not_called()


def test_plan_repo_settings_detects_vulnerability_alerts_drift():
    client = MagicMock()
    client.get_repo.return_value = {}
    client.get_vulnerability_alerts.return_value = False
    config = PolicyConfig(version=1, branches={}, repo_settings=RepoSettingsPolicy(vulnerability_alerts=True))
    result = plan_repo_settings(client, config)
    assert len(result.changes) == 1
    assert result.changes[0].field == "vulnerability_alerts"


def test_apply_repo_settings_enables_vulnerability_alerts():
    client = MagicMock()
    client.get_repo.return_value = {}
    client.get_vulnerability_alerts.return_value = False
    config = PolicyConfig(version=1, branches={}, repo_settings=RepoSettingsPolicy(vulnerability_alerts=True))
    result = apply_repo_settings(client, config)
    assert result.applied is True
    client.enable_vulnerability_alerts.assert_called_once()


def test_apply_repo_settings_disables_vulnerability_alerts():
    client = MagicMock()
    client.get_repo.return_value = {}
    client.get_vulnerability_alerts.return_value = True
    config = PolicyConfig(version=1, branches={}, repo_settings=RepoSettingsPolicy(vulnerability_alerts=False))
    result = apply_repo_settings(client, config)
    assert result.applied is True
    client.disable_vulnerability_alerts.assert_called_once()
    client.enable_vulnerability_alerts.assert_not_called()


def test_apply_repo_settings_disables_automated_security_fixes():
    client = MagicMock()
    client.get_repo.return_value = {}
    client.get_automated_security_fixes.return_value = True
    config = PolicyConfig(
        version=1, branches={}, repo_settings=RepoSettingsPolicy(automated_security_fixes=False)
    )
    result = apply_repo_settings(client, config)
    assert result.applied is True
    client.disable_automated_security_fixes.assert_called_once()
    client.enable_automated_security_fixes.assert_not_called()


def test_apply_repo_settings_disables_private_vulnerability_reporting():
    client = MagicMock()
    client.get_repo.return_value = {}
    client.get_private_vulnerability_reporting.return_value = True
    client.disable_private_vulnerability_reporting.return_value = True
    config = PolicyConfig(
        version=1, branches={}, repo_settings=RepoSettingsPolicy(private_vulnerability_reporting=False)
    )
    result = apply_repo_settings(client, config)
    assert result.applied is True
    client.disable_private_vulnerability_reporting.assert_called_once()
    client.enable_private_vulnerability_reporting.assert_not_called()


def test_plan_repo_settings_records_unavailable_when_pvr_ineligible():
    client = MagicMock()
    client.get_repo.return_value = {}
    client.get_private_vulnerability_reporting.return_value = None
    config = PolicyConfig(version=1, branches={}, repo_settings=RepoSettingsPolicy(private_vulnerability_reporting=True))
    result = plan_repo_settings(client, config)
    assert result.changes == []
    assert result.unavailable == ["private_vulnerability_reporting"]


def test_apply_repo_settings_records_unavailable_when_enable_hits_422():
    client = MagicMock()
    client.get_repo.return_value = {}
    client.get_private_vulnerability_reporting.return_value = False
    client.enable_private_vulnerability_reporting.return_value = False
    config = PolicyConfig(version=1, branches={}, repo_settings=RepoSettingsPolicy(private_vulnerability_reporting=True))
    result = apply_repo_settings(client, config)
    assert "private_vulnerability_reporting" in result.unavailable


def test_plan_repo_settings_detects_secret_scanning_drift():
    client = MagicMock()
    client.get_repo.return_value = {"security_and_analysis": {"secret_scanning": {"status": "disabled"}}}
    config = PolicyConfig(version=1, branches={}, repo_settings=RepoSettingsPolicy(secret_scanning=True))
    result = plan_repo_settings(client, config)
    assert len(result.changes) == 1
    assert result.changes[0].field == "secret_scanning"


def test_apply_repo_settings_records_unavailable_when_ghas_not_licensed():
    client = MagicMock()
    client.get_repo.return_value = {"security_and_analysis": {"secret_scanning": {"status": "disabled"}}}
    client.update_security_and_analysis.return_value = None
    config = PolicyConfig(version=1, branches={}, repo_settings=RepoSettingsPolicy(secret_scanning=True))
    result = apply_repo_settings(client, config)
    assert result.unavailable == ["secret_scanning"]
    assert result.applied is False


def test_apply_repo_settings_records_both_fields_unavailable_together():
    client = MagicMock()
    client.get_repo.return_value = {
        "security_and_analysis": {
            "secret_scanning": {"status": "disabled"},
            "secret_scanning_push_protection": {"status": "disabled"},
        }
    }
    client.update_security_and_analysis.return_value = None
    config = PolicyConfig(
        version=1, branches={},
        repo_settings=RepoSettingsPolicy(secret_scanning=True, secret_scanning_push_protection=True),
    )
    result = apply_repo_settings(client, config)
    assert sorted(result.unavailable) == ["secret_scanning", "secret_scanning_push_protection"]


def test_apply_repo_settings_applies_secret_scanning_when_ghas_licensed():
    client = MagicMock()
    client.get_repo.return_value = {"security_and_analysis": {"secret_scanning": {"status": "disabled"}}}
    client.update_security_and_analysis.return_value = {"security_and_analysis": {"secret_scanning": {"status": "enabled"}}}
    config = PolicyConfig(version=1, branches={}, repo_settings=RepoSettingsPolicy(secret_scanning=True))
    result = apply_repo_settings(client, config)
    assert result.applied is True
    assert result.unavailable == []
    client.update_security_and_analysis.assert_called_once_with({"secret_scanning": {"status": "enabled"}})


def test_apply_repo_settings_enables_alerts_before_security_fixes():
    """Both fields are drifted in the same apply -- vulnerability_alerts must be enabled first,
    since GitHub rejects enabling automated_security_fixes before it."""
    call_order = []
    client = MagicMock()
    client.get_repo.return_value = {}
    client.get_vulnerability_alerts.return_value = False
    client.get_automated_security_fixes.return_value = False
    client.enable_vulnerability_alerts.side_effect = lambda: call_order.append("vulnerability_alerts")
    client.enable_automated_security_fixes.side_effect = lambda: call_order.append("automated_security_fixes")
    config = PolicyConfig(
        version=1, branches={},
        repo_settings=RepoSettingsPolicy(vulnerability_alerts=True, automated_security_fixes=True),
    )
    apply_repo_settings(client, config)
    assert call_order == ["vulnerability_alerts", "automated_security_fixes"]


def test_apply_repo_settings_raises_partial_apply_error_identifying_completed_and_failed_operations():
    """Task 3: vulnerability_alerts succeeds, then automated_security_fixes raises -- the journal
    carried by the raised PartialApplyError must show the first operation as applied and the
    second as failed, so the CLI can report exactly which repo-setting operations completed before
    the failure."""
    client = MagicMock()
    client.get_repo.return_value = {}
    client.get_vulnerability_alerts.return_value = False
    client.get_automated_security_fixes.return_value = False
    client.enable_automated_security_fixes.side_effect = GitHubAPIError("boom", status_code=500)
    config = PolicyConfig(
        version=1, branches={},
        repo_settings=RepoSettingsPolicy(vulnerability_alerts=True, automated_security_fixes=True),
    )

    with pytest.raises(PartialApplyError) as exc_info:
        apply_repo_settings(client, config)

    client.enable_vulnerability_alerts.assert_called_once()
    statuses = {entry.resource: entry.status for entry in exc_info.value.summary.journal}
    assert statuses["repo settings: vulnerability_alerts"] == "applied"
    assert statuses["repo settings: automated_security_fixes"] == "failed"
