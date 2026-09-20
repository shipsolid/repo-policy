from repo_policy.diff import Change
from repo_policy.policies.repo_settings import RepoSettingChange
from repo_policy.render import render_plan, render_repo_settings
from repo_policy.repo_settings import RepoSettingsResult


def test_render_plan_reports_no_changes():
    output = render_plan("acme/widgets", "main", [])
    assert "Repository: acme/widgets" in output
    assert "Branch: main" in output
    assert "No changes required." in output


def test_render_plan_shows_symbols_per_action():
    changes = [
        Change(field="linear_history", current_value=False, desired_value=True, action="add"),
        Change(field="allow_force_push", current_value=True, desired_value=False, action="remove"),
        Change(field="pull_requests", current_value="1 approval", desired_value="2 approvals", action="modify"),
    ]
    output = render_plan("acme/widgets", "main", changes)
    assert "+ Linear history" in output
    assert "- Force pushes" in output
    assert "~ Pull request requirements" in output
    assert "3 changes required." in output


def test_render_plan_singular_change_count():
    changes = [Change(field="linear_history", current_value=False, desired_value=True, action="add")]
    output = render_plan("acme/widgets", "main", changes)
    assert "1 change required." in output


def test_render_plan_shows_enforce_admins_label():
    changes = [Change(field="enforce_admins", current_value=False, desired_value=True, action="add")]
    output = render_plan("acme/widgets", "main", changes)
    assert "+ Admin enforcement" in output


def test_render_plan_shows_conversation_resolution_label():
    changes = [Change(field="required_conversation_resolution", current_value=False,
                       desired_value=True, action="add")]
    output = render_plan("acme/widgets", "main", changes)
    assert "+ Conversation resolution" in output


def test_render_plan_shows_lock_branch_label():
    changes = [Change(field="lock_branch", current_value=False, desired_value=True, action="add")]
    output = render_plan("acme/widgets", "main", changes)
    assert "+ Branch lock" in output


def test_render_plan_shows_fork_syncing_label():
    changes = [Change(field="allow_fork_syncing", current_value=False, desired_value=True, action="add")]
    output = render_plan("acme/widgets", "main", changes)
    assert "+ Fork syncing" in output


def test_render_plan_shows_clear_restrictions_label():
    changes = [Change(field="clear_restrictions", current_value=False, desired_value=True, action="add")]
    output = render_plan("acme/widgets", "main", changes)
    assert "+ Push restrictions" in output


def test_render_plan_shows_ruleset_enforcement_label():
    changes = [Change(field="ruleset_enforcement", current_value="disabled", desired_value="active", action="modify")]
    output = render_plan("acme/widgets", "main", changes)
    assert "~ Ruleset enforcement" in output


def test_render_plan_shows_ruleset_target_label():
    changes = [Change(field="ruleset_target", current_value="tag", desired_value="branch", action="modify")]
    output = render_plan("acme/widgets", "main", changes)
    assert "~ Ruleset target" in output


def test_render_plan_shows_ruleset_conditions_label():
    changes = [
        Change(
            field="ruleset_conditions",
            current_value={"include": [], "exclude": []},
            desired_value={"include": ["refs/heads/main"], "exclude": []},
            action="modify",
        )
    ]
    output = render_plan("acme/widgets", "main", changes)
    assert "~ Ruleset branch scope" in output


def test_render_plan_shows_ruleset_bypass_actors_label():
    changes = [
        Change(
            field="ruleset_bypass_actors",
            current_value=[{"actor_id": 5, "actor_type": "RepositoryRole", "bypass_mode": "always"}],
            desired_value=[],
            action="modify",
        )
    ]
    output = render_plan("acme/widgets", "main", changes)
    assert "~ Ruleset bypass actors" in output


def test_render_plan_shows_ruleset_effectiveness_label():
    changes = [
        Change(
            field="ruleset_effectiveness",
            current_value="not contributing an active rule on this branch",
            desired_value="active",
            action="modify",
        )
    ]
    output = render_plan("acme/widgets", "main", changes)
    assert "~ Ruleset effective on branch" in output


def test_render_plan_falls_back_to_raw_field_name_for_unknown_field():
    """Defensive fallback: a future BranchPolicy field added to diff._FIELDS without a matching
    _LABELS entry must render its raw name instead of raising KeyError."""
    changes = [Change(field="some_future_field", current_value=False, desired_value=True, action="add")]
    output = render_plan("acme/widgets", "main", changes)
    assert "+ some_future_field" in output


def test_render_repo_settings_reports_no_changes():
    output = render_repo_settings("acme/widgets", RepoSettingsResult())
    assert "No repo-level setting changes required." in output


def test_render_repo_settings_shows_a_change():
    result = RepoSettingsResult(changes=[
        RepoSettingChange("delete_branch_on_merge", False, True, "add"),
    ])
    output = render_repo_settings("acme/widgets", result)
    assert "+ Delete branch on merge" in output


def test_render_repo_settings_shows_unavailable():
    result = RepoSettingsResult(unavailable=["private_vulnerability_reporting"])
    output = render_repo_settings("acme/widgets", result)
    assert "? Private vulnerability reporting unavailable on this repository" in output


def test_render_repo_settings_unavailable_only_does_not_claim_changes_required():
    """result.changes is empty here -- only result.unavailable is populated (a declared field
    that's ineligible on this repo, no actual drift). The summary line must not say '0 changes
    required.' directly beneath the unavailable warning above it -- that reads as self-
    contradictory even though the process still exits with drift status for the caller."""
    result = RepoSettingsResult(unavailable=["private_vulnerability_reporting"])
    output = render_repo_settings("acme/widgets", result)
    assert "0 changes required." not in output
    assert "No repo-level setting changes required." in output
