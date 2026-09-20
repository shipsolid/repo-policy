from unittest.mock import MagicMock

from repo_policy.apply import (
    apply_all,
    apply_branch,
    detect_stale_branch_protection,
    plan_branch,
    prune_rulesets,
)
from repo_policy.models import BranchPolicy, PolicyConfig, PullRequestPolicy


def _config(**branch_kwargs) -> PolicyConfig:
    return PolicyConfig(version=1, branches={"main": BranchPolicy(**branch_kwargs)})


def test_plan_branch_reports_no_changes_when_already_compliant():
    client = MagicMock()
    client.get_branch_protection.return_value = None
    client.get_required_signatures.return_value = False
    config = _config()  # every field left unset -> managed-scope compares against itself
    changes, _resolved = plan_branch(client, config, "main")
    assert changes == []


def test_apply_branch_skips_api_calls_when_no_changes():
    client = MagicMock()
    client.get_branch_protection.return_value = None
    client.get_required_signatures.return_value = False
    config = _config()
    result = apply_branch(client, config, "main")
    assert result.applied is False
    client.put_branch_protection.assert_not_called()


def test_apply_branch_puts_protection_when_changes_exist():
    client = MagicMock()
    client.get_branch_protection.return_value = None
    client.get_required_signatures.return_value = False
    config = _config(
        pull_requests=PullRequestPolicy(required=True, approvals=2, code_owner_review=True),
        linear_history=True,
    )
    result = apply_branch(client, config, "main")
    assert result.applied is True
    client.put_branch_protection.assert_called_once()
    payload = client.put_branch_protection.call_args.args[1]
    assert payload["required_linear_history"] is True
    assert payload["required_pull_request_reviews"]["required_approving_review_count"] == 2


def test_apply_branch_sets_signed_commits_separately():
    client = MagicMock()
    client.get_branch_protection.return_value = None
    client.get_required_signatures.return_value = False
    config = _config(signed_commits=True)
    apply_branch(client, config, "main")
    client.set_required_signatures.assert_called_once_with("main", True)


def test_apply_branch_creates_ruleset_when_absent():
    client = MagicMock()
    client.find_ruleset_by_name.return_value = None
    config = _config(enforcement="ruleset", linear_history=True)
    result = apply_branch(client, config, "main")
    assert result.applied is True
    client.create_ruleset.assert_called_once()
    client.update_ruleset.assert_not_called()


def test_apply_branch_updates_existing_ruleset():
    client = MagicMock()
    client.find_ruleset_by_name.return_value = {"id": 7, "name": "repo-policy:main", "rules": []}
    config = _config(enforcement="ruleset", linear_history=True)
    result = apply_branch(client, config, "main")
    assert result.applied is True
    client.update_ruleset.assert_called_once()
    assert client.update_ruleset.call_args.args[0] == 7


def _canonical_ruleset_raw(*, rules: list[dict] | None = None, **overrides) -> dict:
    raw = {
        "id": 7,
        "name": "repo-policy:main",
        "target": "branch",
        "enforcement": "active",
        "conditions": {"ref_name": {"include": ["refs/heads/main"], "exclude": []}},
        "rules": rules if rules is not None else [],
        "bypass_actors": [],
    }
    raw.update(overrides)
    return raw


def test_plan_branch_reports_ruleset_metadata_drift_even_when_rule_content_matches():
    """A disabled ruleset whose rule content already matches policy.yml must not report zero
    drift -- that's the exact false-compliance gap Task 1 closes."""
    client = MagicMock()
    raw = _canonical_ruleset_raw(rules=[{"type": "required_linear_history"}], enforcement="disabled")
    client.find_ruleset_by_name.return_value = raw
    config = _config(enforcement="ruleset", linear_history=True)
    changes, _resolved = plan_branch(client, config, "main")
    assert "ruleset_enforcement" in {c.field for c in changes}
    client.get_rules_for_branch.assert_not_called()


def test_apply_branch_updates_ruleset_for_metadata_only_drift():
    """Field-level content already matches (no diff() changes), but the ruleset itself is
    disabled -- metadata-only drift must still trigger update_ruleset(), not the `if not changes`
    early return that a purely field-level diff would take."""
    client = MagicMock()
    raw = _canonical_ruleset_raw(rules=[{"type": "required_linear_history"}], enforcement="disabled")
    client.find_ruleset_by_name.return_value = raw
    config = _config(enforcement="ruleset", linear_history=True)
    result = apply_branch(client, config, "main")
    assert result.applied is True
    client.update_ruleset.assert_called_once()
    payload = client.update_ruleset.call_args.args[1]
    assert payload["enforcement"] == "active"


def test_plan_branch_reports_ruleset_effectiveness_drift_when_ruleset_contributes_no_active_rule():
    """Metadata and rule content both look canonical, but GitHub's own effective-rules endpoint
    says this ruleset isn't actually active on the branch (e.g. an org-level override) -- this is
    the live cross-check `metadata_changes()` alone can't perform."""
    client = MagicMock()
    raw = _canonical_ruleset_raw(rules=[{"type": "required_linear_history"}])
    client.find_ruleset_by_name.return_value = raw
    client.get_rules_for_branch.return_value = [
        {"type": "required_linear_history", "ruleset_id": 999, "ruleset_source_type": "Organization"}
    ]
    config = _config(enforcement="ruleset", linear_history=True)
    changes, _resolved = plan_branch(client, config, "main")
    assert "ruleset_effectiveness" in {c.field for c in changes}
    client.get_rules_for_branch.assert_called_once_with("main")


def test_plan_branch_reports_no_drift_when_ruleset_contributes_an_active_rule():
    client = MagicMock()
    raw = _canonical_ruleset_raw(rules=[{"type": "required_linear_history"}])
    client.find_ruleset_by_name.return_value = raw
    client.get_rules_for_branch.return_value = [
        {"type": "required_linear_history", "ruleset_id": 7, "ruleset_source_type": "Repository"}
    ]
    config = _config(enforcement="ruleset", linear_history=True)
    changes, _resolved = plan_branch(client, config, "main")
    assert changes == []


def test_plan_branch_skips_effectiveness_check_when_ruleset_has_no_configured_rules():
    """A canonical but intentionally empty ruleset (nothing declared under policy.yml for this
    branch) has nothing to contribute -- checking the effective-rules endpoint for it would always
    report the same false "ineffective" drift regardless of how correctly it's scoped."""
    client = MagicMock()
    raw = _canonical_ruleset_raw(rules=[])
    client.find_ruleset_by_name.return_value = raw
    config = _config(enforcement="ruleset")
    changes, _resolved = plan_branch(client, config, "main")
    assert changes == []
    client.get_rules_for_branch.assert_not_called()


def test_apply_all_applies_every_declared_branch():
    client = MagicMock()
    client.get_branch_protection.return_value = None
    client.get_required_signatures.return_value = False
    config = PolicyConfig(
        version=1, branches={"main": BranchPolicy(), "release": BranchPolicy(linear_history=True)}
    )
    results = apply_all(client, config)
    assert {r.branch for r in results} == {"main", "release"}


def test_prune_rulesets_deletes_only_orphaned_repo_policy_rulesets():
    client = MagicMock()
    client.list_rulesets.return_value = [
        {"id": 1, "name": "repo-policy:main"},
        {"id": 2, "name": "repo-policy:old-branch"},
        {"id": 3, "name": "someone-elses-ruleset"},
    ]
    config = _config(enforcement="ruleset")  # only "main" declared, still under enforcement: ruleset
    deleted = prune_rulesets(client, config)
    assert deleted == ["repo-policy:old-branch"]
    client.delete_ruleset.assert_called_once_with(2)


def test_prune_rulesets_deletes_ruleset_for_branch_switched_to_branch_protection():
    """main is still declared in policy.yml, but its enforcement changed from ruleset to
    branch_protection -- the old repo-policy:main ruleset is now an orphan and must be pruned,
    the same as if main had been removed from policy.yml entirely."""
    client = MagicMock()
    client.list_rulesets.return_value = [{"id": 1, "name": "repo-policy:main"}]
    config = _config(enforcement="branch_protection")  # only "main" declared, now branch_protection
    deleted = prune_rulesets(client, config)
    assert deleted == ["repo-policy:main"]
    client.delete_ruleset.assert_called_once_with(1)


def test_detect_stale_branch_protection_flags_ruleset_branch_with_leftover_protection():
    client = MagicMock()
    client.get_branch_protection.return_value = {"enforce_admins": {"enabled": True}}
    config = _config(enforcement="ruleset")
    assert detect_stale_branch_protection(client, config) == ["main"]


def test_detect_stale_branch_protection_ignores_branch_protection_enforced_branches():
    client = MagicMock()
    config = _config(enforcement="branch_protection")
    assert detect_stale_branch_protection(client, config) == []
    client.get_branch_protection.assert_not_called()


def test_detect_stale_branch_protection_empty_when_no_leftover_protection_exists():
    client = MagicMock()
    client.get_branch_protection.return_value = None
    config = _config(enforcement="ruleset")
    assert detect_stale_branch_protection(client, config) == []


def test_apply_branch_flags_stale_branch_protection_for_ruleset_enforced_branch():
    client = MagicMock()
    client.find_ruleset_by_name.return_value = None
    client.get_branch_protection.return_value = {"enforce_admins": {"enabled": True}}
    config = _config(enforcement="ruleset", linear_history=True)
    result = apply_branch(client, config, "main")
    assert result.stale_branch_protection is True


def test_apply_branch_no_stale_flag_for_branch_protection_enforced_branch():
    client = MagicMock()
    client.get_branch_protection.return_value = None
    client.get_required_signatures.return_value = False
    config = _config()  # enforcement: branch_protection (the default)
    result = apply_branch(client, config, "main")
    assert result.stale_branch_protection is False


def test_prune_rulesets_reuses_a_prefetched_list_without_a_new_call():
    client = MagicMock()
    prefetched = [{"id": 2, "name": "repo-policy:old-branch"}]
    config = _config()  # only "main" declared
    deleted = prune_rulesets(client, config, rulesets_cache=prefetched)
    assert deleted == ["repo-policy:old-branch"]
    client.list_rulesets.assert_not_called()


def test_apply_all_fetches_ruleset_list_at_most_once_for_multiple_ruleset_branches():
    client = MagicMock()
    client.list_rulesets.return_value = []
    client.find_ruleset_by_name.return_value = None
    config = PolicyConfig(
        version=1,
        branches={
            "main": BranchPolicy(enforcement="ruleset", linear_history=True),
            "release": BranchPolicy(enforcement="ruleset", linear_history=True),
        },
    )
    apply_all(client, config)
    assert client.list_rulesets.call_count == 1


def test_apply_all_skips_ruleset_prefetch_when_no_branch_uses_it():
    client = MagicMock()
    client.get_branch_protection.return_value = None
    client.get_required_signatures.return_value = False
    config = _config()  # branch_protection (the default), not ruleset
    apply_all(client, config)
    client.list_rulesets.assert_not_called()


def test_apply_twice_against_ineffective_ruleset_converges_to_zero_changes():
    """Step 8 idempotency check: a ruleset that's disabled, excludes its own branch, and carries a
    bypass actor is fully ineffective despite already having the right rule content -- the first
    apply must correct all of that in a single update_ruleset call, and re-reading back exactly
    what was written (plus a live effective-rules confirmation) must show zero further drift and
    perform zero further mutations."""
    ineffective_raw = {
        "id": 7,
        "name": "repo-policy:main",
        "target": "branch",
        "enforcement": "disabled",
        "conditions": {"ref_name": {"include": ["refs/heads/main"], "exclude": ["refs/heads/main"]}},
        "rules": [{"type": "required_linear_history"}],
        "bypass_actors": [{"actor_id": 5, "actor_type": "RepositoryRole", "bypass_mode": "always"}],
    }
    client = MagicMock()
    client.find_ruleset_by_name.return_value = ineffective_raw
    config = _config(enforcement="ruleset", linear_history=True)

    first_result = apply_branch(client, config, "main")
    assert first_result.applied is True
    client.update_ruleset.assert_called_once()
    ruleset_id, canonical_payload = client.update_ruleset.call_args.args
    assert ruleset_id == 7
    assert canonical_payload["enforcement"] == "active"
    assert canonical_payload["conditions"] == {"ref_name": {"include": ["refs/heads/main"], "exclude": []}}
    assert canonical_payload["bypass_actors"] == []

    # Second pass: GitHub now reflects exactly what the first apply wrote, and its effective-rules
    # endpoint confirms the ruleset is genuinely active on the branch.
    client.reset_mock()
    canonical_raw = {**ineffective_raw, **canonical_payload, "id": 7}
    client.find_ruleset_by_name.return_value = canonical_raw
    client.get_rules_for_branch.return_value = [
        {"type": "required_linear_history", "ruleset_id": 7, "ruleset_source_type": "Repository"}
    ]

    second_result = apply_branch(client, config, "main")
    assert second_result.applied is False
    assert second_result.changes == []
    client.update_ruleset.assert_not_called()
    client.create_ruleset.assert_not_called()
