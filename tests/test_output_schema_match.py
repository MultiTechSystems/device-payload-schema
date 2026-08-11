"""The `match` construct in the generated output JSON Schema.

`process_fields` handled `switch` and `tlv` but not `match`, so every field inside a
match case was missing from the output schema. The three corpus schemas that use the
construct described almost nothing: rbs30x.yaml declared 3 properties and its decoder
reports 50.

Both syntaxes are covered. Option B nests the construct under `match:` with `cases`
keyed by value; the legacy form puts `on:`/`cases:` on a `type: match` field, with cases
as a list of `{case, fields}`. Nothing in the corpus uses the legacy form, so its tests
are the only thing holding that path up.
"""

import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

from generate_output_schema import generate_output_schema, match_branches  # noqa: E402


def props(schema):
    return generate_output_schema(schema).get("properties", {})


def option_b(body, extra_fields=""):
    return yaml.safe_load(
        "name: t\nendian: big\nfields:\n"
        "  - name: kind\n    type: u8\n    var: k\n"
        "  - match:\n" + body + extra_fields
    )


class TestOptionB:
    def test_every_case_contributes_its_fields(self):
        schema = option_b(
            "      field: $k\n"
            "      cases:\n"
            "        0: [{name: a, type: u8}]\n"
            "        1: [{name: b, type: u8}]\n"
        )
        assert {"a", "b"} <= set(props(schema))

    def test_a_case_given_as_a_dict_with_fields_contributes(self):
        schema = option_b(
            "      field: $k\n"
            "      cases:\n"
            "        0:\n          fields: [{name: a, type: u8}]\n"
        )
        assert "a" in props(schema)

    def test_name_declares_the_discriminator(self):
        # `name:` puts the matched value itself in the output.
        schema = option_b(
            "      field: $k\n"
            "      name: kind_value\n"
            "      cases:\n        0: [{name: a, type: u8}]\n"
        )
        assert props(schema)["kind_value"]["type"] == "integer"

    def test_var_alone_declares_nothing(self):
        # `var:` only stores a variable; it is not reported.
        schema = option_b(
            "      field: $k\n"
            "      var: stashed\n"
            "      cases:\n        0: [{name: a, type: u8}]\n"
        )
        assert "stashed" not in props(schema)

    def test_an_internal_name_is_not_declared(self):
        schema = option_b(
            "      field: $k\n"
            "      name: _hidden\n"
            "      cases:\n        0: [{name: a, type: u8}]\n"
        )
        assert "_hidden" not in props(schema)

    def test_a_default_list_contributes_its_fields(self):
        schema = option_b(
            "      field: $k\n"
            "      cases:\n        0: [{name: a, type: u8}]\n"
            "      default: [{name: fallback, type: u8}]\n"
        )
        assert "fallback" in props(schema)

    def test_a_default_inside_cases_contributes(self):
        schema = option_b(
            "      field: $k\n"
            "      cases:\n"
            "        0: [{name: a, type: u8}]\n"
            "        default: [{name: fallback, type: u8}]\n"
        )
        assert "fallback" in props(schema)

    def test_error_and_skip_defaults_contribute_nothing(self):
        for keyword in ("error", "skip"):
            schema = option_b(
                "      field: $k\n"
                "      cases:\n        0: [{name: a, type: u8}]\n"
                f"      default: {keyword}\n"
            )
            assert set(props(schema)) == {"kind", "a"}, keyword

    def test_types_widen_across_cases(self):
        # The same name reported as a lookup label in one case and a raw integer in
        # another has to accept both - a flat output has one property for it.
        schema = option_b(
            "      field: $k\n"
            "      cases:\n"
            "        0: [{name: state, type: u8, lookup: {0: closed, 1: open}}]\n"
            "        1: [{name: state, type: u8}]\n"
        )
        declared = props(schema)["state"]["type"]
        assert set(declared if isinstance(declared, list) else [declared]) == {
            "string",
            "integer",
        }

    def test_a_ref_inside_a_case_resolves(self):
        schema = yaml.safe_load(
            "name: t\nendian: big\n"
            "definitions:\n"
            "  block:\n    fields:\n      - name: inner\n        type: u8\n"
            "fields:\n"
            "  - name: kind\n    type: u8\n    var: k\n"
            "  - match:\n"
            "      field: $k\n"
            "      cases:\n"
            "        0: [{$ref: '#/definitions/block'}]\n"
        )
        assert "inner" in props(schema)

    def test_a_nested_match_resolves(self):
        schema = yaml.safe_load(
            "name: t\nendian: big\nfields:\n"
            "  - name: kind\n    type: u8\n    var: k\n"
            "  - match:\n"
            "      field: $k\n"
            "      cases:\n"
            "        0:\n"
            "          - match:\n"
            "              field: $k\n"
            "              cases:\n"
            "                0: [{name: deep, type: u8}]\n"
        )
        assert "deep" in props(schema)


class TestLegacySyntax:
    """`type: match` with `on:`. Unused in the corpus, so only these tests cover it."""

    def test_cases_as_a_list_of_case_field_pairs(self):
        schema = yaml.safe_load(
            "name: t\nendian: big\nfields:\n"
            "  - name: kind\n    type: u8\n"
            "  - name: body\n    type: match\n    on: kind\n"
            "    cases:\n"
            "      - case: 1\n        fields: [{name: a, type: u8}]\n"
            "      - case: 2\n        fields: [{name: b, type: u8}]\n"
        )
        assert {"a", "b"} <= set(props(schema))

    def test_default_fields_contribute(self):
        schema = yaml.safe_load(
            "name: t\nendian: big\nfields:\n"
            "  - name: kind\n    type: u8\n"
            "  - name: body\n    type: match\n    on: kind\n"
            "    cases:\n"
            "      - case: 1\n        fields: [{name: a, type: u8}]\n"
            "    default: [{name: fallback, type: u8}]\n"
        )
        assert "fallback" in props(schema)

    def test_the_match_field_itself_is_not_declared_as_a_value(self):
        # `body` is the construct, not a reported scalar.
        schema = yaml.safe_load(
            "name: t\nendian: big\nfields:\n"
            "  - name: kind\n    type: u8\n"
            "  - name: body\n    type: match\n    on: kind\n"
            "    cases:\n"
            "      - case: 1\n        fields: [{name: a, type: u8}]\n"
        )
        assert "body" not in props(schema)


class TestMatchBranches:
    def test_a_construct_with_no_cases_yields_nothing(self):
        assert match_branches({"match": {"field": "$k"}}) == []

    def test_non_dict_entries_are_dropped(self):
        branches = match_branches(
            {"match": {"field": "$k", "cases": {0: [{"name": "a"}, "junk", None]}}}
        )
        assert branches == [[{"name": "a"}]]


class TestTheSchemasThatNeededIt:
    def test_the_three_match_schemas_declare_far_more_than_before(self):
        # Counts at the commit before match traversal: 3, 3 and 2.
        for rel, floor in (
            ("schemas/devices/radio-bridge/rbs30x.yaml", 50),
            ("schemas/devices/dragino/laq4.yaml", 13),
            ("schemas/devices/radionode/rn320bth.yaml", 12),
        ):
            schema = yaml.safe_load(
                REPO_ROOT.joinpath(rel).read_text(encoding="utf-8")
            )
            assert len(props(schema)) >= floor, rel
