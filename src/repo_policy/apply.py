from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Literal

from repo_policy.diff import Change, diff, resolve_desired
from repo_policy.github_client import GitHubAPIError, GitHubClient
from repo_policy.models import BranchPolicy, PolicyConfig, effective_strict
from repo_policy.policies import branch_protection, rulesets
from repo_policy.policies.repo_settings import RepoSettingChange


@dataclass
class BranchResult:
    branch: str
    changes: list[Change]
    stale_branch_protection: bool = False

    @property
    def applied(self) -> bool:
        return bool(self.changes)


@dataclass
class PlannedBranch:
    """The read+resolve+diff outcome for one declared branch, computed with zero writes --
    everything apply_branch/apply_all's mutation step needs to decide whether, and how, to mutate
    this branch. apply_all's preflight phase builds one of these for every declared branch before
    attempting any branch's mutation, so a later branch's planning failure can never follow an
    earlier branch's write."""

    branch: str
    resolved: BranchPolicy
    current_raw: dict | None
    ruleset_id: int | None
    changes: list[Change]


ApplyStatus = Literal["applied", "verified", "unavailable", "failed"]


@dataclass
class ApplyJournalEntry:
    """One resource's outcome from an apply mutation pass -- a branch (resource is its name) or,
    from repo_settings.apply_repo_settings, one repo-setting mutation group (resource is prefixed
    "repo settings: "). `verified` means the resource was read and diffed with zero drift, so
    nothing was written; `unavailable` means the mutation call completed but GitHub reports the
    field ineligible on this repository (e.g. GitHub Advanced Security not licensed); `failed`
    means the mutation call itself raised. `changes` accepts either diff.Change (branches) or
    policies.repo_settings.RepoSettingChange (repo settings) -- the two are structurally identical
    but nominally distinct, and this journal is shared by both call sites."""

    resource: str
    changes: Sequence[Change | RepoSettingChange]
    status: ApplyStatus


@dataclass
class ApplySummary:
    """Ordered record of an apply run's mutation phase, kept even when the run ends in an error --
    PartialApplyError carries exactly this, so a mutation failure never hides the mutations that
    already succeeded before it. `verification_drift` is the subset of branch changes discovered
    via the live effective-rules cross-check (apply._ruleset_effectiveness_changes) rather than a
    declared-field or ruleset-metadata comparison -- surfaced separately since it reflects GitHub's
    own runtime evaluation, not policy.yml drift. `unavailable` mirrors
    repo_settings.RepoSettingsResult.unavailable for callers that only have an ApplySummary (e.g.
    a PartialApplyError raised mid apply_repo_settings)."""

    journal: list[ApplyJournalEntry] = field(default_factory=list)
    verification_drift: list[Change] = field(default_factory=list)
    unavailable: list[str] = field(default_factory=list)


class PartialApplyError(Exception):
    """Raised when an apply mutation phase (apply_all's branches, or
    repo_settings.apply_repo_settings' sequential calls) fails after one or more earlier mutations
    in the same phase already succeeded. Carries the ApplySummary built so far -- including the
    failed resource's own journal entry -- so the CLI can render exactly what happened before
    exiting, instead of an earlier success being silently lost behind a bare traceback. Preflight
    (read/resolve/diff) failures never raise this: they happen before any mutation is attempted,
    so the underlying GitHubAPIError/PolicyResolutionError propagates on its own -- there's nothing
    partial to report yet."""

    def __init__(self, summary: ApplySummary, cause: Exception) -> None:
        super().__init__(str(cause))
        self.summary = summary
        self.cause = cause


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


def _plan_branch_full(
    client: GitHubClient, config: PolicyConfig, branch: str, *, rulesets_cache: list[dict] | None = None
) -> PlannedBranch:
    """The full read+resolve+diff pass for one branch, performing no write -- the shared preflight
    step behind apply_branch's single-branch path and apply_all's mutation loop. plan_branch
    (below) is a thin projection of this for audit.py, which only needs the changes/resolved
    pair."""
    desired = config.branches[branch]
    current, raw, ruleset_id = fetch_current(
        client, branch, desired.enforcement, rulesets_cache=rulesets_cache
    )
    resolved = resolve_desired(desired, current, strict=effective_strict(config, branch))
    changes = _branch_changes(client, branch, desired, resolved, current, raw, ruleset_id)
    return PlannedBranch(
        branch=branch, resolved=resolved, current_raw=raw, ruleset_id=ruleset_id, changes=changes
    )


def plan_branch(
    client: GitHubClient, config: PolicyConfig, branch: str, *, rulesets_cache: list[dict] | None = None
) -> tuple[list[Change], BranchPolicy]:
    planned = _plan_branch_full(client, config, branch, rulesets_cache=rulesets_cache)
    return planned.changes, planned.resolved


def _mutate_planned_branch(client: GitHubClient, config: PolicyConfig, planned: PlannedBranch) -> None:
    """The single write implied by planned.changes -- unchanged from apply_branch's original
    inline mutation block (branch-protection PUT, or ruleset create/update), only relocated so
    apply_branch's single-branch path and apply_all's mutation loop can share it instead of
    duplicating it. Caller must only call this when planned.changes is non-empty."""
    desired = config.branches[planned.branch]
    if desired.enforcement == "branch_protection":
        payload = branch_protection.to_api_payload(planned.resolved, planned.current_raw)
        client.put_branch_protection(planned.branch, payload)
        if any(change.field == "signed_commits" for change in planned.changes):
            client.set_required_signatures(planned.branch, bool(planned.resolved.signed_commits))
    else:
        payload = rulesets.to_api_payload(planned.branch, planned.resolved, current_raw=planned.current_raw)
        if planned.ruleset_id is None:
            client.create_ruleset(payload)
        else:
            client.update_ruleset(planned.ruleset_id, payload)


def apply_branch(
    client: GitHubClient, config: PolicyConfig, branch: str, *, rulesets_cache: list[dict] | None = None
) -> BranchResult:
    """Single-branch plan-then-mutate, unchanged in behavior and calling contract from before this
    task -- it self-detects stale classic branch protection inline (not via apply_all's preflight)
    so it remains safely callable on its own, outside apply_all's orchestration; see
    test_apply_branch_flags_stale_branch_protection_for_ruleset_enforced_branch and the CHANGELOG
    entry documenting why that inline check was kept apply_branch-local rather than deduplicated
    into apply_all."""
    desired = config.branches[branch]
    planned = _plan_branch_full(client, config, branch, rulesets_cache=rulesets_cache)
    stale = desired.enforcement == "ruleset" and client.get_branch_protection(branch) is not None

    if not planned.changes:
        return BranchResult(branch=branch, changes=[], stale_branch_protection=stale)

    _mutate_planned_branch(client, config, planned)
    return BranchResult(branch=branch, changes=planned.changes, stale_branch_protection=stale)


def _plan_all_branches(
    client: GitHubClient, config: PolicyConfig, *, rulesets_cache: list[dict] | None = None
) -> list[PlannedBranch]:
    """Preflight: read, resolve, and diff every declared branch before any branch is mutated. A
    read/resolve failure (GitHubAPIError, PolicyResolutionError) propagates directly from here,
    before apply_all's mutation loop has run at all -- so "planning failures cause no writes" is
    simply this function never returning a partial list."""
    return [
        _plan_branch_full(client, config, branch, rulesets_cache=rulesets_cache)
        for branch in config.branches
    ]


def apply_all(
    client: GitHubClient, config: PolicyConfig, *, rulesets_cache: list[dict] | None = None
) -> ApplySummary:
    """Preflight every branch (reads only), then mutate one branch at a time, journaling each
    outcome as it happens. A mutation failure retains every journal entry recorded before it (the
    branches that already applied cleanly) and raises PartialApplyError instead of losing them to
    an uncaught exception -- see PartialApplyError's docstring. Note this no longer calls
    apply_branch(): apply_branch's own inline stale-branch-protection self-detection (see its
    docstring) is intentionally not part of this preflight/mutation split, and stays a caller-side
    concern (cli.py calls detect_stale_branch_protection independently, mirroring audit_all)."""
    if rulesets_cache is None:
        rulesets_cache = prefetch_rulesets(client, config)

    planned_branches = _plan_all_branches(client, config, rulesets_cache=rulesets_cache)

    summary = ApplySummary(
        verification_drift=[
            change
            for planned in planned_branches
            for change in planned.changes
            if change.field == "ruleset_effectiveness"
        ]
    )

    for planned in planned_branches:
        if not planned.changes:
            summary.journal.append(
                ApplyJournalEntry(resource=planned.branch, changes=[], status="verified")
            )
            continue
        try:
            _mutate_planned_branch(client, config, planned)
        except GitHubAPIError as exc:
            summary.journal.append(
                ApplyJournalEntry(resource=planned.branch, changes=planned.changes, status="failed")
            )
            raise PartialApplyError(summary, exc) from exc
        summary.journal.append(
            ApplyJournalEntry(resource=planned.branch, changes=planned.changes, status="applied")
        )

    return summary


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
