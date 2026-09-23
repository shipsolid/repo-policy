#!/usr/bin/env bash
# Build command for Cloudflare Workers' native Git integration (dashboard-
# configured, watches this repo directly — see Settings -> Builds on the
# repo-policy Worker). Produces .docs-engine/dist, which wrangler.jsonc's
# assets.directory points at; Cloudflare runs `wrangler deploy` after this
# script exits. Uses docs-site.cloudflare.yaml (base: /) rather than
# docs-site.yaml, since this build serves the domain root, not a Pages
# subpath — see that file's header comment.
set -euo pipefail

git clone --depth 1 \
  https://github.com/shipsolid/docs-site.git \
  .docs-engine

cd .docs-engine
npm ci

export DOCS_SRC="$OLDPWD/docs"
export PROJECT_README="$OLDPWD/README.md"
export DOCS_SITE_CONFIG="$OLDPWD/docs-site.cloudflare.yaml"
export REPO_SLUG="${REPO_SLUG:?REPO_SLUG must be set}"

node scripts/gen-docs.mjs
node scripts/check-links.mjs
npx astro build
