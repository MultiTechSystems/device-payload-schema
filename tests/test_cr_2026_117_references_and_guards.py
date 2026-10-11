"""CR-2026-117: an undeclared reference is rejected (PS-539), and a guard condition on an
absent value fails, so its `else` applies (PS-540). PS-536's tag component name binding
landed with PR #93 and is pinned by `_language-conformance/tag-component-ref.yaml`.

The PS-539 cases mirror the spec prototype's TestCR2026117ReferencesAndGuards, and the
message is its wording, which Go, Java and C# give identically. Python rejects at decode
time (PS-328); the validator and the TS013 generator reject at once.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

from generate_ts013_codec import TS013Generator  # noqa: E402
from schema_interpreter import SchemaInterpreter, reference_errors  # noqa: E402
from validate_schema import validate_schema_structure  # noqa: E402

MESSAGE = (
    "`$batery` ({key}) names no field, var, tag component, index or count_as "
    "the schema declares (PS-539)"
)


def schema_of(fields, **extra):
    schema = {"name": "t", "version": 1, "endian": "big", "fields": fields}
    schema.update(extra)
    return schema


def decode(fields, payload):
    result = SchemaInterpreter(schema_of(fields)).decode(bytes.fromhex(payload))
    assert result.success, result.errors
    return result.data


UNDECLARED = [
    ({"name": "x", "type": "number", "ref": "$batery"}, "ref"),
    (
        {
            "name": "x",
            "type": "number",
            "compute": {"op": "add", "a": "$battery", "b": "$batery"},
        },
        "b",
    ),
    ({"name": "x", "type": "u8", "name_from": "k_${batery}"}, "name_from"),
    (
        {"match": {"field": "$batery", "cases": {1: [{"name": "y", "type": "u8"}]}}},
        "field",
    ),
    (
        {
            "name": "x",
            "type": "number",
            "ref": "$battery",
            "guard": {"when": [{"field": "$batery", "gt": 0}], "else": 0},
        },
        "field",
    ),
    (
        {
            "name": "x",
            "type": "repeat",
            "count": "$batery",
            "fields": [{"name": "y", "type": "u8"}],
        },
        "count",
    ),
]


class TestUndeclaredReference:
    @pytest.mark.parametrize("field,key", UNDECLARED)
    def test_the_interpreter_refuses_it(self, field, key):
        schema = schema_of([{"name": "battery", "type": "u8"}, field])
        result = SchemaInterpreter(schema).decode(bytes.fromhex("0101"))
        assert not result.success
        assert MESSAGE.format(key=key) in result.errors

    @pytest.mark.parametrize("field,key", UNDECLARED)
    def test_the_validator_refuses_it(self, field, key):
        errors = validate_schema_structure(
            schema_of([{"name": "battery", "type": "u8"}, field])
        )
        assert MESSAGE.format(key=key) in errors

    @pytest.mark.parametrize("field,key", UNDECLARED)
    def test_the_generator_refuses_it(self, field, key):
        with pytest.raises(ValueError, match="PS-539"):
            TS013Generator(
                schema_of([{"name": "battery", "type": "u8"}, field])
            ).generate()

    def test_a_reference_into_a_definition_is_checked_where_spliced(self):
        schema = schema_of(
            [{"$ref": "#/definitions/d"}],
            definitions={
                "d": {"fields": [{"name": "x", "type": "number", "ref": "$batery"}]}
            },
        )
        assert reference_errors(schema) == [MESSAGE.format(key="ref")]


class TestWhatCountsAsDeclared:
    def test_a_var_a_tag_component_an_index_and_count_as(self):
        fields = [
            {"name": "raw", "type": "u8", "var": "kind"},
            {"name": "n", "type": "number", "ref": "$kind"},
            {
                "tlv": {
                    "tag_fields": [
                        {"name": "rsn", "type": "u8"},
                        {"name": "ch", "type": "u8"},
                    ],
                    "tag_key": "ch",
                    "cases": {1: [{"name": "r", "type": "number", "ref": "$rsn"}]},
                }
            },
            {
                "name": "e",
                "type": "repeat",
                "count": 1,
                "index": "i",
                "count_as": "k",
                "fields": [{"name": "v", "type": "number", "ref": "$i"}],
            },
            {"name": "after", "type": "number", "ref": "$k"},
        ]
        assert reference_errors(schema_of(fields)) == []

    def test_an_unnamed_element_is_declared_as_element(self):
        # PS-528: an `element` with no name is decoded as `element`.
        fields = [
            {
                "name": "v",
                "type": "repeat",
                "count": 2,
                "present_if": {"field": "$element", "ne": 0},
                "element": {"type": "u8"},
            }
        ]
        assert reference_errors(schema_of(fields)) == []
        fields[0]["element"]["name"] = "x"
        assert reference_errors(schema_of(fields)) == [
            MESSAGE.replace("batery", "element").format(key="field")
        ]

    def test_a_metadata_name_counts_and_its_source_is_not_checked(self):
        schema = schema_of(
            [
                {"name": "a", "type": "u8"},
                {"name": "b", "type": "number", "ref": "$eui"},
            ],
            metadata={"include": [{"name": "eui", "source": "$device_eui"}]},
        )
        assert reference_errors(schema) == []

    def test_data_is_not_read_as_a_reference(self):
        fields = [
            {"name": "a", "type": "u8", "lookup": {1: "$nothing"}},
            {
                "name": "b",
                "type": "number",
                "ref": "$a",
                "guard": {"when": [{"field": "$a", "gt": 0}], "else": "$nothing"},
            },
        ]
        assert reference_errors(schema_of(fields)) == []

    def test_a_match_default_is_walked_as_a_field_list(self):
        fields = [
            {"name": "k", "type": "u8"},
            {
                "match": {
                    "field": "$k",
                    "cases": {1: [{"name": "y", "type": "u8"}]},
                    "default": [{"name": "z", "type": "number", "ref": "$batery"}],
                }
            },
        ]
        assert reference_errors(schema_of(fields)) == [MESSAGE.format(key="ref")]

    def test_a_senml_name_template_is_a_reference(self):
        fields = [
            {"name": "a", "type": "u8", "senml": {"name": "t_${batery}", "unit": "Cel"}}
        ]
        assert reference_errors(schema_of(fields)) == [MESSAGE.format(key="name")]


GUARDED = [
    {"name": "a", "type": "u8"},
    {"name": "d", "type": "u8"},
    {"name": "r", "type": "number", "compute": {"op": "div", "a": "$a", "b": "$d"}},
]


class TestGuardOnAnAbsentValue:
    def test_the_condition_fails_so_else_applies(self):
        fields = GUARDED + [
            {
                "name": "c",
                "type": "number",
                "ref": "$a",
                "guard": {"when": [{"field": "$r", "gte": 0}], "else": -1},
            }
        ]
        assert decode(fields, "0800") == {"a": 8, "d": 0, "c": -1}

    def test_without_else_the_field_is_omitted(self):
        fields = GUARDED + [
            {
                "name": "c",
                "type": "number",
                "ref": "$a",
                "guard": {"when": [{"field": "$r", "gte": 0}]},
            }
        ]
        assert decode(fields, "0800") == {"a": 8, "d": 0}

    def test_ne_does_not_pass_on_an_absent_value(self):
        # An absent operand is not "not equal to" anything: the condition fails.
        fields = GUARDED + [
            {
                "name": "c",
                "type": "number",
                "ref": "$a",
                "guard": {"when": [{"field": "$r", "ne": 3}], "else": -1},
            }
        ]
        assert decode(fields, "0800") == {"a": 8, "d": 0, "c": -1}

    def test_a_present_operand_still_passes(self):
        fields = GUARDED + [
            {
                "name": "c",
                "type": "number",
                "ref": "$a",
                "guard": {"when": [{"field": "$r", "gte": 0}], "else": -1},
            }
        ]
        assert decode(fields, "0802") == {"a": 8, "d": 2, "r": 4, "c": 8}

    @pytest.mark.parametrize("payload,expected", [("0800", -1), ("0802", 8)])
    def test_the_generated_codec_agrees(self, payload, expected):
        fields = GUARDED + [
            {
                "name": "c",
                "type": "number",
                "ref": "$a",
                "guard": {"when": [{"field": "$r", "ne": 3}], "else": -1},
            }
        ]
        code = TS013Generator(schema_of(fields), slim=True).generate()
        script = (
            code
            + "\nprocess.stdout.write(JSON.stringify(decodeUplink("
            + json.dumps({"bytes": list(bytes.fromhex(payload)), "fPort": 1})
            + ").data));"
        )
        done = subprocess.run(
            ["node"], input=script, capture_output=True, text=True, check=True
        )
        assert json.loads(done.stdout)["c"] == expected
