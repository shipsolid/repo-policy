from __future__ import annotations

from dataclasses import dataclass

from repo_policy.diff import Change, diff, resolve_desired
from repo_policy.github_client import GitHubClient
from repo_policy.models import BranchPolicy, PolicyConfig, effective_strict
from repo_policy.policies import branch_protection, rulesets


@dataclass
class BranchResult:
    branch: str
    changes: list[Change]
    stale_branch_protection: bool = False

    @property
    def applied(self) -> bool:
        return bool(self.changes)


def prefetch_rulesets(
    client: GitHubClient, config: PolicyConfig, *, force: bool = False
) -> list[dict] | None:
    """Fetch the repo's full ruleset list once per orchestration call (apply_all/audit_all/
    prune_rulesets), instead of each ruleset-enforced branch independently re-fetching it via
    find_ruleset_by_name. Returns None (skip the fetch) unless a branch actually needs it, or
    `force=True` (prune_rulesets always needs the full list, even if no *current* branch is
    ruleset-enforced — an orphan can come from a branch removed from policy.yml entirely)."""
    needs_it = force or any(b.enforcement == "ruleset" for b in config.branches.values())
    return client.list_rulesets() if needs_it else None


def fetch_current(
    client: GitHubClient, branch: str, enforcement: str, *, rulesets_cache: list[dict] | None = None
) -> tuple[BranchPolicy, dict | None, int | None]:
    if enforcement == "branch_protection":
        raw = client.get_branch_protection(branch)
        signed = client.get_required_signatures(branch)
        return branch_protection.from_api(raw, signed_commits=signed), raw, None
    raw = client.find_ruleset_by_name(rulesets.ruleset_name(branch), rulesets=rulesets_cache)
    ruleset_id = raw["id"] if raw else None
    return rulesets.from_api(raw), raw, ruleset_id


def _branch_changes(
    client: GitHubClient,
    branch: str,
    desired: BranchPolicy,
    resolved: BranchPolicy,
    current: BranchPolicy,
    raw: dict | None,
    ruleset_id: int | None,
) -> list[Change]:
    """Field-level drift, plus -- for enforcement: ruleset branches -- the ruleset's own
    canonical-ownership metadata (rulesets.metadata_changes) and, only once neither of those
    reports anything, a live cross-check against GitHub's own effective-rules endpoint
    (GitHubClient.get_rules_for_branch). That ordering matters: it's the mechanism that closes the
    false-compliance gap (a ruleset can look right in both its rule content and its own metadata
    and still not be what GitHub is actually enforcing on this branch) without spending an extra
    API call whenever there's already other drift to report. The effective-rules check is also
    skipped whenever the ruleset has nothing configured to enforce (an intentionally permissive,
    empty ruleset) -- it can never show up as an active rule regardless of how correctly it's
    scoped, so checking would always misreport it as ineffective."""
    changes = diff(resolved, current)
    if desired.enforcement != "ruleset":
        return changes
    changes = changes + rulesets.metadata_changes(branch, raw)
    if changes or ruleset_id is None:
        return changes
    payload = rulesets.to_api_payload(branch, resolved, current_raw=raw)
    if not payload["rules"]:
        return changes
    return changes + _ruleset_effectiveness_changes(client, branch, ruleset_id)


def _ruleset_effectiveness_changes(client: GitHubClient, branch: str, ruleset_id: int) -> list[Change]:
    """When this fires, apply_branch's "repair" is a best-effort re-PUT of the exact same already-
    canonical payload -- there's no metadata field left to correct, since metadata_changes() (the
    only thing that would have given apply something concrete to fix) already reported nothing.
    That re-PUT still has real value (GitHub eventual-consistency lag on the effective-rules view
    is one legitimate cause, and it's a cheap, safe no-op otherwise), but it can't force convergence
    against a cause outside repo-policy's control, e.g. an org-level ruleset override; see
    test_apply_twice_against_effectiveness_only_drift_converges_to_zero_changes for the case this
    does resolve on a second apply."""
    active_ruleset_ids = {rule.get("ruleset_id") for rule in client.get_rules_for_branch(branch)}
    if ruleset_id in active_ruleset_ids:
        return []
    return [
        Change(
            field="ruleset_effectiveness",
            current_value="not contributing an active rule on this branch",
            desired_value="active",
            action="modify",
        )
    ]


def plan_branch(
    client: GitHubClient, config: PolicyConfig, branch: str, *, rulesets_cache: list[dict] | None = None
) -> tuple[list[Change], BranchPolicy]:
    desired = config.branches[branch]
    current, raw, ruleset_id = fetch_current(
        client, branch, desired.enforcement, rulesets_cache=rulesets_cache
    )
    resolved = resolve_desired(desired, current, strict=effective_strict(config, branch))
    changes = _branch_changes(client, branch, desired, resolved, current, raw, ruleset_id)
    return changes, resolved


def apply_branch(
    client: GitHubClient, config: PolicyConfig, branch: str, *, rulesets_cache: list[dict] | None = None
) -> BranchResult:
    desired = config.branches[branch]
    current, raw, ruleset_id = fetch_current(
        client, branch, desired.enforcement, rulesets_cache=rulesets_cache
    )
    resolved = resolve_desired(desired, current, strict=effective_strict(config, branch))
    changes = _branch_changes(client, branch, desired, resolved, current, raw, ruleset_id)
    stale = (
        desired.enforcement == "ruleset" and client.get_branch_protection(branch) is not None
    )

    if not changes:
        return BranchResult(branch=branch, changes=[], stale_branch_protection=stale)

    if desired.enforcement == "branch_protection":
        payload = branch_protection.to_api_payload(resolved, raw)
        client.put_branch_protection(branch, payload)
        if resolved.signed_commits != current.signed_commits:
            client.set_required_signatures(branch, bool(resolved.signed_commits))
    else:
        payload = rulesets.to_api_payload(branch, resolved, current_raw=raw)
        if ruleset_id is None:
            client.create_ruleset(payload)
        else:
            client.update_ruleset(ruleset_id, payload)

    return BranchResult(branch=branch, changes=changes, stale_branch_protection=stale)


def apply_all(
    client: GitHubClient, config: PolicyConfig, *, rulesets_cache: list[dict] | None = None
) -> list[BranchResult]:
    if rulesets_cache is None:
        rulesets_cache = prefetch_rulesets(client, config)
    return [
        apply_branch(client, config, branch, rulesets_cache=rulesets_cache) for branch in config.branches
    ]


def find_orphaned_ruleset_names(config: PolicyConfig, all_rulesets: list[dict]) -> list[str]:
    """`repo-policy:` rulesets whose branch is no longer declared under enforcement: ruleset --
    either removed from policy.yml entirely, or still present but switched to
    enforcement: branch_protection. Only ever matches rulesets following the `repo-policy:`
    naming convention, so branch-name-plus-enforcement is enough to prove ownership -- never
    matches anything else. Shared by prune_rulesets (which deletes them, strict-mode only) and
    audit.detect_orphaned_rulesets (which reports them before that deletion happens, gated the
    same way)."""
    declared_ruleset_names = {
        rulesets.ruleset_name(branch)
        for branch, policy in config.branches.items()
        if policy.enforcement == "ruleset"
    }
    return [
        summary["name"]
        for summary in all_rulesets
        if summary["name"].startswith("repo-policy:") and summary["name"] not in declared_ruleset_names
    ]


def prune_rulesets(
    client: GitHubClient, config: PolicyConfig, *, rulesets_cache: list[dict] | None = None
) -> list[str]:
    """Strict-mode only, gated by the top-level `strict` default (a removed branch has no
    per-branch setting left to consult)."""
    all_rulesets = rulesets_cache if rulesets_cache is not None else client.list_rulesets()
    orphaned_names = set(find_orphaned_ruleset_names(config, all_rulesets))
    deleted: list[str] = []
    for summary in all_rulesets:
        if summary["name"] in orphaned_names:
            client.delete_ruleset(summary["id"])
            deleted.append(summary["name"])
    return deleted


def detect_stale_branch_protection(client: GitHubClient, config: PolicyConfig) -> list[str]:
    """Branches declared under enforcement: ruleset that still have a classic branch-protection
    object on GitHub -- most likely left over from a prior enforcement: branch_protection policy.
    repo-policy cannot safely delete classic branch protection (no ownership marker distinguishes
    what it created from what a human configured by hand -- see ARCHITECTURE.md's documented
    limitation for the equivalent branch-removal case), so this only detects and reports it;
    removing it is a manual, GitHub-side action. This costs one extra GET per ruleset-enforced
    branch on every audit/plan/apply run -- there's no metadata to tell "always was ruleset" apart
    from "just switched from branch_protection" without checking live state every time."""
    return [
        branch
        for branch, policy in config.branches.items()
        if policy.enforcement == "ruleset" and client.get_branch_protection(branch) is not None
    ]
