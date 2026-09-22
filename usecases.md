# Practical use cases

Core use case is **GitHub repository governance as code**: define the expected repository
configuration in YAML, detect drift, preview changes, and enforce it. It currently covers branch
protection, GitHub Rulesets, PR requirements, required checks, branch hygiene, and several
repository security settings.

| Use case                                              | What `repo-policy` does                                                                                                                                            |
| ----------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **1. Branch protection as code**                      | Define protection for `main`, `develop`, `release/*`, etc. instead of configuring each branch manually in GitHub UI.                                               |
| **2. Repository compliance audit**                    | Run `repo-policy audit` to determine whether the live repository matches the declared policy. Useful for periodic governance checks.                               |
| **3. Configuration drift detection**                  | Detect when someone manually changes branch protection, required approvals, status checks, or supported repo settings.                                             |
| **4. Pull-request governance**                        | Require PRs, minimum approvals, CODEOWNER review, stale-review dismissal, and last-push approval.                                                                  |
| **5. CI quality gates**                               | Declare checks such as `build`, `test`, `sast`, `sca`, or `terraform-plan` as mandatory before merge.                                                              |
| **6. Protect production branches**                    | Prevent force-pushes and deletion of `main` or release branches, require signed commits, and enforce linear history.                                               |
| **7. GitHub Rulesets management**                     | Use modern GitHub Rulesets rather than classic branch protection on selected branches.                                                                             |
| **8. Safe migration to Rulesets**                     | Detect stale classic protection after switching a branch to Ruleset-based enforcement.                                                                             |
| **9. Repository security baseline**                   | Enforce supported settings such as Dependabot alerts, automated security fixes, secret scanning, push protection, and private vulnerability reporting.             |
| **10. Pre-change review / change management**         | Run `plan` before `apply` to show exactly what will be added, removed, or modified.                                                                                |
| **11. Automated remediation**                         | Run `apply` to reconcile the repository with `policy.yml`, followed by an independent verification of the resulting state.                                         |
| **12. GitOps-style repository administration**        | Treat repository administration similarly to infrastructure configuration: policy changes go through Git commits and PR review.                                    |
| **13. Policy versioning and audit history**           | Because `policy.yml` lives in Git, you get blame/history showing who changed repository governance, when, and why.                                                 |
| **14. Developer self-service**                        | Developers propose governance changes through PRs rather than requesting an admin to manually modify repository settings.                                          |
| **15. Security-team guardrails**                      | Security/platform teams can establish baseline requirements such as signed commits, secret scanning, and mandatory security checks.                                |
| **16. Platform-engineering golden repository**        | Put a standard `policy.yml` into repository templates so newly created services start with the expected controls.                                                  |
| **17. Environment-specific branch governance**        | Give `main`, `develop`, `release`, and maintenance branches different levels of protection.                                                                        |
| **18. Incremental adoption on existing repositories** | Managed-scope behavior means you can govern only selected branches/settings without taking control of everything in the repository.                                |
| **19. Strong desired-state enforcement**              | Enable strict mode where selected configuration should exactly match the declared policy.                                                                          |
| **20. Scheduled compliance monitoring**               | Run the GitHub Action daily/weekly and fail when repository configuration has drifted. The project itself already uses this self-governance pattern. ([GitHub][1]) |

The `validate → audit → plan → apply` lifecycle is particularly useful because validation can happen
offline, `audit` is read-only, `plan` exposes the intended diff, and `apply` subsequently verifies
the live state again.

## Where I think the product becomes particularly useful

A strong enterprise scenario would look like this:

```text
Organization security baseline
        │
        ▼
repository-policy.yml
        │
        ├── PR approvals
        ├── required CI checks
        ├── signed commits
        ├── force-push protection
        ├── deletion protection
        ├── secret scanning
        └── Dependabot
        │
        ▼
     PR Review
        │
        ▼
repo-policy validate
        │
        ▼
repo-policy plan
        │
        ▼
Approved change
        │
        ▼
repo-policy apply
        │
        ▼
Scheduled audit ─────► Drift detected
                           │
                           └── alert / PR / remediation
```

That turns a normally hidden collection of GitHub UI settings into a **reviewable engineering
artifact**.

## Personas who could use it

The same tool has value for several audiences:

- **Individual/open-source maintainer:** avoid accidentally weakening protection on an important
  repository.
- **Platform engineer:** establish standard repository controls across engineering teams.
- **SRE:** protect release paths and require reliability-related CI gates.
- **DevSecOps/security engineer:** enforce secret scanning, security checks, signed commits, and
  dependency security controls.
- **Engineering manager:** ensure repository controls do not depend on individual developers
  remembering configuration standards.
- **Compliance/audit team:** demonstrate that repository controls are declared, versioned,
  reviewable, and continuously checked.

There is one important boundary today: it is primarily **single-repository governance**.
Organization-wide inheritance and multi-repository orchestration are currently roadmap items rather
than shipped capabilities.
