---
title: "repo-policy Documentation"
description: "Documentation hub for repo-policy — declarative GitHub repository governance: branch protection and ruleset configuration as YAML, audited and applied locally or in CI."
---

Documentation for `repo-policy` — a lightweight, declarative tool for GitHub branch protection and
repository ruleset configuration. Define the expected state in YAML; audit, preview, and apply it
locally or in CI.

## Architecture decisions

| Page | What it covers |
| ---- | -------------- |
| [ADR 0001](adrs/0001-use-httpx-over-pygithub.md) | `httpx` directly instead of PyGithub for the GitHub API client |
| [ADR 0002](adrs/0002-explicit-enforcement-field-per-branch.md) | Branch protection vs. ruleset backend, selected per branch |
| [ADR 0003](adrs/0003-managed-scope-default-strict-opt-in.md) | Managed-scope (non-destructive) default; full enforcement is opt-in |
| [ADR 0004](adrs/0004-ruleset-ownership-via-naming-convention.md) | Ruleset ownership by naming convention, not a state file |
| [ADR 0005](adrs/0005-nested-actor-list-fields.md) | Nested actor-list field validation, all-empty-declaration rule |

## Guides

| Page | What it covers |
| ---- | -------------- |
| [CI/CD Pipeline](ci-cd.md) | Workflow architecture, gates, release process |
| [Test Strategy](test-strategy.md) | Test pyramid, mocking approach, coverage |
| [Troubleshooting](troubleshooting.md) | Common errors and how to fix them |
| [Release Readiness: v1 Audit](release-readiness-v1.md) | Audit-remediation verification evidence |

## Reference

| Page | What it covers |
| ---- | -------------- |
| [Project README](project-readme.md) | Full README — features, install, CLI, schema & validation |
| [Architecture](architecture.md) | System design overview |
| [Usage](usage.md) | Detailed usage guide |
| [FAQ](faq.md) | Frequently asked questions |
| [Use Cases](usecases.md) | Real-world use cases this tool covers |
| [Roadmap](roadmap.md) | Planned features |
| [Changelog](changelog.md) | Release history |
| [Contributing](contributing.md) | How to contribute |
| [Code of Conduct](code-of-conduct.md) | Community standards |
| [Security](security.md) | Vulnerability reporting, token permissions |
| [Support](support.md) | Getting help |

## Source

Full source code lives at [github.com/shipsolid/repo-policy](https://github.com/shipsolid/repo-policy).
