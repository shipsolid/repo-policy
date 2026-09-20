from unittest.mock import MagicMock

from repo_policy.apply import apply_all
from repo_policy.models import BranchPolicy, PolicyConfig, PullRequestPolicy, StatusChecksPolicy


def test_apply_twice_against_already_compliant_repo_makes_zero_mutating_calls():
    config = PolicyConfig(
        version=1,
        branches={
            "main": BranchPolicy(
                pull_requests=PullRequestPolicy(required=True, approvals=2, code_owner_review=True),
                status_checks=StatusChecksPolicy(required=["build", "test"]),
                signed_commits=True,
                linear_history=True,
                allow_force_push=False,
                allow_deletion=False,
            )
        },
    )

    client = MagicMock()
    # First call: nothing exists yet.
    client.get_branch_protection.return_value = None
    client.get_required_signatures.return_value = False

    first_summary = apply_all(client, config)
    assert first_summary.journal[0].status == "applied"
    client.put_branch_protection.assert_called_once()
    put_payload = client.put_branch_protection.call_args.args[1]
    client.set_required_signatures.assert_called_once_with("main", True)

    # Second call: the mock now reflects exactly what the first apply wrote.
    client.reset_mock()
    client.get_branch_protection.return_value = put_payload
    client.get_required_signatures.return_value = True

    second_summary = apply_all(client, config)
    assert second_summary.journal[0].status == "verified"
    assert second_summary.journal[0].changes == []
    client.put_branch_protection.assert_not_called()
    client.set_required_signatures.assert_not_called()
    client.create_ruleset.assert_not_called()
    client.update_ruleset.assert_not_called()
    client.delete_ruleset.assert_not_called()
