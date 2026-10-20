# SPDX-FileCopyrightText: Copyright 2026 plexsphere contributors
# SPDX-License-Identifier: BUSL-1.1
#
# Unit tests for scripts/validate.py. Run from the repository root:
#
#     python3 -m unittest discover -s scripts -p 'test_*.py' -v

from __future__ import annotations

import contextlib
import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path

import validate

ROOT = Path(__file__).resolve().parent.parent
PREFIX = "t: parameterSchema: "
# plexsphere's supportedParameterSources (parameter_source.go), spelled out
# here so that a source dropped from or added to the validator fails a test.
SOURCES = (
    "openstack.availability-zone",
    "openstack.external-network",
    "openstack.flavor",
    "openstack.image",
    "openstack.network",
)
# Go's unicode.IsSpace set plus U+FEFF, as code points, for the same reason.
GO_SPACE_CODE_POINTS = (
    0x09, 0x0A, 0x0B, 0x0C, 0x0D, 0x20, 0x85, 0xA0, 0x1680,
    *range(0x2000, 0x200B), 0x2028, 0x2029, 0x202F, 0x205F, 0x3000, 0xFEFF,
)


def param(**fields: object) -> dict:
    """One parameter entry, named "a" and typed "string" unless overridden."""
    entry: dict = {"name": "a", "type": "string"}
    entry.update(fields)
    return entry


def doc(*entries: object) -> dict:
    return {"parameters": list(entries)}


class TypedValueTest(unittest.TestCase):
    def test_accepted(self) -> None:
        cases = [
            ("integer", 3, 3),
            ("integer", 3.0, 3),
            ("integer", 1e2, 100),
            ("integer", -0.0, 0),
            ("integer", -(2**63), -(2**63)),
            # The largest float64 below 2**63.
            ("integer", 2**63 - 1024, 2**63 - 1024),
            # plexsphere decodes through float64, so 2**53 + 1 becomes 2**53.
            ("integer", 2**53 + 1, 2**53),
            ("string", "x", "x"),
            ("string", "", ""),
            ("boolean", False, False),
            ("boolean", True, True),
        ]
        for typ, value, want in cases:
            with self.subTest(typ=typ, value=value):
                got, ok = validate.typed_value(typ, value)
                self.assertTrue(ok)
                self.assertEqual(want, got)
                self.assertIs(type(want), type(got))

    def test_refused(self) -> None:
        cases = [
            ("integer", 3.5),
            ("integer", True),
            ("integer", 10**400),
            ("integer", float("inf")),
            ("integer", float("nan")),
            ("integer", None),
            ("integer", "3"),
            # Outside the int64 range every platform's plexsphere imports.
            ("integer", 2**63),
            ("integer", 2**63 - 1),
            ("integer", 1e20),
            ("integer", 10**30),
            ("integer", -(2**63) - 2048),
            ("string", 5),
            ("string", None),
            ("boolean", "true"),
            ("boolean", 0),
            ("boolean", None),
            ("bogus", "x"),
        ]
        for typ, value in cases:
            with self.subTest(typ=typ, value=value):
                self.assertEqual((None, False), validate.typed_value(typ, value))


class AcceptedSchemaTest(unittest.TestCase):
    def assertAccepted(self, schema: object, provider_kinds: list | None = None) -> None:
        kinds = ["openstack"] if provider_kinds is None else provider_kinds
        self.assertEqual([], validate.validate_parameter_schema("t", schema, kinds))

    def test_empty_documents(self) -> None:
        for schema in (None, {}, {"parameters": None}, {"parameters": []}):
            with self.subTest(schema=schema):
                self.assertAccepted(schema, [])

    def test_openstack_instance_schema(self) -> None:
        meta = json.loads((ROOT / "catalog/openstack-instance/metadata.json").read_text())
        self.assertAccepted(meta["parameterSchema"])

    def test_accepted_parameters(self) -> None:
        cases = {
            "null default on a required parameter": param(required=True, default=None),
            "integer default 3": param(type="integer", default=3),
            "integer default 3.0": param(type="integer", default=3.0),
            "integer default 1e2": param(type="integer", default=1e2),
            "null required": param(required=None),
            "empty source": param(source=""),
            "null source": param(source=None),
            "null description": param(description=None),
            "null allowedValues": param(allowedValues=None),
            "null allowedValues on a sourced parameter": param(source="openstack.image", allowedValues=None),
            "whitespace-only name": param(name="  "),
            "required with allowed values": param(required=True, allowedValues=["a", "b"]),
            "default among allowed values": param(default="a", allowedValues=["a", "b"]),
            "default x among allowed x": param(default="x", allowedValues=["x"]),
            "integer allowed values": param(type="integer", allowedValues=[1, 2.0, 1e1]),
            "integer default equal to an allowed value in float64": param(
                type="integer", default=2**53 + 1, allowedValues=[2**53]
            ),
            "lone surrogate default equal to U+FFFD": param(default="\ud800", allowedValues=["\ufffd"]),
            "U+001C is not space": param(allowedValues=["\u001c"]),
            "U+200B is not space": param(allowedValues=["\u200b"]),
            "U+180E is not space": param(allowedValues=["\u180e"]),
            "1024-byte description": param(description="x" * 1024),
            "1023-byte description": param(description="\u20ac" * 341),
            "lone surrogate in a description": param(description="\ud800"),
            "341 lone surrogates in a description": param(description="\ud800" * 341),
            "required sourced parameter": param(required=True, source="openstack.image"),
            "optional sourced parameter with a default": param(source="openstack.network", default="n"),
        }
        for label, entry in cases.items():
            with self.subTest(label):
                self.assertAccepted(doc(entry))

    def test_every_source(self) -> None:
        self.assertEqual(set(SOURCES), validate.PARAMETER_SOURCES)
        for source in SOURCES:
            with self.subTest(source=source):
                self.assertAccepted(doc(param(source=source)))

    def test_source_with_listed_provider_kind(self) -> None:
        self.assertAccepted(doc(param(source="openstack.image")), ["aws", "openstack"])

    def test_names_differing_only_in_case(self) -> None:
        self.assertAccepted(doc(param(), param(name="A")))


class RefusedSchemaTest(unittest.TestCase):
    def assertRefused(self, schema: object, want: str, provider_kinds: list | None = None) -> None:
        kinds = ["openstack"] if provider_kinds is None else provider_kinds
        got = validate.validate_parameter_schema("t", schema, kinds)
        self.assertEqual([PREFIX + want], got)

    def assertRefusedAll(self, schema: object, want: list[str], provider_kinds: list | None = None) -> None:
        kinds = ["openstack"] if provider_kinds is None else provider_kinds
        got = validate.validate_parameter_schema("t", schema, kinds)
        self.assertEqual([PREFIX + w for w in want], got)

    def test_document_shape(self) -> None:
        self.assertRefused([], "must be a JSON object")
        self.assertRefused("x", "must be a JSON object")
        self.assertRefused({"parameters": [], "extra": 1}, 'unknown key "extra"')
        self.assertRefused({"parameters": {}}, "parameters must be a list")
        self.assertRefused({"parameters": "x"}, "parameters must be a list")
        self.assertRefused({"parameters": ["x"]}, "parameters[0] must be a JSON object")
        self.assertRefused({"parameters": [5]}, "parameters[0] must be a JSON object")
        self.assertRefused({"parameters": [None]}, "parameter at index 0 declares an empty name")

    def test_every_document_key_sorted_then_parameters(self) -> None:
        # Inserted out of order, so a check that keeps insertion order fails.
        self.assertRefusedAll(
            {"zz": 1, "aa": 1, "parameters": [param(type="float")]},
            [
                'unknown key "aa"',
                'unknown key "zz"',
                'parameter "a" declares unsupported type "float"',
            ],
        )

    def test_non_object_entry_moves_to_the_next_entry(self) -> None:
        self.assertRefusedAll(
            {"parameters": ["x", param(type="float")]},
            [
                "parameters[0] must be a JSON object",
                'parameter "a" declares unsupported type "float"',
            ],
        )

    def test_unknown_keys(self) -> None:
        self.assertRefused(
            doc(param(allowed_values=["x"])),
            'parameter at index 0 has unknown key "allowed_values"',
        )
        self.assertRefused(
            doc({"Name": "a", "type": "string"}),
            'parameter at index 0 has unknown key "Name"',
        )
        self.assertRefused(doc(param(zz=1, aa=1)), 'parameter at index 0 has unknown key "aa"')

    def test_repeated_keys(self) -> None:
        cases = [
            # Go reads both occurrences and refuses the string; json.loads keeps true.
            (
                '{"parameters":[{"name":"a","type":"string","required":"x","required":true}]}',
                ['parameter at index 0 repeats key "required"'],
            ),
            # Go keeps the earlier source when a later one is null.
            (
                '{"parameters":[{"name":"a","type":"string","source":"openstack.flavour","source":null}]}',
                ['parameter at index 0 repeats key "source"'],
            ),
            # Both insertion orders, so only sorted order passes both.
            (
                '{"parameters":[{"type":"string","type":"string","name":"a","name":"a"}]}',
                ['parameter at index 0 repeats key "name"'],
            ),
            (
                '{"parameters":[{"name":"a","name":"a","type":"string","type":"string"}]}',
                ['parameter at index 0 repeats key "name"'],
            ),
            (
                '{"aa":1,"aa":2,"zz":1,"zz":2,"parameters":[{"name":"a","type":"float"}]}',
                [
                    'repeats key "aa"',
                    'repeats key "zz"',
                    'unknown key "aa"',
                    'unknown key "zz"',
                    'parameter "a" declares unsupported type "float"',
                ],
            ),
            (
                '{"parameters":[{"name":"a","type":"string","zz":1,"zz":2}]}',
                ['parameter at index 0 repeats key "zz"'],
            ),
            # Go merges the second list into the first one element by element.
            (
                '{"parameters":[{"name":"a","type":"string","required":true}],'
                '"parameters":[{"name":"a","type":"string","default":"x"}]}',
                ['repeats key "parameters"'],
            ),
        ]
        for text, want in cases:
            with self.subTest(text=text):
                self.assertRefusedAll(validate.load_json(text), want)

    def test_field_types(self) -> None:
        cases = [
            (param(name=5), '"name" must be a string'),
            (param(type=5), '"type" must be a string'),
            (param(required="true"), '"required" must be a boolean'),
            (param(required=1), '"required" must be a boolean'),
            (param(required=0), '"required" must be a boolean'),
            (param(description=5), '"description" must be a string'),
            (param(source=5), '"source" must be a string'),
            (param(allowedValues="x"), '"allowedValues" must be a list'),
            (param(allowedValues={"x": 1}), '"allowedValues" must be a list'),
            # Two wrong fields: the earlier one in the rule's order is named.
            ({"name": 5, "type": 5}, '"name" must be a string'),
            (param(type=5, required="x"), '"type" must be a string'),
            (param(required="x", description=5), '"required" must be a boolean'),
            (param(description=5, source=5), '"description" must be a string'),
            (param(source=5, allowedValues="x"), '"source" must be a string'),
        ]
        for entry, want in cases:
            with self.subTest(want=want, entry=entry):
                self.assertRefused(doc(entry), "parameter at index 0: " + want)

    def test_name(self) -> None:
        empty = "parameter at index 0 declares an empty name"
        self.assertRefused(doc({"type": "string"}), empty)
        self.assertRefused(doc(param(name="")), empty)
        self.assertRefused(doc(param(name=None)), empty)
        self.assertRefused(
            doc(param(name="a\x00b")),
            "parameter at index 0 declares a name containing a NUL character",
        )

    def test_type(self) -> None:
        for typ in ("float", "number", "Integer"):
            with self.subTest(typ=typ):
                self.assertRefused(doc(param(type=typ)), f'parameter "a" declares unsupported type "{typ}"')
        self.assertRefused(doc({"name": "a"}), 'parameter "a" declares unsupported type ""')
        self.assertRefused(doc(param(type=None)), 'parameter "a" declares unsupported type ""')

    def test_description(self) -> None:
        too_long = 'parameter "a" declares a description longer than 1024 bytes'
        self.assertRefused(doc(param(description="x" * 1025)), too_long)
        self.assertRefused(doc(param(description="\u20ac" * 342)), too_long)
        self.assertRefused(doc(param(description="\ud800" * 342)), too_long)
        self.assertRefused(
            doc(param(description="a\x00b")),
            'parameter "a" declares a description containing a NUL character',
        )

    def test_source(self) -> None:
        self.assertRefused(
            doc(param(source="openstack.flavour")),
            'parameter "a" declares unknown source "openstack.flavour"',
        )
        self.assertRefused(
            doc(param(source="OpenStack.flavor")),
            'parameter "a" declares unknown source "OpenStack.flavor"',
        )
        self.assertRefused(doc(param(source=" ")), 'parameter "a" declares unknown source " "')
        self.assertRefused(
            doc(param(type="integer", source="openstack.flavor")),
            'parameter "a" of type "integer" must not declare a source',
        )
        self.assertRefused(
            doc(param(source="openstack.flavor", allowedValues=[])),
            'parameter "a" declares both a source and allowed values',
        )

    def test_source_provider_kind(self) -> None:
        for source in ("openstack.flavor", "openstack.image"):
            for kinds in (["aws"], []):
                with self.subTest(source=source, kinds=kinds):
                    self.assertRefused(
                        doc(param(source=source)),
                        f'parameter "a" declares source "{source}", '
                        'whose provider kind "openstack" the entry does not list',
                        kinds,
                    )

    def test_allowed_values(self) -> None:
        self.assertRefused(
            doc(param(type="boolean", allowedValues=[True])),
            'parameter "a" of type "boolean" must not declare allowed values',
        )
        self.assertRefused(
            doc(param(allowedValues=[])),
            'parameter "a" declares an empty list of allowed values',
        )
        mismatch = 'parameter "a" declares an allowed value whose type does not match "string"'
        self.assertRefused(doc(param(allowedValues=["a", 1])), mismatch)
        self.assertRefused(doc(param(allowedValues=["a", None])), mismatch)
        self.assertEqual(set(map(chr, GO_SPACE_CODE_POINTS)), set(validate.GO_SPACE))
        for blank in ("", "\t\n", *map(chr, GO_SPACE_CODE_POINTS)):
            with self.subTest(blank=blank):
                self.assertRefused(
                    doc(param(allowedValues=[blank])),
                    'parameter "a" declares a blank string as an allowed value',
                )
        self.assertRefused(
            doc(param(allowedValues=["a\x00"])),
            'parameter "a" declares an allowed value containing a NUL character',
        )
        self.assertRefused(
            doc(param(allowedValues=["a", "a"])),
            'parameter "a" declares the allowed value a more than once',
        )
        self.assertRefused(
            doc(param(type="integer", allowedValues=[3, 3.0])),
            'parameter "a" declares the allowed value 3 more than once',
        )
        self.assertRefused(
            doc(param(type="integer", allowedValues=[2**53 + 1, 2**53])),
            'parameter "a" declares the allowed value 9007199254740992 more than once',
        )

    def test_defaults(self) -> None:
        self.assertRefused(
            doc(param(required=True, default="x")),
            'required parameter "a" must not declare a default',
        )
        self.assertRefused(
            doc(param(default=5)),
            'parameter "a" declares a default whose type does not match "string"',
        )
        self.assertRefused(
            doc(param(default={"x": 1})),
            'parameter "a" declares a default whose type does not match "string"',
        )
        for value in (3.5, True, "3", 10**400, {"x": 1}, 2**63, 1e20):
            with self.subTest(value=value):
                self.assertRefused(
                    doc(param(type="integer", default=value)),
                    'parameter "a" declares a default whose type does not match "integer"',
                )
        self.assertRefused(
            doc(param(type="boolean", default="true")),
            'parameter "a" declares a default whose type does not match "boolean"',
        )
        self.assertRefused(
            doc(param(default="a\x00")),
            'parameter "a" declares a default containing a NUL character',
        )
        self.assertRefused(
            doc(param(default="y", allowedValues=["x"])),
            'parameter "a" declares a default that is not one of its allowed values',
        )

    def test_first_failing_rule_wins(self) -> None:
        # Each entry breaks two adjacent rules; the earlier rule's message is
        # the one plexsphere's validateRawParameter returns.
        cases = [
            ({"name": "a", "type": "string", "bogus": 1, "required": "true"}, ["openstack"],
             'parameter at index 0 has unknown key "bogus"'),
            (param(name="", type=5), ["openstack"],
             'parameter at index 0: "type" must be a string'),
            (param(name="", type="float"), ["openstack"],
             "parameter at index 0 declares an empty name"),
            (param(name="a\x00b", type="float"), ["openstack"],
             "parameter at index 0 declares a name containing a NUL character"),
            (param(type="float", description="x" * 1025), ["openstack"],
             'parameter "a" declares unsupported type "float"'),
            (param(description="x" * 1025 + "\x00"), ["openstack"],
             'parameter "a" declares a description longer than 1024 bytes'),
            (param(description="\x00", source="bogus"), ["openstack"],
             'parameter "a" declares a description containing a NUL character'),
            (param(type="integer", source="openstack.flavour"), ["openstack"],
             'parameter "a" declares unknown source "openstack.flavour"'),
            (param(type="integer", source="openstack.flavor", allowedValues=[1]), ["openstack"],
             'parameter "a" of type "integer" must not declare a source'),
            (param(source="openstack.flavor", allowedValues=[]), [],
             'parameter "a" declares both a source and allowed values'),
            # plexsphere checks the provider kind after the whole parse and
            # reports the default here; the issue records this exception.
            (param(source="openstack.flavor", default=5), [],
             'parameter "a" declares source "openstack.flavor", '
             'whose provider kind "openstack" the entry does not list'),
            (param(type="boolean", allowedValues=[]), ["openstack"],
             'parameter "a" of type "boolean" must not declare allowed values'),
            (param(allowedValues=[1, ""]), ["openstack"],
             'parameter "a" declares an allowed value whose type does not match "string"'),
            (param(allowedValues=[" ", " "]), ["openstack"],
             'parameter "a" declares a blank string as an allowed value'),
            (param(default="y", allowedValues=["x", 1]), ["openstack"],
             'parameter "a" declares an allowed value whose type does not match "string"'),
            (param(required=True, default=5), ["openstack"],
             'required parameter "a" must not declare a default'),
            (param(type="integer", default="a\x00"), ["openstack"],
             'parameter "a" declares a default whose type does not match "integer"'),
            (param(default="a\x00", allowedValues=["x"]), ["openstack"],
             'parameter "a" declares a default containing a NUL character'),
        ]
        for entry, kinds, want in cases:
            with self.subTest(want=want):
                self.assertRefused(doc(entry), want, kinds)

    def test_duplicate_name(self) -> None:
        self.assertRefused(doc(param(), param()), 'duplicate parameter "a"')

    def test_duplicate_of_a_refused_entry_is_not_reported(self) -> None:
        self.assertRefused(doc(param(type="float"), param()), 'parameter "a" declares unsupported type "float"')

    def test_rules_run_before_the_duplicate_check(self) -> None:
        self.assertRefused(doc(param(), param(type="float")), 'parameter "a" declares unsupported type "float"')

    def test_lone_surrogates_read_as_u_fffd(self) -> None:
        # Go decodes a lone surrogate escape to U+FFFD, so these collide there.
        self.assertRefused(
            doc(param(allowedValues=["\ud800", "\udfff"])),
            'parameter "a" declares the allowed value \ufffd more than once',
        )
        self.assertRefused(
            doc(param(allowedValues=["\ud800", "\ufffd"])),
            'parameter "a" declares the allowed value \ufffd more than once',
        )
        self.assertRefused(doc(param(name="\ud800"), param(name="\ud801")), 'duplicate parameter "\ufffd"')
        self.assertRefused(
            doc(param(name="\ud800x", type="float")),
            'parameter "\ufffdx" declares unsupported type "float"',
        )
        self.assertRefused({"\ud800": 1, "parameters": []}, 'unknown key "\ufffd"')
        self.assertRefused(doc(param(**{"\udc00": 1})), 'parameter at index 0 has unknown key "\ufffd"')

    def test_deeply_nested_values(self) -> None:
        # json.loads parses these; the check must report them, not recurse into them.
        lists = "[" * 1000 + "1" + "]" * 1000
        objects = '{"k":' * 400 + "1" + "}" * 400
        cases = [
            ('{"parameters":[{"name":"a","type":"integer","default":%s}]}' % lists,
             ['parameter "a" declares a default whose type does not match "integer"']),
            ('{"parameters":[{"name":"a","type":"integer","default":%s}]}' % objects,
             ['parameter "a" declares a default whose type does not match "integer"']),
            ('{"parameters":[{"name":"a","type":"string","allowedValues":[%s]}]}' % objects,
             ['parameter "a" declares an allowed value whose type does not match "string"']),
            ('{"x":%s,"parameters":[]}' % lists, ['unknown key "x"']),
        ]
        for text, want in cases:
            with self.subTest(want=want, text=text[:40]):
                self.assertRefusedAll(validate.load_json(text), want)

    def test_one_message_per_broken_entry_in_order(self) -> None:
        self.assertRefusedAll(
            doc(param(type="float"), param(name="b", source="openstack.flavour")),
            [
                'parameter "a" declares unsupported type "float"',
                'parameter "b" declares unknown source "openstack.flavour"',
            ],
        )


class CatalogTest(unittest.TestCase):
    def validate_copy(self, mutate) -> list[str]:
        """Validate a copy of openstack-instance whose metadata `mutate` changed."""
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp) / "openstack-instance"
            shutil.copytree(ROOT / "catalog/openstack-instance", d)
            meta_p = d / "metadata.json"
            meta = json.loads(meta_p.read_text())
            mutate(meta)
            meta_p.write_text(json.dumps(meta))
            return validate.validate_blueprint(d)

    @staticmethod
    def set_on_image_id(meta: dict, **fields: object) -> None:
        for entry in meta["parameterSchema"]["parameters"]:
            if entry["name"] == "imageId":
                entry.update(fields)

    def run_main(self, slug: str, mutate) -> tuple[int, str]:
        """Run validate.main() on a copy of the catalog whose `slug` entry
        metadata `mutate` changed; return the exit code and the output."""
        with tempfile.TemporaryDirectory() as tmp:
            shutil.copytree(ROOT / "catalog", Path(tmp) / "catalog")
            meta_p = Path(tmp) / "catalog" / slug / "metadata.json"
            meta = json.loads(meta_p.read_text())
            mutate(meta)
            meta_p.write_text(json.dumps(meta))
            out = io.StringIO()
            with contextlib.chdir(tmp), contextlib.redirect_stdout(out):
                code = validate.main()
        return code, out.getvalue()

    def test_current_catalog_is_valid(self) -> None:
        for d in sorted(p for p in (ROOT / "catalog").iterdir() if p.is_dir()):
            with self.subTest(slug=d.name):
                self.assertEqual([], validate.validate_blueprint(d))

    def test_openstack_entries_declare_sources_and_descriptions(self) -> None:
        # The console offers a parameter's cloud objects only when it
        # declares a source, and shows its description as the field's hint.
        cases = {
            "openstack-instance": (
                "v1alpha3",
                {
                    "imageId": "openstack.image",
                    "flavorId": "openstack.flavor",
                    "networkId": "openstack.network",
                    "availabilityZone": "openstack.availability-zone",
                },
            ),
            "openstack-landing-zone": (
                "v1alpha2",
                {
                    "externalNetworkId": "openstack.external-network",
                    "networkName": None,
                    "cidr": None,
                    "dnsNameserver": None,
                },
            ),
        }
        for slug, (version, sources) in cases.items():
            with self.subTest(slug=slug):
                meta = json.loads((ROOT / "catalog" / slug / "metadata.json").read_text())
                params = meta["parameterSchema"]["parameters"]
                self.assertEqual(version, meta["version"])
                self.assertEqual(len(sources), len(params))
                self.assertEqual(sources, {entry["name"]: entry.get("source") for entry in params})
                for entry in params:
                    with self.subTest(slug=slug, parameter=entry["name"]):
                        description = entry.get("description")
                        self.assertIsInstance(description, str)
                        self.assertNotEqual("", description.strip())

    def test_bad_source_fails_through_validate_blueprint(self) -> None:
        got = self.validate_copy(lambda m: self.set_on_image_id(m, source="openstack.flavour"))
        self.assertEqual(
            [
                "openstack-instance: parameterSchema: "
                'parameter "imageId" declares unknown source "openstack.flavour"'
            ],
            got,
        )

    def test_sourced_parameter_without_a_description_passes(self) -> None:
        # The import accepts a sourced parameter without a description.
        got = self.validate_copy(lambda m: self.set_on_image_id(m, description=None))
        self.assertEqual([], got)

    def test_missing_parameter_schema_is_reported_once(self) -> None:
        got = self.validate_copy(lambda m: m.pop("parameterSchema"))
        self.assertEqual(
            ["openstack-instance: metadata.json missing required field 'parameterSchema'"],
            got,
        )

    def test_provider_kinds_string_does_not_raise(self) -> None:
        def mutate(meta: dict) -> None:
            meta["providerKinds"] = "openstack"

        self.assertEqual(
            [
                "openstack-instance: providerKinds must be a non-empty list",
                *(
                    "openstack-instance: parameterSchema: "
                    f'parameter "{name}" declares source "{source}", '
                    'whose provider kind "openstack" the entry does not list'
                    for name, source in (
                        ("imageId", "openstack.image"),
                        ("flavorId", "openstack.flavor"),
                        ("networkId", "openstack.network"),
                        ("availabilityZone", "openstack.availability-zone"),
                    )
                ),
            ],
            self.validate_copy(mutate),
        )

    def test_repeated_key_fails_through_validate_blueprint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp) / "openstack-instance"
            shutil.copytree(ROOT / "catalog/openstack-instance", d)
            meta_p = d / "metadata.json"
            text = meta_p.read_text()
            old = '"name": "imageId",'
            self.assertEqual(1, text.count(old))
            meta_p.write_text(text.replace(old, old + ' "source": "openstack.flavour", "source": null,'))
            got = validate.validate_blueprint(d)
        self.assertEqual(
            ['openstack-instance: parameterSchema: parameter at index 0 repeats key "source"'],
            got,
        )

    def validate_text(self, edit) -> list[str]:
        """Validate a copy of openstack-instance whose metadata.json text
        `edit` changed, for JSON that json.dumps cannot write."""
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp) / "openstack-instance"
            shutil.copytree(ROOT / "catalog/openstack-instance", d)
            meta_p = d / "metadata.json"
            meta_p.write_text(edit(meta_p.read_text()))
            return validate.validate_blueprint(d)

    def test_schema_input_keys_read_differently_by_plexsphere(self) -> None:
        def append(member: str):
            return lambda text: text.rstrip()[:-1] + ", " + member + "}\n"

        # Go matches metadata.json keys case-insensitively (U+017F folds to
        # "s") and keeps the last match, so each of these replaces the value
        # the schema check reads.
        cases = [
            (append('"ParameterSchema": {"parameters": [{"name": "a", "type": "float"}]}'),
             "ParameterSchema", "parameterSchema"),
            (append('"ProviderKinds": ["aws"]'), "ProviderKinds", "providerKinds"),
            (append('"parameter\\u017fchema": {"parameters": []}'), "parameter\u017fchema", "parameterSchema"),
        ]
        for edit, key, field in cases:
            with self.subTest(key=key):
                self.assertEqual(
                    [f"openstack-instance: metadata.json key {key!r} differs from {field!r} only in case"],
                    self.validate_text(edit),
                )
        # Go reads every occurrence, and the first one does not decode.
        self.assertEqual(
            ["openstack-instance: metadata.json repeats key 'providerKinds'"],
            self.validate_text(lambda text: text.replace("{", '{"providerKinds": "openstack", ', 1)),
        )
        # Go does not fold U+0131 (dotless i) to "i", so this key is not read.
        self.assertEqual([], self.validate_text(append('"prov\\u0131derKinds": ["aws"]')))

    def test_main_fails_on_a_refused_schema(self) -> None:
        def mutate(meta: dict) -> None:
            meta["parameterSchema"]["parameters"][2]["default"] = "3"

        code, out = self.run_main("aws-eks-cluster-daemonset", mutate)
        self.assertEqual(1, code)
        self.assertIn(
            "FAIL aws-eks-cluster-daemonset: parameterSchema: "
            'parameter "nodeCount" declares a default whose type does not match "integer"',
            out,
        )

    def test_main_prints_a_lone_surrogate_as_u_fffd(self) -> None:
        code, out = self.run_main(
            "openstack-instance",
            lambda m: self.set_on_image_id(m, name="\ud800", type="float"),
        )
        self.assertEqual(1, code)
        self.assertIn(
            'FAIL openstack-instance: parameterSchema: parameter "\ufffd" declares unsupported type "float"',
            out,
        )
        out.encode("utf-8")  # print() on a UTF-8 stdout raises for a lone surrogate


if __name__ == "__main__":
    unittest.main()
