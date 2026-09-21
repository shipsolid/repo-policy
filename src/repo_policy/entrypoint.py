"""Translates GitHub Actions `with:` inputs (INPUT_* env vars) into repo-policy CLI args."""

from __future__ import annotations

import os
import sys

from repo_policy.cli import main


def run() -> None:
    # `or` (not .get(key, default)) so an explicitly empty `with: config: ''` in the caller's
    # workflow still falls back to the documented default instead of passing "" through.
    config_path = os.environ.get("INPUT_CONFIG") or ".github/repository-policy.yml"
    mode = os.environ.get("INPUT_MODE") or "audit"
    if mode not in {"validate", "audit", "plan", "apply"}:
        print(
            f"::error::unsupported mode '{mode}' — expected validate, audit, plan, or apply",
            file=sys.stderr,
        )
        sys.exit(2)
    sys.argv = ["repo-policy", mode, "--config", config_path]
    main()


if __name__ == "__main__":
    run()
