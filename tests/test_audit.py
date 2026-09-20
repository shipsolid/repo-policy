from unittest.mock import MagicMock

import pytest

from repo_policy.audit import audit_all, detect_orphaned_rulesets
from repo_policy.models import BranchPolicy, PolicyConfig, PullRequestPolicy


def test_audit_all_reports_compliant_when_no_drift():
    client = MagicMock()
    client.get_branch_protection.return_value = None
    client.get_required_signatures.return_value = False
    config = PolicyConfig(version=1, branches={"main": BranchPolicy()})
    results, orphaned_rulesets = audit_all(client, config)
    assert len(results) == 1
    assert results[0].compliant is True
    assert results[0].changes == []
    assert orphaned_rulesets == []


def test_audit_all_reports_drift():
    client = MagicMock()
    client.get_branch_protection.return_value = None
    client.get_required_signatures.return_value = False
    config = PolicyConfig(
        version=1,
        branches={"main": BranchPolicy(pull_requests=PullRequestPolicy(required=True, approvals=1, code_owner_review=False))},
    )
    results, _orphaned_rulesets = audit_all(client, config)
    assert results[0].compliant is False
    assert len(results[0].changes) == 1


def test_audit_all_covers_every_declared_branch():
    client = MagicMock()
    client.get_branch_protection.return_value = None
    client.get_required_signatures.return_value = False
    config = PolicyConfig(version=1, branches={"main": BranchPolicy(), "release": BranchPolicy()})
    results, _orphaned_rulesets = audit_all(client, config)
    assert {r.branch for r in results} == {"main", "release"}


def test_audit_all_flags_stale_branch_protection_and_treats_it_as_non_compliant():
    client = MagicMock()
    client.find_ruleset_by_name.return_value = None
    client.get_branch_protection.return_value = {"enforce_admins": {"enabled": True}}
    config = PolicyConfig(version=1, branches={"main": BranchPolicy(enforcement="ruleset")})
    results, _orphaned_rulesets = audit_all(client, config)
    assert results[0].stale_branch_protection is True
    assert results[0].compliant is False


def _canonical_ruleset_raw(*, rules: list[dict] | None = None, **overrides) -> dict:
    raw = {
        "id": 7,
        "name": "repo-policy:main",
        "target": "branch",
        "enforcement": "active",
        "conditions": {"ref_name": {"include": ["refs/heads/main"], "exclude": []}},
        "rules": rules if rules is not None else [{"type": "required_linear_history"}],
        "bypass_actors": [],
    }
    raw.update(overrides)
    return raw


@pytest.mark.parametrize(
    "raw_patch",
    [
        {"enforcement": "disabled"},
        {"enforcement": "evaluate"},
        {"target": "tag"},
        {"conditions": {"ref_name": {"include": [], "exclude": []}}},
        {"bypass_actors": [{"actor_id": 5, "actor_type": "RepositoryRole", "bypass_mode": "always"}]},
    ],
)
def test_audit_all_reports_noncompliant_for_every_ineffective_ruleset_variant(raw_patch):
    """Acceptance criterion: audit must report drift (non-compliant) for every ruleset-
    applicability variant the plan closes -- disabled/evaluate enforcement, wrong target, a
    missing/excluded branch condition, and a bypass actor."""
    client = MagicMock()
    client.find_ruleset_by_name.return_value = _canonical_ruleset_raw() | raw_patch
    config = PolicyConfig(
        version=1, branches={"main": BranchPolicy(enforcement="ruleset", linear_history=True)}
    )
    results, _orphaned_rulesets = audit_all(client, config)
    assert results[0].compliant is False


def test_audit_all_reports_noncompliant_when_ruleset_contributes_no_active_rule():
    """Metadata and rule content are both canonical, but GitHub's effective-rules endpoint says
    the ruleset isn't actually active on the branch -- audit must not report compliance."""
    client = MagicMock()
    client.find_ruleset_by_name.return_value = _canonical_ruleset_raw()
    client.get_rules_for_branch.return_value = []
    config = PolicyConfig(
        version=1, branches={"main": BranchPolicy(enforcement="ruleset", linear_history=True)}
    )
    results, _orphaned_rulesets = audit_all(client, config)
    assert results[0].compliant is False


def test_audit_all_reports_compliant_for_a_genuinely_effective_canonical_ruleset():
    client = MagicMock()
    client.get_branch_protection.return_value = None
    client.find_ruleset_by_name.return_value = _canonical_ruleset_raw()
    client.get_rules_for_branch.return_value = [
        {"type": "required_linear_history", "ruleset_id": 7, "ruleset_source_type": "Repository"}
    ]
    config = PolicyConfig(
        version=1, branches={"main": BranchPolicy(enforcement="ruleset", linear_history=True)}
    )
    results, _orphaned_rulesets = audit_all(client, config)
    assert results[0].compliant is True


def test_audit_all_fetches_ruleset_list_at_most_once_for_multiple_ruleset_branches():
    client = MagicMock()
    client.list_rulesets.return_value = []
    client.find_ruleset_by_name.return_value = None
    config = PolicyConfig(
        version=1,
        branches={
            "main": BranchPolicy(enforcement="ruleset"),
            "release": BranchPolicy(enforcement="ruleset"),
        },
    )
    audit_all(client, config)
    assert client.list_rulesets.call_count == 1


def test_audit_all_reports_orphaned_rulesets_in_strict_mode():
    """A branch removed from policy.yml (or switched off enforcement: ruleset) leaves an orphaned
    repo-policy: ruleset that the next strict apply's prune_rulesets would silently delete --
    audit/plan must surface it as drift, not report full compliance right up until that deletion."""
    client = MagicMock()
    client.list_rulesets.return_value = [{"id": 1, "name": "repo-policy:old-branch"}]
    config = PolicyConfig(version=1, strict=True, branches={})
    results, orphaned_rulesets = audit_all(client, config)
    assert results == []
    assert orphaned_rulesets == ["repo-policy:old-branch"]


def test_audit_all_does_not_report_orphaned_rulesets_outside_strict_mode():
    """Outside strict mode, prune_rulesets is never invoked (see cli.py's `if config.strict:`
    gate), so warning about an orphan that will never actually be pruned would be noise."""
    client = MagicMock()
    client.list_rulesets.return_value = [{"id": 1, "name": "repo-policy:old-branch"}]
    config = PolicyConfig(version=1, strict=False, branches={})
    _results, orphaned_rulesets = audit_all(client, config)
    assert orphaned_rulesets == []


def test_detect_orphaned_rulesets_empty_outside_strict_mode():
    client = MagicMock()
    config = PolicyConfig(version=1, strict=False, branches={})
    assert detect_orphaned_rulesets(client, config) == []
    client.list_rulesets.assert_not_called()


def test_detect_orphaned_rulesets_reuses_a_prefetched_list_without_a_new_call():
    client = MagicMock()
    prefetched = [{"id": 1, "name": "repo-policy:old-branch"}]
    config = PolicyConfig(version=1, strict=True, branches={})
    assert detect_orphaned_rulesets(client, config, rulesets_cache=prefetched) == ["repo-policy:old-branch"]
    client.list_rulesets.assert_not_called()
