from repo_policy.models import RepoSettingsPolicy
from repo_policy.policies import repo_settings


def test_diff_flat_settings_detects_change():
    current_repo = {"delete_branch_on_merge": False, "allow_update_branch": True}
    desired = RepoSettingsPolicy(delete_branch_on_merge=True)
    changes, unavailable = repo_settings.diff_flat_settings(current_repo, desired)
    assert unavailable == []
    assert len(changes) == 1
    assert changes[0].field == "delete_branch_on_merge"
    assert changes[0].current_value is False
    assert changes[0].desired_value is True
    assert changes[0].action == "add"


def test_diff_flat_settings_skips_undeclared_fields():
    current_repo = {"delete_branch_on_merge": False, "allow_update_branch": False}
    desired = RepoSettingsPolicy(delete_branch_on_merge=True)  # allow_update_branch left unset
    changes, unavailable = repo_settings.diff_flat_settings(current_repo, desired)
    assert unavailable == []
    assert len(changes) == 1
    assert changes[0].field == "delete_branch_on_merge"


def test_diff_flat_settings_empty_when_already_compliant():
    current_repo = {"delete_branch_on_merge": True, "allow_update_branch": True}
    desired = RepoSettingsPolicy(delete_branch_on_merge=True, allow_update_branch=True)
    assert repo_settings.diff_flat_settings(current_repo, desired) == ([], [])


def test_to_flat_settings_payload_builds_dict_from_changes():
    current_repo = {"delete_branch_on_merge": False, "allow_update_branch": False}
    desired = RepoSettingsPolicy(delete_branch_on_merge=True, allow_update_branch=True)
    changes, _unavailable = repo_settings.diff_flat_settings(current_repo, desired)
    payload = repo_settings.to_flat_settings_payload(changes)
    assert payload == {"delete_branch_on_merge": True, "allow_update_branch": True}


def test_diff_security_and_analysis_detects_change():
    current_repo = {"security_and_analysis": {"secret_scanning": {"status": "disabled"}}}
    desired = RepoSettingsPolicy(secret_scanning=True)
    changes, unavailable = repo_settings.diff_security_and_analysis(current_repo, desired)
    assert unavailable == []
    assert len(changes) == 1
    assert changes[0].field == "secret_scanning"
    assert changes[0].action == "add"


def test_diff_security_and_analysis_treats_present_empty_block_as_disabled():
    """The block IS present (not None) but has no sub-keys -- e.g. a repo where these features
    were simply never configured. Distinct from the block being wholly absent due to token scope
    (see test_diff_security_and_analysis_reports_unavailable_when_block_absent below): this
    legitimately means 'disabled', matching GitHub's documented default, so it still produces a
    real, diffable Change rather than 'unavailable'."""
    current_repo = {"security_and_analysis": {}}
    desired = RepoSettingsPolicy(secret_scanning=True)
    changes, unavailable = repo_settings.diff_security_and_analysis(current_repo, desired)
    assert unavailable == []
    assert len(changes) == 1
    assert changes[0].current_value is False
    assert changes[0].action == "add"


def test_diff_security_and_analysis_reports_unavailable_when_block_absent():
    """Bug fix: current_repo.get("security_and_analysis") is None (the key is missing entirely,
    not an empty dict) when the authenticated token lacks permission to see this field -- e.g. a
    fine-grained PAT scoped to Administration: Read-only. That must not be conflated with the
    block being present and the features genuinely disabled: both declared fields are
    structurally undeterminable, so both land in `unavailable` and neither produces a false
    'add' Change."""
    current_repo = {}  # no security_and_analysis key at all
    desired = RepoSettingsPolicy(secret_scanning=True, secret_scanning_push_protection=True)
    changes, unavailable = repo_settings.diff_security_and_analysis(current_repo, desired)
    assert changes == []
    assert sorted(unavailable) == ["secret_scanning", "secret_scanning_push_protection"]


def test_diff_security_and_analysis_skips_undeclared_fields_when_block_absent():
    """Mirrors diff_toggle/diff_flat_settings' existing 'not declared -- don't touch' rule: only
    fields actually wanted by the policy should ever show up in `unavailable`."""
    current_repo = {}  # no security_and_analysis key at all
    desired = RepoSettingsPolicy(secret_scanning=True)  # push protection left unset
    changes, unavailable = repo_settings.diff_security_and_analysis(current_repo, desired)
    assert changes == []
    assert unavailable == ["secret_scanning"]


def test_diff_security_and_analysis_empty_when_already_compliant():
    current_repo = {
        "security_and_analysis": {
            "secret_scanning": {"status": "enabled"},
            "secret_scanning_push_protection": {"status": "enabled"},
        }
    }
    desired = RepoSettingsPolicy(secret_scanning=True, secret_scanning_push_protection=True)
    changes, unavailable = repo_settings.diff_security_and_analysis(current_repo, desired)
    assert changes == []
    assert unavailable == []


def test_to_security_and_analysis_payload_builds_status_wrapped_dict():
    current_repo = {
        "security_and_analysis": {"secret_scanning_push_protection": {"status": "enabled"}}
    }
    desired = RepoSettingsPolicy(secret_scanning=True, secret_scanning_push_protection=False)
    changes, unavailable = repo_settings.diff_security_and_analysis(current_repo, desired)
    assert unavailable == []
    payload = repo_settings.to_security_and_analysis_payload(changes)
    assert payload == {
        "secret_scanning": {"status": "enabled"},
        "secret_scanning_push_protection": {"status": "disabled"},
    }


def test_diff_toggle_returns_change_when_different():
    changes = repo_settings.diff_toggle("vulnerability_alerts", False, True)
    assert len(changes) == 1
    assert changes[0].action == "add"


def test_diff_toggle_empty_when_equal():
    assert repo_settings.diff_toggle("vulnerability_alerts", True, True) == []


def test_diff_toggle_empty_when_current_is_none():
    """current_value=None means 'unavailable' -- never produces a Change."""
    assert repo_settings.diff_toggle("private_vulnerability_reporting", None, True) == []


def test_diff_toggle_empty_when_desired_is_none():
    assert repo_settings.diff_toggle("vulnerability_alerts", False, None) == []


def test_diff_flat_settings_reports_unavailable_when_key_absent():
    """GET /repos/{owner}/{repo} omits delete_branch_on_merge/allow_update_branch entirely when
    the token can't see them (live-confirmed with a fine-grained Administration: Read-only PAT on
    shipsolid/repo-policy, 2026-09-21). Absent means "cannot determine", never False -- otherwise
    a declared `true` reports a false 'add' on every audit and PATCHes on every apply."""
    current_repo = {"full_name": "acme/widgets"}  # neither flat key present
    desired = RepoSettingsPolicy(delete_branch_on_merge=True, allow_update_branch=True)
    changes, unavailable = repo_settings.diff_flat_settings(current_repo, desired)
    assert changes == []
    assert unavailable == ["delete_branch_on_merge", "allow_update_branch"]


def test_diff_flat_settings_skips_undeclared_fields_when_key_absent():
    current_repo = {}
    desired = RepoSettingsPolicy(delete_branch_on_merge=True)  # allow_update_branch undeclared
    changes, unavailable = repo_settings.diff_flat_settings(current_repo, desired)
    assert changes == []
    assert unavailable == ["delete_branch_on_merge"]
