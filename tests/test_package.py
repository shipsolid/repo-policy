import re
from pathlib import Path

import repo_policy


def test_version_is_a_valid_semver_string():
    # Not a hardcoded literal: semantic-release bumps this on every release, and a hardcoded
    # value here is exactly what let __version__ silently diverge from the published version
    # for 4 releases before anyone noticed (confirmed via a real `pip install repo-policy`).
    assert re.match(r"^\d+\.\d+\.\d+$", repo_policy.__version__)


def test_requirements_action_in_matches_pyproject_dependencies():
    # requirements-action.in (the Docker Action's hash-locked dependency input, Task 7) is
    # hand-copied from pyproject.toml's [project.dependencies] and must stay identical to it --
    # otherwise the Action's container and the published wheel could require different dependency
    # versions of the exact same repo-policy release. Plain string matching (not a TOML parser)
    # so this test doesn't need tomllib and stays runnable on the full requires-python range
    # (tomllib is 3.11+); scripts/verify-action-lock.sh runs the equivalent check in CI.
    repo_root = Path(__file__).resolve().parent.parent
    pyproject_text = (repo_root / "pyproject.toml").read_text()

    checked = 0
    for line in (repo_root / "requirements-action.in").read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped == "hatchling":
            continue
        assert f'"{stripped}"' in pyproject_text, (
            f"requirements-action.in has {stripped!r}, which is not one of pyproject.toml's "
            "[project.dependencies] -- keep the two in sync"
        )
        checked += 1

    assert checked > 0, "requirements-action.in has no runtime dependency lines to check"
