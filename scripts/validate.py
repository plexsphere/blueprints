#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright 2026 plexsphere contributors
# SPDX-License-Identifier: BUSL-1.1
#
# Structural validation for the blueprint catalog.
#
# Mirrors the checks plexsphere enforces in
# `internal/provisioning/blueprints/manifest.ValidatePair` so a bundle
# that passes here is accepted by the consuming BlueprintSource (see
# plexsphere#293), plus the catalog conventions documented in the
# README. Run from the repository root:
#
#     python3 scripts/validate.py
#
# Exits non-zero and prints one `FAIL` line per problem.

from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml

CATALOG = Path("catalog")
GROUP = "blueprints.plexsphere.com"
APIEXT_GROUP = "apiextensions.crossplane.io"
REQUIRED_METADATA = (
    "slug",
    "displayName",
    "description",
    "version",
    "providerKinds",
    "injectionStrategy",
    "parameterSchema",
)
INJECTION_STRATEGIES = {
    "provider-secret",
    "helm-values",
    "cloud-init-user-data",
}
# Optional lifecycle hint the broker reads to decide whether the resource
# enrols a mesh Node ("node", the default when omitted) or is a nodeless
# cloud object the substrate composes straight to Ready ("standalone").
MESH_ROLES = {
    "node",
    "standalone",
}


def load_yaml(path: Path) -> dict:
    with path.open() as fh:
        return yaml.safe_load(fh)


def validate_blueprint(d: Path) -> list[str]:
    slug = d.name
    errs: list[str] = []

    # --- presence of the three required files --------------------------
    xrd_p, comp_p, meta_p = d / "xrd.yaml", d / "composition.yaml", d / "metadata.json"
    for p in (xrd_p, comp_p, meta_p):
        if not p.is_file():
            errs.append(f"{slug}: missing {p.name}")
    if errs:
        return errs

    xrd = load_yaml(xrd_p)
    comp = load_yaml(comp_p)
    meta = json.loads(meta_p.read_text())

    # --- XRD: apiVersion group + kind ----------------------------------
    xrd_api = str(xrd.get("apiVersion", ""))
    if not xrd_api.startswith(APIEXT_GROUP + "/"):
        errs.append(f"{slug}: xrd apiVersion {xrd_api!r} not under {APIEXT_GROUP}")
    if xrd.get("kind") != "CompositeResourceDefinition":
        errs.append(f"{slug}: xrd kind must be CompositeResourceDefinition")

    names = xrd.get("spec", {}).get("names", {})
    xrd_group = xrd.get("spec", {}).get("group")
    xrd_kind = names.get("kind")
    xrd_plural = names.get("plural")
    xrd_name = xrd.get("metadata", {}).get("name")
    versions = xrd.get("spec", {}).get("versions", []) or []

    if xrd_group != GROUP:
        errs.append(f"{slug}: xrd group {xrd_group!r} != {GROUP}")
    if xrd_plural and xrd_name != f"{xrd_plural}.{xrd_group}":
        errs.append(
            f"{slug}: xrd metadata.name {xrd_name!r} != {xrd_plural}.{xrd_group}"
        )

    served = [v for v in versions if v.get("served")]
    if not served:
        errs.append(f"{slug}: xrd has no served version")
    xrd_version = served[0].get("name") if served else None

    # --- Composition: apiVersion group + kind + compositeTypeRef -------
    comp_api = str(comp.get("apiVersion", ""))
    if not comp_api.startswith(APIEXT_GROUP + "/"):
        errs.append(f"{slug}: composition apiVersion {comp_api!r} not under {APIEXT_GROUP}")
    if comp.get("kind") != "Composition":
        errs.append(f"{slug}: composition kind must be Composition")

    ref = comp.get("spec", {}).get("compositeTypeRef", {})
    # compositeTypeRef must name the XRD's group + version + kind.
    if xrd_group and xrd_version and ref.get("apiVersion") != f"{xrd_group}/{xrd_version}":
        errs.append(
            f"{slug}: compositeTypeRef.apiVersion {ref.get('apiVersion')!r} "
            f"!= {xrd_group}/{xrd_version}"
        )
    if ref.get("kind") != xrd_kind:
        errs.append(
            f"{slug}: compositeTypeRef.kind {ref.get('kind')!r} != XRD kind {xrd_kind!r}"
        )

    # --- catalog conventions ------------------------------------------
    if comp.get("metadata", {}).get("name") != slug:
        errs.append(
            f"{slug}: composition metadata.name "
            f"{comp.get('metadata', {}).get('name')!r} != slug (convention)"
        )

    # --- metadata.json -------------------------------------------------
    for field in REQUIRED_METADATA:
        if field not in meta:
            errs.append(f"{slug}: metadata.json missing required field {field!r}")
    if meta.get("slug") != slug:
        errs.append(f"{slug}: metadata.json slug {meta.get('slug')!r} != directory name")
    if "version" in meta and xrd_version and meta["version"] != xrd_version:
        errs.append(
            f"{slug}: metadata.json version {meta['version']!r} "
            f"!= xrd served version {xrd_version!r}"
        )
    if meta.get("injectionStrategy") not in INJECTION_STRATEGIES:
        errs.append(
            f"{slug}: injectionStrategy {meta.get('injectionStrategy')!r} "
            f"not one of {sorted(INJECTION_STRATEGIES)}"
        )
    # meshRole is optional (defaults to "node"); reject only a present-but-bogus value.
    if "meshRole" in meta and meta["meshRole"] not in MESH_ROLES:
        errs.append(
            f"{slug}: meshRole {meta['meshRole']!r} not one of {sorted(MESH_ROLES)}"
        )
    pk = meta.get("providerKinds")
    if not isinstance(pk, list) or not pk:
        errs.append(f"{slug}: providerKinds must be a non-empty list")

    return errs


def main() -> int:
    if not CATALOG.is_dir():
        print(f"FAIL: catalog directory {CATALOG} not found (run from repo root)")
        return 1

    dirs = sorted(p for p in CATALOG.iterdir() if p.is_dir())
    if not dirs:
        print("FAIL: no blueprints under catalog/")
        return 1

    all_errs: list[str] = []
    for d in dirs:
        errs = validate_blueprint(d)
        if errs:
            all_errs.extend(errs)
        else:
            print(f"ok   {d.name}")

    if all_errs:
        print()
        for e in all_errs:
            print(f"FAIL {e}")
        print(f"\n{len(all_errs)} problem(s) across {len(dirs)} blueprint(s)")
        return 1

    print(f"\nall {len(dirs)} blueprint(s) valid")
    return 0


if __name__ == "__main__":
    sys.exit(main())
