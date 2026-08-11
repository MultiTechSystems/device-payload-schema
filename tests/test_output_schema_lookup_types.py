"""Typing a `lookup` and an `enum` in the generated output JSON Schema.

PS-106: lookup values MAY be numbers or strings. The generator assumed otherwise - a
mapping was declared `["string", "integer"]` whatever it mapped to, and a sequence was
declared `{"type": "string", "enum": [...]}` even when its entries were numbers. So the
declaration was simultaneously too loose for the 23 string-valued mappings in the corpus
and wrong for a mapping to floats.

The reported value's type comes from the mapping's own values now, and both forms are
closed, so the value set is declared as an `enum`:

- A mapping omits the field where the value is not a key and no `default` is declared
  (PS-269), so the raw number is never reported.
- A sequence is indexed from zero (PS-104) and an out-of-bounds index MUST be an error
  (PS-105).

`type: enum` depends on whether a `default` is declared: with one, an unmapped value is
reported as that default (PS-068) and the set is closed; without one it is reported as the
string `"unknown(<n>)"`, so the set is open and no enum can be declared.

Every claim here was measured against the interpreter rather than read off the code.
"""

import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

from generate_output_schema import (  # noqa: E402
    generate_output_schema,
    lookup_json_schema,
    scalar_json_type,
    union_types,
)
from schema_interpreter import SchemaInterpreter  # noqa: E402


def declared(body):
    schema = yaml.safe_load("name: t\nendian: big\nfields:\n" + body)
    return generate_output_schema(schema)["properties"]["v"]


def reported(body, payload_hex):
    schema = yaml.safe_load("name: t\nendian: big\nfields:\n" + body)
    return SchemaInterpreter(schema).decode(bytes.fromhex(payload_hex)).data


class TestScalarTypes:
    @pytest.mark.parametrize(
        "value,expected",
        [
            (True, "boolean"),   # before integer: a bool is an int in Python
            (False, "boolean"),
            (3, "integer"),
            (-3, "integer"),
            (1.5, "number"),
            ("x", "string"),
            (None, "null"),
        ],
    )
    def test_scalar_json_type(self, value, expected):
        assert scalar_json_type(value) == expected

    def test_integer_collapses_into_number(self):
        # Every integer is a JSON number, so the union would be redundant.
        assert union_types([1, 1.5]) == ["number"]

    def test_distinct_types_are_kept_in_first_seen_order(self):
        assert union_types(["a", 1]) == ["string", "integer"]

    def test_duplicates_collapse(self):
        assert union_types(["a", "b"]) == ["string"]


class TestMappingLookup:
    def test_string_values_are_a_string(self):
        # Was ["string", "integer"], which admitted a number that can never be reported.
        assert declared("  - name: v\n    type: u8\n    lookup: {0: alpha, 1: beta}\n") == {
            "type": "string",
            "enum": ["alpha", "beta"],
        }

    def test_integer_values_are_an_integer(self):
        assert declared("  - name: v\n    type: u8\n    lookup: {0: 100, 1: 250}\n") == {
            "type": "integer",
            "enum": [100, 250],
        }

    def test_float_values_are_a_number(self):
        # The old code declared this an integer.
        assert declared("  - name: v\n    type: u8\n    lookup: {0: 1.5, 1: 2.5}\n") == {
            "type": "number",
            "enum": [1.5, 2.5],
        }

    def test_mixed_values_are_a_union(self):
        assert declared(
            "  - name: v\n    type: u8\n    lookup: {0: alpha, 1: 250}\n"
        ) == {"type": ["string", "integer"], "enum": ["alpha", 250]}

    def test_a_default_label_joins_the_value_set(self):
        assert declared(
            "  - name: v\n    type: u8\n    lookup: {0: alpha, default: other}\n"
        ) == {"type": "string", "enum": ["alpha", "other"]}

    @pytest.mark.parametrize(
        "body,payload,expected",
        [
            ("  - name: v\n    type: u8\n    lookup: {0: alpha, 1: beta}\n", "01", "beta"),
            ("  - name: v\n    type: u8\n    lookup: {0: 100, 1: 250}\n", "01", 250),
            ("  - name: v\n    type: u8\n    lookup: {0: 1.5}\n", "00", 1.5),
        ],
    )
    def test_the_declared_type_accepts_what_is_reported(self, body, payload, expected):
        assert reported(body, payload)["v"] == expected
        assert expected in declared(body)["enum"]

    def test_an_unmapped_value_reports_nothing_so_the_set_stays_closed(self):
        # PS-269. This is what makes an `enum` safe here: there is no raw fallback.
        body = "  - name: v\n    type: u8\n    lookup: {0: alpha, 1: beta}\n"
        assert "v" not in reported(body, "09")

    def test_an_unmapped_value_with_a_default_reports_the_default(self):
        body = "  - name: v\n    type: u8\n    lookup: {0: alpha, default: other}\n"
        assert reported(body, "09")["v"] == "other"


class TestSequenceLookup:
    def test_string_entries_are_a_string(self):
        assert declared("  - name: v\n    type: u8\n    lookup: [zero, one]\n") == {
            "type": "string",
            "enum": ["zero", "one"],
        }

    def test_numeric_entries_are_an_integer(self):
        # The old code declared any sequence a string, whatever it held.
        assert declared("  - name: v\n    type: u8\n    lookup: [10, 20]\n") == {
            "type": "integer",
            "enum": [10, 20],
        }

    def test_an_in_range_index_reports_its_entry(self):
        body = "  - name: v\n    type: u8\n    lookup: [zero, one]\n"
        assert reported(body, "01")["v"] == "one"

    def test_out_of_bounds_is_an_error_so_the_enum_stays_closed(self):
        """PS-105, now implemented, is what makes the closed `enum` correct.

        While every implementation silently reported the raw index instead, this
        declaration was arguably too strict. It is exactly right now: no conformant
        decode can report a value outside the sequence.
        """
        schema = yaml.safe_load(
            "name: t\nendian: big\nfields:\n"
            "  - name: v\n    type: u8\n    lookup: [zero, one]\n"
        )
        result = SchemaInterpreter(schema).decode(bytes.fromhex("09"))
        assert not result.success
        assert "v" not in result.data


class TestEnumType:
    def test_string_values_stay_a_string_with_no_enum(self):
        # An unmapped value is reported as "unknown(<n>)", so the set is open.
        assert declared(
            "  - name: v\n    type: enum\n    base: u8\n    values: {0: alpha}\n"
        ) == {"type": "string"}

    def test_a_default_closes_the_set_so_it_is_enumerated(self):
        # PS-068: an unmapped value is reported as `default`, not "unknown(<n>)".
        assert declared(
            "  - name: v\n    type: enum\n    base: u8\n    values: {0: alpha}\n"
            "    default: other\n"
        ) == {"type": "string", "enum": ["alpha", "other"]}

    def test_the_default_is_what_is_actually_reported(self):
        body = ("  - name: v\n    type: enum\n    base: u8\n    values: {0: alpha}\n"
                "    default: other\n")
        assert reported(body, "09")["v"] == "other"
        assert "other" in declared(body)["enum"]

    def test_numeric_values_still_admit_the_unknown_string(self):
        assert declared(
            "  - name: v\n    type: enum\n    base: u8\n    values: {0: 100}\n"
        ) == {"type": ["integer", "string"]}

    def test_the_unknown_form_is_what_is_actually_reported(self):
        body = "  - name: v\n    type: enum\n    base: u8\n    values: {0: alpha}\n"
        assert reported(body, "09")["v"] == "unknown(9)"

    def test_no_values_stays_permissive(self):
        assert declared("  - name: v\n    type: enum\n    base: u8\n")["type"] == [
            "string",
            "integer",
        ]


class TestLookupJsonSchema:
    @pytest.mark.parametrize("lookup", [None, "text", {}, [], 5])
    def test_unusable_lookups_yield_nothing(self, lookup):
        assert lookup_json_schema(lookup) is None

    def test_repeated_values_appear_once(self):
        assert lookup_json_schema({0: "a", 1: "a", 2: "b"})["enum"] == ["a", "b"]
