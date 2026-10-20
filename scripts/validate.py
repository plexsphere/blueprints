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
# README. Each entry's `parameterSchema` is held to the rules of
# plexsphere's `ParseParameterSchema` and to the provider-kind rule of
# `buildBlueprintVersion` (both in `internal/provisioning/blueprints`, as of
# plexsphere/plexsphere@9fed145aee6c9567a2b3d42687de5848ad690148): an
# import that breaks one reports the entry with an `error` outcome, and a
# publish refuses the same schema with `invalid_parameter_schema`. Run
# from the repository root:
#
#     python3 scripts/validate.py
#
# Exits non-zero and prints one `FAIL` line per problem.

from __future__ import annotations

import json
import re
import sys
from collections import Counter
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

# --- parameter schema: the rules plexsphere's ParseParameterSchema applies --
PARAMETER_TYPES = {"boolean", "integer", "string"}
# The closed, case-sensitive set of cloud object kinds a string parameter
# may name as its source; the text before the "." is the provider kind the
# entry must list.
PARAMETER_SOURCES = {
    "openstack.availability-zone",
    "openstack.external-network",
    "openstack.flavor",
    "openstack.image",
    "openstack.network",
}
PARAMETER_KEYS = {"name", "type", "required", "default", "description", "allowedValues", "source"}
MAX_PARAMETER_DESCRIPTION_BYTES = 1024
# What Go's unicode.IsSpace reports, plus U+FEFF: the characters plexsphere
# strips before it decides that an allowed value is blank.
GO_SPACE = (
    "\t\n\v\f\r \u0085\u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006"
    "\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000\ufeff"
)
# plexsphere decodes an integer as a float64 and keeps it when the int64
# conversion round-trips. Go leaves the conversion of an out-of-range
# float to the platform (arm64 saturates, amd64 does not), so only the
# range every platform imports is accepted here.
INT64_MIN = -(2**63)
INT64_END = 2**63
# The JSON type each parameter field must have when it is not null.
PARAMETER_FIELD_TYPES = (
    ("name", str, "string"),
    ("type", str, "string"),
    ("required", bool, "boolean"),
    ("description", str, "string"),
    ("source", str, "string"),
    ("allowedValues", list, "list"),
)
# The metadata.json keys whose values the parameter-schema check reads.
SCHEMA_INPUT_KEYS = ("parameterSchema", "providerKinds")
# json.loads has already joined every valid surrogate pair, so a surrogate
# code point left in a decoded string is a lone one.
LONE_SURROGATE = re.compile("[\ud800-\udfff]")


class JSONObject(dict):
    """A decoded JSON object that remembers which keys its text repeated.

    json.loads keeps only the last value of a repeated key. plexsphere's Go
    decoder reads every occurrence, so an earlier one can still fail the
    import (a value of the wrong type, or a "parameters" list it merges into
    the next one element by element). The schema check refuses a repeated
    key instead.
    """

    def __init__(self, pairs: object = (), repeated: object = ()) -> None:
        super().__init__(pairs)
        self.repeated: tuple[str, ...] = tuple(repeated)


def load_json(text: str) -> object:
    def object_pairs(pairs: list[tuple[str, object]]) -> JSONObject:
        counts = Counter(key for key, _ in pairs)
        return JSONObject(pairs, sorted(key for key, n in counts.items() if n > 1))

    return json.loads(text, object_pairs_hook=object_pairs)


def go_decoded(value: object, depth: int = 4) -> object:
    """Return a decoded JSON value as Go's encoding/json holds it: Go turns
    each lone surrogate escape into U+FFFD, where json.loads keeps it.

    Only the first `depth` levels are rewritten. The rules read nothing
    deeper than an allowed value (document, parameters, entry,
    allowedValues, element), and stopping there keeps a deeply nested
    value from exhausting Python's recursion limit.
    """
    if isinstance(value, str):
        return LONE_SURROGATE.sub("\ufffd", value)
    if depth == 0:
        return value
    if isinstance(value, list):
        return [go_decoded(item, depth - 1) for item in value]
    if isinstance(value, dict):
        return JSONObject(
            ((go_decoded(key), go_decoded(item, depth - 1)) for key, item in value.items()),
            (go_decoded(key) for key in getattr(value, "repeated", ())),
        )
    return value


def load_yaml(path: Path) -> dict:
    with path.open() as fh:
        return yaml.safe_load(fh)


def quote(s: str) -> str:
    return json.dumps(s, ensure_ascii=False)


def typed_value(typ: str, value: object) -> tuple[object, bool]:
    """Decode a default or an allowed value the way plexsphere's
    decodeTypedValue does.

    Returns the value in the form plexsphere compares it in, and whether
    its JSON type matches the declared type. JSON null never matches. An
    integer goes through float64 like plexsphere's, so 3.0 and 1e2 are
    integers, 3.5 and true are not, and the result is an int in the int64
    range.
    """
    if value is None:
        return None, False
    if typ == "string":
        return (value, True) if isinstance(value, str) else (None, False)
    if typ == "boolean":
        return (value, True) if isinstance(value, bool) else (None, False)
    if typ == "integer":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None, False
        try:
            f = float(value)
        except OverflowError:
            return None, False
        if not f.is_integer() or not INT64_MIN <= f < INT64_END:
            return None, False
        return int(f), True
    return None, False


def is_blank(s: str) -> bool:
    return s.strip(GO_SPACE) == ""


def parameter_problem(index: int, entry: dict, provider_kinds: list) -> str | None:
    """Return the first rule one parameter entry breaks, in the order
    plexsphere's validateRawParameter checks them, or None."""
    repeated = getattr(entry, "repeated", ())
    if repeated:
        return f"parameter at index {index} repeats key {quote(repeated[0])}"
    unknown = sorted(set(entry) - PARAMETER_KEYS)
    if unknown:
        return f"parameter at index {index} has unknown key {quote(unknown[0])}"
    for field, kind, label in PARAMETER_FIELD_TYPES:
        value = entry.get(field)
        if value is not None and not isinstance(value, kind):
            return f'parameter at index {index}: "{field}" must be a {label}'

    # A JSON null reads as the field's zero value, as it does in Go.
    name = entry.get("name") or ""
    typ = entry.get("type") or ""
    required = entry.get("required") or False
    description = entry.get("description") or ""
    source = entry.get("source") or ""
    allowed = entry.get("allowedValues")
    default = entry.get("default")

    if name == "":
        return f"parameter at index {index} declares an empty name"
    if "\x00" in name:
        return f"parameter at index {index} declares a name containing a NUL character"
    if typ not in PARAMETER_TYPES:
        return f"parameter {quote(name)} declares unsupported type {quote(typ)}"
    if len(description.encode("utf-8")) > MAX_PARAMETER_DESCRIPTION_BYTES:
        return (
            f"parameter {quote(name)} declares a description longer than "
            f"{MAX_PARAMETER_DESCRIPTION_BYTES} bytes"
        )
    if "\x00" in description:
        return f"parameter {quote(name)} declares a description containing a NUL character"

    if source:
        if source not in PARAMETER_SOURCES:
            return f"parameter {quote(name)} declares unknown source {quote(source)}"
        if typ != "string":
            return f"parameter {quote(name)} of type {quote(typ)} must not declare a source"
        if allowed is not None:
            return f"parameter {quote(name)} declares both a source and allowed values"
        kind = source.split(".", 1)[0]
        if kind not in provider_kinds:
            return (
                f"parameter {quote(name)} declares source {quote(source)}, "
                f"whose provider kind {quote(kind)} the entry does not list"
            )

    values: list | None = None
    if allowed is not None:
        if typ == "boolean":
            return f'parameter {quote(name)} of type "boolean" must not declare allowed values'
        if not allowed:
            return f"parameter {quote(name)} declares an empty list of allowed values"
        values = []
        for item in allowed:
            value, ok = typed_value(typ, item)
            if not ok:
                return (
                    f"parameter {quote(name)} declares an allowed value "
                    f"whose type does not match {quote(typ)}"
                )
            if isinstance(value, str):
                if is_blank(value):
                    return f"parameter {quote(name)} declares a blank string as an allowed value"
                if "\x00" in value:
                    return (
                        f"parameter {quote(name)} declares an allowed value "
                        "containing a NUL character"
                    )
            if value in values:
                return f"parameter {quote(name)} declares the allowed value {value} more than once"
            values.append(value)

    if default is not None:
        if required:
            return f"required parameter {quote(name)} must not declare a default"
        value, ok = typed_value(typ, default)
        if not ok:
            return f"parameter {quote(name)} declares a default whose type does not match {quote(typ)}"
        if isinstance(value, str) and "\x00" in value:
            return f"parameter {quote(name)} declares a default containing a NUL character"
        if values is not None and value not in values:
            return f"parameter {quote(name)} declares a default that is not one of its allowed values"
    return None


def validate_parameter_schema(slug: str, schema: object, provider_kinds: list) -> list[str]:
    """Check a metadata.json `parameterSchema` value against the rules a
    plexsphere import applies to it. Returns one message per problem: one
    per broken parameter, and the first rule each one breaks, the way
    ParseParameterSchema reports a parameter.
    """
    prefix = f"{slug}: parameterSchema: "
    schema = go_decoded(schema)
    if schema is None:
        return []
    if not isinstance(schema, dict):
        return [prefix + "must be a JSON object"]

    errs = [prefix + f"repeats key {quote(key)}" for key in schema.repeated]
    errs += [prefix + f"unknown key {quote(key)}" for key in sorted(set(schema) - {"parameters"})]
    params = schema.get("parameters")
    if params is None:
        return errs
    if not isinstance(params, list):
        errs.append(prefix + "parameters must be a list")
        return errs

    seen: set[str] = set()
    for index, entry in enumerate(params):
        if entry is None:
            entry = {}
        if not isinstance(entry, dict):
            errs.append(prefix + f"parameters[{index}] must be a JSON object")
            continue
        problem = parameter_problem(index, entry, provider_kinds)
        if problem is None:
            name = entry["name"]
            if name in seen:
                problem = f"duplicate parameter {quote(name)}"
            seen.add(name)
        if problem is not None:
            errs.append(prefix + problem)
    return errs


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
    meta = load_json(meta_p.read_text())

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
    # plexsphere decodes metadata.json with Go's case-insensitive field
    # matching, where the last match wins and every occurrence must decode.
    # A repeat or a case variant of a key the schema check reads would make
    # plexsphere read a value other than the one checked here.
    for field in SCHEMA_INPUT_KEYS:
        if field in getattr(meta, "repeated", ()):
            errs.append(f"{slug}: metadata.json repeats key {field!r}")
        for key in sorted(meta):
            if key != field and key.casefold() == field.casefold():
                errs.append(f"{slug}: metadata.json key {key!r} differs from {field!r} only in case")
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
    if "parameterSchema" in meta:
        errs.extend(
            validate_parameter_schema(slug, meta["parameterSchema"], pk if isinstance(pk, list) else [])
        )

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
