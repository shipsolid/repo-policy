import importlib.metadata
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

import repo_policy
from repo_policy.cli import _resolve_repo, main
from repo_policy.github_client import GitHubAPIError, GitHubClient


def test_validate_exits_0_on_valid_config():
    runner = CliRunner()
    result = runner.invoke(main, ["validate", "--config", "tests/fixtures/policy_valid.yml"])
    assert result.exit_code == 0
    assert "is valid" in result.output


def test_validate_exits_2_on_invalid_config():
    runner = CliRunner()
    result = runner.invoke(main, ["validate", "--config", "tests/fixtures/policy_invalid.yml"])
    assert result.exit_code == 2


def test_validate_exits_2_on_missing_config():
    runner = CliRunner()
    result = runner.invoke(main, ["validate", "--config", "tests/fixtures/nope.yml"])
    assert result.exit_code == 2


@patch("repo_policy.cli.GitHubClient")
def test_audit_exits_0_when_compliant(mock_client_cls):
    mock_client = mock_client_cls.return_value.__enter__.return_value
    mock_client.get_branch_protection.return_value = None
    mock_client.get_required_signatures.return_value = False
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "audit",
            "--config",
            "tests/fixtures/policy_no_requirements.yml",
            "--repo",
            "acme/widgets",
            "--token",
            "t",
        ],
    )
    assert result.exit_code == 0
    assert "is compliant" in result.output


@patch("repo_policy.cli.GitHubClient")
def test_audit_exits_1_on_drift(mock_client_cls):
    mock_client = mock_client_cls.return_value.__enter__.return_value
    mock_client.get_branch_protection.return_value = None
    mock_client.get_required_signatures.return_value = False
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "audit",
            "--config",
            "tests/fixtures/policy_valid.yml",
            "--repo",
            "acme/widgets",
            "--token",
            "t",
        ],
    )
    assert result.exit_code == 1


@patch("repo_policy.cli.GitHubClient")
def test_audit_reports_orphaned_ruleset_and_exits_1_in_strict_mode(mock_client_cls):
    """A branch removed from policy.yml (or switched off enforcement: ruleset) under top-level
    strict mode leaves an orphaned repo-policy: ruleset that the next apply's prune_rulesets
    would silently delete -- audit must warn about it and treat it as drift, not report full
    compliance right up until that deletion happens (policy_strict.yml declares only "main"
    under branch_protection with strict: true, so "main"'s own declared change plus the
    unrelated orphaned ruleset both count toward drift here)."""
    mock_client = mock_client_cls.return_value.__enter__.return_value
    mock_client.get_branch_protection.return_value = None
    mock_client.get_required_signatures.return_value = False
    mock_client.list_rulesets.return_value = [{"id": 9, "name": "repo-policy:removed-branch"}]
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "audit",
            "--config",
            "tests/fixtures/policy_strict.yml",
            "--repo",
            "acme/widgets",
            "--token",
            "t",
        ],
    )
    assert result.exit_code == 1
    assert "orphaned ruleset repo-policy:removed-branch detected" in result.output


@patch("repo_policy.cli.GitHubClient")
def test_plan_renders_diff_and_exits_1_on_drift(mock_client_cls):
    mock_client = mock_client_cls.return_value.__enter__.return_value
    mock_client.get_branch_protection.return_value = None
    mock_client.get_required_signatures.return_value = False
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "plan",
            "--config",
            "tests/fixtures/policy_valid.yml",
            "--repo",
            "acme/widgets",
            "--token",
            "t",
        ],
    )
    assert result.exit_code == 1
    assert "Repository: acme/widgets" in result.output


@patch("repo_policy.cli.GitHubClient")
def test_apply_exits_0_and_applies_changes(mock_client_cls):
    mock_client = mock_client_cls.return_value.__enter__.return_value
    mock_client.get_branch_protection.return_value = None
    mock_client.get_required_signatures.return_value = False

    # Task 4: verify_after_apply re-reads live state after the mutation succeeds -- reflect what
    # a real PUT would persist (the same round trip test_idempotency.py's apply-twice test
    # already exercises) so the post-apply convergence check sees compliance and exit 0 holds.
    def _put_branch_protection(branch, payload):
        mock_client.get_branch_protection.return_value = payload

    def _set_required_signatures(branch, enabled):
        mock_client.get_required_signatures.return_value = enabled

    mock_client.put_branch_protection.side_effect = _put_branch_protection
    mock_client.set_required_signatures.side_effect = _set_required_signatures

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "apply",
            "--config",
            "tests/fixtures/policy_valid.yml",
            "--repo",
            "acme/widgets",
            "--token",
            "t",
        ],
    )
    assert result.exit_code == 0, result.output
    mock_client.put_branch_protection.assert_called_once()


@patch("repo_policy.cli.GitHubClient")
def test_apply_exits_1_when_post_apply_read_shows_the_mutation_did_not_persist(
    mock_client_cls, tmp_path
):
    """Task 4 Step 1 / acceptance criterion: a 2xx PUT response alone can never produce a
    successful apply. GitHub accepts the PUT without error, but the live state, once
    independently re-read, still doesn't reflect required_linear_history -- e.g. an org ruleset
    or webhook silently reverted it. Must exit 1 with the remaining drift printed, not the
    "applied" success path a naive PUT-status check would take."""
    mock_client = mock_client_cls.return_value.__enter__.return_value
    mock_client.get_branch_protection.return_value = None  # never reflects the PUT
    mock_client.get_required_signatures.return_value = False
    config_path = tmp_path / "policy.yml"
    config_path.write_text("version: 1\nbranches:\n  main:\n    linear_history: true\n")
    runner = CliRunner()
    result = runner.invoke(
        main, ["apply", "--config", str(config_path), "--repo", "acme/widgets", "--token", "t"]
    )
    assert result.exit_code == 1
    mock_client.put_branch_protection.assert_called_once()
    assert "apply completed but policy is not converged" in result.output
    assert "Linear history" in result.output


@patch("repo_policy.cli.GitHubClient")
def test_apply_exits_1_when_declared_setting_is_unavailable_on_the_repository(
    mock_client_cls, tmp_path
):
    """Task 4 Step 2: private_vulnerability_reporting declared true on a repository where GitHub
    reports it structurally ineligible -- the mutation phase completes without raising (there's no
    Change to apply, see plan_repo_settings), but the field can never actually be satisfied.
    Must exit 1, not 0."""
    mock_client = mock_client_cls.return_value.__enter__.return_value
    mock_client.get_repo.return_value = {}
    mock_client.get_private_vulnerability_reporting.return_value = None  # ineligible
    config_path = tmp_path / "policy.yml"
    config_path.write_text(
        "version: 1\nbranches: {}\nrepo_settings:\n  private_vulnerability_reporting: true\n"
    )
    runner = CliRunner()
    result = runner.invoke(
        main, ["apply", "--config", str(config_path), "--repo", "acme/widgets", "--token", "t"]
    )
    assert result.exit_code == 1
    assert "private_vulnerability_reporting" in result.output
    assert "apply completed but policy is not converged" in result.output


@patch("repo_policy.cli.GitHubClient")
def test_apply_strict_mode_shares_one_ruleset_fetch_between_apply_and_prune(mock_client_cls):
    mock_client = mock_client_cls.return_value.__enter__.return_value
    mock_client.get_branch_protection.return_value = None
    mock_client.get_required_signatures.return_value = False
    rulesets_state = [{"id": 9, "name": "repo-policy:removed-branch"}]
    mock_client.list_rulesets.side_effect = lambda: list(rulesets_state)

    def _put_branch_protection(branch, payload):
        mock_client.get_branch_protection.return_value = payload

    def _set_required_signatures(branch, enabled):
        mock_client.get_required_signatures.return_value = enabled

    def _delete_ruleset(ruleset_id):
        rulesets_state[:] = [r for r in rulesets_state if r["id"] != ruleset_id]

    mock_client.put_branch_protection.side_effect = _put_branch_protection
    mock_client.set_required_signatures.side_effect = _set_required_signatures
    mock_client.delete_ruleset.side_effect = _delete_ruleset

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "apply",
            "--config",
            "tests/fixtures/policy_strict.yml",
            "--repo",
            "acme/widgets",
            "--token",
            "t",
        ],
    )
    assert result.exit_code == 0, result.output
    # Task 4: one prefetch before mutation (shared by apply_all + prune_rulesets, unchanged from
    # before this task) plus one fresh fetch inside verify_after_apply's post-mutation audit_all --
    # deliberately not the same cached list, since a just-pruned ruleset must be re-read live.
    assert mock_client.list_rulesets.call_count == 2
    mock_client.delete_ruleset.assert_called_once_with(9)
    assert "removed orphaned ruleset repo-policy:removed-branch" in result.output


@patch("repo_policy.cli.GitHubClient")
def test_audit_reports_usage_error_on_malformed_repo(mock_client_cls):
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "audit",
            "--config",
            "tests/fixtures/policy_no_requirements.yml",
            "--repo",
            "widgets",
            "--token",
            "t",
        ],
    )
    assert result.exit_code == 2
    assert "invalid repository" in result.output
    mock_client_cls.assert_not_called()


@patch("repo_policy.cli.GitHubClient")
def test_audit_reports_usage_error_on_repo_with_extra_slashes(mock_client_cls):
    """A --repo with more than one slash (typo, or a pasted URL fragment like
    'owner/name/tree/main') previously passed _split_repo's single check (only zero slashes was
    rejected) via split('/', 1), silently discarding everything after the second segment and
    building a malformed API path instead of failing fast."""
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "audit",
            "--config",
            "tests/fixtures/policy_no_requirements.yml",
            "--repo",
            "acme/widgets/extra",
            "--token",
            "t",
        ],
    )
    assert result.exit_code == 2
    assert "invalid repository" in result.output
    mock_client_cls.assert_not_called()


@patch("repo_policy.cli.GitHubClient")
def test_audit_reports_usage_error_when_repo_cannot_be_resolved(
    mock_client_cls, tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    (tmp_path / "policy.yml").write_text("version: 1\nbranches:\n  main: {}\n")
    runner = CliRunner()
    result = runner.invoke(main, ["audit", "--token", "t"])
    assert result.exit_code == 2
    mock_client_cls.assert_not_called()


@patch("repo_policy.cli.GitHubClient")
def test_audit_reports_usage_error_when_no_token_configured(mock_client_cls, monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "audit",
            "--config",
            "tests/fixtures/policy_no_requirements.yml",
            "--repo",
            "acme/widgets",
        ],
    )
    assert result.exit_code == 2
    assert "no GitHub token" in result.output
    mock_client_cls.assert_not_called()


@patch("repo_policy.cli.GitHubClient")
def test_audit_ignores_repo_settings_when_no_branches_declared_and_section_absent(mock_client_cls):
    """policy_no_requirements.yml has branches but no repo_settings key -- confirms zero extra
    API calls for every existing policy.yml written before this feature existed."""
    mock_client = mock_client_cls.return_value.__enter__.return_value
    mock_client.get_branch_protection.return_value = None
    mock_client.get_required_signatures.return_value = False
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "audit",
            "--config",
            "tests/fixtures/policy_no_requirements.yml",
            "--repo",
            "acme/widgets",
            "--token",
            "t",
        ],
    )
    assert result.exit_code == 0
    mock_client.get_repo.assert_not_called()


@patch("repo_policy.cli.GitHubClient")
def test_plan_renders_repo_settings_drift(mock_client_cls):
    mock_client = mock_client_cls.return_value.__enter__.return_value
    mock_client.get_repo.return_value = {"delete_branch_on_merge": False}
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "plan",
            "--config",
            "tests/fixtures/policy_repo_settings.yml",
            "--repo",
            "acme/widgets",
            "--token",
            "t",
        ],
    )
    assert result.exit_code == 1
    assert "Repo-level settings:" in result.output
    assert "+ Delete branch on merge" in result.output


@patch("repo_policy.cli.GitHubClient")
def test_apply_reports_partial_success_count_when_some_changes_are_unavailable(
    mock_client_cls, tmp_path
):
    """A field that 422s (GHAS not licensed) lands in both result.changes and
    result.unavailable -- the printed "applied N change(s)" count must exclude it, not just the
    applied boolean. Task 4: delete_branch_on_merge converges cleanly (round-tripped below via
    update_repo_settings), but secret_scanning remains permanently unavailable -- so despite that
    partial success, the overall apply must still exit 1, not 0 (see
    test_apply_exits_1_when_declared_setting_is_unavailable_on_the_repository for the acceptance-
    criteria-level version of this same gap)."""
    mock_client = mock_client_cls.return_value.__enter__.return_value
    repo_response = {
        "delete_branch_on_merge": False,
        "security_and_analysis": {"secret_scanning": {"status": "disabled"}},
    }
    mock_client.get_repo.return_value = repo_response
    mock_client.update_repo_settings.side_effect = lambda payload: repo_response.update(payload)
    mock_client.update_security_and_analysis.return_value = None  # 422: GHAS not licensed
    config_path = tmp_path / "policy.yml"
    config_path.write_text(
        "version: 1\nbranches: {}\nrepo_settings:\n"
        "  delete_branch_on_merge: true\n  secret_scanning: true\n"
    )
    runner = CliRunner()
    result = runner.invoke(
        main, ["apply", "--config", str(config_path), "--repo", "acme/widgets", "--token", "t"]
    )
    assert result.exit_code == 1, result.output
    assert "repo settings: applied 1 change(s)" in result.output
    assert "repo settings: secret_scanning unavailable on this repository" in result.output
    assert "apply completed but policy is not converged" in result.output


@patch("repo_policy.cli.GitHubClient")
def test_audit_reports_drift_for_declared_but_unavailable_setting_with_no_other_changes(
    mock_client_cls, tmp_path
):
    """A declared repo-setting that's structurally ineligible (e.g. private_vulnerability_reporting
    on a repo that doesn't support it) must not be silently dropped just because there's zero
    other drift -- the policy can never actually be satisfied, so this must not report compliant."""
    mock_client = mock_client_cls.return_value.__enter__.return_value
    mock_client.get_repo.return_value = {}
    mock_client.get_private_vulnerability_reporting.return_value = None  # ineligible
    config_path = tmp_path / "policy.yml"
    config_path.write_text(
        "version: 1\nbranches: {}\nrepo_settings:\n  private_vulnerability_reporting: true\n"
    )
    runner = CliRunner()
    result = runner.invoke(
        main, ["audit", "--config", str(config_path), "--repo", "acme/widgets", "--token", "t"]
    )
    assert result.exit_code == 1
    assert "private_vulnerability_reporting" in result.output
    assert "unavailable" in result.output


@patch("repo_policy.cli.GitHubClient")
def test_apply_applies_repo_settings_drift(mock_client_cls):
    mock_client = mock_client_cls.return_value.__enter__.return_value
    repo_response = {"delete_branch_on_merge": False}
    mock_client.get_repo.return_value = repo_response
    # Task 4: verify_after_apply re-reads get_repo() after the mutation -- reflect the write so
    # the post-apply convergence check sees compliance and exit 0 holds.
    mock_client.update_repo_settings.side_effect = lambda payload: repo_response.update(payload)
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "apply",
            "--config",
            "tests/fixtures/policy_repo_settings.yml",
            "--repo",
            "acme/widgets",
            "--token",
            "t",
        ],
    )
    assert result.exit_code == 0, result.output
    mock_client.update_repo_settings.assert_called_once_with({"delete_branch_on_merge": True})
    assert "repo settings: applied 1 change(s)" in result.output


@patch("repo_policy.cli.GitHubClient")
def test_audit_reports_stale_branch_protection_and_exits_1(mock_client_cls, tmp_path):
    mock_client = mock_client_cls.return_value.__enter__.return_value
    mock_client.find_ruleset_by_name.return_value = None
    mock_client.get_branch_protection.return_value = {"enforce_admins": {"enabled": True}}
    config_path = tmp_path / "policy.yml"
    config_path.write_text("version: 1\nbranches:\n  main:\n    enforcement: ruleset\n")
    runner = CliRunner()
    result = runner.invoke(
        main, ["audit", "--config", str(config_path), "--repo", "acme/widgets", "--token", "t"]
    )
    assert result.exit_code == 1
    assert "stale classic branch protection" in result.output


@patch("repo_policy.cli.GitHubClient")
def test_apply_reports_stale_branch_protection_warning_and_exits_1(mock_client_cls, tmp_path):
    """Task 4: repo-policy never deletes classic branch protection automatically (see
    detect_stale_branch_protection's docstring), so once flagged it can never self-resolve -- but
    apply must now report that as non-convergence via exit 1 (matching what audit/plan already do
    for the same condition, e.g. test_audit_reports_stale_branch_protection_and_exits_1), not
    silently exit 0 just because the ruleset mutation itself succeeded. The ruleset side of this
    branch is round-tripped (created, then read back as canonical and effective) so exit 1 is
    isolated to the stale-protection condition under test, not an artifact of a ruleset mock that
    never reflects its own mutation."""
    mock_client = mock_client_cls.return_value.__enter__.return_value
    mock_client.get_branch_protection.return_value = {"enforce_admins": {"enabled": True}}
    ruleset_state: dict = {}

    def _find_ruleset_by_name(name, rulesets=None):
        return ruleset_state.get(name)

    def _create_ruleset(payload):
        created = {**payload, "id": 1}
        ruleset_state[payload["name"]] = created
        return created

    mock_client.find_ruleset_by_name.side_effect = _find_ruleset_by_name
    mock_client.create_ruleset.side_effect = _create_ruleset
    mock_client.get_rules_for_branch.return_value = [
        {"type": "required_linear_history", "ruleset_id": 1, "ruleset_source_type": "Repository"}
    ]
    config_path = tmp_path / "policy.yml"
    config_path.write_text(
        "version: 1\nbranches:\n  main:\n    enforcement: ruleset\n    linear_history: true\n"
    )
    runner = CliRunner()
    result = runner.invoke(
        main, ["apply", "--config", str(config_path), "--repo", "acme/widgets", "--token", "t"]
    )
    assert result.exit_code == 1, result.output
    assert "classic branch protection still exists" in result.output
    assert "apply completed but policy is not converged" in result.output


@patch("repo_policy.cli.GitHubClient")
def test_audit_reports_config_error_on_invalid_managed_scope_merge(mock_client_cls, tmp_path):
    mock_client = mock_client_cls.return_value.__enter__.return_value
    mock_client.get_branch_protection.return_value = {
        "allow_fork_syncing": True,
        "lock_branch": True,
    }
    mock_client.get_required_signatures.return_value = False
    config_path = tmp_path / "policy.yml"
    config_path.write_text("version: 1\nbranches:\n  main:\n    lock_branch: false\n")
    runner = CliRunner()
    result = runner.invoke(
        main, ["audit", "--config", str(config_path), "--repo", "acme/widgets", "--token", "t"]
    )
    assert result.exit_code == 2


@patch("repo_policy.cli.GitHubClient")
def test_audit_reports_usage_error_when_git_remote_command_fails(
    mock_client_cls, tmp_path, monkeypatch
):
    """A nonzero exit from `git remote get-url origin` (e.g. no such remote) must not be silently
    parsed as if it succeeded -- only checking stdout content (not returncode) risks treating a
    failing command's incidental stdout as a valid owner/repo pair."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    (tmp_path / "policy.yml").write_text("version: 1\nbranches:\n  main: {}\n")
    with patch("repo_policy.cli.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            returncode=128, stdout="", stderr="fatal: no such remote 'origin'"
        )
        runner = CliRunner()
        result = runner.invoke(main, ["audit", "--token", "t"])
    assert result.exit_code == 2
    assert "could not determine repository" in result.output
    mock_client_cls.assert_not_called()


@patch("repo_policy.cli.subprocess.run", side_effect=FileNotFoundError("git not found"))
@patch("repo_policy.cli.GitHubClient")
def test_audit_reports_usage_error_when_git_binary_is_missing(
    mock_client_cls, mock_run, tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    (tmp_path / "policy.yml").write_text("version: 1\nbranches:\n  main: {}\n")
    runner = CliRunner()
    result = runner.invoke(main, ["audit", "--token", "t"])
    assert result.exit_code == 2
    assert "could not determine repository" in result.output
    mock_client_cls.assert_not_called()


@patch("repo_policy.cli.GitHubClient")
def test_apply_exits_3_and_reports_partial_success_when_a_later_branch_mutation_fails(
    mock_client_cls, tmp_path
):
    """Task 3: "main" (declared first) mutates cleanly, then "release" (declared second) raises --
    the CLI must still report "main: applied ..." before the terminal error, not lose it behind
    the uncaught exception, and must exit 3."""
    mock_client = mock_client_cls.return_value.__enter__.return_value
    mock_client.get_branch_protection.return_value = None
    mock_client.get_required_signatures.return_value = False

    def _put_branch_protection(branch, payload):
        if branch == "release":
            raise GitHubAPIError("boom", status_code=500)
        return {}

    mock_client.put_branch_protection.side_effect = _put_branch_protection
    config_path = tmp_path / "policy.yml"
    config_path.write_text(
        "version: 1\nbranches:\n  main:\n    linear_history: true\n  release:\n    linear_history: true\n"
    )
    runner = CliRunner()
    result = runner.invoke(
        main, ["apply", "--config", str(config_path), "--repo", "acme/widgets", "--token", "t"]
    )
    assert result.exit_code == 3
    assert "main: applied" in result.output
    assert "release: failed" in result.output


@patch("repo_policy.cli.GitHubClient")
def test_apply_exits_3_and_reports_partial_success_when_a_repo_setting_mutation_fails(
    mock_client_cls, tmp_path
):
    """Task 3: vulnerability_alerts succeeds, then automated_security_fixes raises -- output must
    identify both the completed and the failed repo-setting operations, and exit 3."""
    mock_client = mock_client_cls.return_value.__enter__.return_value
    mock_client.get_repo.return_value = {}
    mock_client.get_vulnerability_alerts.return_value = False
    mock_client.get_automated_security_fixes.return_value = False
    mock_client.enable_automated_security_fixes.side_effect = GitHubAPIError(
        "boom", status_code=500
    )
    config_path = tmp_path / "policy.yml"
    config_path.write_text(
        "version: 1\nbranches: {}\nrepo_settings:\n"
        "  vulnerability_alerts: true\n  automated_security_fixes: true\n"
    )
    runner = CliRunner()
    result = runner.invoke(
        main, ["apply", "--config", str(config_path), "--repo", "acme/widgets", "--token", "t"]
    )
    assert result.exit_code == 3
    assert "repo settings: vulnerability_alerts: applied" in result.output
    assert "repo settings: automated_security_fixes: failed" in result.output


@patch("repo_policy.cli.GitHubClient")
def test_audit_reports_config_error_when_client_construction_fails_under_proxy_misconfiguration(
    mock_client_cls,
):
    """Task 5: a SOCKS-scheme proxy env var (e.g. ALL_PROXY=socks5h://...) without the optional
    `socksio` package makes httpx.Client(...) raise ImportError inside GitHubClient.__init__ --
    previously this propagated uncaught, surfacing as a raw traceback with Python's default exit
    code (colliding with EXIT_DRIFT == 1, making it impossible for a CI pipeline branching on exit
    code to tell "proxy/setup is broken" apart from "there's real policy drift"). Client-
    construction failures of any kind must become a clean, actionable EXIT_CONFIG_ERROR (2)."""
    mock_client_cls.side_effect = ImportError(
        "Using SOCKS proxy, but the 'socksio' package is not installed. Make sure to install "
        "httpx using `pip install httpx[socks]`."
    )
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "audit",
            "--config",
            "tests/fixtures/policy_no_requirements.yml",
            "--repo",
            "acme/widgets",
            "--token",
            "t",
        ],
    )
    assert result.exit_code == 2
    assert "Traceback" not in result.output
    output_lower = result.output.lower()
    assert "proxy" in output_lower
    assert "socks" in output_lower


def test_github_client_construction_succeeds_under_a_socks_proxy_environment(monkeypatch):
    """Task 5: with the `httpx[socks]` extra installed, a SOCKS-scheme proxy env var must not
    raise ImportError at client-construction time -- this is what makes the exit-2 wrapping in
    _build_client a true "predictable exit code" rather than a permanent workaround papering over
    a broken default install. No request is performed -- construction and close only."""
    monkeypatch.setenv("ALL_PROXY", "socks5h://127.0.0.1:9")
    client = GitHubClient(token="t", owner="acme", repo="widgets")
    client.close()


@patch("repo_policy.cli.GitHubClient")
def test_cli_errors_never_leak_the_supplied_token(mock_client_cls):
    """Task 8 security-hygiene regression test: the token supplied via --token flows into
    GitHubClient solely to build the `Authorization: Bearer <token>` header (github_client.py's
    __init__) -- it is never stored anywhere else and no code path is supposed to interpolate it
    into a rendered message (see render.py's own docstring: "never a token or a full API
    request/response payload"). This test forces a real failure (a GitHubAPIError, the same
    exception type a live 401/403/500 response raises) on the first API call each of audit/plan/
    apply makes, with a distinctive, easily-`grep`-able fake token supplied, and asserts that
    token string never appears anywhere in stdout/stderr -- guarding against a future change that
    accidentally interpolates the raw token into a GitHubAPIError message, a click exception, or
    an uncaught traceback instead of the response text alone."""
    secret_token = (
        "ghp_ThisTokenMustNeverAppearInAnyCLIOutput000111"  # test fixture, not a real credential
    )
    mock_client = mock_client_cls.return_value.__enter__.return_value
    mock_client.get_branch_protection.side_effect = GitHubAPIError(
        "GitHub API error 401 on GET /repos/acme/widgets/branches/main/protection: Bad credentials",
        status_code=401,
    )
    runner = CliRunner()
    for args in (
        [
            "audit",
            "--config",
            "tests/fixtures/policy_valid.yml",
            "--repo",
            "acme/widgets",
            "--token",
            secret_token,
        ],
        [
            "plan",
            "--config",
            "tests/fixtures/policy_valid.yml",
            "--repo",
            "acme/widgets",
            "--token",
            secret_token,
        ],
        [
            "apply",
            "--config",
            "tests/fixtures/policy_valid.yml",
            "--repo",
            "acme/widgets",
            "--token",
            secret_token,
        ],
    ):
        result = runner.invoke(main, args)
        assert result.exit_code == 3, result.output
        assert secret_token not in result.output
        assert "Bad credentials" in result.output  # the real GitHub error text still surfaces


def test_version_flag_prints_repo_policy_dunder_version_and_exits_0():
    """`--version` must surface repo_policy.__version__ (not a second, independently-maintained
    string) -- exercised against the installed package's own metadata rather than a hardcoded
    literal, so this test can't itself go stale the way __init__.py's hardcoded string once did
    (see docs/test-strategy.md's "__version__/PyPI version divergence" known bug)."""
    runner = CliRunner()
    result = runner.invoke(main, ["--version"])
    assert result.exit_code == 0
    assert repo_policy.__version__ in result.output
    assert importlib.metadata.version("repo-policy") in result.output


@patch("repo_policy.cli.GitHubClient")
def test_audit_reports_flat_setting_unavailable_instead_of_false_drift(mock_client_cls, tmp_path):
    """The bug behind every failed policy-audit.yml run up to 2026-09-21: a token that can't see
    delete_branch_on_merge must produce an 'unavailable' line (still exit 1), never a
    '+ False -> True' change."""
    mock_client = mock_client_cls.return_value.__enter__.return_value
    mock_client.get_repo.return_value = {"full_name": "acme/widgets"}
    config_path = tmp_path / "policy.yml"
    config_path.write_text(
        "version: 1\nbranches: {}\nrepo_settings:\n  delete_branch_on_merge: true\n"
    )
    runner = CliRunner()
    result = runner.invoke(
        main, ["audit", "--config", str(config_path), "--repo", "acme/widgets", "--token", "t"]
    )
    assert result.exit_code == 1
    assert "repo settings: delete_branch_on_merge unavailable on this repository" in result.output
    assert "change(s) required" not in result.output


@pytest.mark.parametrize(
    "remote_url",
    [
        "git@github.com:acme/widgets.git",
        "https://github.com/acme/widgets.git",
        "ssh://git@github.com/acme/widgets",
        "git@github.com-work:acme/widgets.git",  # ~/.ssh/config host alias
    ],
)
def test_resolve_repo_parses_every_supported_origin_url_shape(remote_url, monkeypatch):
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    with patch("repo_policy.cli.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout=remote_url + "\n", stderr="")
        assert _resolve_repo(None) == "acme/widgets"
