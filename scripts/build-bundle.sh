#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright 2026 plexsphere contributors
# SPDX-License-Identifier: BUSL-1.1
#
# Build (and optionally push) the Blueprint Catalog OCI bundle.
#
# The bundle is a plain OCI artifact: one layer per catalog file, with
# the layer's `org.opencontainers.image.title` annotation set to the
# repo-relative path `catalog/<slug>/<file>` and a per-file-kind media
# type. The consuming BlueprintSource (plexsphere#293) walks the layers,
# groups them by slug directory, and reads xrd.yaml + composition.yaml +
# metadata.json for each blueprint. This contract is documented in the
# README under "Bundle contract".
#
# Usage:
#   scripts/build-bundle.sh <ref>            # build + push to a registry
#   scripts/build-bundle.sh --dry-run <ref>  # build into a local OCI layout
#
# Examples:
#   scripts/build-bundle.sh ghcr.io/plexsphere/blueprints:v0.1.0
#   scripts/build-bundle.sh --dry-run ghcr.io/plexsphere/blueprints:v0.1.0
#
# Requires: oras (https://oras.land). On --dry-run no registry auth or
# network is needed; the artifact is assembled into ./_bundle (an OCI
# image layout) so packaging can be validated on pull requests.

set -euo pipefail

ARTIFACT_TYPE="application/vnd.plexsphere.blueprints.catalog.v1"
MT_XRD="application/vnd.plexsphere.blueprint.xrd.v1+yaml"
MT_COMPOSITION="application/vnd.plexsphere.blueprint.composition.v1+yaml"
MT_METADATA="application/vnd.plexsphere.blueprint.metadata.v1+json"

DRY_RUN=0
if [[ "${1:-}" == "--dry-run" ]]; then
  DRY_RUN=1
  shift
fi

REF="${1:-}"
if [[ -z "$REF" ]]; then
  echo "usage: $0 [--dry-run] <oci-ref>" >&2
  exit 2
fi

cd "$(dirname "$0")/.."

if [[ ! -d catalog ]]; then
  echo "error: catalog/ not found" >&2
  exit 1
fi

# Assemble the layer list: three files per blueprint, addressed by their
# repo-relative path so oras records that path as the layer title.
files=()
for d in catalog/*/; do
  slug="$(basename "$d")"
  files+=("catalog/${slug}/xrd.yaml:${MT_XRD}")
  files+=("catalog/${slug}/composition.yaml:${MT_COMPOSITION}")
  files+=("catalog/${slug}/metadata.json:${MT_METADATA}")
done

# Provenance annotations. REVISION/SOURCE come from CI when present.
REVISION="${GITHUB_SHA:-$(git rev-parse HEAD 2>/dev/null || echo unknown)}"
SOURCE="${GITHUB_SERVER_URL:-https://github.com}/${GITHUB_REPOSITORY:-plexsphere/blueprints}"

annotations=(
  "--annotation" "org.opencontainers.image.source=${SOURCE}"
  "--annotation" "org.opencontainers.image.revision=${REVISION}"
  "--annotation" "com.plexsphere.blueprints.bundle=catalog"
)

echo "Bundling ${#files[@]} files from $(ls -d catalog/*/ | wc -l | tr -d ' ') blueprint(s)"

if [[ "$DRY_RUN" -eq 1 ]]; then
  rm -rf _bundle
  oras push --oci-layout "_bundle:${REF##*:}" \
    --artifact-type "$ARTIFACT_TYPE" \
    "${annotations[@]}" \
    "${files[@]}"
  echo "dry-run: assembled OCI layout in ./_bundle (not pushed)"
else
  oras push "$REF" \
    --artifact-type "$ARTIFACT_TYPE" \
    "${annotations[@]}" \
    "${files[@]}"
  echo "pushed: $REF"
fi
