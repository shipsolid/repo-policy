from __future__ import annotations

import os
import subprocess
import sys

import click

from repo_policy.apply import (
    PartialApplyError,
    apply_all,
    detect_stale_branch_protection,
    prefetch_rulesets,
    prune_rulesets,
)
from repo_policy.audit import audit_all
from repo_policy.config import ConfigError, load_policy
from repo_policy.diff import PolicyResolutionError
from repo_policy.github_client import GitHubAPIError, GitHubClient
from repo_policy.models import PolicyConfig
from repo_policy.render import render_apply_journal, render_plan, render_repo_settings
from repo_policy.repo_settings import apply_repo_settings, plan_repo_settings

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
    url = result.stdout.strip()
    url = url.removesuffix(".git")
    for separator in ("github.com:", "github.com/"):
        if separator in url:
            return url.split(separator, 1)[1]
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
    click.ClickException (from _resolve_repo/_split_repo/_resolve_token) both propagate uncaught
    -- each caller keeps its own ConfigError exit-code handling, while ClickException is already
    handled automatically by click's own command dispatch."""
    config = load_policy(config_path)
    resolved_repo = _resolve_repo(repo)
    owner, name = _split_repo(resolved_repo)
    client = GitHubClient(token=_resolve_token(token), owner=owner, repo=name)
    return config, resolved_repo, client


@click.group()
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

    any_drift = False
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
        any_drift = any_drift or not result.compliant

    for ruleset_name in orphaned_rulesets:
        click.echo(
            f"orphaned ruleset {ruleset_name} detected -- its branch is no longer declared "
            "under enforcement: ruleset; the next apply (strict mode) will remove it"
        )
    any_drift = any_drift or bool(orphaned_rulesets)

    if repo_settings_result.changes or repo_settings_result.unavailable:
        if render:
            click.echo(render_repo_settings(resolved_repo, repo_settings_result))
        else:
            if repo_settings_result.changes:
                click.echo(f"repo settings: {len(repo_settings_result.changes)} change(s) required")
            for field_name in repo_settings_result.unavailable:
                click.echo(f"repo settings: {field_name} unavailable on this repository")
        any_drift = True

    if not any_drift and not render:
        click.echo(f"{resolved_repo} is compliant.")
    return EXIT_DRIFT if any_drift else EXIT_OK


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
        config, _resolved_repo, client = _build_client(config_path, repo, token)
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
    except GitHubAPIError as exc:
        click.echo(str(exc), err=True)
        sys.exit(EXIT_API_ERROR)
    except PolicyResolutionError as exc:
        click.echo(str(exc), err=True)
        sys.exit(EXIT_CONFIG_ERROR)

    if repo_settings_result.applied:
        applied_count = sum(
            1 for c in repo_settings_result.changes if c.field not in repo_settings_result.unavailable
        )
        click.echo(f"repo settings: applied {applied_count} change(s)")
    for field_name in repo_settings_result.unavailable:
        click.echo(f"repo settings: {field_name} unavailable on this repository")

    sys.exit(EXIT_OK)
