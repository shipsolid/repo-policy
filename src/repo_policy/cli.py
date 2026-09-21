from __future__ import annotations

import os
import re
import subprocess
import sys

import click

from repo_policy import __version__
from repo_policy.apply import (
    PartialApplyError,
    apply_all,
    detect_stale_branch_protection,
    prefetch_rulesets,
    prune_rulesets,
)
from repo_policy.audit import AuditResult, audit_all
from repo_policy.config import ConfigError, load_policy
from repo_policy.diff import PolicyResolutionError
from repo_policy.github_client import GitHubAPIError, GitHubClient
from repo_policy.models import PolicyConfig
from repo_policy.render import render_apply_journal, render_plan, render_repo_settings
from repo_policy.repo_settings import RepoSettingsResult, apply_repo_settings, plan_repo_settings

EXIT_OK = 0
EXIT_DRIFT = 1
EXIT_CONFIG_ERROR = 2
EXIT_API_ERROR = 3


class _ConfigClickException(click.ClickException):
    """Setup/config failures (missing token, unresolvable repo, malformed --repo) are policy.yml-
    adjacent user errors, not policy drift -- ClickException's default exit_code (1) collides with
    this CLI's own EXIT_DRIFT (1), which would make a CI pipeline branching on exit code unable to
    tell a missing GITHUB_TOKEN apart from real drift. Force EXIT_CONFIG_ERROR (2) instead."""

    exit_code = EXIT_CONFIG_ERROR


def _config_error(message: str) -> click.ClickException:
    return _ConfigClickException(message)


def _resolve_token(token: str | None) -> str:
    resolved = token or os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not resolved:
        raise _config_error("no GitHub token found; pass --token or set GITHUB_TOKEN/GH_TOKEN")
    return resolved


# `(?:^|[@/])` anchors on an actual host-start position (start of string, or right after `@`/`/`)
# so a lookalike domain that merely contains the substring `github.com` -- `mygithub.com`,
# `github.company.com`, etc. -- never matches; the optional `(?:-[^/:]*)?` is the ~/.ssh/config
# host-alias suffix (github.com-work), then the `:` of scp-style SSH or the `/` of https:// and
# ssh://, then exactly owner/name.
_GITHUB_REMOTE = re.compile(r"(?:^|[@/])github\.com(?:-[^/:]*)?[:/](?P<repo>[^/]+/[^/]+)$")


def _resolve_repo(repo: str | None) -> str:
    if repo:
        return repo
    env_repo = os.environ.get("GITHUB_REPOSITORY")
    if env_repo:
        return env_repo
    try:
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"], capture_output=True, text=True, check=False
        )
    except FileNotFoundError:
        raise _config_error("could not determine repository; pass --repo owner/name") from None
    if result.returncode != 0:
        raise _config_error("could not determine repository; pass --repo owner/name") from None
    url = result.stdout.strip().removesuffix(".git")
    match = _GITHUB_REMOTE.search(url)
    if match:
        return match.group("repo")
    raise _config_error("could not determine repository; pass --repo owner/name")


def _split_repo(resolved_repo: str) -> tuple[str, str]:
    parts = resolved_repo.split("/")
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise _config_error(f"invalid repository {resolved_repo!r}; expected 'owner/name'")
    owner, name = parts
    return owner, name


def _build_client(
    config_path: str, repo: str | None, token: str | None
) -> tuple[PolicyConfig, str, GitHubClient]:
    """Shared setup for _run_check (audit/plan) and apply: load the policy, resolve the target
    repository, and construct the GitHub client. ConfigError (from load_policy) and
    click.ClickException (from _resolve_repo/_split_repo/_resolve_token, and now also from a
    failed GitHubClient(...) construction below) all propagate uncaught -- each caller keeps its
    own ConfigError exit-code handling, while click.ClickException is already handled
    automatically by click's own command dispatch: Command.main() wraps the entire
    self.invoke(ctx) call (the whole group -> subcommand dispatch chain) in a single
    `except ClickException` that calls e.show() and sys.exit(e.exit_code), regardless of how deep
    in the call stack the exception was raised -- so neither _run_check nor apply needs its own
    except block for it.

    A GitHubClient(...) construction failure (e.g. ImportError: 'socksio' is not installed, when
    a SOCKS-scheme proxy env var like ALL_PROXY=socks5h://... is set but the httpx[socks] extra
    is missing) previously propagated as a raw, uncaught exception -- surfacing as a Python
    traceback with Python's own default exit code, which could collide with this CLI's
    EXIT_DRIFT (1) and make it impossible for a CI pipeline branching on exit code to tell
    "proxy/setup is broken" apart from "there's real policy drift". It's a setup problem, not
    drift and not an attempted-and-failed API call, so it's wrapped into the same
    _ConfigClickException (exit 2) as every other config/setup failure above."""
    config = load_policy(config_path)
    resolved_repo = _resolve_repo(repo)
    owner, name = _split_repo(resolved_repo)
    token_value = _resolve_token(token)
    try:
        client = GitHubClient(token=token_value, owner=owner, repo=name)
    except Exception as exc:
        raise _config_error(
            f"could not initialize GitHub client: {exc}; this is usually a proxy "
            "misconfiguration -- check HTTP_PROXY/HTTPS_PROXY/ALL_PROXY/NO_PROXY, and if you're "
            "using a SOCKS proxy (socks5/socks5h/socks4 scheme), confirm SOCKS support is "
            "installed (this package depends on httpx[socks]; reinstall repo-policy if it's "
            "missing)"
        ) from exc
    return config, resolved_repo, client


@click.group()
@click.version_option(version=__version__, prog_name="repo-policy")
def main() -> None:
    """repo-policy: declarative GitHub repository governance."""


@main.command()
@click.option("--config", "config_path", default="policy.yml", show_default=True)
def validate(config_path: str) -> None:
    try:
        load_policy(config_path)
    except ConfigError as exc:
        click.echo(str(exc), err=True)
        sys.exit(EXIT_CONFIG_ERROR)
    click.echo(f"{config_path} is valid.")
    sys.exit(EXIT_OK)


def _compute_drift(
    results: list[AuditResult],
    orphaned_rulesets: list[str],
    repo_settings_result: RepoSettingsResult,
) -> bool:
    """The one drift definition shared by _run_check (audit/plan) and verify_after_apply (apply's
    post-mutation convergence check): any branch's own noncompliance (declared-field drift,
    ruleset-ownership metadata, live-effectiveness cross-check, or stale classic branch
    protection -- see AuditResult.compliant), any orphaned ruleset a strict apply would still
    need to prune, or repo-settings drift/unavailability. Kept in exactly one place so apply's
    post-mutation compliance bar can never silently diverge from what audit/plan already treat as
    drift."""
    return (
        any(not result.compliant for result in results)
        or bool(orphaned_rulesets)
        or not repo_settings_result.compliant
    )


def _render_findings(
    resolved_repo: str,
    results: list[AuditResult],
    orphaned_rulesets: list[str],
    repo_settings_result: RepoSettingsResult,
    *,
    render: bool,
) -> None:
    """Prints exactly what _run_check (audit/plan) has always printed for a given
    results/orphaned_rulesets/repo_settings_result triple -- factored out so apply's post-mutation
    convergence-failure report can show the same per-resource detail (Task 4) instead of a second,
    drifting copy of these message strings."""
    for result in results:
        if render:
            click.echo(render_plan(resolved_repo, result.branch, result.changes))
        elif result.changes:
            click.echo(f"{result.branch}: {len(result.changes)} change(s) required")
        if result.stale_branch_protection:
            click.echo(
                f"{result.branch}: stale classic branch protection detected -- this branch is "
                "declared under enforcement: ruleset but GitHub still has a classic "
                "branch-protection object for it, most likely left over from a prior "
                "enforcement: branch_protection policy; repo-policy cannot safely remove it "
                "automatically (no ownership marker), remove it manually if it's no longer wanted"
            )

    for ruleset_name in orphaned_rulesets:
        click.echo(
            f"orphaned ruleset {ruleset_name} detected -- its branch is no longer declared "
            "under enforcement: ruleset; the next apply (strict mode) will remove it"
        )

    if not repo_settings_result.compliant:
        if render:
            click.echo(render_repo_settings(resolved_repo, repo_settings_result))
        else:
            if repo_settings_result.changes:
                click.echo(f"repo settings: {len(repo_settings_result.changes)} change(s) required")
            for field_name in repo_settings_result.unavailable:
                click.echo(f"repo settings: {field_name} unavailable on this repository")


def _run_check(config_path: str, repo: str | None, token: str | None, *, render: bool) -> int:
    try:
        config, resolved_repo, client = _build_client(config_path, repo, token)
    except ConfigError as exc:
        click.echo(str(exc), err=True)
        return EXIT_CONFIG_ERROR

    try:
        with client as client:
            results, orphaned_rulesets = audit_all(client, config)
            repo_settings_result = plan_repo_settings(client, config)
    except GitHubAPIError as exc:
        click.echo(str(exc), err=True)
        return EXIT_API_ERROR
    except PolicyResolutionError as exc:
        click.echo(str(exc), err=True)
        return EXIT_CONFIG_ERROR

    _render_findings(resolved_repo, results, orphaned_rulesets, repo_settings_result, render=render)

    any_drift = _compute_drift(results, orphaned_rulesets, repo_settings_result)
    if not any_drift and not render:
        click.echo(f"{resolved_repo} is compliant.")
    return EXIT_DRIFT if any_drift else EXIT_OK


def verify_after_apply(
    client: GitHubClient, config: PolicyConfig
) -> tuple[list[AuditResult], list[str], RepoSettingsResult]:
    """Independent, fully-fresh re-check of live GitHub state, run after apply's mutation phase
    completes without raising -- the mechanism that closes both gaps Task 4 targets: a 2xx
    response is never trusted as proof a policy took effect, and a declared repo setting that
    comes back `unavailable` is never silently treated as satisfied. Deliberately reuses the exact
    same read-only engine audit/plan already run (audit_all + plan_repo_settings) rather than
    reading back apply's own mutation-response journal (apply.ApplySummary.verification_drift/
    .unavailable) -- a genuine independent re-fetch is a stronger correctness guarantee than
    trusting a summary derived from the mutation calls' own responses. audit_all takes no
    rulesets_cache here on purpose: rulesets may have just been created, updated, or pruned by the
    apply that preceded this call, so a cached pre-mutation list would be stale."""
    results, orphaned_rulesets = audit_all(client, config)
    repo_settings_result = plan_repo_settings(client, config)
    return results, orphaned_rulesets, repo_settings_result


@main.command()
@click.option("--config", "config_path", default="policy.yml", show_default=True)
@click.option("--repo", default=None)
@click.option("--token", default=None)
def audit(config_path: str, repo: str | None, token: str | None) -> None:
    sys.exit(_run_check(config_path, repo, token, render=False))


@main.command()
@click.option("--config", "config_path", default="policy.yml", show_default=True)
@click.option("--repo", default=None)
@click.option("--token", default=None)
def plan(config_path: str, repo: str | None, token: str | None) -> None:
    sys.exit(_run_check(config_path, repo, token, render=True))


@main.command()
@click.option("--config", "config_path", default="policy.yml", show_default=True)
@click.option("--repo", default=None)
@click.option("--token", default=None)
def apply(config_path: str, repo: str | None, token: str | None) -> None:
    try:
        config, resolved_repo, client = _build_client(config_path, repo, token)
    except ConfigError as exc:
        click.echo(str(exc), err=True)
        sys.exit(EXIT_CONFIG_ERROR)

    try:
        with client as client:
            rulesets_cache = prefetch_rulesets(client, config, force=config.strict)
            stale_branches = set(detect_stale_branch_protection(client, config))

            try:
                branch_summary = apply_all(client, config, rulesets_cache=rulesets_cache)
            except PartialApplyError as exc:
                # Render whatever mutations already succeeded before this one failed -- the whole
                # point of this task -- then surface the underlying API error and exit 3, same as
                # an outright GitHubAPIError below.
                for line in render_apply_journal(exc.summary.journal):
                    click.echo(line)
                click.echo(str(exc.cause), err=True)
                sys.exit(EXIT_API_ERROR)

            for line in render_apply_journal(branch_summary.journal):
                click.echo(line)
            for branch in config.branches:
                if branch in stale_branches:
                    click.echo(
                        f"{branch}: classic branch protection still exists on GitHub for this "
                        "ruleset-enforced branch -- remove it manually, repo-policy will not delete it "
                        "automatically"
                    )

            if config.strict:
                for deleted_name in prune_rulesets(client, config, rulesets_cache=rulesets_cache):
                    click.echo(f"- removed orphaned ruleset {deleted_name}")

            try:
                repo_settings_result = apply_repo_settings(client, config)
            except PartialApplyError as exc:
                for line in render_apply_journal(exc.summary.journal):
                    click.echo(line)
                click.echo(str(exc.cause), err=True)
                sys.exit(EXIT_API_ERROR)

            # Task 4: mutations above raised nothing, but a 2xx response is never proof by itself
            # that the policy actually took effect -- re-check live state from scratch (still
            # inside the client's open context) before deciding this apply succeeded.
            verify_results, verify_orphaned_rulesets, verify_repo_settings = verify_after_apply(
                client, config
            )
    except GitHubAPIError as exc:
        click.echo(str(exc), err=True)
        sys.exit(EXIT_API_ERROR)
    except PolicyResolutionError as exc:
        click.echo(str(exc), err=True)
        sys.exit(EXIT_CONFIG_ERROR)

    if repo_settings_result.applied:
        applied_count = sum(
            1
            for c in repo_settings_result.changes
            if c.field not in repo_settings_result.unavailable
        )
        click.echo(f"repo settings: applied {applied_count} change(s)")
    for field_name in repo_settings_result.unavailable:
        click.echo(f"repo settings: {field_name} unavailable on this repository")

    if _compute_drift(verify_results, verify_orphaned_rulesets, verify_repo_settings):
        _render_findings(
            resolved_repo,
            verify_results,
            verify_orphaned_rulesets,
            verify_repo_settings,
            render=True,
        )
        click.echo("apply completed but policy is not converged")
        sys.exit(EXIT_DRIFT)

    sys.exit(EXIT_OK)
