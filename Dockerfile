# Digest-pinned so the base image can never drift out from under a released Action version --
# resolved with: docker buildx imagetools inspect python:3.12-slim
FROM python:3.12.14-slim-trixie@sha256:2f17fc044b579bab302c2e8054d3a686e2cb9a83de48e70534b94cd8ebbe06a9

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
COPY requirements-action.txt ./

# Two-phase install: locked/hashed dependencies first (--require-hashes refuses to proceed if
# requirements-action.txt is missing a hash or doesn't match pyproject.toml -- see
# scripts/verify-action-lock.sh), then the package itself with --no-deps (nothing beyond what's
# already locked) --no-build-isolation (reuses the hatchling already installed above instead of
# letting pip resolve a fresh, unpinned build environment for it).
RUN pip install --no-cache-dir --require-hashes -r requirements-action.txt
RUN pip install --no-cache-dir --no-deps --no-build-isolation .

# Runs as the image's built-in non-root account: repo-policy only reads the mounted config file
# and talks to the GitHub API, so it never needs root inside the container.
USER nobody

ENTRYPOINT ["python", "-m", "repo_policy.entrypoint"]
