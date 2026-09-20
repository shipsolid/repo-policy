from __future__ import annotations

from dataclasses import dataclass

from repo_policy.apply import (
    detect_stale_branch_protection,
    find_orphaned_ruleset_names,
    plan_branch,
    prefetch_rulesets,
)
from repo_policy.diff import Change
from repo_policy.github_client import GitHubClient
from repo_policy.models import PolicyConfig


@dataclass
class AuditResult:
    branch: str
    changes: list[Change]
    stale_branch_protection: bool = False

    @property
    def compliant(self) -> bool:
        return not self.changes and not self.stale_branch_protection


def detect_orphaned_rulesets(
    client: GitHubClient, config: PolicyConfig, *, rulesets_cache: list[dict] | None = None
) -> list[str]:
    """Strict-mode only, gated the same way as prune_rulesets itself (a removed branch has no
    per-branch setting left to consult, and outside strict mode prune_rulesets is never invoked --
    see cli.py's `if config.strict:` gate -- so warning about an orphan that will never actually
    be pruned would be noise). Surfaces which `repo-policy:` rulesets the next strict apply would
    delete, so audit/plan can report that upcoming destructive action as drift instead of
    reporting full compliance right up until it happens."""
    if not config.strict:
        return []
    all_rulesets = rulesets_cache if rulesets_cache is not None else client.list_rulesets()
    return find_orphaned_ruleset_names(config, all_rulesets)


def audit_all(client: GitHubClient, config: PolicyConfig) -> tuple[list[AuditResult], list[str]]:
    rulesets_cache = prefetch_rulesets(client, config, force=config.strict)
    stale_branches = set(detect_stale_branch_protection(client, config))
    results = []
    for branch in config.branches:
        changes, _resolved = plan_branch(client, config, branch, rulesets_cache=rulesets_cache)
        results.append(
            AuditResult(
                branch=branch, changes=changes, stale_branch_protection=branch in stale_branches
            )
        )
    orphaned_rulesets = detect_orphaned_rulesets(client, config, rulesets_cache=rulesets_cache)
    return results, orphaned_rulesets
